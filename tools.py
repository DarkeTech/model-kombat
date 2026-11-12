"""Tool handlers for Model Kombat (v0.2).

Contract: handlers take (args: dict, **kwargs), return a JSON string always,
never raise. Host ctx is bound at register time via set_ctx(); outside a live
Hermes session handlers degrade with a clear error JSON.

Scoring is mechanical: exact counts, numeric matches, synonym-aware phrase
matches, and subprocess execution of model-written code (opt-in only).
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import random
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
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


# ------------------------------------------------------- cards + task lookup


def _cards_path() -> Path:
    return _data_dir() / "cards.json"


def _read_json_file(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def _write_json_atomic(path: Path, payload) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — best-effort persistence
        pass


def _store_card(card: dict) -> None:
    """Remember a scouted card so kombat_run_fight can take its id later."""
    if not isinstance(card, dict) or not card.get("id"):
        return
    data = _read_json_file(_cards_path(), {})
    if not isinstance(data, dict):
        data = {}
    data[str(card["id"])] = card
    if len(data) > 50:  # keep the most recent 50 cards
        for key in list(data)[:-50]:
            data.pop(key, None)
    _write_json_atomic(_cards_path(), data)


def _lanes_module():
    try:
        from . import lanes as _ln
    except ImportError:
        import lanes as _ln  # type: ignore
    return _ln


def _task_lookup() -> dict[str, dict]:
    """Every task the engine can fight: new pool + legacy suite (lane-normalised)."""
    try:
        ln = _lanes_module()
        pool = dict(ln.load_task_pool())
        legacy_lanes = dict(getattr(ln, "LEGACY_LANES", {}))
    except Exception:
        pool, legacy_lanes = {}, {}
    try:
        suite = _load_suite()
    except Exception:
        suite = {"tasks": {}}
    for tid, task in suite["tasks"].items():
        pool.setdefault(tid, dict(task))
    for tid, task in pool.items():
        if not task.get("lane"):
            task["lane"] = legacy_lanes.get(str(tid).upper())
        task.setdefault("difficulty", 2)
    return pool


def _coerce_seed(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_card(value, intensity: str | None = None):
    """Resolve a card argument: stored card id, scouted card object, or a spec.

    Returns ``(card, error)``. ``None`` card means "no card given" (legacy
    suite); an error string means the caller asked for a card we cannot find.
    ``intensity`` applies only when building a card from a ``{"lanes": ...}``
    spec (a stored/complete card keeps the intensity it was built with).
    """
    if value is None or value == "":
        return None, None
    if isinstance(value, str):
        cid = value.strip()
        if cid.startswith("{"):
            try:
                return _coerce_card(json.loads(cid), intensity)
            except (json.JSONDecodeError, TypeError):
                pass
        data = _read_json_file(_cards_path(), {})
        card = data.get(cid) if isinstance(data, dict) else None
        if isinstance(card, dict) and card.get("rounds"):
            return card, None
        return None, f"fight card {cid!r} not found (call kombat_scout to build and store one)."
    if isinstance(value, dict):
        nested = value.get("card")
        if isinstance(nested, dict):
            return _coerce_card(nested, intensity)
        if isinstance(nested, str) and not value.get("rounds"):
            return _coerce_card(nested, intensity)
        if isinstance(value.get("rounds"), list) and value["rounds"]:
            return value, None
        if isinstance(value.get("id"), str) and not value.get("lanes"):
            return _coerce_card(value["id"], intensity)
        lanes_spec = value.get("lanes")
        if isinstance(lanes_spec, (list, dict)):
            try:
                card = _lanes_module().build_card(
                    lanes_spec,
                    _coerce_seed(value.get("seed"), 0),
                    extra_tasks=value.get("project_tasks") if isinstance(value.get("project_tasks"), list) else None,
                    intensity=_intensity(value.get("intensity") or intensity),
                )
            except Exception as exc:  # noqa: BLE001 — bad card spec degrades
                return None, f"could not build card from given lanes: {exc}"
            _store_card(card)
            return card, None
    return None, "card must be a card object from kombat_scout, {\"lanes\": [...]}, or a stored card id."


def _plan_from_card(card: dict) -> dict:
    rounds = []
    for idx, rnd in enumerate(card.get("rounds") or []):
        if not isinstance(rnd, dict):
            continue
        raw_ids = rnd.get("tasks") or []
        ids = [str(t) for t in raw_ids if isinstance(t, (str, int))]
        try:
            points = int(rnd.get("points", 1))
        except (TypeError, ValueError):
            points = 1
        rounds.append(
            {
                "id": str(rnd.get("id") or f"R{idx + 1}"),
                "name": str(rnd.get("name") or "Round"),
                "points": points,
                "tasks": ids,
                "card": rnd,
            }
        )
    return {
        "mode": "card",
        "card": card,
        "rounds": rounds,
        "sudden_death_task": card.get("sudden_death_task"),
    }


def _plan_from_suite(suite: dict, order: list[str] | None = None) -> dict:
    rounds = []
    selected = set(order or [])
    for rnd in suite.get("rounds") or []:
        ids = [str(t) for t in (rnd.get("tasks") or [])]
        if order is not None:
            ids = [t for t in ids if t in selected]
        if not ids:
            continue
        rounds.append(
            {
                "id": str(rnd.get("id") or "R"),
                "name": str(rnd.get("name") or "Round"),
                "points": int(rnd.get("points", 1) or 1),
                "tasks": ids,
                "card": None,
            }
        )
    return {
        "mode": "legacy",
        "card": None,
        "rounds": rounds,
        "sudden_death_task": suite.get("sudden_death_task"),
    }


def _fight_plan(args: dict, suite: dict) -> tuple[dict | None, str | None]:
    """Resolve args["card"] (object/spec/id) or fall back to the legacy suite."""
    card_arg = args.get("card")
    if card_arg is None and isinstance(args.get("input"), dict):
        card_arg = args["input"].get("card")
    card, err = _coerce_card(card_arg, _intensity(args.get("intensity")))
    if err:
        return None, err
    if card:
        plan = _plan_from_card(card)
        if not any(rnd["tasks"] for rnd in plan["rounds"]):
            return None, "fight card has no task ids."
        wanted = args.get("tasks")
        if wanted:
            wanted_set = {str(w) for w in wanted}
            for rnd in plan["rounds"]:
                rnd["tasks"] = [t for t in rnd["tasks"] if t in wanted_set]
            plan["rounds"] = [r for r in plan["rounds"] if r["tasks"]]
            if not plan["rounds"]:
                return None, "No matching tasks in the card."
        return plan, None

    wanted = args.get("tasks")
    order = None
    if wanted:
        wanted_set = {str(w).upper() for w in wanted}
        order = [t for t in suite["order"] if t in wanted_set]
        if not order:
            return None, "No matching tasks. Call kombat_list_tasks for valid IDs."
    return _plan_from_suite(suite, order), None


# --------------------------------------------------- rate limit + retries

_RATE_LIMIT_RE = re.compile(
    r"\b429\b|rate[\s_-]?limit|too many requests|overloaded|throttl|quota|capacity|try again later",
    re.I,
)
_RETRY_AFTER_RES = (
    re.compile(r"retry[\s_-]?after[\"']?\s*[:=]?\s*[\"']?(\d+(?:\.\d+)?)", re.I),
    re.compile(r"try again in\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)\s+(?:before|until)\s+(?:retry|the next)", re.I),
)


def _as_float(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float_from_any(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    num = _as_float(value)
    if num is not None:
        return num
    if isinstance(value, str):
        for pattern in _RETRY_AFTER_RES:
            m = pattern.search(value)
            if m:
                return _as_float(m.group(1))
    return None


def _retry_after_from_text(text: str) -> float | None:
    if not text:
        return None
    for pattern in _RETRY_AFTER_RES:
        m = pattern.search(text)
        if m:
            return _as_float(m.group(1))
    return None


def _detect_rate_limit(exc: Exception) -> tuple[bool, float | None]:
    """Return (is_rate_limit, retry_after_seconds) for a provider exception."""
    retry_after = None
    for attr in ("retry_after", "retry_after_s", "retry_after_seconds", "retryAfter"):
        if hasattr(exc, attr):
            val = getattr(exc, attr, None)
            retry_after = retry_after or _float_from_any(val)
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    headers = getattr(exc, "headers", None)
    response = getattr(exc, "response", None)

    header_map = {}
    for holder in (headers, getattr(response, "headers", None)):
        if holder is None:
            continue
        try:
            items = holder.items() if hasattr(holder, "items") else []
            header_map.update({str(k).lower(): v for k, v in items})
        except Exception:  # noqa: BLE001 — weird header objects
            pass
    for key in ("retry-after", "retry_after", "x-ratelimit-reset-after"):
        if key in header_map:
            retry_after = retry_after or _float_from_any(header_map[key])

    if response is not None and status is None:
        status = getattr(response, "status_code", None)
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 — non-JSON body
            payload = None
        if isinstance(payload, dict):
            candidates = [payload.get("retry_after"), payload.get("retryAfter")]
            error_obj = payload.get("error")
            if isinstance(error_obj, dict):
                candidates.append(error_obj.get("retry_after"))
            for candidate in candidates:
                val = _float_from_any(candidate)
                if val is not None:
                    retry_after = retry_after or val
                    break

    text = f"{type(exc).__name__}: {exc}"
    body = getattr(exc, "body", None)
    if isinstance(body, (str, dict)):
        text += " " + (body if isinstance(body, str) else json.dumps(body, default=str))
    retry_after = retry_after or _retry_after_from_text(text)

    is_rate = False
    try:
        is_rate = int(status) == 429 if status is not None else False
    except (TypeError, ValueError):
        is_rate = False
    if not is_rate:
        is_rate = bool(_RATE_LIMIT_RE.search(text))
    return is_rate, retry_after


def _wait_state(budget_s: float) -> dict:
    return {"budget": max(0.0, float(budget_s)), "waited": 0.0}


def _sleep_wait(seconds: float, state: dict) -> float:
    """Sleep at most the remaining per-fight wait budget; return slept seconds."""
    try:
        remaining = max(0.0, state["budget"] - state["waited"])
    except Exception:
        remaining = 0.0
    wait = max(0.0, min(float(seconds), remaining))
    if wait <= 0:
        return 0.0
    time.sleep(wait)
    state["waited"] += wait
    return wait


# --------------------------------------------- hard per-call deadline (v0.6)
#
# ctx.llm.complete(timeout=...) does not reliably bound a slowly streamed
# response (observed: an 824s answer with a 420s host tool timeout). Every call
# therefore runs in a worker thread and is abandoned at the deadline — the
# fight never waits on a stuck provider call. Late results are discarded.

_LLM_POOL: ThreadPoolExecutor | None = None
_LLM_POOL_LOCK = threading.Lock()


def _llm_pool() -> ThreadPoolExecutor:
    global _LLM_POOL
    if _LLM_POOL is None:
        with _LLM_POOL_LOCK:
            if _LLM_POOL is None:
                _LLM_POOL = ThreadPoolExecutor(
                    max_workers=8, thread_name_prefix="kombat-llm"
                )
    return _LLM_POOL


def _complete_bounded(ctx, messages, fighter: str, temperature, max_tokens, timeout_s: float):
    """Call ``ctx.llm.complete`` with a hard wall-clock deadline.

    Raises ``TimeoutError`` when the deadline passes (the worker thread is
    abandoned, not joined). Provider exceptions propagate unchanged.
    """
    call = ctx.llm.complete
    future = _llm_pool().submit(
        call,
        messages,
        model=fighter,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout_s,
        purpose="model-kombat fight",
    )
    try:
        return future.result(timeout=float(timeout_s) if timeout_s else None)
    except FutureTimeout:
        future.cancel()
        raise TimeoutError(
            f"LLM call exceeded hard deadline of {timeout_s}s (abandoned)"
        ) from None



def _complete_with_retry(
    ctx,
    fighter: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout_s: float,
    *,
    max_attempts: int = 3,
    max_rate_wait_s: float = 120.0,
    wait_state: dict | None = None,
    pace_ms: float = 0.0,
):
    """Call one model with 429-aware waiting, jittered backoff, bounded tries.

    PermissionError (the trust gate) is re-raised immediately — never retried.
    Returns ``(text, usage, attempts, error, waited_s)``.
    """
    system = (
        "You are a fighter in a model benchmark. Answer the task exactly as written, "
        "following every format constraint. Do not add commentary about the benchmark."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    state = wait_state if isinstance(wait_state, dict) else _wait_state(240.0)
    waited = 0.0
    last_exc = None
    attempts = 0
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        attempts = attempt
        try:
            result = _complete_bounded(
                ctx, messages, fighter, temperature, max_tokens, timeout_s
            )
            text = result.get("text", "") if isinstance(result, dict) else getattr(result, "text", "")
            return str(text or ""), _usage_of(result, fighter), attempt, None, waited
        except PermissionError:
            raise
        except Exception as exc:  # noqa: BLE001 — provider errors are data, not crashes
            last_exc = exc
            is_rate, retry_after = _detect_rate_limit(exc)
            if isinstance(exc, TimeoutError) and not is_rate:
                # A hung call is transient: retry quickly instead of backing off
                # (the whole point is to stop waiting on the stuck provider).
                wait = 0.1
            elif is_rate:
                want = retry_after if retry_after is not None else 8.0
                wait = min(float(want), float(max_rate_wait_s))
            else:
                base = min(30.0, 1.5 * (2 ** (attempt - 1)))
                wait = base * random.uniform(0.75, 1.25)
            if attempt >= max_attempts:
                break
            slept = _sleep_wait(wait, state)
            waited += slept
            if slept <= 0:
                break  # wait budget exhausted: report errored, never failed
    return "", {}, attempts, str(last_exc), waited


def score_check(check: dict, answer: str, allow_exec: bool = False) -> dict:
    """v0.4 mechanical check-spec scorer (SPEC §3); delegates to engine_checks.

    Kept here as the stable entry point for the fight engine so callers do not
    need to know which module owns the implementation.
    """
    try:
        from . import engine_checks as _ec
    except ImportError:  # top-level import outside the plugin package (tests)
        import engine_checks as _ec  # type: ignore
    return _ec.score_check(check, answer, allow_exec)


def _data_dir() -> Path:
    if _DATA_DIR_OVERRIDE is not None:
        return _DATA_DIR_OVERRIDE
    # Profile-aware first: the host knows which profile this plugin instance
    # belongs to. HERMES_HOME / ~/.hermes fallbacks are for hosts without
    # plugin_storage (or a bare top-level import in tests).
    try:
        from plugins.plugin_storage import plugin_data_dir  # type: ignore

        return Path(plugin_data_dir("model-kombat"))
    except Exception:
        pass
    home = os.environ.get("HERMES_HOME")
    if home:
        return Path(home) / "plugin-data" / "model-kombat"
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


def _cap(key: str, raw, default, cast=float, zero_is_unlimited: bool = False):
    """A configured limit that a tool argument may LOWER but never raise.

    Spend and wall-clock caps are user settings; tool arguments are model
    input, so they only ever tighten a cap. ``zero_is_unlimited`` covers
    max_fight_seconds, where 0 means "no wall cap" in the engine — there an
    argument of 0 must not be read as "remove the cap", it falls back to the
    configured value.
    """
    configured = cast(_cfg(key, default))
    if raw is None:
        return configured
    requested = cast(raw)
    if zero_is_unlimited:
        if requested <= 0:
            return configured
        if configured <= 0:  # the user disabled this cap entirely
            return requested
        return min(requested, configured)
    return max(0, min(requested, configured))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------- task discrimination memory

INTENSITIES = ("standard", "hard", "gauntlet")
TASK_STATS_CAP = 500


def _intensity(value=None) -> str:
    """Resolve an intensity argument, falling back to the ``default_intensity`` setting."""
    raw = str(value or "").strip().lower()
    if not raw:
        raw = str(_cfg("default_intensity", "standard") or "standard").strip().lower()
    return raw if raw in INTENSITIES else "standard"


def _task_stats_path() -> Path:
    return _data_dir() / "task_stats.json"


def _load_task_stats() -> dict[str, dict]:
    """Per-task discrimination stats (``{}`` when none recorded yet)."""
    try:
        return _lanes_module().load_task_stats(_task_stats_path())
    except Exception:  # noqa: BLE001 — optional memory
        return {}


def _update_task_stats(record: dict) -> bool:
    """Fold one scored fight into ``task_stats.json`` (atomic, idempotent per fight_id).

    Per task id: ``fights``, ``both_perfect``, ``both_zero``, ``split``,
    ``mean_gap`` (mean |scoreA-scoreB| over scored pairs) and ``per_model``
    (``{model_id: {"n", "mean"}}``). A fight is applied at most once; the
    applied-id list is capped at 500.
    """
    path = _task_stats_path()
    data = _read_json_file(path, {})
    if not isinstance(data, dict):
        data = {}
    raw_stats = data.get("tasks")
    stats: dict = raw_stats if isinstance(raw_stats, dict) else {}
    raw_applied = data.get("applied")
    applied: list[str] = [str(x) for x in raw_applied] if isinstance(raw_applied, list) else []
    fight_id = str(record.get("fight_id") or "").strip()
    if fight_id and fight_id in applied:
        return False

    fighters = {
        "a": str(record.get("fighter_a") or ""),
        "b": str(record.get("fighter_b") or ""),
    }

    def _num(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    for rnd in record.get("rounds") or []:
        if not isinstance(rnd, dict):
            continue
        for task in rnd.get("tasks") or []:
            if not isinstance(task, dict) or task.get("excluded"):
                continue
            fa, fb = _num(task.get("a_fraction")), _num(task.get("b_fraction"))
            if fa is None or fb is None:
                continue
            tid = str(task.get("id") or "").strip()
            if not tid:
                continue
            raw_entry = stats.get(tid)
            entry: dict = raw_entry if isinstance(raw_entry, dict) else {}
            prev = 0
            try:
                prev = int(entry.get("fights") or 0)
            except (TypeError, ValueError):
                prev = 0
            gap = abs(fa - fb)
            try:
                mean_gap = float(entry.get("mean_gap") or 0.0)
            except (TypeError, ValueError):
                mean_gap = 0.0
            entry["mean_gap"] = round((mean_gap * prev + gap) / (prev + 1), 6)
            entry["fights"] = prev + 1
            entry["both_perfect"] = int(entry.get("both_perfect") or 0) + int(fa >= 1.0 and fb >= 1.0)
            entry["both_zero"] = int(entry.get("both_zero") or 0) + int(fa <= 0.0 and fb <= 0.0)
            entry["split"] = int(entry.get("split") or 0) + int(fa != fb)

            raw_per_model = entry.get("per_model")
            per_model: dict = raw_per_model if isinstance(raw_per_model, dict) else {}
            for side, score in (("a", fa), ("b", fb)):
                model = fighters[side]
                if not model:
                    continue
                raw_slot = per_model.get(model)
                slot: dict = raw_slot if isinstance(raw_slot, dict) else {}
                try:
                    n = int(slot.get("n") or 0)
                except (TypeError, ValueError):
                    n = 0
                try:
                    mean = float(slot.get("mean") or 0.0)
                except (TypeError, ValueError):
                    mean = 0.0
                slot["n"] = n + 1
                slot["mean"] = round((mean * n + score) / (n + 1), 6)
                per_model[model] = slot
            entry["per_model"] = per_model
            stats[tid] = entry

    if fight_id:
        applied.append(fight_id)
    data["tasks"] = stats
    data["applied"] = applied[-TASK_STATS_CAP:]
    data["updated"] = _now_iso()
    _write_json_atomic(path, data)
    return True


# ------------------------------------------------------------- pricing / cost

#: Host usage never sets ``cost_usd`` (agent/plugin_llm.py only reports token
#: counts), so the engine prices calls itself. Prices are USD per 1M tokens.
_PRICES_FILE = Path(__file__).parent / "prices.json"


def _price_table() -> dict:
    """Shipped ``prices.json`` merged with the ``model_prices`` setting.

    The setting wins key-by-key, so a profile can add models or override a
    price without editing the shipped table. Entries are
    ``{"in": USD/1M input, "out": USD/1M output}`` (an optional ``label`` is
    informational).
    """
    table: dict = {}
    raw = _read_json_file(_PRICES_FILE, {})
    if isinstance(raw, dict):
        for mid, entry in raw.items():
            if isinstance(entry, dict):
                table[str(mid)] = dict(entry)
    override = _cfg("model_prices", None)
    if isinstance(override, str) and override.strip().startswith("{"):
        try:
            override = json.loads(override)
        except (json.JSONDecodeError, TypeError):
            override = None
    if isinstance(override, dict):
        for mid, entry in override.items():
            if isinstance(entry, dict):
                merged = dict(table.get(str(mid), {}))
                merged.update(entry)
                table[str(mid)] = merged
    return table


def _price_for(model: str):
    table = _price_table()
    if str(model) in table:
        return table[str(model)]
    low = str(model or "").lower()
    for mid, entry in table.items():
        if mid.lower() == low:
            return entry
    return None


def _estimate_cost(usage: dict, model: str) -> tuple[float | None, str]:
    """Return ``(cost_usd, cost_source)`` for one call.

    ``cost_source`` is ``"host"`` when the host supplied a cost, ``"estimated"``
    when priced from ``prices.json``/``model_prices``, and ``"unknown"`` when
    the model has no price (cost is ``None``, never silently 0).
    """
    if not isinstance(usage, dict):
        return None, "unknown"
    host_cost = usage.get("cost_usd")
    if host_cost is not None:
        try:
            return float(host_cost), "host"
        except (TypeError, ValueError):
            pass
    price = _price_for(model)
    if not isinstance(price, dict):
        return None, "unknown"
    try:
        in_price = float(price.get("in"))
        out_price = float(price.get("out"))
    except (TypeError, ValueError):
        return None, "unknown"
    try:
        in_tokens = float(usage.get("input_tokens") or 0)
        out_tokens = float(usage.get("output_tokens") or 0)
    except (TypeError, ValueError):
        return None, "unknown"
    return round((in_tokens * in_price + out_tokens * out_price) / 1_000_000.0, 9), "estimated"


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


# ---------------------------------------------------------------- scout

def _scout_json(text: str):
    """Parse kanban CLI output: bare JSON, {'output': ...} or a JSON array."""
    raw = str(text or "").strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    match = re.search(r"\[.*\]", raw, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _scout_task_text(task: dict) -> str:
    parts = []
    for key in ("title", "description", "body", "summary", "details", "notes"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    labels = task.get("labels")
    if isinstance(labels, list) and labels:
        parts.append("labels: " + ", ".join(str(x) for x in labels))
    return "\n".join(parts)


def _scout_active_board(_kb) -> str | None:
    """Resolve the current kanban board slug (None = let the CLI pick)."""
    try:
        ok, out = _kb._dispatch(_kb._cli("boards", "list", "--json"))
        if not ok:
            return None
        data = _scout_json(out)
        items = data if isinstance(data, list) else (data or {}).get("boards") if isinstance(data, dict) else None
        for board in items or []:
            if not isinstance(board, dict):
                continue
            if board.get("is_current") or board.get("current"):
                slug = board.get("slug") or board.get("name")
                return str(slug) if slug else None
    except Exception:  # noqa: BLE001 — scouting degrades, never fails
        return None
    return None


def _scout_kanban_task(task_id: str, board: str | None = None) -> tuple[dict | None, str | None]:
    try:
        try:
            try:
                from . import kanban as _kb
            except ImportError:  # top-level import outside the plugin package (tests)
                import kanban as _kb  # type: ignore
        except ImportError:
            import kanban as _kb  # type: ignore
        board = board or _scout_active_board(_kb)
        prefix = ("--board", board) if board else ()
        ok, out = _kb._dispatch(_kb._cli(*prefix, "show", task_id, "--json"))
        if not ok:
            return None, f"kanban unavailable: {str(out)[:200]}"
        data = _kb._extract_json(out)
        if not isinstance(data, dict):
            return None, "kanban returned no JSON for that card"
        task = data.get("task")
        if not isinstance(task, dict):
            task = data if "title" in data else None
        if not task:
            return None, f"kanban card {task_id!r} not found"
        return task, None
    except Exception as exc:  # noqa: BLE001 — degrade cleanly
        return None, f"kanban unavailable: {type(exc).__name__}: {exc}"


def _scout_kanban_open(limit: int = 20) -> tuple[list[dict], str | None]:
    """Open cards on the active board, resolved through `show` for full text."""
    try:
        try:
            try:
                from . import kanban as _kb
            except ImportError:  # top-level import outside the plugin package (tests)
                import kanban as _kb  # type: ignore
        except ImportError:
            import kanban as _kb  # type: ignore
        board = _scout_active_board(_kb)
        prefix = ("--board", board) if board else ()
        ok, out = _kb._dispatch(_kb._cli(*prefix, "list", "--json"))
        if not ok:
            return [], f"kanban unavailable: {str(out)[:200]}"
        data = _scout_json(out)
        items = None
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            items = data["tasks"]
        elif isinstance(data, list):
            items = data
        if items is None:
            return [], "kanban list returned no JSON"
        closed = {"done", "archived", "cancelled", "canceled", "failed", "removed"}
        cards: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower().replace(" ", "_")
            if status in closed:
                continue
            tid = item.get("id")
            if not isinstance(tid, str):
                continue
            task, _ = _scout_kanban_task(tid, board)
            cards.append(task or item)
            if len(cards) >= limit:
                break
        return cards, None
    except Exception as exc:  # noqa: BLE001 — degrade cleanly
        return [], f"kanban unavailable: {type(exc).__name__}: {exc}"


@_guard
def kombat_scout(args: dict, **kwargs) -> str:
    """Classify a task/card/board into lanes and build a deterministic card."""
    try:
        try:
            from . import lanes as _ln
        except ImportError:
            import lanes as _ln  # type: ignore
    except Exception as exc:
        return _err(f"lane engine unavailable: {exc}")

    card_id = args.get("card")
    want_board = bool(args.get("board") or args.get("all"))
    text = args.get("input", args.get("text", ""))

    # Accept {"input": {"card": "..."}} as well as top-level keys.
    if isinstance(text, dict):
        payload = text
        card_id = payload.get("card", card_id)
        want_board = bool(payload.get("board", want_board))
        text = payload.get("input") or payload.get("text") or ""
    text = str(text or "").strip()

    warnings: list[str] = []
    pieces = [text] if text else []
    source = "text"
    kanban_info: dict | None = None

    if card_id:
        source = "card"
        task, warn = _scout_kanban_task(str(card_id))
        kanban_info = {"card": str(card_id)}
        if task:
            body = _scout_task_text(task)
            kanban_info.update(
                {"id": task.get("id", str(card_id)), "title": task.get("title"),
                 "status": task.get("status")}
            )
            pieces = [body] if body else pieces
        else:
            warnings.append(warn or "kanban card unavailable")
    elif want_board:
        source = "board"
        cards, warn = _scout_kanban_open()
        kanban_info = {"board": True, "cards": len(cards)}
        if warn:
            warnings.append(warn)
        texts = [_scout_task_text(c) for c in cards]
        texts = [t for t in texts if t]
        if texts:
            pieces = texts
    elif not text:
        warnings.append("no input text — using fallback lanes")

    combined = "\n".join(p for p in pieces if p).strip()
    classified = _ln.classify(combined)

    seed = args.get("seed")
    if seed is None:
        import hashlib

        seed = int(hashlib.sha1(combined.encode("utf-8")).hexdigest()[:8], 16)

    intensity = _intensity(args.get("intensity"))
    stats = _load_task_stats()
    try:
        extra = args.get("project_tasks")
        card = _ln.build_card(
            classified, seed,
            extra_tasks=extra if isinstance(extra, list) else None,
            intensity=intensity,
            stats=stats,
        )
    except Exception as exc:
        return _err(f"failed to build fight card: {type(exc).__name__}: {exc}")
    # Persist by default so kombat_run_fight can accept the card id later.
    # `persist: false` keeps scouting read-only (no cards.json side effect).
    if args.get("persist", True) is not False:
        _store_card(card)

    expected = _ln.discrimination_level(card, stats)
    reasons = [
        "classified lanes: "
        + ", ".join(f"{item['lane']} ({item['weight']})" for item in classified),
        f"intensity: {intensity} (expected discrimination: {expected})",
    ]
    for rnd in card["rounds"]:
        tasks = rnd["tasks"]
        detail = f"{len(tasks)} task(s)"
        if tasks:
            detail += ": " + ", ".join(tasks)
        reasons.append(f"{rnd['id']} {rnd['name']} ({rnd['points']}pt/task) — {detail}")
    if card.get("sudden_death_task"):
        reasons.append(f"Sudden death — {card['sudden_death_task']}")
    if source == "card" and kanban_info and kanban_info.get("title"):
        reasons.append(f"scouted kanban card {kanban_info['id']}: {kanban_info['title']}")
    if source == "board":
        reasons.append(f"scouted {(kanban_info or {}).get('cards', 0)} open card(s) on the active board")
    reasons.extend(warnings)

    return _ok(
        source=source,
        input=combined[:2000],
        lanes=classified,
        card=card,
        intensity=intensity,
        stats_used=bool(card.get("stats_used")),
        expected_discrimination=expected,
        reasons=reasons,
        kanban=kanban_info,
        warnings=warnings,
    )


# ---------------------------------------------------------------- run a fight

def _usage_of(result, model: str | None = None) -> dict:
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
    if "total_tokens" not in out:
        try:
            out["total_tokens"] = int(out.get("input_tokens") or 0) + int(out.get("output_tokens") or 0)
        except (TypeError, ValueError):
            pass
    # Price the call when the host gave no cost (the host never does).
    cost, source = _estimate_cost(out, model or "")
    out["cost_usd"] = cost
    out["cost_source"] = source
    return out


def _live_state(data_dir, fight_id, card, fighters, task_rounds=None):
    """Best-effort live.json writer (lazy import: missing module never breaks a fight).

    Accepts either the frozen contract ``LiveState(data_dir, fight_id, card,
    fighters)`` or the richer in-repo API (keyword fighters/fight_id) by
    inspecting the constructor.
    """
    try:
        try:
            from . import live as _live
        except ImportError:
            import live as _live  # type: ignore
        cls = _live.LiveState
        try:
            import inspect

            params = set(inspect.signature(cls.__init__).parameters)
        except Exception:  # noqa: BLE001
            params = set()
        if "fighter_a" in params:
            kwargs = {}
            if "broadcast" in params:
                kwargs["broadcast"] = bool(_CTX is not None and hasattr(_CTX, "spawn_task"))
            state = cls(
                data_dir,
                card=card,
                fighter_a=(fighters or {}).get("a"),
                fighter_b=(fighters or {}).get("b"),
                fight_id=fight_id,
                **kwargs,
            )
        else:
            state = cls(data_dir, fight_id, card, fighters)
        return _LiveAdapter(state, task_rounds or {}, fighters or {})
    except Exception:
        return None


class _LiveAdapter:
    """Normalise the frozen LiveState method names onto whatever live.py offers."""

    def __init__(self, state, task_rounds: dict, fighters: dict):
        self.state = state
        self.task_rounds = task_rounds or {}
        self.fighters = fighters or {}

    def round_state(self, round_id, status):
        st = self.state
        if hasattr(st, "round_state"):
            return st.round_state(round_id, status)
        try:
            if status == "fighting" and hasattr(st, "set_status") and getattr(st, "status", "") in ("idle", ""):
                st.set_status("fighting")
        except Exception:  # noqa: BLE001
            pass
        return None

    def task_update(self, task_id, side, **fields):
        st = self.state
        if hasattr(st, "task_update"):
            return st.task_update(task_id, side, **fields)
        round_id = self.task_rounds.get(task_id)
        if hasattr(st, "update_task"):
            return st.update_task(round_id, task_id, side, **fields)
        if hasattr(st, "apply_task_result"):
            return st.apply_task_result(round_id, task_id, side, fields)
        return None

    def finish(self, winner, ko, points, usage, report_path, edge=None):
        st = self.state
        if hasattr(st, "add_usage"):
            for side in ("a", "b"):
                fighter = self.fighters.get(side)
                u = (usage or {}).get(fighter) if isinstance(usage, dict) else None
                if isinstance(u, dict):
                    try:
                        call_cost = u.get("cost_usd")
                        st.add_usage(
                            side,
                            cost_usd=(
                                float(call_cost)
                                if isinstance(call_cost, (int, float)) and not isinstance(call_cost, bool)
                                else None
                            ),
                            tokens=int(u.get("tokens") or 0),
                            latency_s=float(u.get("latency_s") or 0.0),
                        )
                    except Exception:  # noqa: BLE001
                        pass
        try:
            return st.finish(winner=winner, ko=ko, report_path=report_path, edge=edge)
        except TypeError:
            return st.finish(winner, ko, points, usage, report_path)

    def error(self, message):
        st = self.state
        method = getattr(st, "error", None)
        if callable(method):
            return method(message)
        if hasattr(st, "fail"):
            return st.fail(message)
        return None


def _live_call(live_state, method: str, *args, **kwargs) -> None:
    if live_state is None:
        return
    try:
        getattr(live_state, method)(*args, **kwargs)
    except Exception:  # noqa: BLE001 — live state is best-effort
        pass


# ------------------------------------------------------- background job state

JOB: dict = {
    "running": False,
    "kind": None,
    "job_id": None,
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
    "fight_id": None,
}


def reset_job() -> None:
    """Tests/CLI escape hatch: forget the background job state."""
    JOB.update(
        running=False, kind=None, job_id=None, started_at=None,
        finished_at=None, result=None, error=None, fight_id=None,
    )


def _job_mark_done() -> None:
    JOB["running"] = False
    JOB["finished_at"] = _now_iso()


def start_job(ctx, kind: str, fn, fn_args: dict) -> dict:
    """Run ``fn(fn_args)`` in the background; return immediately.

    Uses ``ctx.spawn_task`` (the same path as the /kombat slash command) and
    falls back to a daemon thread when no event loop is available. The live job
    state is exposed through ``kombat_history({"running": true})``.
    """
    try:
        if JOB.get("running"):
            return {
                "started": False,
                "status": "busy",
                "job_id": JOB.get("job_id"),
                "message": "A fight is already running. Poll kombat_history {\"running\": true}.",
            }
        JOB.update(
            running=True,
            kind=kind,
            job_id=f"kombat-{uuid.uuid4().hex[:8]}",
            started_at=_now_iso(),
            finished_at=None,
            result=None,
            error=None,
            fight_id=fn_args.get("fight_id"),
        )

        def _work():
            try:
                JOB["result"] = fn(dict(fn_args))
            except BaseException as exc:  # noqa: BLE001 — surfaced via job state
                JOB["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                _job_mark_done()

        async def _runner():
            await asyncio.to_thread(_work)

        spawned = False
        if ctx is not None and hasattr(ctx, "spawn_task"):
            try:
                ctx.spawn_task(_runner(), name=f"model-kombat:{kind}")
                spawned = True
            except Exception:  # noqa: BLE001 — no running loop
                spawned = False
        if not spawned:
            threading.Thread(
                target=_work, name=f"model-kombat:{kind}", daemon=True
            ).start()
        return {
            "started": True,
            "status": "started",
            "job_id": JOB["job_id"],
            "kind": kind,
            "running": True,
        }
    except Exception as exc:  # noqa: BLE001 — background launch never breaks a tool
        _job_mark_done()
        return {"started": False, "status": "error", "error": str(exc), "job_id": JOB.get("job_id")}


def job_snapshot() -> dict:
    """Live job + last live.json for ``kombat_history({"running": true})``."""
    live_data = _read_json_file(_data_dir() / "live.json", {})
    if not isinstance(live_data, dict):
        live_data = {}
    result = JOB.get("result")
    parsed = None
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            parsed = None
    summary = None
    if isinstance(parsed, dict):
        summary = {
            "ok": parsed.get("ok"),
            "fight_id": parsed.get("fight_id"),
            "errors": len(parsed.get("errors") or []),
            "time_cap_hit": parsed.get("time_cap_hit"),
            "interrupted": parsed.get("interrupted"),
        }
    return {
        "running": bool(JOB.get("running")),
        "kind": JOB.get("kind"),
        "job_id": JOB.get("job_id"),
        "started_at": JOB.get("started_at"),
        "finished_at": JOB.get("finished_at"),
        "error": JOB.get("error"),
        "result": summary,
        "live": live_data,
        "live_status": live_data.get("status"),
        "hint": (
            "Fight still running — poll again. Score it when done with "
            "kombat_score_answers {\"last\": true}."
            if JOB.get("running")
            else "No fight running. Score the stored fight with kombat_score_answers {\"last\": true}."
        ),
    }



def _render_report_delegate(fighter_a, fighter_b, rounds_out, usage, winner, ko, edge=None) -> str:
    """Prefer arcade-artist's render module; fall back to the built-in renderer."""
    try:
        try:
            from . import render as _render
        except ImportError:
            import render as _render  # type: ignore
        try:
            return _render.render_report(fighter_a, fighter_b, rounds_out, usage, winner, ko, edge=edge)
        except TypeError:
            # render.py may not have adopted the optional `edge` field yet.
            return _render.render_report(fighter_a, fighter_b, rounds_out, usage, winner, ko)
    except Exception:  # noqa: BLE001 — missing/broken renderer falls back
        return _render_report(fighter_a, fighter_b, rounds_out, usage, winner, ko, edge=edge)


