"""Tool handlers for Model Kombat.

Rules (Hermes plugin contract): handlers take (args: dict, **kwargs), return a JSON
string always, never raise. The host ctx (for ctx.llm + state) is bound at register
time via set_ctx() — handlers called outside a live Hermes session degrade with a
clear error instead of crashing.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_CTX = None


def set_ctx(ctx) -> None:
    """Bound by register() in __init__.py so handlers can reach ctx.llm / ctx.state."""
    global _CTX
    _CTX = ctx


def _err(message: str, **extra) -> str:
    return json.dumps({"ok": False, "error": message, **extra})


def _ok(**payload) -> str:
    return json.dumps({"ok": True, **payload})


def _load_tasks() -> list[dict]:
    path = Path(__file__).parent / "tasks.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir  # type: ignore

        return Path(plugin_data_dir("model-kombat"))
    except Exception:
        return Path.home() / ".hermes" / "plugin-data" / "model-kombat"


# ---------------------------------------------------------------- list tasks

def kombat_list_tasks(args: dict, **kwargs) -> str:
    try:
        tasks = _load_tasks()
        return _ok(
            count=len(tasks),
            tasks=[
                {"id": t["id"], "title": t["title"], "scorer": t["scorer"]}
                for t in tasks
            ],
        )
    except Exception as exc:  # never raise out of a handler
        return _err(f"Failed to load task suite: {exc}")


# ---------------------------------------------------------------- run a fight

def _complete_text(result) -> str:
    if isinstance(result, dict):
        return str(result.get("text") or "")
    return str(getattr(result, "text", "") or "")


def kombat_run_fight(args: dict, **kwargs) -> str:
    fighter_a = str(args.get("fighter_a") or "").strip()
    fighter_b = str(args.get("fighter_b") or "").strip()
    if not fighter_a or not fighter_b:
        return _err("Both fighter_a and fighter_b model IDs are required.")
    if _CTX is None or not hasattr(_CTX, "llm"):
        return _err(
            "No live Hermes host context (ctx.llm unavailable). Run this inside a Hermes "
            "session with the plugin enabled, not from a bare Python import."
        )

    try:
        tasks = _load_tasks()
    except Exception as exc:
        return _err(f"Failed to load task suite: {exc}")

    wanted = args.get("tasks")
    if wanted:
        wanted = {str(w).upper() for w in wanted}
        tasks = [t for t in tasks if t["id"].upper() in wanted]
    if not tasks:
        return _err("No matching tasks. Call kombat_list_tasks for valid IDs.")

    temperature = args.get("temperature", 0)
    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        return _err("temperature must be a number.")
    max_tokens = args.get("max_tokens", 2000)
    try:
        max_tokens = int(max_tokens)
    except (TypeError, ValueError):
        return _err("max_tokens must be an integer.")

    system = (
        "You are a fighter in a model benchmark. Answer the task exactly as written, "
        "following every format constraint. Do not add commentary about the benchmark."
    )
    results: dict[str, dict[str, str]] = {fighter_a: {}, fighter_b: {}}
    errors: list[str] = []

    for task in tasks:
        for fighter in (fighter_a, fighter_b):
            try:
                res = _CTX.llm.complete(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": task["prompt"]},
                    ],
                    model=fighter,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    purpose=f"model-kombat fight {task['id']}",
                )
                results[fighter][task["id"]] = _complete_text(res)
            except PermissionError as exc:
                return _err(
                    f"Host refused the model override: {exc}. "
                    "Enable it in config.yaml: plugins.entries.model-kombat.llm."
                    "allow_model_override: true (optionally restrict with allowed_models), "
                    "then retry the fight. Nothing was sent.",
                    partial=results,
                )
            except Exception as exc:  # one fighter/task failing shouldn't kill the fight
                errors.append(f"{task['id']} vs {fighter}: {exc}")
                results[fighter][task["id"]] = ""

    # Remember the last fight so /kombat score can score it without re-pasting.
    try:
        if _CTX is not None and hasattr(_CTX, "state"):
            _CTX.state.set(
                "last_fight",
                {
                    "fighter_a": fighter_a,
                    "fighter_b": fighter_b,
                    "answers_a": results[fighter_a],
                    "answers_b": results[fighter_b],
                },
            )
    except Exception:
        pass  # state is best-effort

    return _ok(
        fighters=[fighter_a, fighter_b],
        rounds=[t["id"] for t in tasks],
        answers_a=results[fighter_a],
        answers_b=results[fighter_b],
        errors=errors,
    )


# ---------------------------------------------------------------- scorers

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _score_money(answer: str, task: dict) -> dict:
    found, missing = [], []
    low = _norm(answer)
    for exp in task.get("expected", []):
        # Accept $44.30 and 44.3 / 44.30 without the dollar sign.
        core = exp.replace("$", "").rstrip("0").rstrip(".") if "." in exp else exp
        if _norm(exp) in low or core in low:
            found.append(exp)
        else:
            missing.append(exp)
    return {
        "score": len(found),
        "max": len(task.get("expected", [])),
        "passed": not missing,
        "details": [f"found: {', '.join(found) or 'none'}", f"missing: {', '.join(missing) or 'none'}"],
    }


def _score_recall(answer: str, task: dict) -> dict:
    low = _norm(answer)
    found = [e for e in task.get("expected", []) if e in low]
    missing = [e for e in task.get("expected", []) if e not in low]
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
    missing = [e for e in task.get("expected", []) if e not in answer]
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


def _score_debug(answer: str, task: dict) -> dict:
    """Heuristic: the duplicates bug is the point. Labelled heuristic in output."""
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
        "details": [f"heuristic — {label}: {'yes' if ok else 'no'}" for label, ok in checks],
    }


_LEAP_HARNESS = '''
import sys
checks = [(2000, True), (1900, False), (2024, True), (2023, False), (2100, False)]
for year, want in checks:
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
'''


def _extract_code(answer: str) -> str | None:
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", answer, re.S | re.I)
    if blocks:
        return blocks[-1].strip()
    if "def is_leap_year" in answer:
        return answer.strip()
    return None


def _score_code_exec(answer: str, task: dict, allow_exec: bool) -> dict:
    if not allow_exec:
        return {
            "score": 0,
            "max": 1,
            "passed": False,
            "skipped": True,
            "details": ["code execution disabled (allow_code_execution=false) — scored as skipped"],
        }
    code = _extract_code(answer)
    if not code:
        return {"score": 0, "max": 1, "passed": False, "details": ["no python code block found"]}
    if "is_leap_year" not in code:
        return {"score": 0, "max": 1, "passed": False, "details": ["no is_leap_year definition in code"]}
    try:
        with tempfile.TemporaryDirectory(prefix="kombat-") as td:
            script = Path(td) / "candidate.py"
            script.write_text(code + "\n" + _LEAP_HARNESS, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        passed = proc.returncode == 0 and "ALL CHECKS PASSED" in proc.stdout
        detail = (proc.stdout + proc.stderr).strip().splitlines()
        return {
            "score": 1 if passed else 0,
            "max": 1,
            "passed": passed,
            "details": detail[-3:] if detail else ["no output"],
        }
    except subprocess.TimeoutExpired:
        return {"score": 0, "max": 1, "passed": False, "details": ["timeout after 10s"]}
    except Exception as exc:
        return {"score": 0, "max": 1, "passed": False, "details": [f"harness error: {exc}"]}


_SCORERS = {
    "money": _score_money,
    "recall": _score_recall,
    "format25": _score_format25,
    "summary50": _score_summary50,
    "debug": _score_debug,
}


def _score_one(scorer_name: str, answer: str, task: dict, allow_exec: bool) -> dict:
    try:
        if scorer_name == "code_exec":
            return _score_code_exec(answer, task, allow_exec)
        fn = _SCORERS.get(scorer_name)
        if fn is None:
            return {"score": 0, "max": 0, "passed": False, "details": [f"unknown scorer {scorer_name!r}"]}
        return fn(answer, task)
    except Exception as exc:
        return {"score": 0, "max": 0, "passed": False, "details": [f"scorer error: {exc}"]}


# ---------------------------------------------------------------- report

def _render_report(fighter_a: str, fighter_b: str, rounds: list[dict]) -> str:
    def esc(x) -> str:
        return html.escape(str(x))

    rows = []
    for r in rounds:
        sa, sb = r["a"], r["b"]
        if sa["score"] > sb["score"]:
            verdict, cls = f"{esc(fighter_a)} takes the round", "a"
        elif sb["score"] > sa["score"]:
            verdict, cls = f"{esc(fighter_b)} takes the round", "b"
        else:
            verdict, cls = "Round is a draw", "draw"
        ko = ""
        ka, kb = sa.get("passed"), sb.get("passed")
        if ka and not kb:
            ko = f" — K.O.: {esc(fighter_a)} passed, {esc(fighter_b)} failed"
        elif kb and not ka:
            ko = f" — K.O.: {esc(fighter_b)} passed, {esc(fighter_a)} failed"
        rows.append(
            f'<tr class="{cls}"><td><b>{esc(r["id"])}</b><br><small>{esc(r["title"])}</small></td>'
            f'<td>{sa["score"]}/{sa["max"]}<br><small>{esc("; ".join(sa["details"]))}</small></td>'
            f'<td>{sb["score"]}/{sb["max"]}<br><small>{esc("; ".join(sb["details"]))}</small></td>'
            f'<td>{verdict}{ko}</td></tr>'
        )
    body = "\n".join(rows)
    pa = sum(r["points_a"] for r in rounds)
    pb = sum(r["points_b"] for r in rounds)
    if pa > pb:
        winner = f"{esc(fighter_a)} wins the fight"
    elif pb > pa:
        winner = f"{esc(fighter_b)} wins the fight"
    else:
        winner = "The fight is a draw"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Model Kombat — {esc(fighter_a)} vs {esc(fighter_b)}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0d0d12;color:#eee;max-width:960px;margin:2rem auto;padding:0 1rem}}
h1{{letter-spacing:.05em}} .versus{{display:flex;gap:1rem;align-items:center;justify-content:center;font-size:1.4rem;font-weight:700}}
.card{{background:#1a1a24;border:1px solid #333;border-radius:10px;padding:1rem;flex:1;text-align:center;word-break:break-word}}
table{{width:100%;border-collapse:collapse;margin-top:1rem}} td,th{{border:1px solid #333;padding:.5rem;vertical-align:top}}
tr.a td:first-child{{border-left:4px solid #4da3ff}} tr.b td:first-child{{border-left:4px solid #ff5a5a}}
small{{color:#aaa}} .winner{{text-align:center;font-size:1.2rem;margin-top:1rem}}</style></head>
<body>
<h1>🥊 MODEL KOMBAT</h1>
<div class="versus"><div class="card">{esc(fighter_a)}</div><div>VS</div><div class="card">{esc(fighter_b)}</div></div>
<p class="winner">{winner} — {pa} : {pb} (points)</p>
<table><tr><th>Round</th><th>{esc(fighter_a)}</th><th>{esc(fighter_b)}</th><th>Verdict</th></tr>{body}</table>
<p><small>Scored mechanically by Model Kombat. Code rounds only execute with allow_code_execution=true.</small></p>
</body></html>
"""


