"""Tests for Model Kombat scorers/tools (no Hermes host needed, except mock-ccx).

Run: python -m pytest tests/ -q   (or: python tests/test_scoring.py)
"""

import json
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Isolate plugin-data writes to a throwaway HERMES_HOME for the whole run.
_TEST_HOME = tempfile.mkdtemp(prefix="kombat-test-home-")
os.environ["HERMES_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, True)

import tools  # noqa: E402

CROUTON_T4 = (
    "Stay hydrated with this reusable steel bottle for daily use. "
    "Double-wall insulation keeps drinks cold for hours. "
    "Simply refill, recap, and carry it anywhere daily."
)
CROUTON_T5 = (
    "On 14 March, the council voted 7-3 to approve the Riverwalk project. "
    "Led by Hartwell Builders, it costs $2.4 million over 18 months for 1.2 km "
    "of path. Mayor Lucia Chen said construction begins in June; Councillor "
    "Raj Patel opposed cost and consultation. Public consultation will be "
    "held in April."
)
CROUTON_T6 = '''```python
def is_leap_year(year: int) -> bool:
    """Return True if year is a Gregorian leap year."""
    if isinstance(year, bool) or not isinstance(year, int):
        raise TypeError("year must be an int (bool is not accepted)")
    if year <= 0:
        raise ValueError("year must be positive")
    return year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)
```'''
BAD_T6 = "```python\ndef is_leap_year(year):\n    return year % 4 == 0\n```"
GOOD_T7 = '''```python
def is_valid_sudoku(grid):
    """Return True iff grid is a valid completed Sudoku."""
    if not isinstance(grid, list) or len(grid) != 9:
        return False
    if any(not isinstance(r, list) or len(r) != 9 for r in grid):
        return False
    want = set(range(1, 10))
    if any(set(r) != want for r in grid):
        return False
    if any({grid[r][c] for r in range(9)} != want for c in range(9)):
        return False
    for br in (0, 3, 6):
        for bc in (0, 3, 6):
            if {grid[r][c] for r in range(br, br + 3) for c in range(bc, bc + 3)} != want:
                return False
    return True
```'''
GOOD_T2 = '''```python
def second_largest(xs):
    """Second-largest distinct value, or None."""
    distinct = sorted(set(xs), reverse=True)
    return distinct[1] if len(distinct) >= 2 else None
```'''
GOOD_T8 = '''```python
def rle_encode(s):
    """Run-length encode s as (char, count) tuples."""
    out = []
    for ch in s:
        if out and out[-1][0] == ch:
            out[-1] = (ch, out[-1][1] + 1)
        else:
            out.append((ch, 1))
    return out
```'''

TASKS = {t["id"]: t for t in tools._load_tasks()}


def test_data_dir_honors_hermes_home():
    with tempfile.TemporaryDirectory() as td:
        tools.set_data_dir_override(None)
        try:
            os.environ["HERMES_HOME"] = td
            assert tools._data_dir() == Path(td) / "plugin-data" / "model-kombat"
        finally:
            os.environ["HERMES_HOME"] = _TEST_HOME


def test_list_tasks():
    out = json.loads(tools.kombat_list_tasks({}))
    assert out["ok"]
    assert [r["id"] for r in out["rounds"]] == ["R1", "R2", "R3"]
    assert [r["points_per_task"] for r in out["rounds"]] == [1, 2, 3]
    assert out["sudden_death_task"] == "T8"
    assert [t["id"] for t in out["tasks"]] == ["T1", "T4", "T3", "T5", "T2", "T6", "T7", "T8"]


def test_money_scorer():
    good = tools._score_one("money", "a) $44.30, b) $5.70, c) $12.20", TASKS["T1"], False)
    assert good["passed"] and good["score"] == 3
    # numeric tolerance: 44.3 without $ still counts
    plain = tools._score_one("money", "total 44.3, change 5.7, refund 12.2", TASKS["T1"], False)
    assert plain["passed"], plain["details"]
    bad = tools._score_one("money", "total $44.30", TASKS["T1"], False)
    assert not bad["passed"] and bad["score"] == 1


def test_recall_synonyms():
    # "no one was injured" must count for the injuries fact (the T3 false-negative fix).
    ans = "Serviced on Sol 43 by Tomas Beck. Tank R-7 contained nitrogen. No one was injured."
    out = tools._score_one("recall", ans, TASKS["T3"], False)
    assert out["passed"], out["details"]


def test_format25_scorer():
    out = tools._score_one("format25", CROUTON_T4, TASKS["T4"], False)
    assert out["passed"], out["details"]
    wrong = tools._score_one("format25", "Stay amazing and best!", TASKS["T4"], False)
    assert not wrong["passed"]


def test_summary50_scorer():
    out = tools._score_one("summary50", CROUTON_T5, TASKS["T5"], False)
    assert out["passed"], out["details"]
    dropped = tools._score_one("summary50", "The council approved a project in March.", TASKS["T5"], False)
    assert not dropped["passed"]


def test_code_exec_passes_and_fails():
    good = tools._score_one("code_exec", CROUTON_T6, TASKS["T6"], True)
    assert good["passed"], good["details"]
    bad = tools._score_one("code_exec", BAD_T6, TASKS["T6"], True)
    assert not bad["passed"]
    skipped = tools._score_one("code_exec", CROUTON_T6, TASKS["T6"], False)
    assert skipped.get("skipped") is True


def test_debug_exec_and_sudoku_and_rle():
    t2 = tools._score_one("debug_exec", GOOD_T2, TASKS["T2"], True)
    assert t2["passed"], t2["details"]
    t7 = tools._score_one("code_exec", GOOD_T7, TASKS["T7"], True)
    assert t7["passed"], t7["details"]
    t8 = tools._score_one("code_exec", GOOD_T8, TASKS["T8"], True)
    assert t8["passed"], t8["details"]
    # no code -> heuristic fallback still credits a good explanation+fix
    prose = ("It fails on duplicates: [5, 5, 4] returns 5 instead of 4. "
             "Fix with distinct values:\n```python\ndef second_largest(xs):\n"
             "    d = sorted(set(xs), reverse=True)\n    return d[1] if len(d) >= 2 else None\n```")
    fb = tools._score_one("debug_exec", prose, TASKS["T2"], False)
    assert fb["passed"], fb["details"]


def test_weighted_scoring_and_ko():
    with tempfile.TemporaryDirectory() as td:
        tools.set_data_dir_override(Path(td))
        tools.set_ctx(_ConfigCtx(allow_code_execution=True))
        try:
            out = json.loads(
                tools.kombat_score_answers(
                    {
                        "fighter_a": "crouton-alpha",
                        "fighter_b": "sloppy-model",
                        "answers_a": {"T4": CROUTON_T4, "T5": CROUTON_T5, "T6": CROUTON_T6, "T7": GOOD_T7},
                        "answers_b": {"T4": "Stay amazing!", "T5": "A project was approved.", "T6": BAD_T6, "T7": BAD_T6},
                        "render_report": False,
                    }
                )
            )
        finally:
            tools.set_ctx(None)
            tools.set_data_dir_override(None)
    assert out["ok"], out
    assert out["winner"] == "crouton-alpha", out["points"]
    # T4 (R1=1pt) + T5 (R2=2pts) + T6/T7 (R3=3pts each) all to A
    assert out["points"] == {"crouton-alpha": 9.0, "sloppy-model": 0.0}, out["points"]
    assert out["ko"] is True  # passed every Championship task, opponent failed them


def test_errored_and_skipped_excluded():
    with tempfile.TemporaryDirectory() as td:
        tools.set_data_dir_override(Path(td))
        try:
            out = json.loads(
                tools.kombat_score_answers(
                    {
                        "fighter_a": "a",
                        "fighter_b": "b",
                        "answers_a": {"T1": "a) $44.30, b) $5.70, c) $12.20", "T6": CROUTON_T6},
                        "answers_b": {"T1": "wrong", "T6": BAD_T6},
                        "errors_a": ["T6"],
                        "render_report": False,
                    }
                )
            )
        finally:
            tools.set_data_dir_override(None)
    assert out["ok"], out
    # T6 excluded (A errored) -> only T1 counts: A wins 1pt, B 0. No KO.
    assert out["points"] == {"a": 1.0, "b": 0.0}, out["points"]
    assert out["ko"] is False


def test_history_and_export():
    with tempfile.TemporaryDirectory() as td:
        tools.set_data_dir_override(Path(td))
        try:
            tools.kombat_score_answers(
                {
                    "fighter_a": "a", "fighter_b": "b",
                    "answers_a": {"T1": "a) $44.30, b) $5.70, c) $12.20"},
                    "answers_b": {"T1": "wrong"},
                    "render_report": False,
                }
            )
            hist = json.loads(tools.kombat_history({"limit": 10}))
            assert hist["ok"] and hist["count"] == 1 and hist["fights"][0]["winner"] == "a"
            for fmt in ("json", "md", "csv"):
                exp = json.loads(tools.kombat_export({"format": fmt, "index": 0}))
                assert exp["ok"] and Path(exp["path"]).exists(), exp
        finally:
            tools.set_data_dir_override(None)


class _FakeUsage:
    input_tokens = 10
    output_tokens = 20
    total_tokens = 30
    cost_usd = 0.001


class _FakeResult:
    def __init__(self, text):
        self.text = text
        self.usage = _FakeUsage()


class _FakeLlm:
    def complete(self, messages, **kwargs):
        return _FakeResult("a) $44.30, b) $5.70, c) $12.20")


class _FakeState:
    def __init__(self):
        self.store = {}

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)