def _live_fail(fight_id, card, fighters, reason: str) -> None:
    """Write live.json status=error + reason when there is no live handle yet."""
    try:
        live_card = dict(card) if isinstance(card, dict) else {
            "title": "Model Kombat", "lanes": [], "rounds": [],
        }
        live = _live_state(
            _data_dir(), fight_id or "rematch", live_card, fighters or {},
        )
        _live_call(live, "error", reason)
    except Exception:  # noqa: BLE001 — best-effort
        pass


def _interrupt_bout(board, live, reason: str | None, fight_id: str | None = None) -> dict | None:
    """Close the scoreboard + mark live.json error for a fight that never scored.

    Never raises; never masks the caller's original exception.
    """
    reason = str(reason or "fight interrupted").strip() or "fight interrupted"
    try:
        _live_call(live, "error", reason)
    except Exception:  # noqa: BLE001
        pass
    if board is None:
        return None
    try:
        return board.interrupt(reason)
    except BaseException:  # noqa: BLE001 — interrupt must never mask the real error
        return None


@_guard
def kombat_run_fight(args: dict, **kwargs) -> str:
    """Pure fight. ``background: true`` starts it as a job and returns at once."""
    if isinstance(args, dict) and args.get("background"):
        fighter_a = str(args.get("fighter_a") or _cfg("fighter_a", "") or "").strip()
        fighter_b = str(args.get("fighter_b") or _cfg("fighter_b", "") or "").strip()
        if not fighter_a or not fighter_b:
            return _err("Both fighter_a and fighter_b model IDs are required.")
        if _CTX is None or not hasattr(_CTX, "llm"):
            return _err(
                "No live Hermes host context (ctx.llm unavailable). Run this inside a Hermes "
                "session with the plugin enabled, not from a bare Python import."
            )
        payload = {k: v for k, v in args.items() if k != "background"}
        job = start_job(_CTX, "fight", kombat_run_fight, payload)
        return _ok(**job)
    return _run_fight(args)


