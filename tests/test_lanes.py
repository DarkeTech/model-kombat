"""Tests for the v0.4 lane classifier, task-pool loader and fight-card builder.

Run: python3 tests/test_lanes.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lanes  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TASK_DIR = FIXTURES / "engine_tasks"
LEGACY = FIXTURES / "engine_legacy.json"
PROJECT = FIXTURES / "engine_project.json"


# ------------------------------------------------------------------ classifier

LANG = {"coding", "debugging", "extraction", "summarization",
        "reasoning", "instruction", "recall", "sql"}


def test_classify_examples():
    cases = [
        ("Write a Python function `is_leap_year(year)` that returns True for leap years.",
         "coding"),
        ("Fix this traceback: the function crashes with a TypeError when called.",
         "debugging"),
        ("Extract the invoice fields and return only a JSON object with vendor and total.",
         "extraction"),
        ("Summarize the article in at most 40 words, keeping the key points.",
         "summarization"),
        ("Calculate how much change she gets after buying 7 packs; show your working.",
         "reasoning"),
        ("Exactly 3 sentences, exactly 25 words, must start with Stay, do not use amazing.",
         "instruction"),
        ("Read the passage and answer: on which Sol was the recycler serviced, and by whom?",
         "recall"),
        ("Write a SQL query with a JOIN and GROUP BY over the orders table.",
         "sql"),
    ]
    for text, want in cases:
        result = lanes.classify(text)
        assert result, text
        assert result[0]["lane"] == want, (text, result)
        assert len(result) <= 3
        assert all(item["lane"] in LANG for item in result)
        weights = [item["weight"] for item in result]
        assert weights == sorted(weights, reverse=True), result


def test_classify_empty_and_fallback():
    for text in ("", "   ", None):
        result = lanes.classify(text)  # type: ignore[arg-type]
        assert [item["lane"] for item in result] == ["reasoning", "instruction"], result
    # Nothing recognisable also falls back, never returns an empty list.
    result = lanes.classify("zzzz qqqq")
    assert [item["lane"] for item in result] == ["reasoning", "instruction"]


def test_classify_top_three_only():
    text = ("Write a python function, fix the bug in the traceback, extract json fields, "
            "summarize the passage, calculate the total, exactly 20 words, select from sql.")
    result = lanes.classify(text)
    assert len(result) == 3
    assert all(item["weight"] > 0 for item in result)


# ------------------------------------------------------------------ loader

def test_load_pool_fixtures():
    pool = lanes.load_task_pool(tasks_dir=TASK_DIR, legacy_path=LEGACY,
                                project_path=PROJECT)
    # New pool files loaded with their lane/difficulty.
    assert pool["fx-coding-001"]["lane"] == "coding"
    assert pool["fx-coding-001"]["difficulty"] == 1
    assert pool["fx-sql-006"]["difficulty"] == 3
    # Legacy mapping from the SPEC task-forger note.
    expected = {
        "T1": ("reasoning", 1),
        "T4": ("instruction", 1),
        "T3": ("recall", 2),
        "T5": ("summarization", 2),
        "T2": ("debugging", 2),
        "T6": ("coding", 3),
        "T7": ("coding", 3),
        "T8": ("coding", 3),
    }
    for tid, (lane, difficulty) in expected.items():
        assert pool[tid]["lane"] == lane, (tid, pool[tid].get("lane"))
        assert pool[tid]["difficulty"] == difficulty, (tid, pool[tid]["difficulty"])
    # Project tasks merged in.
    assert pool["proj-coding-001"]["lane"] == "coding"
    assert pool["proj-coding-001"]["difficulty"] == 2


def test_loader_tolerates_missing_and_bad_files(tmp_path=None):
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "broken.json").write_text("{not json", encoding="utf-8")
        (root / "good.json").write_text(json.dumps({
            "lane": "sql",
            "tasks": [{"id": "x-1", "prompt": "p", "check": {"type": "exact", "expected": "1"}}],
        }), encoding="utf-8")
        pool = lanes.load_task_pool(tasks_dir=root,
                                    legacy_path=root / "missing.json",
                                    project_path=root / "missing-project.json")
        assert list(pool) == ["x-1"]
        assert pool["x-1"]["lane"] == "sql"
        assert pool["x-1"]["difficulty"] == 2  # default when absent


# ------------------------------------------------------------------ card builder

def _fixture_pool():
    return lanes.load_task_pool(tasks_dir=TASK_DIR, legacy_path=LEGACY,
                                project_path=PROJECT)


def test_card_determinism_same_seed():
    pool = _fixture_pool()
    lanes_in = lanes.classify("Write a python function; also select rows from this sql table.")
    a = lanes.build_card(lanes_in, 1234, pool=pool)
    b = lanes.build_card(lanes_in, "1234", pool=pool)
    assert a["id"] == b["id"]
    assert [r["tasks"] for r in a["rounds"]] == [r["tasks"] for r in b["rounds"]]
    assert a["sudden_death_task"] == b["sudden_death_task"]
    # Round-tripping the card's own lane_weights (same order/weights) reproduces it.
    c = lanes.build_card(a["lane_weights"], 1234, pool=pool)
    assert [r["tasks"] for r in a["rounds"]] == [r["tasks"] for r in c["rounds"]]
    assert a["sudden_death_task"] == c["sudden_death_task"]


def test_card_determinism_different_seed():
    pool = _fixture_pool()
    lanes_in = [{"lane": "coding", "weight": 3.0}, {"lane": "sql", "weight": 2.0}]
    a = lanes.build_card(lanes_in, 1, pool=pool)
    b = lanes.build_card(lanes_in, 2, pool=pool)
    assert a["id"] != b["id"]
    # Different seeds may pick different tasks; the structure stays identical.
    assert [r["id"] for r in a["rounds"]] == [r["id"] for r in b["rounds"]]


def test_card_structure_and_no_repeats():
    pool = _fixture_pool()
    card = lanes.build_card([{"lane": "coding", "weight": 3.0},
                             {"lane": "sql", "weight": 2.0},
                             {"lane": "reasoning", "weight": 1.0}], 7, pool=pool)
    assert card["lanes"] == ["coding", "sql", "reasoning"]
    assert card["seed"] == 7
    assert card["id"].startswith("card-")
    assert [r["id"] for r in card["rounds"]] == ["R1", "R2", "R3"]
    assert [r["points"] for r in card["rounds"]] == [1, 2, 3]
    for rnd in card["rounds"]:
        assert rnd["tasks"], rnd
        for tid in rnd["tasks"]:
            assert tid in pool, tid
    assert len(card["rounds"][0]["tasks"]) == 2
    assert 2 <= len(card["rounds"][1]["tasks"]) <= 3
    assert 1 <= len(card["rounds"][2]["tasks"]) <= 2
    assert card["sudden_death_task"] in pool
    seen = [tid for rnd in card["rounds"] for tid in rnd["tasks"]]
    seen.append(card["sudden_death_task"])
    assert len(seen) == len(set(seen)), f"task repeated on card: {seen}"
    # Every R3 task is difficulty 3.
    assert all(pool[tid]["difficulty"] == 3 for tid in card["rounds"][2]["tasks"])


def test_card_rounds_are_lane_appropriate():
    pool = _fixture_pool()
    card = lanes.build_card([{"lane": "sql", "weight": 4.0}], 99, pool=pool)
    for rnd in card["rounds"]:
        for tid in rnd["tasks"]:
            assert pool[tid]["lane"] == "sql", (rnd["id"], tid, pool[tid]["lane"])
    assert pool[card["sudden_death_task"]]["lane"] == "sql"
    assert card["rounds"][0]["tasks"] and all(
        pool[tid]["difficulty"] == 1 for tid in card["rounds"][0]["tasks"])


def test_extra_tasks_mixed_into_main_event():
    pool = _fixture_pool()
    extra = [{
        "id": "proj-extra-001", "lane": "coding", "difficulty": 2,
        "title": "project extra", "prompt": "p",
        "check": {"type": "exact", "expected": "ok"}, "reference": "ok",
    }]
    card = lanes.build_card([{"lane": "coding", "weight": 3.0},
                             {"lane": "sql", "weight": 2.0}],
                            5, extra_tasks=extra, pool=pool)
    r2 = card["rounds"][1]["tasks"]
    assert "proj-extra-001" in r2, r2
    assert 2 <= len(r2) <= 3


def test_card_with_only_extra_tasks_as_pool():
    extra = [{
        "id": f"only-{n}", "lane": "coding", "difficulty": d,
        "title": "t", "prompt": "p", "check": {"type": "exact", "expected": "x"},
    } for n, d in enumerate([1, 1, 2, 2, 3, 3], start=1)]
    card = lanes.build_card([{"lane": "coding", "weight": 1.0}], 3,
                            extra_tasks=extra, pool={})
    assert card["rounds"][0]["tasks"]
    assert card["rounds"][2]["tasks"]
    assert card["sudden_death_task"]


def test_card_partial_pool_falls_back_to_available_lanes():
    # e.g. the reasoning lane file has not landed yet: never ship empty rounds.
    full = _fixture_pool()
    sql_only = {tid: task for tid, task in full.items() if task.get("lane") == "sql"}
    card = lanes.build_card([{"lane": "reasoning", "weight": 2.0}], 11, pool=sql_only)
    assert all(rnd["tasks"] for rnd in card["rounds"]), card
    assert card["sudden_death_task"] in sql_only


def test_card_unknown_lane_falls_back():
    pool = _fixture_pool()
    card = lanes.build_card([{"lane": "nonsense", "weight": 9.0}], 1, pool=pool)
    assert card["lanes"] == ["reasoning", "instruction"]
    assert card["rounds"][0]["tasks"]


# ------------------------------------------------------------------ scout tool

def test_scout_text_path_is_ok_and_deterministic():
    import tools

    args = {"input": "Fix this traceback in my Python function, then write a unit test."}
    first = json.loads(tools.kombat_scout(args))
    second = json.loads(tools.kombat_scout(args))
    assert first["ok"], first
    assert first["source"] == "text"
    assert first["lanes"][0]["lane"] in ("coding", "debugging")
    assert first["card"]["id"] == second["card"]["id"]
    assert first["card"]["rounds"]
    assert isinstance(first["reasons"], list) and first["reasons"]


def test_scout_card_degrades_without_kanban():
    import tools

    out = json.loads(tools.kombat_scout({"card": "t_missing"}))
    assert out["ok"], out
    assert out["source"] == "card"
    assert out["warnings"]  # no host ctx -> clean warning, not an exception
    assert out["card"]["rounds"]  # fallback lanes still produce a card


def test_tools_score_check_alias():
    import tools

    out = tools.score_check({"type": "exact", "expected": "ok"}, "OK")
    assert out["status"] == "scored" and out["score"] == 1.0, out


def test_scout_uses_active_kanban_board_with_fake_host():
    import importlib.util

    root = ROOT
    spec = importlib.util.spec_from_file_location(
        "mkpkg_scout", root / "__init__.py", submodule_search_locations=[str(root)]
    )
    assert spec is not None and spec.loader is not None
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["mkpkg_scout"] = pkg
    try:
        spec.loader.exec_module(pkg)

        class FakeCtx:
            def __init__(self):
                self.commands = []

            def dispatch_tool(self, name, args):
                command = args["command"]
                self.commands.append(command)
                if "boards list" in command:
                    payload = [{"slug": "proj-alpha", "is_current": True}]
                elif " list " in command:
                    payload = [{"id": "t_2", "title": "Summarize the report", "status": "todo"},
                               {"id": "t_done", "title": "Old", "status": "done"}]
                elif " show " in command:
                    if "t_2" in command:
                        payload = {"task": {
                            "id": "t_2", "status": "todo",
                            "title": "Summarize the quarterly report",
                            "body": "Summarize the report in at most 50 words; keep all dates and names.",
                        }}
                    else:
                        payload = {"task": {
                            "id": "t_1", "status": "todo",
                            "title": "Fix this traceback in the parser",
                            "body": "The function raises a TypeError on empty input. Find the bug.",
                        }}
                else:
                    payload = {}
                return json.dumps({"output": json.dumps(payload), "exit_code": 0})

            def get_config(self, key, default=None):
                return default

        ctx = FakeCtx()
        pkg.tools.set_ctx(ctx)
        try:
            card_out = json.loads(pkg.tools.kombat_scout({"card": "t_1"}))
            board_out = json.loads(pkg.tools.kombat_scout({"board": True}))
        finally:
            pkg.tools.set_ctx(None)

        assert card_out["ok"], card_out
        assert card_out["source"] == "card"
        assert card_out["lanes"][0]["lane"] == "debugging", card_out["lanes"]
        assert card_out["card"]["rounds"]
        assert any("--board proj-alpha" in c for c in ctx.commands), ctx.commands

        assert board_out["ok"], board_out
        assert board_out["source"] == "board"
        assert board_out["kanban"]["cards"] == 1, board_out["kanban"]  # done card skipped
        assert board_out["lanes"][0]["lane"] == "summarization", board_out["lanes"]
    finally:
        sys.modules.pop("mkpkg_scout", None)


if __name__ == "__main__":
    test_classify_examples()
    test_classify_empty_and_fallback()
    test_classify_top_three_only()
    test_load_pool_fixtures()
    test_loader_tolerates_missing_and_bad_files()
    test_card_determinism_same_seed()
    test_card_determinism_different_seed()
    test_card_structure_and_no_repeats()
    test_card_rounds_are_lane_appropriate()
    test_extra_tasks_mixed_into_main_event()
    test_card_with_only_extra_tasks_as_pool()
    test_card_unknown_lane_falls_back()
    test_scout_text_path_is_ok_and_deterministic()
    test_scout_card_degrades_without_kanban()
    test_tools_score_check_alias()
    test_scout_uses_active_kanban_board_with_fake_host()
    print("all lanes tests passed")
