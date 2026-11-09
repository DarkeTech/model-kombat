"""Phase B engine tests: card fights, 429 waits, card scoring, recommendations.

Run: python3 tests/test_engine_phase_b.py   (or pytest tests/test_engine_phase_b.py)
"""

import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools  # noqa: E402

POOL = tools._task_lookup()

# Real pool references score 1.0 against their own checks (SPEC §3 rule).
R1A, R1B = "reasoning-001", "reasoning-002"
R2A, R2B = "reasoning-003", "reasoning-004"
R3A, R3B = "reasoning-005", "reasoning-006"
SD = "recall-004"  # phrases check, not on the card rounds

CARD = {
    "id": "card-phase-b-test",
    "title": "Kombat card — reasoning",
    "lanes": ["reasoning"],
    "seed": 42,
    "rounds": [
        {"id": "R1", "name": "Warm-up", "points": 1, "tasks": [R1A, R1B]},
        {"id": "R2", "name": "Main Event", "points": 2, "tasks": [R2A, R2B]},
        {"id": "R3", "name": "Championship", "points": 3, "tasks": [R3A, R3B]},
    ],
    "sudden_death_task": SD,
}


def _ref(tid):
    return str(POOL[tid]["reference"])


ALL_REFS = {tid: _ref(tid) for tid in (R1A, R1B, R2A, R2B, R3A, R3B, SD)}


class _FakeUsage:
    input_tokens = 10
    output_tokens = 20
    total_tokens = 30
    cost_usd = 0.001


class _FakeResult:
    def __init__(self, text):
        self.text = text
        self.usage = _FakeUsage()


class _FakeState:
    def __init__(self):
        self.store = {}

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)


class _FakeLlm:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(kwargs.get("model"))
        outcome = self.behavior(len(self.calls), kwargs.get("model"), messages)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResult(outcome)


class _FakeCtx:
    def __init__(self, behavior, config=None):
        self.llm = _FakeLlm(behavior)
        self.state = _FakeState()
        self.config = config or {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)


def _run(behavior, args, config=None):
    ctx = _FakeCtx(behavior, config or {"show_on_kanban": False})
    tools.set_ctx(ctx)
    try:
        with tempfile.TemporaryDirectory() as td:
            tools.set_data_dir_override(Path(td))
            try:
                result = json.loads(tools.kombat_run_fight(args))
            finally:
                tools.set_data_dir_override(None)
        return result, ctx
    finally:
        tools.set_ctx(None)


class _SleepRecorder:
    def __init__(self):
        self.sleeps = []
        self._orig = tools.time.sleep

    def __enter__(self):
        def fake_sleep(seconds):
            self.sleeps.append(seconds)

        tools.time.sleep = fake_sleep
        return self

    def __exit__(self, *exc):
        tools.time.sleep = self._orig


def test_run_fight_on_card_with_429_recovery():
    def behavior(n, model, messages):
        if n == 1:
            return Exception("429 Too Many Requests: rate limit exceeded; retry_after: 112")
        return "answer"

    with _SleepRecorder() as rec:
        out, _ctx = _run(
            behavior,
            {
                "fighter_a": "fake/a",
                "fighter_b": "fake/b",
                "card": {"lanes": ["reasoning", "instruction"], "seed": 7},
                "max_rate_wait_s": 5,
                "rate_wait_budget_s": 30,
                "budget_usd": 50,
            },
        )
    assert out["ok"], out
    assert out["mode"] == "card"
    assert out["card"]["rounds"], out["card"]
    assert out["rounds"], out["rounds"]
    assert out["errors"] == [], out["errors"]
    first_pair = out["meta_a"][out["rounds"][0]]
    assert first_pair["attempts"] == 2, first_pair  # waited, then recovered
    assert rec.sleeps and rec.sleeps[0] == 5.0, rec.sleeps  # min(112, max_rate_wait_s=5)
    assert out["waited_s"]["fake/a"] == 5.0, out["waited_s"]


def test_run_fight_429_exhaustion_stays_errored():
    def behavior(n, model, messages):
        return Exception("429 rate limit: too many requests (retry_after: 7)")

    with _SleepRecorder() as rec:
        out, _ctx = _run(
            behavior,
            {
                "fighter_a": "fake/a",
                "fighter_b": "fake/b",
                "card": {"lanes": ["reasoning"], "seed": 3},
                "max_rate_wait_s": 1,
                "rate_wait_budget_s": 2,
                "max_attempts": 3,
                "auto_rematch": True,
                "budget_usd": 50,
            },
        )
    assert out["ok"], out
    assert out["errors"], "all pairs should be errored"
    statuses = {m.get("status") for m in out["meta_a"].values()} | {
        m.get("status") for m in out["meta_b"].values()
    }
    assert statuses == {"errored"}, statuses
    assert "failed" not in json.dumps(out).lower()
    # Per-fight wait budget is never exceeded.
    assert all(v <= 2.0 + 1e-6 for v in out["waited_s"].values()), out["waited_s"]
    assert sum(rec.sleeps) <= 4.0 + 1e-6, rec.sleeps