class _FakeCtx:
    llm = _FakeLlm()

    def __init__(self):
        self.state = _FakeState()

    def get_config(self, key, default=None):
        return default


class _ConfigCtx(_FakeCtx):
    """A host whose plugin config carries explicit settings (the user's side)."""

    def __init__(self, **cfg):
        super().__init__()
        self.cfg = cfg

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)


def test_run_fight_with_mock_host():
    tools.set_ctx(_FakeCtx())
    try:
        out = json.loads(
            tools.kombat_run_fight(
                {"fighter_a": "fake/a", "fighter_b": "fake/b", "tasks": ["T1"], "budget_usd": 5.0}
            )
        )
    finally:
        tools.set_ctx(None)
    assert out["ok"], out
    assert out["answers_a"]["T1"].startswith("a) $44.30")
    # T1 + the up-front sudden-death task are both called per fighter.
    assert out["usage"]["fake/a"]["tokens"] == 60
    assert out["usage"]["fake/a"]["cost_usd"] == 0.002
    assert out["sudden_death_task"] == "T8"
    assert out["sudden_death_usage"]["fake/a"]["tokens"] == 30
    assert out["meta_a"]["T1"]["attempts"] == 1


def test_run_fight_without_host_ctx():
    out = json.loads(
        tools.kombat_run_fight({"fighter_a": "a/b", "fighter_b": "c/d"})
    )
    assert out["ok"] is False  # no ctx bound outside Hermes — must degrade, not crash