def _run_fight(args: dict, **kwargs) -> str:
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

    plan, plan_err = _fight_plan(args, suite)
    if plan_err:
        return _err(plan_err)

    tasks_by_id = _task_lookup()
    referenced = {tid for rnd in plan["rounds"] for tid in rnd["tasks"]}
    if plan.get("sudden_death_task"):
        referenced.add(str(plan["sudden_death_task"]))
    missing = sorted(tid for tid in referenced if tid not in tasks_by_id)
    if missing:
        return _err(f"Fight card references unknown task(s): {', '.join(missing)}.")

    try:
        temperature = float(args.get("temperature", _cfg("temperature", 0)))
        max_tokens = int(args.get("max_tokens", _cfg("max_tokens", 2000)))
        budget = _cap("max_fight_cost_usd", args.get("budget_usd"), 2.0)
        token_budget = _cap("max_fight_tokens", args.get("budget_tokens"), 400000, cast=int)
        timeout_s = float(args.get("timeout_s", _cfg("llm_timeout_s", 180)))
        pace_ms = float(args.get("pace_ms", _cfg("pace_ms", 0)) or 0)
        max_rate_wait_s = float(args.get("max_rate_wait_s", _cfg("max_rate_wait_s", 120)) or 0)
        wait_budget_s = float(args.get("rate_wait_budget_s", _cfg("rate_wait_budget_s", 240)) or 0)
        auto_rematch_cfg = args.get("auto_rematch")
        if auto_rematch_cfg is None:
            auto_rematch_cfg = _cfg("auto_rematch", True)
        auto_rematch = bool(auto_rematch_cfg)
        rematch_passes = max(0, min(2, int(args.get("max_rematch_passes", 2))))
        max_fight_seconds = _cap(
            "max_fight_seconds", args.get("max_fight_seconds"), 360, zero_is_unlimited=True
        )
    except (TypeError, ValueError):
        return _err("temperature/max_tokens/budget_usd/timeout_s/pace_ms must be numbers.")

    fight_id = str(args.get("fight_id") or datetime.now().strftime("%Y%m%d-%H%M%S"))
    pace_s = max(0.0, pace_ms) / 1000.0
    wait_state = _wait_state(wait_budget_s)
    card = plan.get("card")

    live_card = card or {
        "title": "Model Kombat fixed suite",
        "lanes": [],
        "rounds": [
            {"id": r["id"], "name": r["name"], "points": r["points"], "tasks": r["tasks"]}
            for r in plan["rounds"]
        ],
        "sudden_death_task": plan.get("sudden_death_task"),
    }
    live_card = dict(live_card)
    # Sudden death is fought up front but only *shown* when a tie uses it
    # (kombat_score_answers rebuilds live.json with the SD round then).
    live_card.pop("sudden_death_task", None)
    live_rounds = {tid: r["id"] for r in plan["rounds"] for tid in r["tasks"]}
    live = _live_state(
        _data_dir(), fight_id, live_card,
        {"a": fighter_a, "b": fighter_b}, live_rounds,
    )

    # Kanban scoreboard (display only; never fails the fight). Rounds come from
    # the fight card when there is one, so cards track the adaptive card. The
    # board sweeps stale cards and reuses an open bout's ids on a retry.
    board = None
    if bool(_cfg("show_on_kanban", True)):
        try:
            try:
                from . import kanban as _kb
            except ImportError:  # top-level import outside the plugin package (tests)
                import kanban as _kb  # type: ignore

            board_suite = {
                "rounds": [
                    {"id": r["id"], "name": r["name"], "points": r["points"], "tasks": r["tasks"]}
                    for r in plan["rounds"]
                ]
            }
            try:
                fight_key = _kb.bout_key(
                    fighter_a, fighter_b, card,
                    (card or {}).get("intensity") or args.get("intensity") or _intensity(None),
                )
            except Exception:  # noqa: BLE001 — a key is a nicety, not a requirement
                fight_key = None
            board = _kb.Scoreboard(
                fighter_a, fighter_b, board_suite, card=card, fight_key=fight_key
            )
            board.start()
        except Exception:
            board = None

    answered_ok = False
    interrupt_reason: str | None = None
    fight_started = time.time()
    try:
        answers: dict[str, dict[str, str]] = {fighter_a: {}, fighter_b: {}}
        meta: dict[str, dict[str, dict]] = {fighter_a: {}, fighter_b: {}}
        errors: list[dict] = []
        totals = {
            fighter_a: {
                "cost_usd": 0.0, "cost_source": "estimated", "tokens": 0,
                "input_tokens": 0, "output_tokens": 0, "latency_s": 0.0, "waited_s": 0.0,
            },
            fighter_b: {
                "cost_usd": 0.0, "cost_source": "estimated", "tokens": 0,
                "input_tokens": 0, "output_tokens": 0, "latency_s": 0.0, "waited_s": 0.0,
            },
        }
        sd_usage = {
            fighter_a: {"cost_usd": 0.0, "cost_source": "unknown", "tokens": 0,
                        "input_tokens": 0, "output_tokens": 0, "latency_s": 0.0},
            fighter_b: {"cost_usd": 0.0, "cost_source": "unknown", "tokens": 0,
                        "input_tokens": 0, "output_tokens": 0, "latency_s": 0.0},
        }
        cost_sources = {fighter_a: set(), fighter_b: set()}
        budget_exceeded = False
        budget_reason = None

        def _spent_usd() -> float:
            return sum(
                float(slot["cost_usd"])
                for slot in totals.values()
                if isinstance(slot.get("cost_usd"), (int, float))
            )

        def _spent_tokens() -> int:
            return sum(int(slot.get("tokens") or 0) for slot in totals.values())

        def _note_cost(fighter: str, usage: dict) -> float | None:
            """Fold one call's usage into the fighter totals; returns its cost."""
            slot = totals[fighter]
            cost = usage.get("cost_usd")
            source = str(usage.get("cost_source") or "unknown")
            cost_sources[fighter].add(source)
            try:
                slot["input_tokens"] += int(usage.get("input_tokens") or 0)
                slot["output_tokens"] += int(usage.get("output_tokens") or 0)
                slot["tokens"] += int(
                    usage.get("total_tokens") or usage.get("output_tokens") or 0
                )
            except (TypeError, ValueError):
                pass
            if cost is None or source == "unknown":
                slot["cost_usd"] = None
            else:
                try:
                    if slot.get("cost_usd") is not None:
                        slot["cost_usd"] = round(float(slot["cost_usd"]) + float(cost), 9)
                except (TypeError, ValueError):
                    slot["cost_usd"] = None
            return cost if isinstance(cost, (int, float)) else None

        def _check_budget() -> None:
            nonlocal budget_exceeded, budget_reason
            if _spent_usd() > budget:
                budget_exceeded = True
                budget_reason = f"USD cap ${budget:.4f} exceeded"
            elif _spent_tokens() > token_budget:
                budget_exceeded = True
                budget_reason = f"token cap {token_budget} exceeded"

        time_cap_hit = False

        def _time_capped() -> bool:
            """Whole-fight wall-clock cap (default 360s, under the host's 420s)."""
            nonlocal time_cap_hit
            if max_fight_seconds > 0 and (time.time() - fight_started) >= max_fight_seconds:
                time_cap_hit = True
            return time_cap_hit

        def call_pair(tid: str, fighter: str, *, is_sudden_death: bool = False) -> None:
            """One (task, fighter) call; updates answers/meta/totals/live. Raises PermissionError."""
            task = tasks_by_id[tid]
            side = "a" if fighter == fighter_a else "b"
            if pace_s:
                time.sleep(pace_s)
            started = time.perf_counter()
            text, usage, attempts, err, waited = _complete_with_retry(
                _CTX, fighter, task["prompt"], temperature, max_tokens, timeout_s,
                max_attempts=int(args.get("max_attempts", 3) or 3),
                max_rate_wait_s=max_rate_wait_s,
                wait_state=wait_state,
            )
            latency = round(time.perf_counter() - started, 3)
            answers[fighter][tid] = text
            meta[fighter][tid] = {
                "latency_s": latency,
                "usage": usage,
                "attempts": attempts,
                "waited_s": waited,
                "error": err,
                "status": "errored" if err else "scored",
                "sudden_death": bool(is_sudden_death),
            }
            totals[fighter]["latency_s"] += latency
            totals[fighter]["waited_s"] += waited
            cost = _note_cost(fighter, usage) if usage else None
            if is_sudden_death:
                sd_slot = sd_usage[fighter]
                sd_slot["latency_s"] += latency
                try:
                    sd_slot["input_tokens"] += int(usage.get("input_tokens") or 0)
                    sd_slot["output_tokens"] += int(usage.get("output_tokens") or 0)
                    sd_slot["tokens"] += int(
                        usage.get("total_tokens") or usage.get("output_tokens") or 0
                    )
                except (TypeError, ValueError):
                    pass
                if usage.get("cost_usd") is None:
                    sd_slot["cost_usd"] = None
                elif sd_slot.get("cost_usd") is not None:
                    try:
                        sd_slot["cost_usd"] = round(float(sd_slot["cost_usd"]) + float(usage["cost_usd"]), 9)
                    except (TypeError, ValueError):
                        sd_slot["cost_usd"] = None
                if usage.get("cost_source"):
                    sd_slot["cost_source"] = usage["cost_source"]
            _live_call(
                live, "task_update", tid, side,
                state="error" if err else "done",
                score=None,
                latency_s=latency,
                cost_usd=cost,
                detail=(err or "")[:400],
            )
            if err:
                errors.append({"task": tid, "fighter": fighter, "error": err})
            _check_budget()

        for rnd in plan["rounds"]:
            if _time_capped() or budget_exceeded:
                break
            _live_call(live, "round_state", rnd["id"], "fighting")
            if board is not None:
                board.round_fighting(rnd["id"])
            for tid in rnd["tasks"]:
                if _time_capped():
                    break
                _live_call(live, "task_update", tid, "a", state="fighting")
                _live_call(live, "task_update", tid, "b", state="fighting")
                for fighter in (fighter_a, fighter_b):
                    if _time_capped():
                        break
                    try:
                        call_pair(tid, fighter)
                    except PermissionError as exc:
                        _live_call(live, "error", f"host refused model override: {exc}")
                        interrupt_reason = f"host refused model override: {exc}"
                        if board is not None:
                            board.abort()
                        return _err(
                            f"Host refused the model override: {exc}. "
                            "Enable it in config.yaml: plugins.entries.model-kombat.llm."
                            "allow_model_override: true (optionally restrict with allowed_models), "
                            "then retry the fight. Nothing was sent.",
                            partial={"answers_a": answers[fighter_a], "answers_b": answers[fighter_b]},
                        )
                    if budget_exceeded:
                        break
                if budget_exceeded:
                    break
            if budget_exceeded:
                break

        # Sudden death is fetched up front (one cheap extra task per fighter) so a
        # tie after the Championship can be decided without a second run. Scoring
        # only uses these answers on a points tie.
        sd_task = str(plan.get("sudden_death_task") or "").strip() or None
        round_task_ids = {tid for rnd in plan["rounds"] for tid in rnd["tasks"]}
        if sd_task and sd_task in round_task_ids:
            sd_task = None  # already a normal round task; no separate SD fetch
        if sd_task and sd_task in tasks_by_id and not budget_exceeded and not _time_capped():
            for fighter in (fighter_a, fighter_b):
                if _time_capped():
                    break
                try:
                    call_pair(sd_task, fighter, is_sudden_death=True)
                except PermissionError as exc:
                    _live_call(live, "error", f"host refused model override: {exc}")
                    interrupt_reason = f"host refused model override (sudden death): {exc}"
                    if board is not None:
                        board.abort()
                    return _err(
                        f"Host refused the model override for sudden death: {exc}. "
                        "Enable plugins.entries.model-kombat.llm.allow_model_override in config.yaml."
                    )
                if budget_exceeded:
                    break

        # Bounded auto-rematch of errored (task, fighter) pairs inside this fight.
        rematch_passes_done = 0
        if auto_rematch and errors and not budget_exceeded and not _time_capped():
            for pass_no in range(1, rematch_passes + 1):
                if not errors or budget_exceeded or _time_capped():
                    break
                rematch_passes_done = pass_no
                prior = list(errors)
                errors = []
                for position, entry in enumerate(prior):
                    if _time_capped():
                        errors.extend(prior[position:])
                        break
                    tid = entry.get("task")
                    fighter = entry.get("fighter")
                    if tid not in tasks_by_id or fighter not in (fighter_a, fighter_b):
                        errors.append(entry)
                        continue
                    try:
                        call_pair(tid, fighter)
                    except PermissionError as exc:
                        _live_call(live, "error", f"host refused model override: {exc}")
                        interrupt_reason = f"host refused model override (auto-rematch): {exc}"
                        if board is not None:
                            board.abort()
                        return _err(f"Host refused the model override during auto-rematch: {exc}.")
                    if not meta.get(fighter, {}).get(tid, {}).get("error"):
                        continue  # recovered — drop the error
                    errors.append({**entry, "pass": pass_no})
                    if budget_exceeded:
                        break
                if not errors:
                    break

        # Aggregate cost provenance per fighter (unknown beats estimated beats host).
        for fighter in (fighter_a, fighter_b):
            srcs = cost_sources[fighter]
            if "unknown" in srcs or not srcs:
                totals[fighter]["cost_source"] = "unknown"
            elif "estimated" in srcs:
                totals[fighter]["cost_source"] = "estimated"
            else:
                totals[fighter]["cost_source"] = "host"

        # Budget / time-cap stop: every pair that was never attempted is
        # skipped/errored, never failed.
        planned_pairs = [
            (tid, fighter)
            for rnd in plan["rounds"]
            for tid in rnd["tasks"]
            for fighter in (fighter_a, fighter_b)
        ]
        if sd_task:
            planned_pairs.extend((sd_task, fighter) for fighter in (fighter_a, fighter_b))
        for tid, fighter in planned_pairs:
            if tid in meta.get(fighter, {}):
                continue
            if budget_exceeded:
                reason = f"not attempted: {budget_reason}"
            elif time_cap_hit:
                reason = "not attempted: fight time cap"
            else:
                reason = "not attempted"
            meta[fighter][tid] = {
                "latency_s": 0.0, "usage": {}, "attempts": 0, "waited_s": 0.0,
                "error": reason, "status": "skipped", "sudden_death": tid == sd_task,
            }
            answers[fighter].setdefault(tid, "")
            errors.append({"task": tid, "fighter": fighter, "error": reason, "status": "skipped"})

        budget_info = {
            "exceeded": bool(budget_exceeded),
            "reason": budget_reason,
            "cap_usd": budget,
            "cap_tokens": token_budget,
            "cap_seconds": max_fight_seconds,
            "time_cap_hit": bool(time_cap_hit),
            "spent_usd": round(_spent_usd(), 9),
            "spent_tokens": _spent_tokens(),
            "elapsed_s": round(time.time() - fight_started, 3),
        }

        # A budget stop returns the partial answers, but the bout never reaches
        # scoring as a complete fight → close its cards in the finally block.
        if budget_exceeded:
            interrupt_reason = f"budget stop: {budget_reason}"
        else:
            answered_ok = True

        fight = {
            "fight_id": fight_id,
            "fighter_a": fighter_a,
            "fighter_b": fighter_b,
            "mode": "pure",
            "card": card,
            "plan_rounds": plan["rounds"],
            "sudden_death_task": plan.get("sudden_death_task"),
            "sudden_death_usage": sd_usage,
            "answers_a": answers[fighter_a],
            "answers_b": answers[fighter_b],
            "meta_a": meta[fighter_a],
            "meta_b": meta[fighter_b],
            "errors": errors,
            "usage": totals,
            "budget": budget_info,
            "budget_exceeded": budget_exceeded,
            "time_cap_hit": bool(time_cap_hit),
            "interrupted": bool(interrupt_reason),
            "kanban": board.ids if board is not None else None,
            "rematch_passes": rematch_passes_done,
            "waited_s": {fighter: totals[fighter]["waited_s"] for fighter in totals},
        }
        try:
            if hasattr(_CTX, "state"):
                _CTX.state.set("last_fight", fight)
        except Exception:
            pass

        return _ok(
            fight_id=fight_id,
            fighters=[fighter_a, fighter_b],
            card=card,
            mode=plan["mode"],
            intensity=(card or {}).get("intensity"),
            rounds=[tid for rnd in plan["rounds"] for tid in rnd["tasks"]],
            answers_a=answers[fighter_a],
            answers_b=answers[fighter_b],
            meta_a=meta[fighter_a],
            meta_b=meta[fighter_b],
            errors=errors,
            usage=totals,
            sudden_death_task=fight.get("sudden_death_task"),
            sudden_death_usage=sd_usage,
            budget=budget_info,
            budget_exceeded=budget_exceeded,
            rematch_passes=rematch_passes_done,
            waited_s=fight["waited_s"],
            live=live is not None,
            kanban=board.ids if board is not None else None,
            kanban_errors=board.errors if board is not None else [],
            time_cap_hit=bool(time_cap_hit),
            interrupted=bool(interrupt_reason),
            stale_closed=int(getattr(board, "stale_closed", 0) or 0),
            bout_reused=bool(getattr(board, "bout_reused", False)),
        )


    except BaseException as exc:  # KeyboardInterrupt / SystemExit / any crash
        interrupt_reason = interrupt_reason or f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # The fight only closes its cards itself when it reached the end of the
        # run without a budget stop; otherwise the bout is interrupted here.
        if not answered_ok:
            _interrupt_bout(board, live, interrupt_reason, fight_id)


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
    tasks_by_id = _task_lookup()

    try:
        temperature = float(args.get("temperature", _cfg("temperature", 0)))
        max_tokens = int(args.get("max_tokens", _cfg("max_tokens", 2000)))
        budget = _cap("max_fight_cost_usd", args.get("budget_usd"), 2.0)
        token_budget = _cap("max_fight_tokens", args.get("budget_tokens"), 400000, cast=int)
        timeout_s = float(args.get("timeout_s", _cfg("llm_timeout_s", 180)))
        pace_ms = float(args.get("pace_ms", _cfg("pace_ms", 0)) or 0)
        max_rate_wait_s = float(args.get("max_rate_wait_s", _cfg("max_rate_wait_s", 120)) or 0)
        wait_state = _wait_state(float(args.get("rate_wait_budget_s", _cfg("rate_wait_budget_s", 240)) or 0))
    except (TypeError, ValueError):
        return _err("temperature/max_tokens/budget_usd/timeout_s must be numbers.")

    board = None
    completed = False
    reason: str | None = None
    fighters = (last.get("fighter_a"), last.get("fighter_b"))
    try:
        answers = {fighters[0]: dict(last.get("answers_a") or {}),
                   fighters[1]: dict(last.get("answers_b") or {})}
        meta = {fighters[0]: dict(last.get("meta_a") or {}),
                fighters[1]: dict(last.get("meta_b") or {})}
        usage = dict(last.get("usage") or {})
        rematched, still_failing = [], []
        spend = 0.0
        tokens_spent = 0

        for err in errors:
            tid, fighter = err.get("task"), err.get("fighter")
            if tid not in tasks_by_id or fighter not in fighters:
                still_failing.append(err)
                continue
            if pace_ms > 0:
                time.sleep(pace_ms / 1000.0)
            try:
                text, call_usage, attempts, call_err, call_waited = _complete_with_retry(
                    _CTX, fighter, tasks_by_id[tid]["prompt"], temperature, max_tokens, timeout_s,
                    max_rate_wait_s=max_rate_wait_s, wait_state=wait_state,
                )
            except PermissionError as exc:
                return _err(
                    f"Host refused the model override during rematch: {exc}. "
                    "Check plugins.entries.model-kombat.llm.allow_model_override in config.yaml."
                )
            spend += float(call_usage.get("cost_usd") or 0.0)
            tokens_spent += int(call_usage.get("total_tokens") or call_usage.get("output_tokens") or 0)
            if call_err:
                still_failing.append({"task": tid, "fighter": fighter, "error": call_err})
                answers[fighter][tid] = text
                meta[fighter][tid] = {"error": call_err, "attempts": attempts, "latency_s": 0, "usage": call_usage}
            else:
                rematched.append({"task": tid, "fighter": fighter})
                answers[fighter][tid] = text
                meta[fighter][tid] = {"error": None, "attempts": attempts, "latency_s": 0, "usage": call_usage}
            slot = usage.setdefault(fighter, {"cost_usd": 0.0, "tokens": 0, "latency_s": 0.0})
            slot["waited_s"] = round(float(slot.get("waited_s") or 0.0) + float(call_waited or 0.0), 3)
            call_cost = call_usage.get("cost_usd")
            if call_cost is None:
                slot["cost_usd"] = None
            elif slot.get("cost_usd") is not None:
                try:
                    slot["cost_usd"] = round(float(slot["cost_usd"]) + float(call_cost), 9)
                except (TypeError, ValueError):
                    slot["cost_usd"] = None
            slot["tokens"] = int(slot.get("tokens") or 0) + int(call_usage.get("total_tokens") or call_usage.get("output_tokens") or 0)
            slot["input_tokens"] = int(slot.get("input_tokens") or 0) + int(call_usage.get("input_tokens") or 0)
            slot["output_tokens"] = int(slot.get("output_tokens") or 0) + int(call_usage.get("output_tokens") or 0)
            if spend > budget or tokens_spent > token_budget:
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
            try:
                from . import kanban as _kb
            except ImportError:  # top-level import outside the plugin package (tests)
                import kanban as _kb  # type: ignore

            if bool(_cfg("show_on_kanban", True)):
                affected = sorted(
                    {
                        r["id"]
                        for r in (last.get("plan_rounds") or suite["rounds"])
                        if any(t in {e.get("task") for e in errors} for t in r["tasks"])
                    }
                )
                board = _kb.Scoreboard(
                    fighters[0], fighters[1],
                    {"rounds": last.get("plan_rounds") or suite["rounds"]},
                )
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

        completed = True
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

    except BaseException as exc:  # KeyboardInterrupt / SystemExit / any crash
        reason = reason or f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if not completed:
            _interrupt_bout(board, None, reason, (last or {}).get("fight_id"))
            _live_fail((last or {}).get("fight_id"), (last or {}).get("card"), {
                "a": (last or {}).get("fighter_a"), "b": (last or {}).get("fighter_b"),
            }, reason or "rematch interrupted")


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
    """Run candidate code + the legacy checks harness in the hardened runner.

    Delegates to engine_checks.run_legacy_harness, so the legacy T2/T6/T7/T8
    tasks get exactly the hardening the code_exec scorer gets (empty env,
    python -I, rlimits, audit hook, import blocker). Still NOT a security
    sandbox (see README disclosure): the hook is Python-level, so treat all
    candidate code as untrusted.
    """
    try:
        from . import engine_checks as _ec
    except ImportError:  # top-level import outside the plugin package (tests)
        import engine_checks as _ec  # type: ignore
    try:
        return _ec.run_legacy_harness(code, harness, timeout)
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

