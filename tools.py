"""Tool handlers for Model Kombat (v0.2).

Contract: handlers take (args: dict, **kwargs), return a JSON string always,
never raise. Host ctx is bound at register time via set_ctx(); outside a live
Hermes session handlers degrade with a clear error JSON.

Scoring is mechanical: exact counts, numeric matches, synonym-aware phrase
matches, and subprocess execution of model-written code (opt-in only).
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

_CTX = None
_DATA_DIR_OVERRIDE: Path | None = None  # tests only


def set_ctx(ctx) -> None:
    global _CTX
    _CTX = ctx


def set_data_dir_override(path: Path | None) -> None:
    """Tests only: point plugin-data writes at a temp dir."""
    global _DATA_DIR_OVERRIDE
    _DATA_DIR_OVERRIDE = path


def _err(message: str, **extra) -> str:
    return json.dumps({"ok": False, "error": message, **extra})


def _ok(**payload) -> str:
    return json.dumps({"ok": True, **payload})


def _load_suite() -> dict:
    raw = json.loads((Path(__file__).parent / "tasks.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "tasks" not in raw or "rounds" not in raw:
        raise ValueError(
            "tasks.json has an unexpected shape (expected an object with 'tasks' and "
            "'rounds'); the plugin code and suite may be out of sync — reload the plugin."
        )
    tasks = {t["id"]: t for t in raw["tasks"]}
    return {
        "rounds": raw.get("rounds", []),
        "tasks": tasks,
        "order": [t["id"] for t in raw["tasks"]],
        "sudden_death_task": raw.get("sudden_death_task"),
    }


def _guard(fn):
    """Handler contract enforcement: never raise out of a tool.

    Unexpected exceptions become error JSON with a short traceback instead of
    a bare TOOL_ERROR, so failures are diagnosable from the chat transcript.
    """
    import functools
    import traceback

    @functools.wraps(fn)
    def wrapper(args: dict, **kwargs) -> str:
        try:
            return fn(args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — last-resort guard, by design
            tail = "\n".join(traceback.format_exc().splitlines()[-6:])
            return _err(f"internal error: {type(exc).__name__}: {exc}", traceback=tail)

    return wrapper


def _load_tasks() -> list[dict]:
    """Back-compat for older tests/callers: flat task list."""
    suite = _load_suite()
    return [suite["tasks"][tid] for tid in suite["order"]]


def _data_dir() -> Path:
    if _DATA_DIR_OVERRIDE is not None:
        return _DATA_DIR_OVERRIDE
    try:
        from plugins.plugin_storage import plugin_data_dir  # type: ignore

        return Path(plugin_data_dir("model-kombat"))
    except Exception:
        return Path.home() / ".hermes" / "plugin-data" / "model-kombat"


def _cfg(key: str, default=None):
    """Read a plugin setting (settings form / config.yaml), with fallback."""
    if _CTX is None or not hasattr(_CTX, "get_config"):
        return default
    try:
        return _CTX.get_config(key, default=default)
    except TypeError:
        try:
            return _CTX.get_config(key)
        except Exception:
            return default
    except Exception:
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- list tasks

@_guard
def kombat_list_tasks(args: dict, **kwargs) -> str:
    try:
        suite = _load_suite()
        return _ok(
            rounds=[
                {
                    "id": r["id"],
                    "name": r["name"],
                    "points_per_task": r["points"],
                    "tasks": r["tasks"],
                }
                for r in suite["rounds"]
            ],
            sudden_death_task=suite["sudden_death_task"],
            tasks=[
                {
                    "id": t["id"],
                    "title": t["title"],
                    "scorer": t["scorer"],
                    "round": next(
                        (r["id"] for r in suite["rounds"] if t["id"] in r["tasks"]), None
                    ),
                }
                for t in (suite["tasks"][tid] for tid in suite["order"])
            ],
        )
    except Exception as exc:
        return _err(f"Failed to load task suite: {exc}")


# ---------------------------------------------------------------- run a fight

def _usage_of(result) -> dict:
    usage = getattr(result, "usage", None)
    if usage is None and isinstance(result, dict):
        usage = result.get("usage")
    if usage is None:
        return {}
    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
    out = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd"):
        val = get(key)
        if val is not None:
            out[key] = val
    return out


def _complete_with_retry(ctx, fighter: str, prompt: str, temperature: float, max_tokens: int, timeout_s: float):
    """One retry on transient errors; PermissionError (trust gate) never retries."""
    system = (
        "You are a fighter in a model benchmark. Answer the task exactly as written, "
        "following every format constraint. Do not add commentary about the benchmark."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    last_exc = None
    for attempt in (1, 2):
        try:
            result = ctx.llm.complete(
                messages,
                model=fighter,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout_s,
                purpose="model-kombat fight",
            )
            text = result.get("text", "") if isinstance(result, dict) else getattr(result, "text", "")
            return str(text or ""), _usage_of(result), attempt, None
        except PermissionError:
            raise
        except Exception as exc:  # transient provider error — retry once
            last_exc = exc
    return "", {}, 2, str(last_exc)


@_guard
def kombat_run_fight(args: dict, **kwargs) -> str:
    fighter_a = str(args.get("fighter_a") or _cfg("fighter_a", "") or "").strip()
    fighter_b = str(args.get("fighter_b") or _cfg("fighter_b", "") or "").strip()
    if not fighter_a or not fighter_b:
        return _err("Both fighter_a and fighter_b model IDs are required.")
    if _CTX is None or not hasattr(_CTX, "llm"):
        return _err(
            "No live Hermes host context (ctx.llm unavailable). Run this inside a Hermes "
            "session with the plugin enabled, not from a bare Python import."
        )

    try:
        suite = _load_suite()
    except Exception as exc:
        return _err(f"Failed to load task suite: {exc}")

    wanted = args.get("tasks")
    if wanted:
        wanted = {str(w).upper() for w in wanted}
        order = [t for t in suite["order"] if t in wanted]
    else:
        order = list(suite["order"])
    if not order:
        return _err("No matching tasks. Call kombat_list_tasks for valid IDs.")

    try:
        temperature = float(args.get("temperature", _cfg("temperature", 0)))
        max_tokens = int(args.get("max_tokens", _cfg("max_tokens", 2000)))
        budget = float(args.get("budget_usd", _cfg("max_fight_cost_usd", 2.0)))
        timeout_s = float(args.get("timeout_s", _cfg("llm_timeout_s", 180)))
    except (TypeError, ValueError):
        return _err("temperature/max_tokens/budget_usd/timeout_s must be numbers.")

    # Kanban scoreboard (display only; never fails the fight).
    board = None
    if bool(_cfg("show_on_kanban", True)):
        try:
            from . import kanban as _kb

            board = _kb.Scoreboard(fighter_a, fighter_b, suite)
            board.start()
        except Exception:
            board = None

    answers: dict[str, dict[str, str]] = {fighter_a: {}, fighter_b: {}}
    meta: dict[str, dict[str, dict]] = {fighter_a: {}, fighter_b: {}}
    errors: list[dict] = []
    totals = {fighter_a: {"cost_usd": 0.0, "tokens": 0, "latency_s": 0.0},
              fighter_b: {"cost_usd": 0.0, "tokens": 0, "latency_s": 0.0}}
    budget_exceeded = False

    for tid in order:
        task = suite["tasks"][tid]
        round_id = next((r["id"] for r in suite["rounds"] if tid in r["tasks"]), None)
        if board is not None:
            board.round_fighting(round_id)
        for fighter in (fighter_a, fighter_b):
            started = time.perf_counter()
            try:
                text, usage, attempts, err = _complete_with_retry(
                    _CTX, fighter, task["prompt"], temperature, max_tokens, timeout_s
                )
            except PermissionError as exc:
                if board is not None:
                    board.abort()
                return _err(
                    f"Host refused the model override: {exc}. "
                    "Enable it in config.yaml: plugins.entries.model-kombat.llm."
                    "allow_model_override: true (optionally restrict with allowed_models), "
                    "then retry the fight. Nothing was sent.",
                    partial={"answers_a": answers[fighter_a], "answers_b": answers[fighter_b]},
                )
            latency = round(time.perf_counter() - started, 3)
            answers[fighter][tid] = text
            meta[fighter][tid] = {
                "latency_s": latency,
                "usage": usage,
                "attempts": attempts,
                "error": err,
            }
            totals[fighter]["latency_s"] += latency
            totals[fighter]["tokens"] += int(usage.get("total_tokens") or usage.get("output_tokens") or 0)
            totals[fighter]["cost_usd"] += float(usage.get("cost_usd") or 0.0)
            if err:
                errors.append({"task": tid, "fighter": fighter, "error": err})
            if totals[fighter]["cost_usd"] > budget:
                budget_exceeded = True
                break
        if budget_exceeded:
            break

    fight = {
        "fighter_a": fighter_a,
        "fighter_b": fighter_b,
        "mode": "pure",
        "answers_a": answers[fighter_a],
        "answers_b": answers[fighter_b],
        "meta_a": meta[fighter_a],
        "meta_b": meta[fighter_b],
        "errors": errors,
        "usage": totals,
        "budget_exceeded": budget_exceeded,
        "kanban": board.ids if board is not None else None,
    }
    try:
        if hasattr(_CTX, "state"):
            _CTX.state.set("last_fight", fight)
    except Exception:
        pass

    return _ok(
        fighters=[fighter_a, fighter_b],
        rounds=[t for t in order],
        answers_a=answers[fighter_a],
        answers_b=answers[fighter_b],
        meta_a=meta[fighter_a],
        meta_b=meta[fighter_b],
        errors=errors,
        usage=totals,
        budget_exceeded=budget_exceeded,
        kanban=board.ids if board is not None else None,
        kanban_errors=board.errors if board is not None else [],
    )


@_guard
def kombat_rematch_errors(args: dict, **kwargs) -> str:
    """Re-run only the errored (task, fighter) pairs from the last fight.

    Rate limits (429s) are the usual cause: the original fight excluded those
    tasks from points, which can nullify a championship. A rematch re-asks just
    the failed pairs, merges the new answers into last_fight, and shrinks the
    error list to whatever still fails — then kombat_score_answers scores the
    merged fight as usual.
    """
    if _CTX is None or not hasattr(_CTX, "llm"):
        return _err(
            "No live Hermes host context (ctx.llm unavailable). Run this inside a Hermes "
            "session with the plugin enabled, not from a bare Python import."
        )
    try:
        last = _CTX.state.get("last_fight", default=None) if hasattr(_CTX, "state") else None
    except Exception:
        last = None
    if not last:
        return _err("No fight stored in this session yet — run kombat_run_fight first.")
    errors = list(last.get("errors") or [])
    if not errors:
        return _err("Last fight has no errored tasks to rematch. Run kombat_score_answers to score it as-is.")

    try:
        suite = _load_suite()
    except Exception as exc:
        return _err(f"Failed to load task suite: {exc}")

    try:
        temperature = float(args.get("temperature", _cfg("temperature", 0)))
        max_tokens = int(args.get("max_tokens", _cfg("max_tokens", 2000)))
        budget = float(args.get("budget_usd", _cfg("max_fight_cost_usd", 2.0)))
        timeout_s = float(args.get("timeout_s", _cfg("llm_timeout_s", 180)))
    except (TypeError, ValueError):
        return _err("temperature/max_tokens/budget_usd/timeout_s must be numbers.")

    fighters = (last.get("fighter_a"), last.get("fighter_b"))
    answers = {fighters[0]: dict(last.get("answers_a") or {}),
               fighters[1]: dict(last.get("answers_b") or {})}
    meta = {fighters[0]: dict(last.get("meta_a") or {}),
            fighters[1]: dict(last.get("meta_b") or {})}
    usage = dict(last.get("usage") or {})
    rematched, still_failing = [], []
    spend = 0.0

    for err in errors:
        tid, fighter = err.get("task"), err.get("fighter")
        if tid not in suite["tasks"] or fighter not in fighters:
            still_failing.append(err)
            continue
        try:
            text, call_usage, attempts, call_err = _complete_with_retry(
                _CTX, fighter, suite["tasks"][tid]["prompt"], temperature, max_tokens, timeout_s
            )
        except PermissionError as exc:
            return _err(
                f"Host refused the model override during rematch: {exc}. "
                "Check plugins.entries.model-kombat.llm.allow_model_override in config.yaml."
            )
        spend += float(call_usage.get("cost_usd") or 0.0)
        if call_err:
            still_failing.append({"task": tid, "fighter": fighter, "error": call_err})
            answers[fighter][tid] = text
            meta[fighter][tid] = {"error": call_err, "attempts": attempts, "latency_s": 0, "usage": call_usage}
        else:
            rematched.append({"task": tid, "fighter": fighter})
            answers[fighter][tid] = text
            meta[fighter][tid] = {"error": None, "attempts": attempts, "latency_s": 0, "usage": call_usage}
        slot = usage.setdefault(fighter, {"cost_usd": 0.0, "tokens": 0, "latency_s": 0.0})
        slot["cost_usd"] = float(slot.get("cost_usd") or 0.0) + float(call_usage.get("cost_usd") or 0.0)
        slot["tokens"] = int(slot.get("tokens") or 0) + int(call_usage.get("total_tokens") or call_usage.get("output_tokens") or 0)
        if spend > budget:
            still_failing.extend(e for e in errors if e not in still_failing and e not in rematched)
            break

    last.update(
        {
            "answers_a": answers[fighters[0]],
            "answers_b": answers[fighters[1]],
            "meta_a": meta[fighters[0]],
            "meta_b": meta[fighters[1]],
            "errors": still_failing,
            "usage": usage,
        }
    )
    try:
        _CTX.state.set("last_fight", last)
    except Exception:
        pass

    # Best-effort kanban: a rematch is a NEW bout on the board (the original
    # cards are already done and can't be re-completed), so post fresh cards
    # for the affected rounds and point last_fight at them for scoring.
    kanban_note = None
    try:
        from . import kanban as _kb

        if bool(_cfg("show_on_kanban", True)):
            affected = sorted(
                {
                    r["id"]
                    for r in suite["rounds"]
                    if any(t in {e.get("task") for e in errors} for t in r["tasks"])
                }
            )
            board = _kb.Scoreboard(fighters[0], fighters[1], suite)
            board.start(round_ids=affected or None, title_prefix="REMATCH")
            if board.ids.get("parent"):
                board._run(
                    _kb._cli(
                        "--board", _kb.BOARD, "comment", board.ids["parent"],
                        f"Rematch of {len(errors)} errored task(s): {len(rematched)} re-fought, "
                        f"{len(still_failing)} still failing. Original fight card: {last.get('kanban', {}).get('parent')}.",
                    )
                )
            last["kanban"] = board.ids
            try:
                _CTX.state.set("last_fight", last)  # persist new kanban ids for scoring
            except Exception:
                pass
            kanban_note = (
                f"rematch bout posted on kanban board '{_kb.BOARD}' (parent={board.ids.get('parent')})"
                if board.ids.get("parent")
                else f"kanban rematch cards failed: {board.errors[:1]}"
            )
    except Exception as exc:
        kanban_note = f"kanban rematch cards skipped: {exc}"

    return _ok(
        fighters=list(fighters),
        rematched=rematched,
        still_failing=still_failing,
        errors=still_failing,
        answers_a=answers[fighters[0]],
        answers_b=answers[fighters[1]],
        usage=usage,
        kanban=kanban_note,
    )


# ---------------------------------------------------------------- scorers

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _score_money(answer: str, task: dict) -> dict:
    expected = [float(e.replace("$", "").replace(",", "")) for e in task.get("expected", [])]
    found_nums = [float(x) for x in re.findall(r"\$?\s*(\d+(?:\.\d+)?)", answer)]
    found, missing = [], []
    for exp, raw in zip(expected, task.get("expected", [])):
        if any(abs(n - exp) < 0.005 for n in found_nums):
            found.append(raw)
        else:
            missing.append(raw)
    return {
        "score": len(found),
        "max": len(expected),
        "passed": not missing,
        "details": [f"found: {', '.join(found) or 'none'}", f"missing: {', '.join(missing) or 'none'}"],
    }


def _score_recall(answer: str, task: dict) -> dict:
    """Each expected entry is a string or a list of acceptable alternatives."""
    low = _norm(answer)
    found, missing = [], []
    for entry in task.get("expected", []):
        alts = entry if isinstance(entry, list) else [entry]
        hit = next((a for a in alts if _norm(a) in low), None)
        (found if hit else missing).append(hit or alts[0])
    return {
        "score": len(found),
        "max": len(task.get("expected", [])),
        "passed": not missing,
        "details": [f"found: {', '.join(found) or 'none'}", f"missing: {', '.join(missing) or 'none'}"],
    }


_BANNED = ["amazing", "best", "incredible", "perfect"]


def _score_format25(answer: str, task: dict) -> dict:
    text = answer.strip()
    words = text.split()
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    checks = [
        ("exactly 25 words", len(words) == 25, f"{len(words)} words"),
        ("exactly 3 sentences", len(sentences) == 3, f"{len(sentences)} sentences"),
        ("starts with 'Stay'", text.startswith("Stay"), "yes" if text.startswith("Stay") else "no"),
        ("ends with a period", text.endswith("."), "yes" if text.endswith(".") else "no"),
    ]
    banned_found = [w for w in _BANNED if re.search(rf"\b{w}\b", text, re.I)]
    checks.append(("no banned words", not banned_found, f"banned found: {banned_found or 'none'}"))
    score = sum(1 for _, ok, _ in checks if ok)
    return {
        "score": score,
        "max": len(checks),
        "passed": score == len(checks),
        "details": [f"{label}: {detail}" for label, _, detail in checks],
    }


def _score_summary50(answer: str, task: dict) -> dict:
    words = answer.split()
    low = _norm(answer)
    missing = [e for e in task.get("expected", []) if _norm(e) not in low]
    checks = [
        ("at most 50 words", len(words) <= 50, f"{len(words)} words"),
        ("all numbers/dates/names preserved", not missing, f"missing: {missing or 'none'}"),
    ]
    score = sum(1 for _, ok, _ in checks if ok)
    return {
        "score": score,
        "max": len(checks),
        "passed": score == len(checks),
        "details": [f"{label}: {detail}" for label, _, detail in checks],
    }


def _extract_code(answer: str, marker: str) -> str | None:
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", answer, re.S | re.I)
    if blocks:
        return blocks[-1].strip()
    if marker in answer:
        return answer.strip()
    return None


def _execute_candidate(code: str, harness: str, timeout: int = 10) -> dict:
    """Run candidate code + harness in a subprocess. NOT a security sandbox:
    it runs as the user with a timeout and a stripped-down env. Only enable
    code execution for models you trust (see README disclosure)."""
    try:
        with tempfile.TemporaryDirectory(prefix="kombat-") as td:
            script = Path(td) / "candidate.py"
            script.write_text(code + "\n" + harness, encoding="utf-8")
            env = {"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0"}
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=td,
                env=env,
            )
        passed = proc.returncode == 0 and "ALL CHECKS PASSED" in proc.stdout
        lines = (proc.stdout + proc.stderr).strip().splitlines()
        return {"passed": passed, "details": lines[-3:] if lines else ["no output"]}
    except subprocess.TimeoutExpired:
        return {"passed": False, "details": [f"timeout after {timeout}s"]}
    except Exception as exc:
        return {"passed": False, "details": [f"harness error: {exc}"]}


_HARNESSES = {
    "T6": '''
import sys
for year, want in [(2000, True), (1900, False), (2024, True), (2023, False), (2100, False)]:
    got = is_leap_year(year)
    if got is not want:
        print(f"FAIL is_leap_year({year}) = {got!r}, want {want!r}"); sys.exit(1)
for bad, exc in [(True, TypeError), ("2024", TypeError), (0, ValueError), (-4, ValueError)]:
    try:
        is_leap_year(bad)
    except exc:
        continue
    except Exception as other:
        print(f"FAIL is_leap_year({bad!r}) raised {type(other).__name__}, want {exc.__name__}"); sys.exit(1)
    print(f"FAIL is_leap_year({bad!r}) did not raise {exc.__name__}"); sys.exit(1)
print("ALL CHECKS PASSED")
''',
    "T7": '''
import sys
valid = [[1,2,3,4,5,6,7,8,9],[4,5,6,7,8,9,1,2,3],[7,8,9,1,2,3,4,5,6],[2,3,4,5,6,7,8,9,1],[5,6,7,8,9,1,2,3,4],[8,9,1,2,3,4,5,6,7],[3,4,5,6,7,8,9,1,2],[6,7,8,9,1,2,3,4,5],[9,1,2,3,4,5,6,7,8]]
if is_valid_sudoku(valid) is not True:
    print("FAIL valid grid not accepted"); sys.exit(1)
bad_row = [row[:] for row in valid]; bad_row[0][1] = 1
if is_valid_sudoku(bad_row) is not False:
    print("FAIL row duplicate accepted"); sys.exit(1)
bad_box = [row[:] for row in valid]; bad_box[0][0] = 2; bad_box[0][1] = 1
if is_valid_sudoku(bad_box) is not False:
    print("FAIL box duplicate accepted"); sys.exit(1)
if is_valid_sudoku(valid[:8]) is not False:
    print("FAIL 8-row grid accepted"); sys.exit(1)
print("ALL CHECKS PASSED")
''',
    "T8": '''
import sys
cases = [("", []), ("aaabbbba", [("a", 3), ("b", 4), ("a", 1)]), ("abc", [("a", 1), ("b", 1), ("c", 1)]), ("z", [("z", 1)])]
for s, want in cases:
    got = rle_encode(s)
    if got != want:
        print(f"FAIL rle_encode({s!r}) = {got!r}, want {want!r}"); sys.exit(1)
print("ALL CHECKS PASSED")
''',
    "T2": '''
import sys
cases = [([5, 5, 4], 4), ([5, 5], None), ([1, 2, 3], 2), ([], None), ([7], None), ([3, 1, 2], 2)]
for xs, want in cases:
    got = second_largest(list(xs))
    if got != want:
        print(f"FAIL second_largest({xs!r}) = {got!r}, want {want!r}"); sys.exit(1)
print("ALL CHECKS PASSED")
''',
}

_MARKERS = {"T6": "def is_leap_year", "T7": "def is_valid_sudoku", "T8": "def rle_encode", "T2": "def second_largest"}


def _score_code(answer: str, task_id: str, allow_exec: bool) -> dict:
    if not allow_exec:
        return {
            "score": 0, "max": 1, "passed": False, "skipped": True,
            "details": ["code execution disabled (allow_code_execution=false) — round excluded from points"],
        }
    code = _extract_code(answer, _MARKERS.get(task_id, "def "))
    if not code or _MARKERS.get(task_id, "") not in code:
        return {"score": 0, "max": 1, "passed": False, "details": ["no usable code block / function definition found"]}
    result = _execute_candidate(code, _HARNESSES[task_id])
    return {
        "score": 1 if result["passed"] else 0,
        "max": 1,
        "passed": result["passed"],
        "details": result["details"],
    }


def _score_debug_fallback(answer: str) -> dict:
    low = _norm(answer)
    checks = [
        ("names duplicates/distinctness as the bug", bool(re.search(r"duplicat|distinct", low))),
        ("gives a concrete failing example", bool(re.search(r"\[\s*5\s*,\s*5", low)) or "5, 5" in low),
        ("includes corrected code", "```" in answer and ("set(" in low or "distinct" in low)),
    ]
    score = sum(1 for _, ok in checks if ok)
    return {
        "score": score,
        "max": len(checks),
        "passed": score == len(checks),
        "details": [f"heuristic fallback (no executable code) — {label}: {'yes' if ok else 'no'}" for label, ok in checks],
    }


def _score_one(scorer_name: str, answer: str, task: dict, allow_exec: bool) -> dict:
    try:
        if scorer_name == "code_exec":
            return _score_code(answer, task["id"], allow_exec)
        if scorer_name == "debug_exec":
            code = _extract_code(answer, "def second_largest")
            if code and "def second_largest" in code and allow_exec:
                return _score_code(answer, task["id"], True)
            # No code, or execution disabled: fall back to the labelled heuristic.
            return _score_debug_fallback(answer)
        fn = {"money": _score_money, "recall": _score_recall,
              "format25": _score_format25, "summary50": _score_summary50}.get(scorer_name)
        if fn is None:
            return {"score": 0, "max": 0, "passed": False, "details": [f"unknown scorer {scorer_name!r}"]}
        return fn(answer, task)
    except Exception as exc:
        return {"score": 0, "max": 0, "passed": False, "details": [f"scorer error: {exc}"]}


def _fraction(score: dict):
    if score.get("skipped") or score.get("errored") or not score.get("max"):
        return None
    return score["score"] / score["max"]


# ---------------------------------------------------------------- scoring

def _render_report(fighter_a: str, fighter_b: str, rounds_out: list[dict], usage: dict, winner: str, ko: bool) -> str:
    esc = html.escape
    sections = []
    for rnd in rounds_out:
        rows = []
        for r in rnd["tasks"]:
            sa, sb = r["a"], r["b"]
            if r.get("excluded"):
                verdict = f"excluded ({r['excluded']})"
            elif sa["score"] / max(sa["max"], 1) > sb["score"] / max(sb["max"], 1):
                verdict = f"{esc(fighter_a)} takes the task"
            elif sb["score"] / max(sb["max"], 1) > sa["score"] / max(sa["max"], 1):
                verdict = f"{esc(fighter_b)} takes the task"
            else:
                verdict = "Task is a draw"
            ko_note = " — <b>K.O.</b>" if r.get("ko") else ""
            rows.append(
                f'<tr><td><b>{esc(r["id"])}</b><br><small>{esc(r["title"])}</small></td>'
                f'<td>{sa["score"]}/{sa["max"]}<br><small>{esc("; ".join(sa["details"]))}</small></td>'
                f'<td>{sb["score"]}/{sb["max"]}<br><small>{esc("; ".join(sb["details"]))}</small></td>'
                f'<td>{verdict}{ko_note}</td></tr>'
            )
        sections.append(
            f'<h2>{esc(rnd["id"])} — {esc(rnd["name"])} ({rnd["points"]} pt/task)</h2>'
            f'<table><tr><th>Task</th><th>{esc(fighter_a)}</th><th>{esc(fighter_b)}</th><th>Verdict</th></tr>'
            + "".join(rows) + "</table>"
        )
    usage_rows = ""
    for fighter, u in (usage or {}).items():
        usage_rows += (
            f"<tr><td>{esc(fighter)}</td><td>{u.get('latency_s', 0):.1f}s</td>"
            f"<td>{u.get('tokens', 0)}</td><td>${u.get('cost_usd', 0.0):.4f}</td></tr>"
        )
    winner_line = f"{esc(winner)} wins by K.O." if ko else f"{esc(winner)} wins the fight"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Model Kombat — {esc(fighter_a)} vs {esc(fighter_b)}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0d0d12;color:#eee;max-width:980px;margin:2rem auto;padding:0 1rem}}
h1{{letter-spacing:.05em}} .versus{{display:flex;gap:1rem;align-items:center;justify-content:center;font-size:1.3rem;font-weight:700}}
.card{{background:#1a1a24;border:1px solid #333;border-radius:10px;padding:1rem;flex:1;text-align:center;word-break:break-word}}
table{{width:100%;border-collapse:collapse;margin:.5rem 0 1.5rem}} td,th{{border:1px solid #333;padding:.5rem;vertical-align:top}}
small{{color:#aaa}} .winner{{text-align:center;font-size:1.2rem;margin:1rem 0}}</style></head>
<body>
<h1>🥊 MODEL KOMBAT</h1>
<div class="versus"><div class="card">{esc(fighter_a)}</div><div>VS</div><div class="card">{esc(fighter_b)}</div></div>
<p class="winner">{winner_line}</p>
{''.join(sections)}
<h2>Usage</h2>
<table><tr><th>Fighter</th><th>Latency</th><th>Tokens</th><th>Est. cost</th></tr>{usage_rows}</table>
<p><small>Scored mechanically by Model Kombat. Code rounds execute only with allow_code_execution=true (subprocess, timeout, not a security sandbox). Errored/skipped rounds are excluded from points.</small></p>
</body></html>
"""


