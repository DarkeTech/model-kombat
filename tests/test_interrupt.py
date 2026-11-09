"""Lifecycle regression tests: interrupt, stale sweep, retry reuse, deadlines.

Run: python3 tests/test_interrupt.py

Fake kanban dispatcher only — these tests never touch the real model-kombat
board (a temp HERMES_HOME + data-dir override keeps every write local).
"""

import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TEST_HOME = tempfile.mkdtemp(prefix="kombat-test-home-")
os.environ["HERMES_HOME"] = _TEST_HOME
import atexit  # noqa: E402

atexit.register(shutil.rmtree, _TEST_HOME, True)

import tools  # noqa: E402
import kanban  # noqa: E402


# --------------------------------------------------------------- fake board

class FakeBoard:
    """In-memory model-kombat board + command dispatcher (never hits the CLI)."""

    def __init__(self):
        self.tasks = {}          # id -> {title,status,comments,result,created_at}
        self.seq = 0
        self.created = []        # ids of cards created via `create`
        self.refuse_complete = set()
        self.commands = []
        self.now = time.time()

    def add_card(self, title, status="ready", age_min=0.0):
        self.seq += 1
        tid = f"t{self.seq:04d}"
        self.tasks[tid] = {
            "id": tid,
            "title": title,
            "status": status,
            "comments": [],
            "result": None,
            "created_at": int(self.now - age_min * 60),
        }
        return tid

    def dispatch_tool(self, name, payload):
        assert name == "terminal", name
        command = str(payload.get("command") or "")
        self.commands.append(command)
        tokens = shlex.split(command)
        assert tokens[:2] == ["hermes", "kanban"], command
        tokens = tokens[2:]
        board = None
        if tokens[:1] == ["--board"]:
            board = tokens[1]
            tokens = tokens[2:]
        if not tokens:
            return self._out({}, 2)
        verb = tokens[0]
        args = tokens[1:]

        if verb == "boards":
            return self._out({"boards": [{"slug": board or "model-kombat"}]})

        if verb == "list":
            return self._out(list(self.tasks.values()))

        if verb == "show":
            task = self.tasks.get(args[0] if args else "")
            if task is None:
                return self._out({"error": "no such task"}, 1)
            return self._out({"task": task})

        if verb == "create":
            title = args[0] if args else ""
            status = "ready"
            tid = self.add_card(title, status=status)
            self.created.append(tid)
            return self._out({"id": tid, "title": title})

        if verb == "comment":
            task = self.tasks.get(args[0] if args else "")
            if task is None:
                return self._out({"error": "no such task"}, 1)
            task["comments"].append(" ".join(args[1:]))
            return self._out({"ok": True})

        if verb == "complete":
            task = self.tasks.get(args[0] if args else "")
            if task is None:
                return self._out({"error": "no such task"}, 1)
            if task["status"] == "done":
                return self._out({"error": "already done"}, 1)
            if task["id"] in self.refuse_complete:
                return self._out({"error": "completion refused (goal gate)"}, 1)
            task["status"] = "done"
            if "--result" in args:
                task["result"] = args[args.index("--result") + 1]
            return self._out({"ok": True})

        if verb in ("archive", "request-review", "attach"):
            task = self.tasks.get(args[0] if args else "")
            if verb == "archive" and task is not None:
                task["status"] = "archived"
            return self._out({"ok": True})

        return self._out({"ok": True})

    @staticmethod
    def _out(payload, code=0):
        return json.dumps({"output": json.dumps(payload), "exit_code": code})

    def comments_of(self, tid):
        return self.tasks[tid]["comments"]

    def status_of(self, tid):
        return self.tasks[tid]["status"]

    def create_commands(self):
        return [
            c for c in self.commands
            if " create " in c and "boards create" not in c
        ]


class FakeState:
    def __init__(self):
        self.store = {}

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)


class ScriptedLlm:
    """Deterministic fake host LLM; ``hook`` runs before each answer."""

    def __init__(self, text="a) $44.30, b) $5.70, c) $12.20", delay=0.0, hook=None):
        self.text = text
        self.delay = delay
        self.hook = hook
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self.hook is not None:
            self.hook(self.calls)
        if self.delay:
            time.sleep(self.delay)
        return {"text": self.text, "usage": {"input_tokens": 10, "output_tokens": 20,
                                             "total_tokens": 30, "cost_usd": 0.001}}


class FakeCtx:
    def __init__(self, llm=None, board=None, config=None, spawn=None):
        self.llm = llm or ScriptedLlm()
        self.board = board or FakeBoard()
        self.state = FakeState()
        self.config = config or {}
        self.spawned = []
        if spawn is not None:
            self.spawn_task = spawn

    def dispatch_tool(self, name, payload):
        return self.board.dispatch_tool(name, payload)

    def get_config(self, key, default=None):
        return self.config.get(key, default)


# ------------------------------------------------------------------ helpers

SINGLE = {
    "id": "card-test-single",
    "title": "Test card",
    "lanes": ["reasoning"],
    "rounds": [{"id": "R1", "name": "Warm-up", "points": 1, "tasks": ["T1"]}],
    "sudden_death_task": None,
}