def _render_report(fighter_a: str, fighter_b: str, rounds_out: list[dict], usage: dict, winner: str, ko: bool, edge: dict | None = None) -> str:
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
        u = u if isinstance(u, dict) else {}
        cost = u.get("cost_usd")
        cost_txt = f"${float(cost):.4f}" if isinstance(cost, (int, float)) and not isinstance(cost, bool) else "n/a"
        usage_rows += (
            f"<tr><td>{esc(fighter)}</td><td>{u.get('latency_s', 0):.1f}s</td>"
            f"<td>{u.get('tokens', 0)}</td><td>{cost_txt}</td></tr>"
        )
    if winner == "draw":
        winner_line = "The fight is a draw"
        if edge and edge.get("fighter"):
            winner_line += f" — {esc(edge['fighter'])} has the {esc(edge.get('label') or 'efficiency edge')}: {esc(edge.get('reason') or '')}"
    else:
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
<p><small>Scored mechanically by Model Kombat. Code rounds execute only when the allow_code_execution plugin setting is on (subprocess, timeout, not a security sandbox); a tool argument cannot switch that setting on. Errored/skipped rounds are excluded from points.</small></p>
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


def _ladder_module():
    try:
        from . import ladder as _ladder
    except ImportError:
        import ladder as _ladder  # type: ignore
    return _ladder