def test_run_fight_permission_error_aborts_immediately():
    calls = {"n": 0}

    def behavior(n, model, messages):
        calls["n"] += 1
        return PermissionError("model override not allowed")

    with _SleepRecorder():
        out, _ctx = _run(
            behavior,
            {"fighter_a": "fake/a", "fighter_b": "fake/b",
             "card": {"lanes": ["reasoning"], "seed": 5}, "budget_usd": 50},
        )
    assert out["ok"] is False, out
    assert "allow_model_override" in out["error"]
    assert calls["n"] == 1, calls  # aborted on the very first refusal


def _score(answers_a, answers_b, errors_a=None, errors_b=None, card=CARD, render=False):
    with tempfile.TemporaryDirectory() as td:
        tools.set_data_dir_override(Path(td))
        try:
            result = json.loads(
                tools.kombat_score_answers(
                    {
                        "fighter_a": "model/a",
                        "fighter_b": "model/b",
                        "answers_a": answers_a,
                        "answers_b": answers_b,
                        "errors_a": errors_a or [],
                        "errors_b": errors_b or [],
                        "card": card,
                        "allow_code_execution": False,
                        "render_report": render,
                    }
                )
            )
            snapshot = {}
            for item in Path(td).rglob("*"):
                if not item.is_file():
                    continue
                key = str(item.relative_to(td))
                try:
                    snapshot[key] = json.loads(item.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 — keep raw text (html reports)
                    snapshot[key] = item.read_text(encoding="utf-8")
        finally:
            tools.set_data_dir_override(None)
    return result, snapshot


def test_scoring_from_card_with_ko():
    answers_a = dict(ALL_REFS)
    answers_b = {tid: "no idea" for tid in ALL_REFS}
    out, files = _score(answers_a, answers_b, render=True)
    assert out["ok"], out
    assert out["mode"] == "card"
    # Round stakes 1 + 2 + 3 all go to A (higher summed score fraction).
    assert out["points"] == {"model/a": 6.0, "model/b": 0.0}, out["points"]
    assert out["winner"] == "model/a"
    assert out["ko"] is True, out
    assert out["ko_rounds"], out["ko_rounds"]
    assert out["rounds"][0]["points_a"] == 1.0
    assert out["rounds"][2]["points_a"] == 3.0
    assert JSON_has(out["rounds"][0]["tasks"][0], "check_type", "numeric")
    # Ladder + history were updated by the real modules.
    assert "ladder.json" in files
    ladder = files["ladder.json"]
    assert "reasoning" in ladder["lanes"]
    assert out["ladder"] is not None and out["ladder_error"] is None, out.get("ladder_error")
    html_reports = [text for name, text in files.items() if name.endswith(".html")]
    assert html_reports and len(html_reports[0]) > 100, list(files)


def test_scoring_sudden_death_on_tie():
    answers = dict(ALL_REFS)
    answers_b = {**answers, SD: "no idea"}
    out, _files = _score(answers, answers_b)
    assert out["ok"], out
    # Each round is a draw: 0.5 + 1 + 1.5 = 3 each -> sudden death.
    assert out["points"] == {"model/a": 3.0, "model/b": 3.0}, out["points"]
    assert out["sudden_death_winner"] == "model/a", out
    assert out["winner"] == "model/a"
    assert out["rounds"][-1]["id"] == "SD"


def test_scoring_excluded_round_flags_asterisk():
    answers_a = dict(ALL_REFS)
    answers_b = {tid: "no idea" for tid in ALL_REFS}
    out, _files = _score(answers_a, answers_b, errors_a=[R1A, R1B], errors_b=[R1A, R1B])
    assert out["ok"], out
    assert out["incomplete"] is True
    assert out["incomplete_rounds"] == ["R1"]
    assert out["flag"] == "*"
    r1 = next(r for r in out["rounds"] if r["id"] == "R1")
    assert r1["incomplete"] is True and r1["name"].endswith("*")
    assert r1["points_a"] == 0.0 and r1["points_b"] == 0.0
    # Excluded round contributes no points; R2 (2) + R3 (3) go to A.
    assert out["points"] == {"model/a": 5.0, "model/b": 0.0}, out["points"]


def JSON_has(obj, key, value):
    return isinstance(obj, dict) and obj.get(key) == value


# ---------------------------------------------------------------- recommend

class _FakeLadder(types.ModuleType):
    def __init__(self):
        super().__init__("ladder")
        self.written = None

    def recommend(self, data_dir, lane, candidates=None):
        return {
            "ok": True,
            "pick": "fake/model-a",
            "lane": lane,
            "margin": 22.0,
            "confidence": "medium",
            "fights": 4,
            "cost_per_point": 0.001,
            "runner_up": "fake/model-b",
            "why": f"{lane} pick",
            "ranking": [],
        }

    def short_name(self, model_id):
        return "FM-A" if model_id == "fake/model-a" else "model"

    def write_recommendations(self, data_dir, mapping):
        self.written = mapping
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / "recommendations.json").write_text(
            json.dumps(mapping), encoding="utf-8"
        )