THREE = {
    "id": "card-test-three",
    "title": "Test card 3",
    "lanes": ["reasoning"],
    "rounds": [{"id": "R1", "name": "Warm-up", "points": 1, "tasks": ["T1", "T4", "T3"]}],
    "sudden_death_task": None,
}


def _run(args, ctx):
    tools.set_ctx(ctx)
    try:
        return json.loads(tools.kombat_run_fight(args))
    finally:
        tools.set_ctx(None)


def _fight_args(**extra):
    args = {"fighter_a": "fake/a", "fighter_b": "fake/b", "card": dict(SINGLE),
            "budget_usd": 50.0, "budget_tokens": 10_000_000, "auto_rematch": False}
    args.update(extra)
    return args


# -------------------------------------------------------------------- tests

def test_interrupt_mid_fight_closes_all_cards_once():
    """A fight killed midway closes every card exactly once and errors live.json."""
    board = FakeBoard()
    llm = ScriptedLlm(hook=lambda n: (_ for _ in ()).throw(KeyboardInterrupt()) if n == 2 else None)
    ctx = FakeCtx(llm=llm, board=board)
    tools.set_data_dir_override(Path(tempfile.mkdtemp(prefix="kombat-data-")))
    try:
        raised = False
        try:
            tools.set_ctx(ctx)
            tools._run_fight(_fight_args())
        except KeyboardInterrupt:
            raised = True
        finally:
            tools.set_ctx(None)
        assert raised, "KeyboardInterrupt must propagate to the host"

        assert len(board.created) == 2, board.created  # one round card + parent
        for tid in board.created:
            assert board.status_of(tid) == "done", (tid, board.status_of(tid))
            assert board.tasks[tid]["result"] == "Interrupted — not scored"
            interrupted = [c for c in board.comments_of(tid) if c.startswith("Fight interrupted:")]
            assert len(interrupted) == 1, (tid, board.comments_of(tid))
        live = json.loads((tools._data_dir() / "live.json").read_text(encoding="utf-8"))
        assert live["status"] == "error", live["status"]
        assert "KeyboardInterrupt" in (live.get("error") or ""), live.get("error")

        # Interrupting again is a no-op (idempotent).
        sb = kanban.Scoreboard("fake/a", "fake/b", {"rounds": []})
        sb.ids = {"parent": board.created[-1], "rounds": {"R1": board.created[0]}}
        sb.interrupt("second call")
        for tid in board.created:
            interrupted = [c for c in board.comments_of(tid) if c.startswith("Fight interrupted:")]
            assert len(interrupted) == 1, (tid, board.comments_of(tid))
    finally:
        tools.set_data_dir_override(None)


def test_interrupt_archives_when_complete_refused():
    """If the board refuses completion, interrupt archives the card instead."""
    board = FakeBoard()
    ctx = FakeCtx(board=board)
    tools.set_data_dir_override(Path(tempfile.mkdtemp(prefix="kombat-data-")))
    try:
        tools.set_ctx(ctx)
        parent = board.add_card("FIGHT: x/a vs x/b")
        round_card = board.add_card("R1 — Warm-up (1 pt/task)")
        board.refuse_complete.update({parent, round_card})
        sb = kanban.Scoreboard("x/a", "x/b", {"rounds": []})
        sb.ids = {"parent": parent, "rounds": {"R1": round_card}}
        out = sb.interrupt("manual")
        assert out["cards"] == 2, out
        assert board.status_of(parent) == "archived", board.status_of(parent)
        assert board.status_of(round_card) == "archived", board.status_of(round_card)
        for tid in (parent, round_card):
            assert any(c.startswith("Fight interrupted: manual") for c in board.comments_of(tid))
    finally:
        tools.set_ctx(None)
        tools.set_data_dir_override(None)


def test_stale_sweep_closes_only_old_cards_and_spares_live_fight():
    board = FakeBoard()
    old_parent = board.add_card("FIGHT: old/a vs old/b", status="ready", age_min=120)
    old_round = board.add_card("R2 — Main Event (2 pt/task)", status="ready", age_min=90)
    fresh_parent = board.add_card("FIGHT: new/a vs new/b", status="ready", age_min=5)
    not_fight = board.add_card("Fix the parser bug", status="ready", age_min=500)
    live_card = board.add_card("FIGHT: live/a vs live/b", status="ready", age_min=60)
    ctx = FakeCtx(board=board)
    tools.set_data_dir_override(Path(tempfile.mkdtemp(prefix="kombat-data-")))
    try:
        tools.set_ctx(ctx)
        kanban.save_bout("live-key", {"parent": live_card, "rounds": {"R1": board.add_card(
            "R1 — Warm-up (1 pt/task)", status="ready", age_min=90)}}, fighters=["live/a", "live/b"])
        sb = kanban.Scoreboard("x/a", "x/b",
                               {"rounds": [{"id": "R1", "name": "Warm-up", "points": 1, "tasks": ["T1"]}]})
        sb.start()
        assert sb.stale_closed == 2, sb.stale_closed
        assert board.status_of(old_parent) == "done"
        assert board.status_of(old_round) == "done"
        assert "Stale — closed by Model Kombat" in board.comments_of(old_parent)
        assert board.status_of(fresh_parent) == "ready"
        assert board.status_of(not_fight) == "ready"
        assert board.status_of(live_card) == "ready"
    finally:
        tools.set_ctx(None)
        tools.set_data_dir_override(None)