def _score_task_side(task: dict, answer: str, allow_exec: bool) -> dict:
    """Score one answer: new check-spec tasks via engine_checks, legacy via scorers."""
    check = task.get("check")
    if isinstance(check, dict):
        result = score_check(check, answer, allow_exec)
        status = str(result.get("status") or "")
        if status not in ("scored", "skipped", "errored"):
            status = "scored" if result.get("score") is not None else "errored"
        frac = float(result["score"]) if status == "scored" and result.get("score") is not None else None
        return {
            "score": frac if frac is not None else 0.0,
            "max": 1,
            "passed": bool(frac is not None and frac >= 1.0),
            "details": [str(result.get("detail") or "")],
            "status": status,
            "fraction": frac,
        }
    result = _score_one(task.get("scorer", ""), answer, task, allow_exec)
    status = "skipped" if result.get("skipped") else ("errored" if result.get("errored") else "scored")
    frac = _fraction(result) if status == "scored" else None
    return {**result, "status": status, "fraction": frac}


def _meta_detail(meta: dict, tid: str) -> dict:
    item = (meta or {}).get(tid) or {}
    usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
    cost = usage.get("cost_usd")
    return {
        "latency_s": float(item.get("latency_s") or 0.0),
        "cost_usd": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        "attempts": item.get("attempts"),
        "waited_s": float(item.get("waited_s") or 0.0),
    }


def _r3_round(rounds_out: list[dict]) -> dict | None:
    for rnd in rounds_out:
        if rnd.get("id") == "R3":
            return rnd
    return None