def test_rematch_errors_merges():
    # Fake host: last fight has T6 errored for fighter A; rematch answers it.
    class RematchCtx(_FakeCtx):
        def __init__(self):
            super().__init__()
            self.state.set("last_fight", {
                "fighter_a": "fake/a", "fighter_b": "fake/b", "mode": "pure",
                "answers_a": {"T1": "a) $44.30, b) $5.70, c) $12.20", "T6": ""},
                "answers_b": {"T1": "a) $44.30, b) $5.70, c) $12.20", "T6": BAD_T6},
                "meta_a": {}, "meta_b": {},
                "errors": [{"task": "T6", "fighter": "fake/a", "error": "429 rate limit"}],
                "usage": {"fake/a": {"cost_usd": 0.0, "tokens": 10, "latency_s": 1.0},
                          "fake/b": {"cost_usd": 0.0, "tokens": 10, "latency_s": 1.0}},
                "kanban": None,
            })

    tools.set_ctx(RematchCtx())
    try:
        out = json.loads(tools.kombat_rematch_errors({"budget_usd": 5.0}))
    finally:
        tools.set_ctx(None)
    assert out["ok"], out
    assert out["rematched"] == [{"task": "T6", "fighter": "fake/a"}], out["rematched"]
    assert out["still_failing"] == []
    # FakeLlm returns the money string for every call — merge must have written it.
    assert out["answers_a"]["T6"].startswith("a) $44.30")
    assert out["usage"]["fake/a"]["tokens"] == 40  # 10 prior + 30 rematch


def test_rematch_without_errors():
    class CleanCtx(_FakeCtx):
        def __init__(self):
            super().__init__()
            self.state.set("last_fight", {"fighter_a": "a", "fighter_b": "b", "errors": []})

    tools.set_ctx(CleanCtx())
    try:
        out = json.loads(tools.kombat_rematch_errors({}))
    finally:
        tools.set_ctx(None)
    assert out["ok"] is False and "no errored tasks" in out["error"]