def _append_history(record: dict) -> None:
    try:
        path = _data_dir() / "fights.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


@_guard
def kombat_score_answers(args: dict, **kwargs) -> str:
    fighter_a = str(args.get("fighter_a") or "Fighter A").strip()
    fighter_b = str(args.get("fighter_b") or "Fighter B").strip()
    answers_a = args.get("answers_a") or {}
    answers_b = args.get("answers_b") or {}
    if not isinstance(answers_a, dict) or not isinstance(answers_b, dict):
        return _err("answers_a and answers_b must be objects mapping task ID -> answer text.")
    allow_exec = args.get("allow_code_execution")
    if allow_exec is None:
        allow_exec = bool(_cfg("allow_code_execution", False))
    render = args.get("render_report", True)
    usage = args.get("usage") or {}
    errors_a = set(args.get("errors_a") or [])
    errors_b = set(args.get("errors_b") or [])

    try:
        suite = _load_suite()
    except Exception as exc:
        return _err(f"Failed to load task suite: {exc}")

    rounds_out = []
    points = {fighter_a: 0.0, fighter_b: 0.0}
    ko_rounds = []

    def score_task(tid, weight):
        task = suite["tasks"][tid]
        sa = _score_one(task["scorer"], str(answers_a.get(tid, "")), task, allow_exec)
        sb = _score_one(task["scorer"], str(answers_b.get(tid, "")), task, allow_exec)
        excluded = None
        if tid in errors_a or tid in errors_b:
            excluded = "errored"
            if tid in errors_a:
                sa = {**sa, "errored": True}
            if tid in errors_b:
                sb = {**sb, "errored": True}
        elif sa.get("skipped") or sb.get("skipped"):
            excluded = "skipped (code execution off)"
        ko = bool(
            task["scorer"] in ("code_exec", "debug_exec")
            and not excluded
            and sa.get("passed") != sb.get("passed")
        )
        fa, fb = _fraction(sa), _fraction(sb)
        pa = pb = 0.0
        if not excluded and fa is not None and fb is not None:
            if fa > fb:
                pa = float(weight)
            elif fb > fa:
                pb = float(weight)
            else:
                pa = pb = weight / 2
        points[fighter_a] += pa
        points[fighter_b] += pb
        return {
            "id": tid, "title": task["title"], "scorer": task["scorer"],
            "a": sa, "b": sb, "points_a": pa, "points_b": pb,
            "ko": ko, "excluded": excluded,
        }

    for rnd in suite["rounds"]:
        tasks_out = [score_task(tid, rnd["points"]) for tid in rnd["tasks"]
                     if tid in answers_a or tid in answers_b]
        if tasks_out:
            rounds_out.append({**rnd, "tasks": tasks_out})
        ko_rounds.extend(t["id"] for t in tasks_out if t.get("ko"))

    # Sudden death on a tie (only if the SD task was actually fought).
    sd_winner = None
    sd_id = suite["sudden_death_task"]
    if sd_id and (sd_id in answers_a or sd_id in answers_b) and points[fighter_a] == points[fighter_b]:
        sd = score_task(sd_id, 0)  # winner-takes-fight, no points
        rounds_out.append({"id": "SD", "name": "Sudden Death", "points": 0, "tasks": [sd]})
        fa, fb = _fraction(sd["a"]), _fraction(sd["b"])
        if fa is not None and fb is not None and fa != fb:
            sd_winner = fighter_a if fa > fb else fighter_b

    if sd_winner:
        winner = sd_winner
    elif points[fighter_a] > points[fighter_b]:
        winner = fighter_a
    elif points[fighter_b] > points[fighter_a]:
        winner = fighter_b
    else:
        winner = "draw"

    # Championship K.O.: pass every R3 task while opponent fails one.
    ko = False
    r3 = next((r for r in rounds_out if r["id"] == "R3"), None)
    if r3 and winner != "draw":
        def r3_passes(key):
            vals = [t[key].get("passed") for t in r3["tasks"] if not t.get("excluded")]
            return bool(vals) and all(vals)
        if r3_passes("a") and not r3_passes("b"):
            winner, ko = fighter_a, True
        elif r3_passes("b") and not r3_passes("a"):
            winner, ko = fighter_b, True

    report_path = None
    if render:
        try:
            report_dir = _data_dir() / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            report_path = report_dir / f"kombat-{stamp}.html"
            report_path.write_text(
                _render_report(fighter_a, fighter_b, rounds_out, usage, winner, ko),
                encoding="utf-8",
            )
            report_path = str(report_path)
        except Exception as exc:
            report_path = f"report render failed: {exc}"

    record = {
        "ts": _now_iso(),
        "fighter_a": fighter_a,
        "fighter_b": fighter_b,
        "winner": winner,
        "ko": ko,
        "points": points,
        "usage": usage,
        "report": report_path,
        "answers_a": answers_a,
        "answers_b": answers_b,
        "rounds": [
            {
                "id": r["id"], "name": r["name"], "points": r["points"],
                "tasks": [
                    {"id": t["id"], "a": t["a"]["score"], "a_max": t["a"]["max"],
                     "b": t["b"]["score"], "b_max": t["b"]["max"],
                     "points_a": t["points_a"], "points_b": t["points_b"],
                     "ko": t["ko"], "excluded": t["excluded"]}
                    for t in r["tasks"]
                ],
            }
            for r in rounds_out
        ],
    }
    _append_history(record)

    # Kanban scoreboard completion (best-effort, display only).
    kanban_note = None
    try:
        last = None
        if hasattr(_CTX, "state"):
            last = _CTX.state.get("last_fight", default=None)
        if last and last.get("kanban") and bool(_cfg("show_on_kanban", True)):
            from . import kanban as _kb

            kanban_note = _kb.finish_rounds(last["kanban"], rounds_out, fighter_a, fighter_b, winner, report_path)
    except Exception as exc:
        kanban_note = f"kanban update skipped: {exc}"

    return _ok(
        fighters=[fighter_a, fighter_b],
        winner=winner,
        ko=ko,
        sudden_death_winner=sd_winner,
        ko_rounds=ko_rounds,
        points=points,
        rounds=rounds_out,
        usage=usage,
        report=report_path,
        kanban=kanban_note,
    )