class _KanbanCtx(_FakeCtx):
    def __init__(self):
        super().__init__(lambda *a: "unused", {"show_on_kanban": False})

    def dispatch_tool(self, name, payload):
        command = str(payload.get("command") or "")
        if "boards list" in command:
            data = {"boards": [{"slug": "main", "is_current": True}]}
        elif " list " in command:
            data = {
                "tasks": [
                    {"id": "t1", "status": "open"},
                    {"id": "t2", "status": "open"},
                    {"id": "t3", "status": "done"},
                ]
            }
        elif " show t1 " in command:
            data = {"task": {"id": "t1", "status": "open",
                             "title": "Fix the parser bug", "description": "traceback in parser"}}
        elif " show t2 " in command:
            data = {"task": {"id": "t2", "status": "open",
                             "title": "Extract invoice fields", "description": "return JSON fields"}}
        elif " show t3 " in command:
            data = {"task": {"id": "t3", "status": "done", "title": "old"}}
        else:
            data = {}
        return json.dumps({"output": json.dumps(data), "exit_code": 0})


def test_recommend_board_mode_with_fake_kanban():
    fake = _FakeLadder()
    prev = sys.modules.get("ladder")
    sys.modules["ladder"] = fake
    with tempfile.TemporaryDirectory() as td:
        tools.set_data_dir_override(Path(td))
        ctx = _KanbanCtx()
        tools.set_ctx(ctx)
        try:
            out = json.loads(tools.kombat_recommend({"board": True}))
            written = json.loads((Path(td) / "recommendations.json").read_text(encoding="utf-8"))
        finally:
            tools.set_ctx(None)
            tools.set_data_dir_override(None)
            if prev is not None:
                sys.modules["ladder"] = prev
            else:
                sys.modules.pop("ladder", None)
    assert out["ok"], out
    assert out["mode"] == "board"
    assert set(out["mapping"]) == {"t1", "t2"}, out["mapping"]  # done card skipped
    assert written == out["mapping"] == fake.written
    assert out["mapping"]["t1"]["lane"] == "debugging"
    assert out["mapping"]["t1"]["pick"] == "fake/model-a"
    assert out["mapping"]["t1"]["short"] == "FM-A"
    assert out["mapping"]["t1"]["confidence"] == "medium"
    assert out["written"] == 2


def test_ladder_read_tool():
    with tempfile.TemporaryDirectory() as td:
        payload = {"updated": "2026-11-10T00:00:00Z", "lanes": {"sql": {"models": {}}}}
        (Path(td) / "ladder.json").write_text(json.dumps(payload), encoding="utf-8")
        tools.set_data_dir_override(Path(td))
        try:
            out = json.loads(tools.kombat_recommend({"ladder": True}))
        finally:
            tools.set_data_dir_override(None)
    assert out["ok"] and out["mode"] == "ladder"
    assert out["ladder"]["lanes"].keys() == {"sql"}


def test_scoreboard_builds_from_card_rounds():
    import kanban

    commands = []

    class CaptureCtx(_FakeCtx):
        def __init__(self):
            super().__init__(lambda *a: "unused", {"show_on_kanban": False})

        def dispatch_tool(self, name, payload):
            commands.append(str(payload.get("command") or ""))
            return json.dumps({"output": json.dumps({"id": "card-x"}), "exit_code": 0})

    tools.set_ctx(CaptureCtx())
    try:
        board = kanban.Scoreboard("model/a", "model/b", {"rounds": []}, card=CARD)
        board.start()
    finally:
        tools.set_ctx(None)
    created = [c for c in commands if " create " in c]
    assert any("R1 — Warm-up (1 pt/task)" in c for c in created), created
    assert any("R2 — Main Event (2 pt/task)" in c for c in created), created
    assert any("R3 — Championship (3 pt/task)" in c for c in created), created
    assert all("T6" not in c for c in created)  # no fixed-suite leakage
    assert board.ids["rounds"] == {"R1": "card-x", "R2": "card-x", "R3": "card-x"}


if __name__ == "__main__":
    test_run_fight_on_card_with_429_recovery()
    test_run_fight_429_exhaustion_stays_errored()
    test_run_fight_permission_error_aborts_immediately()
    test_scoring_from_card_with_ko()
    test_scoring_sudden_death_on_tie()
    test_scoring_excluded_round_flags_asterisk()
    test_recommend_board_mode_with_fake_kanban()
    test_ladder_read_tool()
    test_scoreboard_builds_from_card_rounds()
    print("all phase B engine tests passed")