def _num_or_none(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _value_ratios(fighter_a: str, fighter_b: str, usage: dict) -> dict:
    """Measured cost/latency ratios behind a draw (display only — never a win).

    Returns ``latency_ratio`` / ``cost_ratio`` oriented so ``>= 1`` is an
    advantage for ``value_fighter``, plus a one-clause ``value_summary`` such as
    ``Nemo-Nano is 2.8× faster and 18.0× cheaper at equal score``. Ratios under
    5% are noise and are reported as ``None``.
    """
    table = usage if isinstance(usage, dict) else {}
    ua = table.get(fighter_a) if isinstance(table.get(fighter_a), dict) else {}
    ub = table.get(fighter_b) if isinstance(table.get(fighter_b), dict) else {}

    def _adv(x, y):
        """(side, ratio) where ratio >= 1 favours the side with the smaller value."""
        if x is None or y is None or x <= 0 or y <= 0 or x == y:
            return None, None
        side, ratio = ("a", y / x) if x < y else ("b", x / y)
        if ratio < 1.05:
            return None, None
        return side, round(ratio, 2)

    speed_side, speed = _adv(_num_or_none(ua.get("latency_s")), _num_or_none(ub.get("latency_s")))
    cost_side, cheap = _adv(_num_or_none(ua.get("cost_usd")), _num_or_none(ub.get("cost_usd")))
    if speed is None and cheap is None:
        return {"value_fighter": None, "latency_ratio": None, "cost_ratio": None,
                "value_summary": None}

    def _id(side):
        return fighter_a if side == "a" else fighter_b

    if speed is not None and cheap is not None and speed_side == cost_side:
        fighter = _id(speed_side)
        summary = f"{_short_name(fighter)} is {speed:.1f}× faster and {cheap:.1f}× cheaper at equal score"
        return {"value_fighter": fighter, "latency_ratio": speed, "cost_ratio": cheap,
                "value_summary": summary}
    parts = []
    if speed is not None:
        parts.append(f"{_short_name(_id(speed_side))} {speed:.1f}× faster")
    if cheap is not None:
        parts.append(f"{_short_name(_id(cost_side))} {cheap:.1f}× cheaper")
    return {
        "value_fighter": _id(speed_side) if speed is not None else _id(cost_side),
        "latency_ratio": speed,
        "cost_ratio": cheap,
        "value_summary": " · ".join(parts) + " at equal score",
    }


def _efficiency_edge(fighter_a: str, fighter_b: str, usage: dict, has_errors: bool = False) -> dict:
    """Tie-break display for a draw: faster latency, then fewer output tokens, then cost.

    This is explicitly **not** a win — it never changes ``winner`` or the ladder.
    When ``has_errors`` is set the accumulated latency includes abandoned/errored
    calls, so the edge is marked provisional and the cost/latency ratios are
    suppressed rather than presenting an 80x "speedup" that is really a timeout.
    """
    usage = usage if isinstance(usage, dict) else {}
    ua = usage.get(fighter_a) if isinstance(usage.get(fighter_a), dict) else {}
    ub = usage.get(fighter_b) if isinstance(usage.get(fighter_b), dict) else {}

    def latency(u):
        return _num_or_none(u.get("latency_s"))

    def out_tokens(u):
        val = _num_or_none(u.get("output_tokens"))
        if val is None:
            val = _num_or_none(u.get("tokens"))
        return val

    def cost(u):
        return _num_or_none(u.get("cost_usd"))

    metrics = (
        ("latency", latency, "faster total latency"),
        ("output_tokens", out_tokens, "fewer output tokens"),
        ("cost", cost, "lower cost"),
    )
    for _key, getter, phrase in metrics:
        va, vb = getter(ua), getter(ub)
        if va is None or vb is None or va == vb:
            continue
        winner = fighter_a if va < vb else fighter_b
        if _key == "latency":
            detail = f"{va:.3f}s vs {vb:.3f}s"
        elif _key == "output_tokens":
            detail = f"{int(va)} vs {int(vb)} output tokens"
        else:
            detail = f"${va:.4f} vs ${vb:.4f}"
        return {
            "fighter": winner,
            "metric": _key,
            "reason": f"{phrase} ({detail}) — efficiency edge (not a win)",
            "label": "efficiency edge (not a win)",
            "a": va,
            "b": vb,
            **({"provisional": True,
                "note": "provisional — this fight had errored/excluded calls, so the totals include abandoned attempts",
                "value_fighter": None, "latency_ratio": None, "cost_ratio": None, "value_summary": None}
               if has_errors else _value_ratios(fighter_a, fighter_b, usage)),
        }
    return {
        "fighter": None,
        "metric": None,
        "reason": "dead heat — identical latency, output tokens and cost",
        "label": "efficiency edge (not a win)",
        "a": None,
        "b": None,
    }


def _last_fight_score_args() -> dict | None:
    """Build kombat_score_answers args from the stored last_fight (``{"last": true}``)."""
    if _CTX is None or not hasattr(_CTX, "state"):
        return None
    try:
        last = _CTX.state.get("last_fight", default=None)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(last, dict) or not last:
        return None
    errors = last.get("errors") or []
    fighter_a = last.get("fighter_a")
    fighter_b = last.get("fighter_b")
    derived = {
        "fighter_a": fighter_a,
        "fighter_b": fighter_b,
        "answers_a": last.get("answers_a") or {},
        "answers_b": last.get("answers_b") or {},
        "meta_a": last.get("meta_a") or {},
        "meta_b": last.get("meta_b") or {},
        "usage": last.get("usage") or {},
        "fight_id": last.get("fight_id"),
        "errors_a": [e.get("task") for e in errors if isinstance(e, dict) and e.get("fighter") == fighter_a],
        "errors_b": [e.get("task") for e in errors if isinstance(e, dict) and e.get("fighter") == fighter_b],
    }
    card = last.get("card")
    if isinstance(card, dict) and card.get("rounds"):
        derived["card"] = card
    return derived


@_guard
def kombat_score_answers(args: dict, **kwargs) -> str:
    if args.get("last") and not (args.get("answers_a") or args.get("answers_b")):
        derived = _last_fight_score_args()
        if not derived:
            return _err("No fight stored in this session yet — run kombat_run_fight first.")
        merged = dict(derived)
        merged.update({k: v for k, v in args.items() if k != "last"})
        args = merged
    fighter_a = str(args.get("fighter_a") or "Fighter A").strip()
    fighter_b = str(args.get("fighter_b") or "Fighter B").strip()
    answers_a = args.get("answers_a") or {}
    answers_b = args.get("answers_b") or {}
    if not isinstance(answers_a, dict) or not isinstance(answers_b, dict):
        return _err("answers_a and answers_b must be objects mapping task ID -> answer text.")
    # Code execution is a USER setting only. It is deliberately absent from
    # the tool schema: a model must not be able to switch on execution of its
    # own Python through a tool call.
    allow_exec = bool(_cfg("allow_code_execution", False))
    render = args.get("render_report", True)
    usage = args.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    errors_a = {str(e) for e in (args.get("errors_a") or [])}
    errors_b = {str(e) for e in (args.get("errors_b") or [])}
    meta_a = args.get("meta_a") if isinstance(args.get("meta_a"), dict) else {}
    meta_b = args.get("meta_b") if isinstance(args.get("meta_b"), dict) else {}

    try:
        suite = _load_suite()
    except Exception as exc:
        return _err(f"Failed to load task suite: {exc}")

    # The card can come from the args or the stored last fight (the /kombat score flow).
    card_arg = args.get("card")
    last = None
    if _CTX is not None and hasattr(_CTX, "state"):
        try:
            last = _CTX.state.get("last_fight", default=None)
        except Exception:
            last = None
    if card_arg is None and isinstance(last, dict):
        last_card = last.get("card")
        if isinstance(last_card, dict) and last_card.get("rounds"):
            card_ids = {
                str(tid)
                for rnd in last_card.get("rounds") or []
                for tid in (rnd.get("tasks") or [])
            }
            if str(last_card.get("sudden_death_task")):
                card_ids.add(str(last_card["sudden_death_task"]))
            answered = {str(t) for t in answers_a} | {str(t) for t in answers_b}
            if not answered or answered <= card_ids:
                card_arg = last_card
    plan, plan_err = _fight_plan({"card": card_arg} if card_arg is not None else {}, suite)
    if plan_err:
        return _err(plan_err)
    tasks_by_id = _task_lookup()
    fight_id = str(args.get("fight_id") or (last or {}).get("fight_id") or datetime.now().strftime("%Y%m%d-%H%M%S"))
    if usage is None or not usage:
        usage = (last or {}).get("usage") or {}

    rounds_out: list[dict] = []
    points = {fighter_a: 0.0, fighter_b: 0.0}
    ko_rounds: list[str] = []
    incomplete_rounds: list[str] = []

    for rnd in plan["rounds"]:
        tasks_out: list[dict] = []
        for tid in rnd["tasks"]:
            if tid not in answers_a and tid not in answers_b:
                continue
            task = tasks_by_id.get(tid)
            if not task:
                continue
            sa = _score_task_side(task, str(answers_a.get(tid, "")), allow_exec)
            sb = _score_task_side(task, str(answers_b.get(tid, "")), allow_exec)
            excluded = None
            if tid in errors_a or tid in errors_b:
                excluded = "errored"
            elif sa["status"] == "skipped" or sb["status"] == "skipped":
                excluded = "skipped (code execution off)"
            elif sa["status"] == "errored" or sb["status"] == "errored":
                excluded = "errored (check spec)"
            fa = sa.get("fraction") if not excluded else None
            fb = sb.get("fraction") if not excluded else None
            tasks_out.append(
                {
                    "id": tid,
                    "title": task.get("title") or tid,
                    "lane": task.get("lane"),
                    "scorer": task.get("scorer") or (task.get("check") or {}).get("type"),
                    "check_type": (task.get("check") or {}).get("type") if isinstance(task.get("check"), dict) else None,
                    "a": sa,
                    "b": sb,
                    "fraction_a": fa,
                    "fraction_b": fb,
                    "points_a": 0.0,
                    "points_b": 0.0,
                    "ko": False,
                    "excluded": excluded,
                    "status_a": sa.get("status"),
                    "status_b": sb.get("status"),
                }
            )

        if not tasks_out:
            continue
        included = [t for t in tasks_out if not t["excluded"]]
        if not included:
            incomplete_rounds.append(rnd["id"])

        if plan["mode"] == "card":
            # Round stake (1/2/3): the round goes to the higher summed score fraction.
            sum_a = sum(t["fraction_a"] or 0.0 for t in included)
            sum_b = sum(t["fraction_b"] or 0.0 for t in included)
            stake = float(rnd["points"])
            if not included:
                wa = wb = 0.0
            elif sum_a > sum_b:
                wa, wb = stake, 0.0
            elif sum_b > sum_a:
                wa, wb = 0.0, stake
            else:
                wa = wb = stake / 2.0
            if included:
                share_a = wa / len(included)
                share_b = wb / len(included)
                for t in included:
                    t["points_a"] = round(share_a, 6)
                    t["points_b"] = round(share_b, 6)
        else:
            # Legacy suite: every task is worth its round's points.
            for t in included:
                fa, fb = t["fraction_a"], t["fraction_b"]
                if fa is None or fb is None:
                    continue
                if fa > fb:
                    t["points_a"] = float(rnd["points"])
                elif fb > fa:
                    t["points_b"] = float(rnd["points"])
                else:
                    t["points_a"] = t["points_b"] = float(rnd["points"]) / 2.0
                if t["scorer"] in ("code_exec", "debug_exec") and t["a"].get("passed") != t["b"].get("passed"):
                    t["ko"] = True
            wa = sum(t["points_a"] for t in tasks_out)
            wb = sum(t["points_b"] for t in tasks_out)

        points[fighter_a] += wa
        points[fighter_b] += wb
        ko_rounds.extend(t["id"] for t in tasks_out if t["ko"])
        round_out = {
            "id": rnd["id"],
            "name": rnd["name"],
            "points": rnd["points"],
            "tasks": tasks_out,
            "points_a": round(wa, 6),
            "points_b": round(wb, 6),
            "incomplete": not included,
        }
        if not included:
            round_out["name"] = rnd["name"] + " *"
        rounds_out.append(round_out)

    # Sudden death only on a points tie (and only if the SD task was fought).
    sd_winner = None
    sd_id = str(plan["sudden_death_task"]) if plan.get("sudden_death_task") else None
    scored_ids = {t["id"] for rnd in rounds_out for t in rnd["tasks"]}
    if sd_id and sd_id not in scored_ids and (sd_id in answers_a or sd_id in answers_b):
        if round(points[fighter_a], 9) == round(points[fighter_b], 9):
            task = tasks_by_id.get(sd_id)
            if task:
                sa = _score_task_side(task, str(answers_a.get(sd_id, "")), allow_exec)
                sb = _score_task_side(task, str(answers_b.get(sd_id, "")), allow_exec)
                excluded = None
                if sd_id in errors_a or sd_id in errors_b:
                    excluded = "errored"
                elif sa["status"] == "skipped" or sb["status"] == "skipped":
                    excluded = "skipped (code execution off)"
                elif sa["status"] == "errored" or sb["status"] == "errored":
                    excluded = "errored (check spec)"
                fa = sa.get("fraction") if not excluded else None
                fb = sb.get("fraction") if not excluded else None
                sd = {
                    "id": sd_id, "title": task.get("title") or sd_id, "lane": task.get("lane"),
                    "scorer": task.get("scorer") or (task.get("check") or {}).get("type"),
                    "check_type": (task.get("check") or {}).get("type") if isinstance(task.get("check"), dict) else None,
                    "a": sa, "b": sb, "fraction_a": fa, "fraction_b": fb,
                    "points_a": 0.0, "points_b": 0.0, "ko": False, "excluded": excluded,
                    "status_a": sa.get("status"), "status_b": sb.get("status"),
                }
                rounds_out.append({"id": "SD", "name": "Sudden Death", "points": 0, "tasks": [sd],
                                   "points_a": 0.0, "points_b": 0.0, "incomplete": excluded is not None})
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

    # Draw display: an efficiency edge is surfaced but never counts as a win.
    had_errors = bool(errors_a or errors_b or any(t.get("excluded") for r in rounds_out for t in r["tasks"]))
    edge = _efficiency_edge(fighter_a, fighter_b, usage, has_errors=had_errors) if winner == "draw" else None

    # Championship K.O.
    ko = False
    r3 = _r3_round(rounds_out)
    if r3 and winner != "draw":
        included_r3 = [t for t in r3["tasks"] if not t["excluded"]]
        if plan["mode"] == "card":
            def wins_all(side: str) -> bool:
                if not included_r3:
                    return False
                for t in included_r3:
                    fa, fb = t["fraction_a"], t["fraction_b"]
                    if fa is None or fb is None:
                        return False
                    if not (fa > fb if side == "a" else fb > fa):
                        return False
                return True

            def fails_one(side: str) -> bool:
                for t in included_r3:
                    frac = t["fraction_a"] if side == "a" else t["fraction_b"]
                    if frac is not None and frac < 1.0:
                        return True
                return False

            ko_a = wins_all("a") and fails_one("b")
            ko_b = wins_all("b") and fails_one("a")
            if ko_a and not ko_b:
                winner, ko = fighter_a, True
            elif ko_b and not ko_a:
                winner, ko = fighter_b, True
        else:
            def r3_passes(side: str) -> bool:
                vals = [t[side].get("passed") for t in included_r3]
                return bool(vals) and all(vals)
            if r3_passes("a") and not r3_passes("b"):
                winner, ko = fighter_a, True
            elif r3_passes("b") and not r3_passes("a"):
                winner, ko = fighter_b, True
    if ko:
        for t in (r3 or {}).get("tasks", []):
            fa, fb = t["fraction_a"], t["fraction_b"]
            if t["excluded"]:
                continue
            if winner == fighter_a and (fa or 0) > (fb or 0) and (fb or 0) < 1.0:
                t["ko"] = True
            elif winner == fighter_b and (fb or 0) > (fa or 0) and (fa or 0) < 1.0:
                t["ko"] = True
        ko_rounds = sorted({t["id"] for rnd in rounds_out for t in rnd["tasks"] if t["ko"]})

    # Report (delegate to render.py with fallback).
    report_path = None
    if render:
        try:
            report_dir = _data_dir() / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            report_path = report_dir / f"kombat-{stamp}.html"
            report_path.write_text(
                _render_report_delegate(fighter_a, fighter_b, rounds_out, usage, winner, ko, edge=edge),
                encoding="utf-8",
            )
            report_path = str(report_path)
        except Exception as exc:
            report_path = f"report render failed: {exc}"

    # Live state: per-task scores + final result.
    live_card = plan.get("card") or {
        "title": "Model Kombat fixed suite",
        "lanes": [],
        "rounds": [
            {"id": r["id"], "name": r["name"], "points": r["points"], "tasks": [t["id"] for t in r["tasks"]]}
            for r in rounds_out
        ],
        "sudden_death_task": plan.get("sudden_death_task"),
    }
    live_card = dict(live_card)
    # Sudden death appears on the board/report only when a tie actually used it.
    if not any(rnd.get("id") == "SD" for rnd in rounds_out):
        live_card.pop("sudden_death_task", None)
    live_rounds = {t["id"]: r["id"] for r in rounds_out for t in r["tasks"]}
    live = _live_state(
        _data_dir(), fight_id, live_card,
        {"a": fighter_a, "b": fighter_b}, live_rounds,
    )
    for rnd in rounds_out:
        for t in rnd["tasks"]:
            for side, fighter, meta in (("a", fighter_a, meta_a), ("b", fighter_b, meta_b)):
                info = _meta_detail(meta, t["id"])
                if t.get("excluded"):
                    state = "skipped" if str(t["excluded"]).startswith("skipped") else "error"
                    score = None
                else:
                    status = t.get(f"status_{side}")
                    state = "done" if status == "scored" else ("skipped" if status == "skipped" else "error")
                    score = t.get(f"fraction_{side}")
                _live_call(
                    live, "task_update", t["id"], side,
                    state=state,
                    score=score,
                    latency_s=info["latency_s"],
                    cost_usd=info["cost_usd"],
                    detail="; ".join(str(d) for d in t[side].get("details") or [])[:400],
                )
        _live_call(live, "round_state", rnd["id"], "done")

    record = {
        "ts": _now_iso(),
        "fight_id": fight_id,
        "fighter_a": fighter_a,
        "fighter_b": fighter_b,
        "mode": plan["mode"],
        "card": plan.get("card"),
        "lanes": (plan.get("card") or {}).get("lanes"),
        "winner": winner,
        "ko": ko,
        "edge": edge,
        "sudden_death_winner": sd_winner,
        "points": points,
        "usage": usage,
        "report": report_path,
        "incomplete": bool(incomplete_rounds),
        "incomplete_rounds": incomplete_rounds,
        "answers_a": answers_a,
        "answers_b": answers_b,
        "rounds": [
            {
                "id": r["id"], "name": r["name"], "points": r["points"],
                "points_a": r.get("points_a"), "points_b": r.get("points_b"),
                "incomplete": r.get("incomplete", False),
                "tasks": [
                    {
                        "id": t["id"], "title": t.get("title"), "lane": t.get("lane"),
                        "scorer": t.get("scorer"),
                        "a": t["a"]["score"], "a_max": t["a"]["max"],
                        "b": t["b"]["score"], "b_max": t["b"]["max"],
                        "a_fraction": t.get("fraction_a"), "b_fraction": t.get("fraction_b"),
                        "a_status": t.get("status_a"), "b_status": t.get("status_b"),
                        "a_detail": "; ".join(str(d) for d in t["a"].get("details") or []),
                        "b_detail": "; ".join(str(d) for d in t["b"].get("details") or []),
                        "points_a": t["points_a"], "points_b": t["points_b"],
                        "ko": t["ko"], "excluded": t["excluded"],
                    }
                    for t in r["tasks"]
                ],
            }
            for r in rounds_out
        ],
    }
    _append_history(record)
    try:
        stats_updated = _update_task_stats(record)
    except Exception:  # noqa: BLE001 — discrimination memory is additive
        stats_updated = False

    # Ladder: per-lane Elo bouts (lazy import so a missing module never breaks scoring).
    ladder_result = None
    ladder_error = None

    def _ladder_status(t: dict, side: str) -> str:
        if t.get("excluded"):
            return "skipped" if str(t["excluded"]).startswith("skipped") else "errored"
        return t.get(f"status_{side}") or "scored"

    try:
        ladder_record = {
            "fight_id": fight_id,
            "fighter_a": fighter_a,
            "fighter_b": fighter_b,
            "tasks": [
                {
                    "id": t["id"],
                    "lane": t.get("lane"),
                    "a": {
                        "score": t.get("fraction_a"),
                        "status": _ladder_status(t, "a"),
                        "latency_s": _meta_detail(meta_a, t["id"])["latency_s"],
                        "cost_usd": _meta_detail(meta_a, t["id"])["cost_usd"],
                    },
                    "b": {
                        "score": t.get("fraction_b"),
                        "status": _ladder_status(t, "b"),
                        "latency_s": _meta_detail(meta_b, t["id"])["latency_s"],
                        "cost_usd": _meta_detail(meta_b, t["id"])["cost_usd"],
                    },
                }
                for rnd in rounds_out
                for t in rnd["tasks"]
            ],
            "usage": {
                fighter: {
                    "cost_usd": (
                        float(u["cost_usd"])
                        if isinstance(u, dict) and isinstance(u.get("cost_usd"), (int, float))
                        and not isinstance(u.get("cost_usd"), bool)
                        else None
                    ),
                    "tokens": int((u or {}).get("tokens") or 0),
                    "latency_s": float((u or {}).get("latency_s") or 0.0),
                }
                for fighter, u in usage.items()
                if isinstance(u, dict)
            },
        }
        ladder_result = _ladder_module().update_ladder(_data_dir(), ladder_record)
    except Exception as exc:  # noqa: BLE001 — ladder is additive, never fatal
        ladder_error = f"{type(exc).__name__}: {exc}"

    # Live finish + kanban completion (both best-effort).
    _live_call(live, "finish", winner, ko, points, usage, report_path, edge=edge)

    kanban_note = None
    try:
        if last and last.get("kanban") and bool(_cfg("show_on_kanban", True)):
            try:
                from . import kanban as _kb
            except ImportError:  # top-level import outside the plugin package (tests)
                import kanban as _kb  # type: ignore

            kanban_note = _kb.finish_rounds(last["kanban"], rounds_out, fighter_a, fighter_b, winner, report_path)
    except Exception as exc:
        kanban_note = f"kanban update skipped: {exc}"

    return _ok(
        fighters=[fighter_a, fighter_b],
        fight_id=fight_id,
        mode=plan["mode"],
        card=plan.get("card"),
        winner=winner,
        ko=ko,
        edge=edge,
        sudden_death_winner=sd_winner,
        ko_rounds=ko_rounds,
        points=points,
        rounds=rounds_out,
        usage=usage,
        report=report_path,
        incomplete=bool(incomplete_rounds),
        incomplete_rounds=incomplete_rounds,
        flag="*" if incomplete_rounds else "",
        ladder=ladder_result,
        ladder_error=ladder_error,
        task_stats_updated=stats_updated,
        kanban=kanban_note,
    )


# ---------------------------------------------------------------- recommend


def _ladder_json() -> dict:
    data = _read_json_file(_data_dir() / "ladder.json", {"updated": None, "lanes": {}})
    if not isinstance(data, dict) or not isinstance(data.get("lanes"), dict):
        return {"updated": None, "lanes": {}}
    return data


def _short_name(model_id: str) -> str:
    try:
        return _ladder_module().short_name(model_id)
    except Exception:  # noqa: BLE001 — display fallback only
        tail = str(model_id or "").rstrip("/").split("/")[-1]
        return tail[:18] or "model"


def _elo_for(lane: str, model: str):
    lane_data = (_ladder_json().get("lanes") or {}).get(lane) or {}
    entry = (lane_data.get("models") or {}).get(model) or {}
    return entry.get("elo")


def _recommend_lane(lane: str, candidates=None) -> tuple[dict | None, str | None]:
    try:
        return _ladder_module().recommend(_data_dir(), lane, candidates), None
    except Exception as exc:  # noqa: BLE001 — missing ladder module degrades
        return None, f"ladder unavailable: {type(exc).__name__}: {exc}"


def _resolve_scout_card(value):
    """Resolve ``card`` as a stored card id, a card object, or ``{"id": ...}``.

    Returns ``(card_or_None, looks_like_card_id)``. It never classifies text;
    callers fall back to the kanban task-id path only when this returns None.
    """
    if isinstance(value, dict):
        if isinstance(value.get("rounds"), list) or isinstance(value.get("lanes"), list):
            return value, True
        nested = value.get("card")
        if nested is not None and nested is not value:
            return _resolve_scout_card(nested)
        nested_id = value.get("id")
        if isinstance(nested_id, str) and not value.get("lanes"):
            return _resolve_scout_card(nested_id)
        return None, False
    if not isinstance(value, str):
        return None, False
    text = value.strip()
    if not text:
        return None, False
    if text.startswith("{"):
        try:
            return _resolve_scout_card(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            return None, False
    data = _read_json_file(_cards_path(), {})
    card = data.get(text) if isinstance(data, dict) else None
    if isinstance(card, dict) and (card.get("rounds") or card.get("lanes")):
        return card, True
    return None, text.startswith("card-")


def _card_lane_weights(card: dict) -> list[dict]:
    """Normalise a card's lane weights: ``lane_weights`` or plain ``lanes``."""
    weights: list[dict] = []
    raw = card.get("lane_weights")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("lane"):
                try:
                    weight = float(item.get("weight", 1.0))
                except (TypeError, ValueError):
                    weight = 1.0
                if weight > 0:
                    weights.append({"lane": str(item["lane"]), "weight": weight})
    if not weights:
        for lane in card.get("lanes") or []:
            if isinstance(lane, str) and lane:
                weights.append({"lane": lane, "weight": 1.0})
    return weights


def _value_edge_for(pick_metrics: dict, runner_metrics: dict):
    """Build ladder ``value_edge`` from the weighted per-lane efficiency accumulators."""
    def _row(acc: dict) -> dict:
        acc = acc if isinstance(acc, dict) else {}
        lat_w = acc.get("lat_w") or 0.0
        cost_w = acc.get("cost_w") or 0.0
        return {
            "model": acc.get("model"),
            "latency_per_task": (acc.get("latency", 0.0) / lat_w) if lat_w else None,
            "cost_per_point": (acc.get("cost", 0.0) / cost_w) if cost_w else None,
        }

    try:
        fn = getattr(_ladder_module(), "value_edge", None)
        if fn is None:
            return None
        return fn(_row(pick_metrics), _row(runner_metrics))
    except Exception:  # noqa: BLE001 — a display edge must never break a recommendation
        return None


def _recommend_card(card: dict, candidates=None) -> dict:
    """Per-lane picks plus one overall pick for a scout card.

    Overall pick = the model with the highest **weighted average lane Elo**,
    ``sum(lane_weight * elo(model, lane)) / sum(lane_weight)`` over the card's
    lanes, restricted to models that have Elo in every one of those lanes when
    such models exist. Ties on Elo break by lane picks won, then coverage, then
    measured cost/latency — never alphabetically. A margin under 15 Elo is not a
    performance win: the pick is labelled ``verdict: "no clear edge"``, confidence
    is forced low, ``why`` says it is an efficiency (latency/cost) tiebreak, and
    ``value_edge`` carries the measured "equal quality — X is Nx cheaper" call.
    """
    weights = _card_lane_weights(card)
    per_lane: list[dict] = []
    warnings: list[str] = []
    weighted: dict[str, float] = {}
    weight_totals: dict[str, float] = {}
    contributions: dict[str, dict] = {}
    coverage: dict[str, int] = {}
    lane_wins: dict[str, int] = {}
    metrics: dict[str, dict] = {}
    for item in weights:
        lane, weight = item["lane"], item["weight"]
        rec, rec_err = _recommend_lane(lane, candidates)
        if rec_err:
            warnings.append(f"{lane}: {rec_err}")
            rec = None
        rec = rec or {}
        rows = {
            str(row.get("model")): row
            for row in (rec.get("ranking") or [])
            if isinstance(row, dict) and row.get("model") is not None
        }
        pick = rec.get("pick")
        pick_row = rows.get(str(pick)) if pick else None
        pick_elo = None
        if isinstance(pick_row, dict):
            pick_elo = pick_row.get("elo")
        if pick_elo is None:
            pick_elo = rec.get("elo")
        if pick_elo is None and pick:
            lane_elo = _elo_for(lane, str(pick))
            pick_elo = lane_elo
        if pick:
            lane_wins[str(pick)] = lane_wins.get(str(pick), 0) + 1
        for model, row in rows.items():
            elo = row.get("elo")
            if not isinstance(elo, (int, float)) or isinstance(elo, bool):
                continue
            weighted[model] = round(weighted.get(model, 0.0) + weight * float(elo), 6)
            weight_totals[model] = round(weight_totals.get(model, 0.0) + weight, 6)
            contributions.setdefault(model, {})[lane] = round(weight * float(elo), 6)
            coverage[model] = coverage.get(model, 0) + 1
            # Weighted means of the per-lane measured efficiency, for the value edge.
            acc = metrics.setdefault(
                model,
                {"model": model, "latency": 0.0, "lat_w": 0.0, "cost": 0.0, "cost_w": 0.0},
            )
            lat = row.get("latency_per_task")
            if isinstance(lat, (int, float)) and not isinstance(lat, bool):
                acc["latency"] += weight * float(lat)
                acc["lat_w"] += weight
            cost = row.get("cost_per_point")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                acc["cost"] += weight * float(cost)
                acc["cost_w"] += weight
        lane_margin = rec.get("margin")
        lane_verdict = rec.get("verdict")
        if not lane_verdict:
            lane_verdict = (
                "no clear edge"
                if isinstance(lane_margin, (int, float)) and not isinstance(lane_margin, bool) and lane_margin < 15
                else "clear edge"
            )
        lane_short = _short_name(pick) if pick else None
        if lane_short and lane_verdict == "no clear edge":
            lane_short = "≈" + lane_short
        per_lane.append(
            {
                "lane": lane,
                "weight": weight,
                "pick": pick,
                "short": lane_short,
                "elo": pick_elo,
                "margin": lane_margin,
                "verdict": lane_verdict,
                "confidence": rec.get("confidence") or "none",
                "fights": rec.get("fights"),
                "cost_per_point": rec.get("cost_per_point"),
                "cost_per_point_display": rec.get("cost_per_point_display") or "n/a",
                "runner_up": rec.get("runner_up"),
                "why": rec.get("why"),
            }
        )

    averages = {
        model: (total / weight_totals[model])
        for model, total in weighted.items()
        if weight_totals.get(model)
    }
    # The overall pick must be evaluable on the whole card: prefer models with
    # Elo in every one of the card's lanes so a thin-coverage model can't win on
    # a flattering subset. Falls back to whatever coverage exists.
    full_coverage = [m for m in averages if coverage.get(m, 0) >= len(weights)]
    eligible = {m: averages[m] for m in (full_coverage or list(averages))}

    def _overall_key(model: str):
        """Deterministic order: Elo, then lane picks won, then coverage, then value.

        Alphabetical order is deliberately last — a tie on Elo must never be
        settled by a model id, which is how the overall pick used to disagree
        with a unanimous per-lane pick.
        """
        acc = metrics.get(model) or {}
        cpp = (acc["cost"] / acc["cost_w"]) if acc.get("cost_w") else None
        lat = (acc["latency"] / acc["lat_w"]) if acc.get("lat_w") else None
        return (
            -round(eligible[model], 6),
            -lane_wins.get(model, 0),
            -coverage.get(model, 0),
            cpp is None,
            cpp if cpp is not None else 0.0,
            lat is None,
            lat if lat is not None else 0.0,
            model,
        )

    ranked = sorted(eligible.items(), key=lambda kv: _overall_key(kv[0]))
    overall = None
    if ranked:
        pick, weighted_elo = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else None
        margin = round(weighted_elo - ranked[1][1], 2) if len(ranked) > 1 else 0.0
        no_edge = margin < 15
        short = _short_name(pick)
        pick_short = ("≈" + short) if no_edge else short
        runner_short = _short_name(runner_up) if runner_up else None
        value = None
        if no_edge and runner_up:
            value = _value_edge_for(metrics.get(pick) or {}, metrics.get(runner_up) or {})
        if no_edge:
            why = (
                f"{pick_short} and {runner_short} are within {margin:.1f} Elo "
                "— this is an efficiency tiebreak (latency/cost), not a performance win."
                if runner_up else
                f"{pick_short} has no runner-up on this card's lanes — efficiency tiebreak, not a performance win."
            )
            if value:
                why = f"{why} Measured: {value['label']}."
        else:
            why = (
                f"{pick_short} has the highest weighted average lane Elo "
                f"({weighted_elo:.1f}) across this card's lanes."
            )
        overall = {
            "pick": pick,
            "short": pick_short,
            "weighted_elo": round(weighted_elo, 2),
            "margin": margin,
            "verdict": "no clear edge" if no_edge else "clear edge",
            "runner_up": runner_up,
            "per_lane": contributions.get(pick, {}),
            "lane_picks_won": lane_wins.get(pick, 0),
            "lanes_considered": len(weights),
            "value_edge": value,
            "confidence": "low" if (len(ranked) < 2 or no_edge) else "medium",
            "why": why,
        }
        if len(ranked) < 2:
            warnings.append("overall pick has no runner-up (ladder thin on this card's lanes)")
        if full_coverage and len(full_coverage) < len(averages):
            warnings.append(
                "overall pick considered only models with Elo in every card lane "
                f"({len(full_coverage)} of {len(averages)} ranked models)"
            )
    else:
        warnings.append("no ladder data for this card's lanes yet — run a fight first")
    return {
        "per_lane": per_lane,
        "overall": overall,
        "warnings": warnings,
        "method": (
            "overall = argmax_model sum(lane_weight * elo(model, lane)) / sum(lane_weight) "
            "— a weighted average lane Elo across the card's lanes; ties break by lane "
            "picks won, then lane coverage, then measured cost/latency (never by name)"
        ),
    }


@_guard
def kombat_recommend(args: dict, **kwargs) -> str:
    """Ladder-backed knockout pick per lane, per scout card, or per open card (board mode)."""
    if args.get("ladder") or str(args.get("mode") or "").lower() == "ladder":
        return _ok(mode="ladder", ladder=_ladder_json())

    candidates = args.get("candidates")
    if isinstance(candidates, str):
        candidates = [c for c in candidates.split(",") if c.strip()]
    elif not isinstance(candidates, list):
        candidates = None

    want_board = bool(args.get("board"))
    if want_board:
        cards, warn = _scout_kanban_open()
        warnings = [warn] if warn else []
        try:
            ln = _lanes_module()
        except Exception as exc:  # noqa: BLE001
            return _err(f"lane engine unavailable: {exc}")
        recommendations = []
        mapping: dict = {}
        for card in cards:
            cid = card.get("id")
            if not cid:
                continue
            text = _scout_task_text(card)
            classified = ln.classify(text)
            lane = classified[0]["lane"] if classified else "reasoning"
            rec, rec_err = _recommend_lane(lane, candidates)
            if rec_err:
                warnings.append(rec_err)
                continue
            pick = rec.get("pick")
            if not pick:
                warnings.append(f"no pick for card {cid} lane {lane}")
                continue
            margin = rec.get("margin")
            verdict = rec.get("verdict")
            if not verdict:
                verdict = (
                    "no clear edge"
                    if isinstance(margin, (int, float)) and not isinstance(margin, bool) and margin < 15
                    else "clear edge"
                )
            short = _short_name(pick)
            if verdict == "no clear edge":
                short = "≈" + short
            entry = {
                "lane": lane,
                "pick": pick,
                "short": short,
                "verdict": verdict,
                "confidence": "low" if verdict == "no clear edge" else (rec.get("confidence") or "low"),
                "elo": rec.get("elo") if rec.get("elo") is not None else _elo_for(lane, pick),
                "value_edge": (rec or {}).get("value_edge"),
                "updated": _now_iso(),
            }
            mapping[str(cid)] = entry
            recommendations.append(
                {"card": str(cid), "title": card.get("title"), "lanes": classified, **entry}
            )
        written = 0
        if mapping:
            try:
                _ladder_module().write_recommendations(_data_dir(), mapping)
                written = len(mapping)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"could not write recommendations.json: {type(exc).__name__}: {exc}")
        return _ok(mode="board", recommendations=recommendations, mapping=mapping,
                   written=written, warnings=warnings)

    lane = args.get("lane")
    card_arg = args.get("card")

    # Scout-card mode: a stored card id ('card-...') or a scouted card object.
    if not lane and card_arg is not None:
        card, looks_like_card = _resolve_scout_card(card_arg)
        if card is not None:
            result = _recommend_card(card, candidates)
            overall = result.get("overall") or {}
            payload = {
                "mode": "card",
                "card_id": card.get("id"),
                "title": card.get("title"),
                "intensity": card.get("intensity"),
                "lanes": card.get("lanes") or [w["lane"] for w in _card_lane_weights(card)],
                "lane_weights": _card_lane_weights(card),
                "per_lane": result["per_lane"],
                "overall": overall or None,
                "pick": overall.get("pick"),
                "short": overall.get("short"),
                "weighted_elo": overall.get("weighted_elo"),
                "margin": overall.get("margin"),
                "verdict": overall.get("verdict"),
                "confidence": overall.get("confidence") or "none",
                "why": overall.get("why"),
                "lane_picks_won": overall.get("lane_picks_won"),
                "lanes_considered": overall.get("lanes_considered"),
                "value_edge": overall.get("value_edge"),
                "method": result["method"],
                "warnings": result["warnings"],
                "cost_per_point": None,
                "cost_per_point_display": "n/a",
            }
            if overall.get("pick"):
                lane_view = next(
                    (row for row in result["per_lane"] if row.get("pick") == overall["pick"]),
                    None,
                )
                if lane_view:
                    payload["cost_per_point"] = lane_view.get("cost_per_point")
                    payload["cost_per_point_display"] = (
                        lane_view.get("cost_per_point_display") or "n/a"
                    )
            return _ok(**payload)
        if looks_like_card:
            return _err(
                f"fight card {str(card_arg)!r} not found (call kombat_scout to build and store one).",
                mode="card",
            )
        # Not a stored card: fall through to the kanban task-id classification.

    text = args.get("input")
    if isinstance(text, dict):
        text = text.get("input") or text.get("text") or ""
    if not lane and card_arg:
        task, warn = _scout_kanban_task(str(card_arg))
        if task:
            text = _scout_task_text(task) or text
    classified = []
    if not lane:
        try:
            classified = _lanes_module().classify(str(text or ""))
        except Exception:  # noqa: BLE001
            classified = []
        lane = classified[0]["lane"] if classified else "reasoning"
    lane = str(lane)
    if lane not in getattr(_lanes_module(), "LANES", (lane,)):
        return _err(f"unknown lane {lane!r}; expected one of {', '.join(getattr(_lanes_module(), 'LANES', ()))}.")

    rec, rec_err = _recommend_lane(lane, candidates)
    if rec_err:
        return _err(rec_err, lane=lane)
    payload = dict(rec or {})
    payload.update({"mode": "lane", "lane": lane, "classified": classified, "recommendation": rec})
    return _ok(**payload)


# ---------------------------------------------------------------- history/export

@_guard
def kombat_history(args: dict, **kwargs) -> str:
    if args.get("running"):
        return _ok(**job_snapshot())
    if args.get("trends"):
        trends = _ladder_module().ladder_trends(
            _data_dir(),
            lane=args.get("lane"),
            model=args.get("model"),
            series_limit=args.get("series_limit", 50),
        )
        return _ok(**{k: v for k, v in trends.items() if k != "ok"})
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
                "fight_id": f.get("fight_id"),
                "fighter_a": f.get("fighter_a"),
                "fighter_b": f.get("fighter_b"),
                "winner": f.get("winner"),
                "ko": f.get("ko"),
                "points": f.get("points"),
                "incomplete": f.get("incomplete", False),
                "lanes": f.get("lanes") or (f.get("card") or {}).get("lanes"),
                "card": (f.get("card") or {}).get("id"),
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
                        f"- {t['id']} [{t.get('lane') or '—'}] {t.get('title') or ''}: "
                        f"A {t['a']}/{t['a_max']} ({t['points_a']} pts) vs "
                        f"B {t['b']}/{t['b_max']} ({t['points_b']} pts)"
                        + (f" — fraction A {t['a_fraction']} / B {t['b_fraction']}"
                           if t.get("a_fraction") is not None or t.get("b_fraction") is not None else "")
                        + (f" — A {t['a_status']}" if t.get("a_status") and t["a_status"] != "scored" else "")
                        + (f" — B {t['b_status']}" if t.get("b_status") and t["b_status"] != "scored" else "")
                        + (" — K.O." if t.get("ko") else "")
                        + (f" — excluded: {t['excluded']}" if t.get("excluded") else "")
                    )
                if rnd.get("incomplete"):
                    lines.append(f"_Round incomplete — excluded from points ({rnd['name']})._")
                lines.append("")
            out_path.write_text("\n".join(lines), encoding="utf-8")
        else:  # csv
            rows = ["round,round_points,round_points_a,round_points_b,incomplete,task,lane,title,"
                    "fighter_a,score_a,fraction_a,status_a,points_a,fighter_b,score_b,fraction_b,"
                    "status_b,points_b,ko,excluded"]
            for rnd in fight.get("rounds", []):
                for t in rnd["tasks"]:
                    rows.append(
                        ",".join(
                            str(x).replace(",", ";")
                            for x in (
                                rnd["id"], rnd.get("points", ""), rnd.get("points_a", ""),
                                rnd.get("points_b", ""), rnd.get("incomplete", False),
                                t["id"], t.get("lane") or "", t.get("title") or "",
                                fight.get("fighter_a"), t["a"], t.get("a_fraction", ""),
                                t.get("a_status", ""), t["points_a"],
                                fight.get("fighter_b"), t["b"], t.get("b_fraction", ""),
                                t.get("b_status", ""), t["points_b"],
                                t.get("ko", False), t.get("excluded") or "",
                            )
                        )
                    )
            out_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    except Exception as exc:
        return _err(f"Export failed: {exc}")
    return _ok(format=fmt, path=str(out_path))