def test_retry_reuse_creates_zero_new_cards():
    board = FakeBoard()
    ctx = FakeCtx(board=board)
    tools.set_data_dir_override(Path(tempfile.mkdtemp(prefix="kombat-data-")))
    try:
        tools.set_ctx(ctx)
        first = json.loads(tools._run_fight(_fight_args()))
        assert first["ok"] and not first["bout_reused"], first
        created = len(board.create_commands())
        assert created == 2, board.create_commands()

        second = json.loads(tools._run_fight(_fight_args()))
        assert second["ok"] and second["bout_reused"] is True, second
        assert len(board.create_commands()) == created, board.create_commands()
        assert second["kanban"] == first["kanban"], (first["kanban"], second["kanban"])
    finally:
        tools.set_ctx(None)
        tools.set_data_dir_override(None)


def test_hung_llm_call_times_out_retries_and_never_blocks():
    board = FakeBoard()
    ctx = FakeCtx(llm=ScriptedLlm(delay=5.0), board=board)
    tools.set_data_dir_override(Path(tempfile.mkdtemp(prefix="kombat-data-")))
    try:
        tools.set_ctx(ctx)
        started = time.time()
        out = json.loads(tools._run_fight(_fight_args(timeout_s=0.5, max_attempts=2)))
        elapsed = time.time() - started
        assert out["ok"], out
        assert elapsed < 3.5, f"fight blocked {elapsed:.2f}s on a hung call"
        # Each of the 2 fighters is tried twice, then marked errored (never failed).
        for fighter in ("fake/a", "fake/b"):
            meta = out["meta_a"] if fighter == "fake/a" else out["meta_b"]
            assert meta["T1"]["status"] == "errored", meta["T1"]
            assert meta["T1"]["attempts"] == 2, meta["T1"]
            assert "hard deadline" in meta["T1"]["error"], meta["T1"]["error"]
        assert all(e.get("status") != "failed" for e in out["errors"])
        assert ctx.llm.calls == 4, ctx.llm.calls  # 2 fighters × 2 attempts
    finally:
        tools.set_ctx(None)
        tools.set_data_dir_override(None)


def test_fight_time_cap_marks_remaining_pairs_skipped():
    board = FakeBoard()
    ctx = FakeCtx(board=board)
    tools.set_data_dir_override(Path(tempfile.mkdtemp(prefix="kombat-data-")))
    try:
        tools.set_ctx(ctx)
        out = json.loads(tools._run_fight(_fight_args(
            card=dict(THREE), max_fight_seconds=0.0001, auto_rematch=False)))
        assert out["ok"], out
        assert out["time_cap_hit"] is True, out
        assert out["interrupted"] is False, out
        for tid in ("T1", "T4", "T3"):
            for side in ("meta_a", "meta_b"):
                assert out[side][tid]["status"] == "skipped", (side, tid, out[side][tid])
                assert out[side][tid]["error"] == "not attempted: fight time cap"
        # Scoring excludes them and marks the round incomplete (asterisk).
        scored = json.loads(tools.kombat_score_answers({"last": True, "render_report": False}))
        assert scored["ok"], scored
        assert scored["incomplete"] is True and scored["flag"] == "*", scored
        assert scored["rounds"][0]["name"].endswith("*"), scored["rounds"][0]["name"]
    finally:
        tools.set_ctx(None)
        tools.set_data_dir_override(None)


def test_background_start_returns_immediately_and_history_reflects_it():
    board = FakeBoard()
    coros = []

    def spawn(coro, name=None):
        coros.append((coro, name))

    ctx = FakeCtx(board=board, spawn=spawn)
    tools.set_data_dir_override(Path(tempfile.mkdtemp(prefix="kombat-data-")))
    tools.reset_job()
    try:
        tools.set_ctx(ctx)
        started = time.time()
        out = json.loads(tools.kombat_run_fight(_fight_args(background=True)))
        elapsed = time.time() - started
        assert elapsed < 1.0, elapsed
        assert out["ok"] and out["started"] is True, out
        assert out["status"] == "started" and out["job_id"], out
        assert board.create_commands() == [], "background start must not create cards inline"

        hist = json.loads(tools.kombat_history({"running": True}))
        assert hist["ok"] and hist["running"] is True, hist
        assert hist["kind"] == "fight" and hist["job_id"] == out["job_id"], hist
        assert hist["live_status"] == "idle" or hist["live_status"] is None, hist
    finally:
        for coro, _ in coros:  # never awaited in this fake host
            coro.close()
        tools.reset_job()
        tools.set_ctx(None)
        tools.set_data_dir_override(None)


def main():
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - test harness
            import traceback
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} interrupt tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