# ---------------------------------------------------------------- history/export

@_guard
def kombat_history(args: dict, **kwargs) -> str:
    try:
        limit = int(args.get("limit", 10))
    except (TypeError, ValueError):
        return _err("limit must be an integer.")
    path = _data_dir() / "fights.jsonl"
    if not path.exists():
        return _ok(count=0, fights=[])
    fights = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            fights.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    recent = fights[-limit:] if limit > 0 else fights
    return _ok(
        count=len(recent),
        fights=[
            {
                "ts": f.get("ts"),
                "fighter_a": f.get("fighter_a"),
                "fighter_b": f.get("fighter_b"),
                "winner": f.get("winner"),
                "ko": f.get("ko"),
                "points": f.get("points"),
                "report": f.get("report"),
            }
            for f in recent
        ],
    )


@_guard
def kombat_export(args: dict, **kwargs) -> str:
    fmt = str(args.get("format") or "json").lower()
    if fmt not in ("json", "md", "csv"):
        return _err("format must be one of: json, md, csv.")
    path = _data_dir() / "fights.jsonl"
    if not path.exists():
        return _err("No fight history yet in this profile.")
    fights = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            fights.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    idx = args.get("index")
    if idx is not None:
        try:
            fights = [fights[int(idx)]]
        except (ValueError, IndexError):
            return _err(f"index {idx!r} out of range (0..{len(fights) - 1}).")
    elif len(fights) != 1:
        return _err(
            f"History holds {len(fights)} fights; pass index (0..{len(fights) - 1}) to export one, "
            "or call kombat_history to see them."
        )
    fight = fights[0]
    export_dir = _data_dir() / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = export_dir / f"fight-{stamp}.{fmt}"
    try:
        if fmt == "json":
            out_path.write_text(json.dumps(fight, indent=2), encoding="utf-8")
        elif fmt == "md":
            lines = [
                f"# Model Kombat — {fight.get('fighter_a')} vs {fight.get('fighter_b')}",
                "",
                f"- When: {fight.get('ts')}",
                f"- Winner: {fight.get('winner')}{' (K.O.)' if fight.get('ko') else ''}",
                f"- Points: {json.dumps(fight.get('points'))}",
                f"- Report: {fight.get('report')}",
                "",
            ]
            for rnd in fight.get("rounds", []):
                lines.append(f"## {rnd['id']} — {rnd['name']} ({rnd['points']} pt/task)")
                for t in rnd["tasks"]:
                    lines.append(
                        f"- {t['id']}: A {t['a']}/{t['a_max']} ({t['points_a']} pts) vs "
                        f"B {t['b']}/{t['b_max']} ({t['points_b']} pts)"
                        + (" — K.O." if t.get("ko") else "")
                        + (f" — excluded: {t['excluded']}" if t.get("excluded") else "")
                    )
                lines.append("")
            out_path.write_text("\n".join(lines), encoding="utf-8")
        else:  # csv
            rows = ["round,task,fighter_a,score_a,max_a,points_a,fighter_b,score_b,max_b,points_b,ko,excluded"]
            for rnd in fight.get("rounds", []):
                for t in rnd["tasks"]:
                    rows.append(
                        ",".join(
                            str(x).replace(",", ";")
                            for x in (
                                rnd["id"], t["id"], fight.get("fighter_a"), t["a"], t["a_max"], t["points_a"],
                                fight.get("fighter_b"), t["b"], t["b_max"], t["points_b"],
                                t.get("ko", False), t.get("excluded") or "",
                            )
                        )
                    )
            out_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    except Exception as exc:
        return _err(f"Export failed: {exc}")
    return _ok(format=fmt, path=str(out_path))