def test_tool_argument_cannot_enable_code_execution():
    """Code execution is a user setting: a tool call must never switch it on."""
    from schemas import KOMBAT_SCORE_ANSWERS

    props = KOMBAT_SCORE_ANSWERS["parameters"]["properties"]
    assert "allow_code_execution" not in props, props.keys()

    with tempfile.TemporaryDirectory() as td:
        tools.set_data_dir_override(Path(td))
        tools.set_ctx(_ConfigCtx(allow_code_execution=False))
        try:
            out = json.loads(
                tools.kombat_score_answers(
                    {
                        "fighter_a": "a",
                        "fighter_b": "b",
                        "answers_a": {"T6": CROUTON_T6},
                        "answers_b": {"T6": BAD_T6},
                        # exactly what a model would try:
                        "allow_code_execution": True,
                        "render_report": False,
                    }
                )
            )
        finally:
            tools.set_ctx(None)
            tools.set_data_dir_override(None)
    assert out["ok"], out
    # Nothing was executed, so the code round is skipped for both fighters and
    # awards no points — the argument changed nothing.
    assert out["points"] == {"a": 0.0, "b": 0.0}, out["points"]
    task = out["rounds"][0]["tasks"][0]
    assert task["a"]["skipped"] is True and task["b"]["skipped"] is True, task
    assert out["rounds"][0]["name"].endswith("*"), out["rounds"][0]["name"]


def test_spend_and_time_caps_only_lower():
    tools.set_ctx(_ConfigCtx(max_fight_cost_usd=1.0, max_fight_tokens=1000,
                             max_fight_seconds=60))
    try:
        # an argument may tighten ...
        assert tools._cap("max_fight_cost_usd", 0.25, 2.0) == 0.25
        assert tools._cap("max_fight_tokens", 500, 400000, cast=int) == 500
        assert tools._cap("max_fight_seconds", 30, 360, zero_is_unlimited=True) == 30
        # ... but never raise the configured cap
        assert tools._cap("max_fight_cost_usd", 99.0, 2.0) == 1.0
        assert tools._cap("max_fight_tokens", 10 ** 9, 400000, cast=int) == 1000
        assert tools._cap("max_fight_seconds", 10 ** 6, 360, zero_is_unlimited=True) == 60
        # 0 means "no wall cap" in the engine, so an argument of 0 must not
        # strip the cap; a negative cost/token argument clamps to 0.
        assert tools._cap("max_fight_seconds", 0, 360, zero_is_unlimited=True) == 60
        assert tools._cap("max_fight_seconds", -5, 360, zero_is_unlimited=True) == 60
        assert tools._cap("max_fight_cost_usd", -5, 2.0) == 0
        # absent argument -> the configured cap
        assert tools._cap("max_fight_cost_usd", None, 2.0) == 1.0
    finally:
        tools.set_ctx(None)
    # no host ctx -> the documented defaults still apply
    assert tools._cap("max_fight_cost_usd", 99.0, 2.0) == 2.0
    assert tools._cap("max_fight_seconds", 0, 360, zero_is_unlimited=True) == 360


def test_legacy_code_runner_is_sandboxed():
    """T2/T6/T7/T8 go through the same hardened runner as code_exec."""
    good = tools._execute_candidate(
        tools._extract_code(CROUTON_T6, "def is_leap_year"), tools._HARNESSES["T6"]
    )
    assert good["passed"], good

    for titre, code in [
        ("socket import", "import socket\ndef is_leap_year(year):\n    return True\n"),
        ("subprocess import", "import subprocess\ndef is_leap_year(year):\n    return True\n"),
        ("ctypes import", "import ctypes\ndef is_leap_year(year):\n    return True\n"),
    ]:
        blocked = tools._execute_candidate(code, tools._HARNESSES["T6"])
        assert not blocked["passed"], (titre, blocked)

    leak = Path(tempfile.gettempdir()) / f"kombat-legacy-leak-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    escape = (
        "def is_leap_year(year):\n"
        f"    open({str(leak)!r}, 'w').write('leak')\n"
        "    return True\n"
    )
    out = tools._execute_candidate(escape, tools._HARNESSES["T6"])
    assert not out["passed"], out
    assert not leak.exists(), f"legacy runner leaked a write to {leak}"


if __name__ == "__main__":
    test_data_dir_honors_hermes_home()
    test_list_tasks()
    test_money_scorer()
    test_recall_synonyms()
    test_format25_scorer()
    test_summary50_scorer()
    test_code_exec_passes_and_fails()
    test_debug_exec_and_sudoku_and_rle()
    test_weighted_scoring_and_ko()
    test_errored_and_skipped_excluded()
    test_history_and_export()
    test_run_fight_with_mock_host()
    test_run_fight_without_host_ctx()
    test_rematch_errors_merges()
    test_rematch_without_errors()
    test_tool_argument_cannot_enable_code_execution()
    test_spend_and_time_caps_only_lower()
    test_legacy_code_runner_is_sandboxed()
    print("all Model Kombat tests passed")