def kombat_score_answers(args: dict, **kwargs) -> str:
    fighter_a = str(args.get("fighter_a") or "Fighter A").strip()
    fighter_b = str(args.get("fighter_b") or "Fighter B").strip()
    answers_a = args.get("answers_a") or {}
    answers_b = args.get("answers_b") or {}
    if not isinstance(answers_a, dict) or not isinstance(answers_b, dict):
        return _err("answers_a and answers_b must be objects mapping task ID -> answer text.")
    allow_exec = bool(args.get("allow_code_execution", False))
    render = args.get("render_report", True)

    try:
        tasks = _load_tasks()
    except Exception as exc:
        return _err(f"Failed to load task suite: {exc}")

    rounds = []
    points_a = points_b = 0.0
    for task in tasks:
        tid = task["id"]
        if tid not in answers_a and tid not in answers_b:
            continue
        sa = _score_one(task["scorer"], str(answers_a.get(tid, "")), task, allow_exec)
        sb = _score_one(task["scorer"], str(answers_b.get(tid, "")), task, allow_exec)
        if sa["score"] > sb["score"]:
            points_a += 1.0
        elif sb["score"] > sa["score"]:
            points_b += 1.0
        else:
            points_a += 0.5
            points_b += 0.5
        rounds.append(
            {
                "id": tid,
                "title": task["title"],
                "a": sa,
                "b": sb,
                "points_a": 1.0 if sa["score"] > sb["score"] else (0.5 if sa["score"] == sb["score"] else 0.0),
                "points_b": 1.0 if sb["score"] > sa["score"] else (0.5 if sa["score"] == sb["score"] else 0.0),
            }
        )

    if not rounds:
        return _err("No overlapping task IDs in answers_a/answers_b. Call kombat_list_tasks for valid IDs.")

    if points_a > points_b:
        winner = fighter_a
    elif points_b > points_a:
        winner = fighter_b
    else:
        winner = "draw"

    report_path = None
    if render:
        try:
            report_dir = _data_dir() / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            report_path = report_dir / f"kombat-{stamp}.html"
            report_path.write_text(_render_report(fighter_a, fighter_b, rounds), encoding="utf-8")
            report_path = str(report_path)
        except Exception as exc:
            report_path = f"report render failed: {exc}"

    return _ok(
        fighters=[fighter_a, fighter_b],
        winner=winner,
        points={fighter_a: points_a, fighter_b: points_b},
        rounds=rounds,
        report=report_path,
    )
