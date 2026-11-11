"""Kanban scoreboard + agentic fight mode for Model Kombat.

DISPLAY ONLY by default: scoreboard cards are unassigned, so the kanban
dispatcher never claims them. Every board call is best-effort — a refusal
(approval gate, missing CLI, gateway unattended run) degrades to
"scoreboard skipped" and never fails the fight.

Agentic mode is different by design: round tasks are dispatched to real
workers (assignee = active profile) with per-task model overrides, so agent
workers solve them with tools. Explicit opt-in only; cost is billed per worker
session and is NOT covered by the pure fight's budget cap (disclosed).

Lifecycle rules (v0.6):
* ``Scoreboard.start`` sweeps stale cards (open FIGHT/REMATCH parents and R1–R3
  round cards older than ``stale_card_minutes``) and reuses an open bout's card
  ids for a retry of the same fight (``bouts.json``, 60 min window).
* ``Scoreboard.interrupt`` closes every card of a bout that never reached
  scoring — never raises, tolerates already-done cards, idempotent.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
from datetime import datetime
from pathlib import Path

BOARD = "model-kombat"

CLOSED_STATUSES = {"done", "archived", "cancelled", "canceled", "failed", "removed"}
BOUT_REUSE_MINUTES = 60
BOUT_SWEEP_LIMIT = 100


def _ctx():
    try:
        from . import tools
    except ImportError:  # top-level import outside the plugin package (tests)
        import tools  # type: ignore

    return tools._CTX


def _tools():
    try:
        from . import tools as _t
    except ImportError:  # top-level import outside the plugin package (tests)
        import tools as _t  # type: ignore
    return _t


def _dispatch(command: str) -> tuple[bool, str]:
    """Run a hermes CLI command via ctx.dispatch_tool('terminal')."""
    ctx = _ctx()
    if ctx is None or not hasattr(ctx, "dispatch_tool"):
        return False, "no host ctx (dispatch_tool unavailable)"
    try:
        raw = ctx.dispatch_tool("terminal", {"command": command})
    except Exception as exc:
        return False, f"dispatch failed: {exc}"
    text = raw if isinstance(raw, str) else json.dumps(raw)
    # Terminal tool returns JSON like {"output": "...", "exit_code": 0, ...}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            out = str(parsed.get("output") or parsed.get("result") or "")
            code = parsed.get("exit_code", 0)
            return code == 0, out or text
    except (json.JSONDecodeError, TypeError):
        pass
    return True, text


def _cli(*args: str) -> str:
    return "hermes kanban " + shlex.join(list(args))


def _cli_path(path) -> str:
    """A filesystem path as a forward-slash string for kanban argv.

    Every board call leaves this module as ONE shell string handed to
    ``ctx.dispatch_tool("terminal")``, so a path carrying Windows separators is
    at the mercy of whichever shell layer the host uses to run it: that layer
    collapses doubled separators to single ones before the CLI sees them, so a
    ``json.dumps`` payload arrives with an unescaped separator and the
    ``--metadata`` parser rejects it ("Invalid escape"). The parent FIGHT card
    then never completes and the bout records an error on its last write. An
    ``--attach`` path is exposed to the same collapsing, just less visibly.

    POSIX separators carry no escaping hazard and are accepted by every
    consumer (argparse, the kanban CLI, Windows file APIs), so paths are
    normalised here rather than shell-quoted harder.
    """
    return Path(str(path)).as_posix()


def _extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _extract_json_any(text: str):
    """Parse a JSON object *or* array out of CLI output (``list --json``)."""
    stripped = (text or "").strip()
    for candidate in (stripped, *re.findall(r"[\[{].*[\]}]", stripped, re.S)):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


# ------------------------------------------------------------- bout registry


def _bouts_path():
    return _tools()._data_dir() / "bouts.json"


def _read_json_file(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return default


def _write_json_atomic(path, payload) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — best-effort persistence
        pass


def _load_bouts() -> dict:
    data = _read_json_file(_bouts_path(), {})
    return data if isinstance(data, dict) else {}


def _save_bouts(data: dict) -> None:
    # Keep the registry bounded: drop closed entries and the oldest open ones.
    open_items = [(k, v) for k, v in data.items() if isinstance(v, dict) and v.get("open")]
    if len(open_items) > 40:
        open_items.sort(key=lambda kv: float(kv[1].get("started_at") or 0))
        for key, _ in open_items[:-40]:
            data.pop(key, None)
    _write_json_atomic(_bouts_path(), data)


def bout_key(fighter_a: str, fighter_b: str, card=None, intensity: str | None = None) -> str:
    """Stable identity for a bout: fighters + card identity + intensity."""
    if isinstance(card, dict):
        ident = str(card.get("id") or "")
        if not ident:
            rounds = card.get("rounds")
            ident = json.dumps(rounds, sort_keys=True) if rounds else "suite"
    else:
        ident = "suite"
    mixing = "|".join(
        [
            str(fighter_a or "").strip(),
            str(fighter_b or "").strip(),
            ident,
            str(intensity or (card or {}).get("intensity") or "standard"),
        ]
    )
    return hashlib.sha256(mixing.encode("utf-8")).hexdigest()[:16]


def find_open_bout(key: str, max_age_minutes: float = BOUT_REUSE_MINUTES) -> dict | None:
    """Return the stored open bout for ``key`` if it is younger than the window."""
    if not key:
        return None
    entry = _load_bouts().get(key)
    if not isinstance(entry, dict) or not entry.get("open"):
        return None
    try:
        age = time.time() - float(entry.get("started_at") or 0)
    except (TypeError, ValueError):
        return None
    if age < 0 or age > float(max_age_minutes) * 60:
        return None
    return entry


def save_bout(key: str, ids: dict, fighters=None, card_id=None, mode: str = "pure") -> None:
    if not key or not isinstance(ids, dict):
        return
    data = _load_bouts()
    data[key] = {
        "key": key,
        "open": True,
        "started_at": time.time(),
        "fighters": list(fighters or []),
        "ids": {"parent": ids.get("parent"), "rounds": dict(ids.get("rounds") or {})},
        "card": card_id,
        "mode": mode,
    }
    _save_bouts(data)


def clear_bout(key: str | None = None, ids: dict | None = None) -> None:
    """Close (remove) the bout entry — by key, or by matching stored card ids."""
    data = _load_bouts()
    changed = False
    if key and key in data:
        data.pop(key, None)
        changed = True
    if isinstance(ids, dict):
        parent = ids.get("parent")
        round_cards = {v for v in (ids.get("rounds") or {}).values() if v}
        for other_key, entry in list(data.items()):
            if not isinstance(entry, dict):
                continue
            entry_ids = entry.get("ids") or {}
            if parent and entry_ids.get("parent") == parent:
                data.pop(other_key, None)
                changed = True
                continue
            stored = {v for v in (entry_ids.get("rounds") or {}).values() if v}
            if round_cards and stored & round_cards:
                data.pop(other_key, None)
                changed = True
    if changed:
        _save_bouts(data)


# --------------------------------------------------------------- stale sweep


def _created_epoch(item: dict) -> float | None:
    raw = item.get("created_at")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        return value / 1000.0 if value > 1e12 else value
    if isinstance(raw, str) and raw.strip():
        text = raw.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.timestamp()
    return None


def _is_fight_card_title(title: str) -> bool:
    title = str(title or "").strip()
    return bool(re.match(r"^(FIGHT|REMATCH)\s*:", title, re.I) or re.match(r"^R[1-3]\b", title))


class Scoreboard:
    """Live fight scoreboard on the model-kombat board. Best-effort only."""

    def __init__(
        self,
        fighter_a: str,
        fighter_b: str,
        suite: dict | None = None,
        card: dict | None = None,
        fight_key: str | None = None,
    ):
        self.fighter_a = fighter_a
        self.fighter_b = fighter_b
        self.suite = suite or {}
        self.fight_key = fight_key
        # Rounds come from the fight card when one is given; the legacy suite is
        # just the fallback shape (both expose {"rounds": [{id,name,points,tasks}]}).
        if isinstance(card, dict) and isinstance(card.get("rounds"), list) and card["rounds"]:
            self.rounds = card["rounds"]
            self.title = card.get("title")
        else:
            self.rounds = self.suite.get("rounds") or []
            self.title = self.suite.get("title")
        self.ids: dict = {"parent": None, "rounds": {}}
        self.card_id = (card or {}).get("id") if isinstance(card, dict) else None
        self.errors: list[str] = []
        self.started_epoch = time.time()
        self.stale_closed = 0
        self.bout_reused = False
        self.finished = False
        self.interrupted: str | None = None

    def _run(self, command: str) -> tuple[bool, str]:
        ok, out = _dispatch(command)
        if not ok:
            self.errors.append(str(out)[:300])
        return ok, out

    # ---------------------------------------------------------------- start

    def _stale_minutes(self) -> float:
        try:
            return max(1.0, float(_tools()._cfg("stale_card_minutes", 30) or 30))
        except (TypeError, ValueError):
            return 30.0

    def _protected_ids(self) -> set[str]:
        """Card ids that belong to a fight we must not sweep (live/current)."""
        protect = {cid for cid in (self.ids.get("rounds") or {}).values() if cid}
        if self.ids.get("parent"):
            protect.add(self.ids["parent"])
        window = max(self._stale_minutes(), BOUT_REUSE_MINUTES)
        try:
            for entry in _load_bouts().values():
                if not isinstance(entry, dict) or not entry.get("open"):
                    continue
                try:
                    age_min = (time.time() - float(entry.get("started_at") or 0)) / 60.0
                except (TypeError, ValueError):
                    continue
                if 0 <= age_min <= window:
                    ids = entry.get("ids") or {}
                    if ids.get("parent"):
                        protect.add(ids["parent"])
                    protect.update(v for v in (ids.get("rounds") or {}).values() if v)
        except Exception:  # noqa: BLE001 — protection is best-effort
            pass
        return protect

    def sweep_stale(self) -> int:
        """Close open fight cards older than ``stale_card_minutes``. Never fails."""
        try:
            ok, out = _dispatch(_cli("--board", BOARD, "list", "--json"))
            if not ok:
                return 0
            data = _extract_json_any(out)
            items = data.get("tasks") if isinstance(data, dict) else data
            if not isinstance(items, list):
                return 0
            protect = self._protected_ids()
            cutoff = time.time() - self._stale_minutes() * 60
            closed = 0
            for item in items[:BOUT_SWEEP_LIMIT]:
                if not isinstance(item, dict):
                    continue
                tid = item.get("id")
                if not isinstance(tid, str) or not tid or tid in protect:
                    continue
                if str(item.get("status") or "").lower() in CLOSED_STATUSES:
                    continue
                if not _is_fight_card_title(item.get("title") or ""):
                    continue
                created = _created_epoch(item)
                if created is None or created > cutoff:
                    continue
                note = "Stale — closed by Model Kombat"
                self._run(_cli("--board", BOARD, "comment", tid, note))
                ok_close, _ = self._run(
                    _cli("--board", BOARD, "complete", tid, "--result", note)
                )
                if not ok_close:
                    self._run(_cli("--board", BOARD, "archive", tid))
                closed += 1
            return closed
        except Exception:  # noqa: BLE001 — sweep must never fail the fight
            return 0

    def start(self, round_ids: list | None = None, title_prefix: str = "FIGHT") -> None:
        self.started_epoch = time.time()
        self.stale_closed = self.sweep_stale()

        if self.fight_key:
            try:
                entry = find_open_bout(self.fight_key)
            except Exception:  # noqa: BLE001 — registry is best-effort
                entry = None
            stored = (entry or {}).get("ids") or {}
            if stored.get("parent") or stored.get("rounds"):
                self.ids = {
                    "parent": stored.get("parent"),
                    "rounds": dict(stored.get("rounds") or {}),
                }
                self.bout_reused = True
                return

        self._run(_cli("boards", "create", BOARD))  # tolerate "exists"
        # No parent/child links: kanban gates children on parents, which would
        # deadlock round completion (rounds must finish before the fight card).
        # Cards group visually by title + creation time instead.
        for rnd in self.rounds:
            if round_ids is not None and rnd["id"] not in round_ids:
                continue
            title = f"{rnd['id']} — {rnd['name']} ({rnd['points']} pt/task)"
            body = "Tasks: " + ", ".join(
                str(t.get("id")) if isinstance(t, dict) else str(t) for t in rnd["tasks"]
            ) + f"\nFighters: {self.fighter_a} vs {self.fighter_b}"
            ok, out = self._run(
                _cli("--board", BOARD, "create", title, "--body", body, "--json")
            )
            data = _extract_json(out) if ok else None
            self.ids["rounds"][rnd["id"]] = (data or {}).get("id")
        parent_title = f"{title_prefix}: {self.fighter_a} vs {self.fighter_b}"
        if self.title:
            parent_title += f" — {self.title}"
        ok, out = self._run(
            _cli("--board", BOARD, "create", parent_title,
                 "--body", "Model Kombat live scoreboard. Display only — cards are unassigned and never dispatched.",
                 "--json")
        )
        data = _extract_json(out) if ok else None
        self.ids["parent"] = (data or {}).get("id")
        if self.fight_key:
            try:
                save_bout(
                    self.fight_key,
                    self.ids,
                    fighters=[self.fighter_a, self.fighter_b],
                    card_id=self.card_id,
                )
            except Exception:  # noqa: BLE001
                pass

    def round_fighting(self, round_id: str | None) -> None:
        card = self.ids["rounds"].get(round_id or "")
        if card:
            self._run(_cli("--board", BOARD, "comment", card, "Fighters answering…"))

    def abort(self) -> None:
        if self.ids["parent"]:
            self._run(_cli("--board", BOARD, "comment", self.ids["parent"],
                           "Fight aborted: host refused the model override."))

    def _card_status(self, card: str) -> str:
        try:
            ok, out = _dispatch(_cli("--board", BOARD, "show", card, "--json"))
            data = _extract_json(out) if ok else None
            return str(((data or {}).get("task") or {}).get("status") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _card_done(self, card: str) -> bool:
        return self._card_status(card) == "done"

    # ------------------------------------------------------------ interrupt

    def interrupt(self, reason: str, extra_ids: list | None = None) -> dict:
        """Close every card of a bout that never reached scoring.

        Idempotent, never raises, tolerates already-completed cards. Returns a
        small summary ({interrupted, cards}).
        """
        if self.finished or self.interrupted:
            return {"interrupted": self.interrupted, "cards": 0}
        self.interrupted = str(reason or "interrupted").strip() or "interrupted"
        note = f"Fight interrupted: {self.interrupted}"
        result = "Interrupted — not scored"
        cards = [self.ids.get("parent")] + list((self.ids.get("rounds") or {}).values())
        for cid in extra_ids or []:
            cards.append(cid)
        closed = 0
        for card in cards:
            if not card:
                continue
            try:
                self._run(_cli("--board", BOARD, "comment", card, note))
                if self._card_done(card):
                    continue
                ok, _ = self._run(
                    _cli("--board", BOARD, "complete", card, "--result", result)
                )
                if not ok:
                    self._run(_cli("--board", BOARD, "archive", card))
                closed += 1
            except Exception:  # noqa: BLE001 — interrupt must never raise
                continue
        try:
            clear_bout(self.fight_key, self.ids)
        except Exception:  # noqa: BLE001
            pass
        return {"interrupted": self.interrupted, "cards": closed}

    # --------------------------------------------------------------- finish

    def finish(self, rounds_out, winner: str, report_path) -> None:
        self.finished = True
        scored_ids = {r["id"] for r in rounds_out}
        for rnd in rounds_out:
            card = self.ids["rounds"].get(rnd["id"])
            if not card:
                continue
            lines = []
            for t in rnd["tasks"]:
                lines.append(
                    f"{t['id']}: A {t['a']['score']}/{t['a']['max']} vs B {t['b']['score']}/{t['b']['max']}"
                    f" (pts {t['points_a']}–{t['points_b']})"
                    + (" K.O." if t.get("ko") else "")
                    + (f" excluded: {t['excluded']}" if t.get("excluded") else "")
                )
            text = "Round scored:\n" + "\n".join(lines)
            if rnd.get("points_a") is not None or rnd.get("points_b") is not None:
                text += f"\nRound stake ({rnd.get('points')} pt): {rnd.get('points_a', 0)}–{rnd.get('points_b', 0)}"
            self._run(_cli("--board", BOARD, "comment", card, text))
            if self._card_done(card):
                continue  # re-score of a finished bout: comment only, no state change
            self._run(_cli("--board", BOARD, "request-review", card))
            self._run(_cli("--board", BOARD, "complete", card, "--result", text[:500]))
        # Close any round card this bout didn't fight (subset fights).
        for round_id, card in self.ids["rounds"].items():
            if card and round_id not in scored_ids and not self._card_done(card):
                self._run(_cli("--board", BOARD, "comment", card, "Not fought in this bout."))
                self._run(_cli("--board", BOARD, "complete", card, "--result", "Not fought in this bout."))
        if self.ids["parent"]:
            # Attach works even on done cards; complete only if not already done.
            if report_path and not str(report_path).startswith("report render failed"):
                self._run(_cli("--board", BOARD, "attach", self.ids["parent"], _cli_path(report_path)))
            if not self._card_done(self.ids["parent"]):
                self._run(
                    _cli("--board", BOARD, "complete", self.ids["parent"],
                         "--result", f"Winner: {winner}", "--metadata",
                         json.dumps({"winner": winner, "report": _cli_path(report_path)}))
                )
            else:
                self._run(_cli("--board", BOARD, "comment", self.ids["parent"],
                               f"Re-scored. Winner: {winner}. Report: {_cli_path(report_path)}"))
        try:
            clear_bout(self.fight_key, self.ids)
        except Exception:  # noqa: BLE001
            pass


def finish_rounds(ids: dict, rounds_out, fighter_a: str, fighter_b: str, winner: str, report_path) -> str:
    """Called from tools.kombat_score_answers after scoring."""
    try:
        board = Scoreboard(fighter_a, fighter_b, {"rounds": []})
        board.ids = ids
        board.finish(rounds_out, winner, report_path)
        return f"kanban board '{BOARD}': parent={ids.get('parent')}, errors={board.errors or 'none'}"
    except Exception as exc:
        return f"kanban update skipped: {exc}"


# ---------------------------------------------------------- agentic mode

def _show_task(task_id: str) -> dict | None:
    ok, out = _dispatch(_cli("--board", BOARD, "show", task_id, "--json"))
    if not ok:
        return None
    data = _extract_json(out)
    return (data or {}).get("task") if data else None


def run_agentic_fight(args: dict) -> dict:
    """Dispatch each task to a real worker (per-task model override) and harvest.

    Returns the same shape as kombat_run_fight (answers/meta/errors) so the
    normal scoring flow applies unchanged. Worker billing is separate from
    ctx.llm spend and is NOT budget-capped here — per-task max-runtime applies.

    Any exit that does not harvest the full set (exception, KeyboardInterrupt,
    SystemExit) closes every card via ``Scoreboard.interrupt``.
    """
    try:
        from . import tools
    except ImportError:  # top-level import outside the plugin package (tests)
        import tools  # type: ignore

    fighter_a = str(args.get("fighter_a") or tools._cfg("fighter_a", "") or "").strip()
    fighter_b = str(args.get("fighter_b") or tools._cfg("fighter_b", "") or "").strip()
    if not fighter_a or not fighter_b:
        return {"ok": False, "error": "Both fighter_a and fighter_b model IDs are required."}
    ctx = _ctx()
    if ctx is None or not hasattr(ctx, "dispatch_tool"):
        return {"ok": False, "error": "No live Hermes host context (dispatch_tool unavailable)."}

    suite = tools._load_suite()
    wanted = args.get("tasks")
    if wanted:
        wanted = {str(w).upper() for w in wanted}
        order = [t for t in suite["order"] if t in wanted]
    else:
        order = list(suite["order"])
    if not order:
        return {"ok": False, "error": "No matching tasks. Call kombat_list_tasks for valid IDs."}

    try:
        per_task_timeout = int(args.get("timeout_s", tools._cfg("agentic_timeout_s", 600)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "timeout_s must be an integer."}
    assignee = getattr(ctx, "profile_name", None) or "default"
    poll_s = float(args.get("poll_s", 5) or 5)

    answers: dict[str, dict[str, str]] = {fighter_a: {}, fighter_b: {}}
    meta: dict[str, dict[str, dict]] = {fighter_a: {}, fighter_b: {}}
    errors: list[dict] = []
    task_cards: dict[str, dict[str, str]] = {}
    board = Scoreboard(fighter_a, fighter_b, suite)
    started = time.time()
    completed = False
    reason: str | None = None

    try:
        board.start()

        for tid in order:
            task = suite["tasks"][tid]
            round_id = next((r["id"] for r in suite["rounds"] if tid in r["tasks"]), None)
            board.round_fighting(round_id)
            card_ids: dict[str, str] = {}
            for fighter in (fighter_a, fighter_b):
                title = f"{tid} — {fighter}"
                body = (
                    task["prompt"]
                    + "\n\nSolve this task. When done, complete the task with result exactly "
                    "equal to your final answer text and nothing else."
                )
                ok, out = board._run(
                    _cli("--board", BOARD, "create", title, "--body", body,
                         "--assignee", assignee, "--model", fighter,
                         "--max-runtime", str(per_task_timeout), "--json")
                )
                data = _extract_json(out) if ok else None
                card_ids[fighter] = (data or {}).get("id") or ""
                if not card_ids[fighter]:
                    errors.append({"task": tid, "fighter": fighter, "error": "kanban create failed"})
                    answers[fighter][tid] = ""
                    meta[fighter][tid] = {"error": "kanban create failed", "attempts": 0, "latency_s": 0, "usage": {}}
            task_cards[tid] = card_ids

            # Poll both fighters' workers until done / blocked / timeout.
            pending = {f: c for f, c in card_ids.items() if c}
            deadline = time.time() + per_task_timeout + 30
            while pending and time.time() < deadline:
                for fighter in list(pending):
                    task_state = _show_task(pending[fighter])
                    if not task_state:
                        continue
                    status = task_state.get("status")
                    if status == "done":
                        answers[fighter][tid] = str(task_state.get("result") or "")
                        meta[fighter][tid] = {"error": None, "attempts": 1, "latency_s": 0, "usage": {}, "agentic_task": pending[fighter]}
                        del pending[fighter]
                    elif status in ("blocked", "archived"):
                        answers[fighter][tid] = ""
                        meta[fighter][tid] = {"error": f"worker ended status={status}", "attempts": 1, "latency_s": 0, "usage": {}}
                        errors.append({"task": tid, "fighter": fighter, "error": f"worker status {status}"})
                        del pending[fighter]
                if pending:
                    time.sleep(poll_s)
            for fighter in list(pending):
                answers[fighter][tid] = ""
                meta[fighter][tid] = {"error": "agentic timeout (worker may still complete later)", "attempts": 1, "latency_s": per_task_timeout, "usage": {}}
                errors.append({"task": tid, "fighter": fighter, "error": "agentic timeout"})
        completed = True
    except BaseException as exc:  # includes KeyboardInterrupt / SystemExit
        reason = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if not completed:
            try:
                extra = [cid for ids in task_cards.values() for cid in ids.values() if cid]
                board.interrupt(reason or "agentic fight did not finish", extra_ids=extra)
            except Exception:  # noqa: BLE001 — never mask the original exception
                pass

    fight = {
        "fighter_a": fighter_a,
        "fighter_b": fighter_b,
        "mode": "agentic",
        "answers_a": answers[fighter_a],
        "answers_b": answers[fighter_b],
        "meta_a": meta[fighter_a],
        "meta_b": meta[fighter_b],
        "errors": errors,
        "usage": {},
        "budget_exceeded": False,
        "kanban": board.ids,
        "agentic_task_cards": task_cards,
        "elapsed_s": round(time.time() - started, 3),
    }
    try:
        if hasattr(ctx, "state"):
            ctx.state.set("last_fight", fight)
    except Exception:
        pass
    return {
        "ok": True,
        "fighters": [fighter_a, fighter_b],
        "mode": "agentic",
        "rounds": order,
        "answers_a": answers[fighter_a],
        "answers_b": answers[fighter_b],
        "meta_a": meta[fighter_a],
        "meta_b": meta[fighter_b],
        "errors": errors,
        "usage": {},
        "kanban": board.ids,
        "kanban_errors": board.errors,
        "stale_closed": board.stale_closed,
        "bout_reused": board.bout_reused,
        "interrupted": board.interrupted,
        "note": "Agentic mode: workers billed per session (not budget-capped); timed-out workers may still complete later.",
    }
