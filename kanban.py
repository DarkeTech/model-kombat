"""Kanban scoreboard + agentic fight mode for Model Kombat.

DISPLAY ONLY by default: scoreboard cards are unassigned, so the kanban
dispatcher never claims them. Every board call is best-effort — a refusal
(approval gate, missing CLI, gateway unattended run) degrades to
"scoreboard skipped" and never fails the fight.

Agentic mode is different by design: round tasks are dispatched to real
workers (assignee = active profile) with per-task model overrides, so agent
workers solve them with tools. Explicit opt-in only; cost is billed per worker
session and is NOT covered by the pure fight's budget cap (disclosed).
"""

from __future__ import annotations

import json
import re
import shlex
import time

BOARD = "model-kombat"


def _ctx():
    from . import tools

    return tools._CTX


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


def _extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


class Scoreboard:
    """Live fight scoreboard on the model-kombat board. Best-effort only."""

    def __init__(self, fighter_a: str, fighter_b: str, suite: dict):
        self.fighter_a = fighter_a
        self.fighter_b = fighter_b
        self.suite = suite
        self.ids: dict = {"parent": None, "rounds": {}}
        self.errors: list[str] = []

    def _run(self, command: str) -> tuple[bool, str]:
        ok, out = _dispatch(command)
        if not ok:
            self.errors.append(out[:300])
        return ok, out

    def start(self) -> None:
        self._run(_cli("boards", "create", BOARD))  # tolerate "exists"
        parent_title = f"FIGHT: {self.fighter_a} vs {self.fighter_b}"
        ok, out = self._run(
            _cli("--board", BOARD, "create", parent_title,
                 "--body", "Model Kombat live scoreboard. Display only — cards are unassigned and never dispatched.",
                 "--json")
        )
        data = _extract_json(out) if ok else None
        self.ids["parent"] = (data or {}).get("id")
        for rnd in self.suite["rounds"]:
            title = f"{rnd['id']} — {rnd['name']} ({rnd['points']} pt/task)"
            body = "Tasks: " + ", ".join(rnd["tasks"]) + f"\nFighters: {self.fighter_a} vs {self.fighter_b}"
            cmd = _cli("--board", BOARD, "create", title, "--body", body, "--json")
            if self.ids["parent"]:
                cmd = _cli("--board", BOARD, "create", title, "--body", body,
                           "--parent", self.ids["parent"], "--json")
            ok, out = self._run(cmd)
            data = _extract_json(out) if ok else None
            self.ids["rounds"][rnd["id"]] = (data or {}).get("id")

    def round_fighting(self, round_id: str | None) -> None:
        card = self.ids["rounds"].get(round_id or "")
        if card:
            self._run(_cli("--board", BOARD, "comment", card, "Fighters answering…"))

    def abort(self) -> None:
        if self.ids["parent"]:
            self._run(_cli("--board", BOARD, "comment", self.ids["parent"],
                           "Fight aborted: host refused the model override."))

    def finish(self, rounds_out, winner: str, report_path) -> None:
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
            self._run(_cli("--board", BOARD, "comment", card, text))
            self._run(_cli("--board", BOARD, "request-review", card))
            self._run(_cli("--board", BOARD, "complete", card, "--result", text[:500]))
        if self.ids["parent"]:
            if report_path and not str(report_path).startswith("report render failed"):
                self._run(_cli("--board", BOARD, "attach", self.ids["parent"], str(report_path)))
            self._run(
                _cli("--board", BOARD, "complete", self.ids["parent"],
                     "--result", f"Winner: {winner}", "--metadata",
                     json.dumps({"winner": winner, "report": str(report_path)}))
            )


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
    """
    from . import tools

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
    poll_s = 5

    board = Scoreboard(fighter_a, fighter_b, suite)
    board.start()

    answers: dict[str, dict[str, str]] = {fighter_a: {}, fighter_b: {}}
    meta: dict[str, dict[str, dict]] = {fighter_a: {}, fighter_b: {}}
    errors: list[dict] = []
    task_cards: dict[str, dict[str, str]] = {}

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
        "note": "Agentic mode: workers billed per session (not budget-capped); timed-out workers may still complete later.",
    }
