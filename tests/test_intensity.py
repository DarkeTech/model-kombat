"""Adaptive intensity, task-discrimination memory, recommend verdict + SD tests.

Run: python3 tests/test_intensity.py
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TEST_HOME = tempfile.mkdtemp(prefix="kombat-test-home-")
os.environ["HERMES_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, True)

import lanes  # noqa: E402
import tools  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TASK_DIR = FIXTURES / "engine_tasks"
LEGACY = FIXTURES / "engine_legacy.json"
PROJECT = FIXTURES / "engine_project.json"

LANES = ["coding", "sql", "reasoning"]


def _pool():
    return lanes.load_task_pool()


def _difficulties(card, pool):
    return {
        rnd["id"]: [pool[tid]["difficulty"] for tid in rnd["tasks"]]
        for rnd in card["rounds"]
    }


# ------------------------------------------------------------- d-mix


def test_standard_mix_keeps_d1_d2_d3():
    pool = _pool()
    card = lanes.build_card(LANES, 5, pool=pool, intensity="standard")
    diffs = _difficulties(card, pool)
    assert diffs["R1"] and all(d == 1 for d in diffs["R1"]), diffs
    assert diffs["R2"] and all(d == 2 for d in diffs["R2"]), diffs
    assert diffs["R3"] and all(d == 3 for d in diffs["R3"]), diffs
    assert card["intensity"] == "standard"
    assert card["stats_used"] is False


def test_hard_mix_has_no_d1():
    pool = _pool()
    card = lanes.build_card(LANES, 5, pool=pool, intensity="hard")
    diffs = _difficulties(card, pool)
    seen = [d for values in diffs.values() for d in values]
    assert seen and 1 not in seen, diffs
    assert all(d == 2 for d in diffs["R1"]), diffs
    assert all(d == 3 for d in diffs["R2"]), diffs
    assert all(d == 3 for d in diffs["R3"]), diffs
    assert pool[card["sudden_death_task"]]["difficulty"] == 3


def test_gauntlet_is_all_d3_with_2_3_3():
    pool = _pool()
    card = lanes.build_card(LANES, 5, pool=pool, intensity="gauntlet")
    for rnd in card["rounds"]:
        assert len(rnd["tasks"]) == {"R1": 2, "R2": 3, "R3": 3}[rnd["id"]], rnd
        assert all(pool[tid]["difficulty"] == 3 for tid in rnd["tasks"]), rnd
    assert pool[card["sudden_death_task"]]["difficulty"] == 3
    seen = [tid for rnd in card["rounds"] for tid in rnd["tasks"]]
    seen.append(card["sudden_death_task"])
    assert len(seen) == len(set(seen)), seen


def test_intensity_is_part_of_card_identity_and_deterministic():
    pool = _pool()
    a = lanes.build_card(LANES, 9, pool=pool, intensity="hard")
    b = lanes.build_card(LANES, 9, pool=pool, intensity="hard")
    c = lanes.build_card(LANES, 9, pool=pool, intensity="gauntlet")
    assert a["id"] == b["id"]
    assert [r["tasks"] for r in a["rounds"]] == [r["tasks"] for r in b["rounds"]]
    assert a["sudden_death_task"] == b["sudden_death_task"]
    assert a["id"] != c["id"]
    assert lanes.build_card(LANES, 9, pool=pool, intensity="nonsense")["intensity"] == "standard"


# ------------------------------------------------------------- stats preference


def _task(tid, lane="coding", difficulty=2):
    return {
        "id": tid, "lane": lane, "difficulty": difficulty, "title": tid,
        "prompt": "p", "check": {"type": "exact", "expected": "x"},
    }


def _filler_d3():
    return {f"fill-{n}": _task(f"fill-{n}", difficulty=3) for n in range(4)}


def test_never_fought_beats_saturated_and_discriminating():
    pool = {tid: _task(tid) for tid in ("fresh", "disc", "sat")}
    pool.update(_filler_d3())
    stats = {
        "disc": {"fights": 1, "split": 1, "both_perfect": 0, "mean_gap": 0.8},
        "sat": {"fights": 4, "split": 0, "both_perfect": 4, "mean_gap": 0.0},
    }
    card = lanes.build_card(["coding"], 3, pool=pool, intensity="hard", stats=stats)
    on_card = [tid for rnd in card["rounds"] for tid in rnd["tasks"]]
    assert "fresh" in on_card and "disc" in on_card, on_card
    assert "sat" not in on_card, on_card
    assert card["stats_used"] is True


def test_discriminating_beats_merely_unsaturated():
    pool = {tid: _task(tid) for tid in ("disc", "dull", "sat")}
    pool.update(_filler_d3())
    stats = {
        "disc": {"fights": 2, "split": 2, "both_perfect": 0, "mean_gap": 0.7},
        "dull": {"fights": 2, "split": 0, "both_perfect": 1, "mean_gap": 0.0},
        "sat": {"fights": 5, "split": 0, "both_perfect": 5, "mean_gap": 0.0},
    }
    card = lanes.build_card(["coding"], 3, pool=pool, intensity="hard", stats=stats)
    on_card = [tid for rnd in card["rounds"] for tid in rnd["tasks"]]
    assert on_card[:2] == ["disc", "dull"], on_card
    assert "sat" not in on_card, on_card


def test_task_preference_orders_split_then_gap():
    stats = {
        "a": {"fights": 2, "split": 1, "both_perfect": 0, "mean_gap": 0.5},
        "b": {"fights": 2, "split": 2, "both_perfect": 0, "mean_gap": 0.1},
        "c": {"fights": 2, "split": 1, "both_perfect": 0, "mean_gap": 0.9},
        "d": {"fights": 2, "split": 0, "both_perfect": 0, "mean_gap": 0.0},
    }
    order = sorted(stats, key=lambda tid: lanes.task_preference(tid, stats))
    assert order == ["b", "c", "a", "d"], order
    assert lanes.task_preference("unknown", stats)[0] == 0  # never fought first
    assert lanes.task_preference(
        "s", {"s": {"fights": 3, "both_perfect": 3, "split": 0}}
    )[0] == 3  # saturated last


def test_stats_snapshot_is_deterministic():
    pool = {tid: _task(tid) for tid in (f"t{n}" for n in range(8))}
    stats = {"t0": {"fights": 1, "split": 1, "both_perfect": 0, "mean_gap": 0.4}}
    a = lanes.build_card(["coding"], 11, pool=pool, intensity="hard", stats=stats)
    b = lanes.build_card(["coding"], 11, pool=pool, intensity="hard", stats=stats)
    assert [r["tasks"] for r in a["rounds"]] == [r["tasks"] for r in b["rounds"]]


# ------------------------------------------------------------- stats idempotency


def test_stats_update_is_idempotent_and_math_is_right():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        tools.set_data_dir_override(data_dir)
        try:
            record = {
                "fight_id": "fight-1",
                "fighter_a": "model/a",
                "fighter_b": "model/b",
                "rounds": [
                    {"id": "R1", "tasks": [
                        {"id": "X", "a_fraction": 1.0, "b_fraction": 0.0},
                        {"id": "Y", "a_fraction": 1.0, "b_fraction": 1.0},
                    ]},
                ],
            }
            assert tools._update_task_stats(record) is True
            assert tools._update_task_stats(record) is False  # same fight_id
            stats = json.loads((data_dir / "task_stats.json").read_text())
            x = stats["tasks"]["X"]
            assert stats["applied"] == ["fight-1"], stats["applied"]
            assert x["fights"] == 1 and x["split"] == 1 and x["both_zero"] == 0
            assert x["both_perfect"] == 0 and x["mean_gap"] == 1.0
            assert x["per_model"]["model/a"] == {"n": 1, "mean": 1.0}
            assert x["per_model"]["model/b"] == {"n": 1, "mean": 0.0}
            y = stats["tasks"]["Y"]
            assert y["both_perfect"] == 1 and y["split"] == 0 and y["mean_gap"] == 0.0
            # A different fight id folds in and averages the gap.
            record["fight_id"] = "fight-2"
            record["rounds"][0]["tasks"][0]["a_fraction"] = 0.0
            record["rounds"][0]["tasks"][0]["b_fraction"] = 0.0
            assert tools._update_task_stats(record) is True
            stats = json.loads((data_dir / "task_stats.json").read_text())
            x = stats["tasks"]["X"]
            assert x["fights"] == 2 and x["both_zero"] == 1
            assert x["mean_gap"] == 0.5  # (1.0 + 0.0) / 2
            assert x["per_model"]["model/a"]["mean"] == 0.5
        finally:
            tools.set_data_dir_override(None)


def test_scored_fight_writes_stats_once():
    pool = tools._task_lookup()
    tid = "reasoning-001"
    ref = str(pool[tid]["reference"])
    card = {
        "id": "card-stats-score", "title": "stats", "lanes": ["reasoning"],
        "lane_weights": [{"lane": "reasoning", "weight": 1.0}], "seed": 1,
        "rounds": [{"id": "R1", "name": "Warm-up", "points": 1, "tasks": [tid]}],
        "sudden_death_task": None,
    }
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        tools.set_data_dir_override(data_dir)
        try:
            args = {
                "fighter_a": "model/a", "fighter_b": "model/b",
                "answers_a": {tid: ref}, "answers_b": {tid: "no idea"},
                "card": card, "fight_id": "fight-stats-1",
                "allow_code_execution": False, "render_report": False,
            }
            first = json.loads(tools.kombat_score_answers(args))
            second = json.loads(tools.kombat_score_answers(args))
            stats = json.loads((data_dir / "task_stats.json").read_text())
        finally:
            tools.set_data_dir_override(None)
    assert first["ok"] and second["ok"], (first, second)
    assert first["task_stats_updated"] is True
    assert second["task_stats_updated"] is False  # idempotent per fight_id
    entry = stats["tasks"][tid]
    assert entry["fights"] == 1, entry
    assert entry["split"] == 1 and entry["both_perfect"] == 0
    assert stats["applied"] == ["fight-stats-1"]


def test_build_card_reads_stats_file_from_data_dir():
    pool = {tid: _task(tid) for tid in ("fresh", "d2-b", "sat")}
    pool["d2-b"]["difficulty"] = 2
    pool.update(_filler_d3())
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "task_stats.json").write_text(json.dumps({
            "tasks": {"sat": {"fights": 3, "both_perfect": 3, "split": 0, "mean_gap": 0.0}},
            "applied": [],
        }), encoding="utf-8")
        tools.set_data_dir_override(Path(td))
        try:
            card = lanes.build_card(["coding"], 3, pool=pool, intensity="hard")
        finally:
            tools.set_data_dir_override(None)
    on_card = [tid for rnd in card["rounds"] for tid in rnd["tasks"]]
    assert card["stats_used"] is True, card
    assert "sat" not in on_card, on_card
    assert {"fresh", "d2-b"} <= set(on_card), on_card


def test_applied_list_is_capped_at_500():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        tools.set_data_dir_override(data_dir)
        try:
            (data_dir / "task_stats.json").write_text(json.dumps({
                "tasks": {}, "applied": [f"old-{n}" for n in range(500)],
            }), encoding="utf-8")
            record = {"fight_id": "newest", "fighter_a": "a", "fighter_b": "b", "rounds": []}
            assert tools._update_task_stats(record) is True
            stats = json.loads((data_dir / "task_stats.json").read_text())
        finally:
            tools.set_data_dir_override(None)
    assert len(stats["applied"]) == 500, len(stats["applied"])
    assert stats["applied"][-1] == "newest"
    assert "old-0" not in stats["applied"]


def test_scout_reports_expected_discrimination_from_stats():
    pool = lanes.load_task_pool()
    stats = {
        "tasks": {
            tid: {"fights": 1, "split": 1, "both_perfect": 0, "both_zero": 0, "mean_gap": 0.6}
            for tid in pool
        },
        "applied": ["seed"],
    }
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "task_stats.json").write_text(json.dumps(stats), encoding="utf-8")
        tools.set_data_dir_override(Path(td))
        try:
            out = json.loads(tools.kombat_scout(
                {"input": "Write a Python function to sort a list.", "intensity": "hard"}
            ))
        finally:
            tools.set_data_dir_override(None)
    assert out["ok"], out
    assert out["stats_used"] is True
    assert out["expected_discrimination"] == "high", out
    assert out["intensity"] == "hard"
    assert out["card"]["stats_used"] is True


def test_fight_plan_accepts_intensity_for_lanes_spec():
    suite = tools._load_suite()
    plan, err = tools._fight_plan(
        {"card": {"lanes": ["coding"], "seed": 2}, "intensity": "gauntlet"}, suite
    )
    assert err is None, err
    assert plan is not None
    assert plan["card"]["intensity"] == "gauntlet"
    tasks = tools._task_lookup()
    diffs = [tasks[tid]["difficulty"] for rnd in plan["rounds"] for tid in rnd["tasks"]]
    assert diffs and set(diffs) == {3}, diffs


# ------------------------------------------------------------- recommend


CARD = {
    "id": "card-rec-intensity",
    "title": "Kombat card — coding",
    "lanes": ["coding"],
    "lane_weights": [{"lane": "coding", "weight": 1.0}],
    "seed": 3,
    "intensity": "hard",
    "rounds": [{"id": "R3", "name": "Championship", "points": 3, "tasks": ["coding-001"]}],
    "sudden_death_task": None,
}


def _model(elo, fights=9):
    return {"elo": elo, "wins": 5, "losses": 4, "draws": 0, "fights": fights,
            "points": 30, "cost_usd": 1.0, "latency_s": 30.0, "scored_tasks": 9}


def _recommend(lanes_map, card=CARD, candidates=None):
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        (data_dir / "ladder.json").write_text(
            json.dumps({"updated": None, "lanes": lanes_map, "applied": []}), encoding="utf-8"
        )
        tools.set_data_dir_override(data_dir)
        try:
            return json.loads(tools.kombat_recommend({"card": card, "candidates": candidates}))
        finally:
            tools.set_data_dir_override(None)


def test_recommend_no_clear_edge_wording():
    out = _recommend({"coding": {"models": {
        "model/top": _model(1600), "model/close": _model(1590),
    }}})
    assert out["ok"], out
    overall = out["overall"]
    assert overall["verdict"] == "no clear edge", overall
    assert overall["confidence"] == "low", overall
    assert overall["short"].startswith("≈"), overall
    assert out["verdict"] == "no clear edge" and out["confidence"] == "low"
    assert "efficiency tiebreak" in overall["why"]
    assert "not a performance win" in overall["why"]
    # Board badge short carries the tie marker too.
    assert out["per_lane"][0]["verdict"] == "no clear edge"
    assert out["per_lane"][0]["short"].startswith("≈")


def test_recommend_clear_edge_keeps_plain_badge():
    out = _recommend({"coding": {"models": {
        "model/top": _model(1700), "model/close": _model(1500),
    }}})
    overall = out["overall"]
    assert overall["verdict"] == "clear edge", overall
    assert overall["short"] == tools._short_name("model/top")
    assert not overall["short"].startswith("≈")
    assert "score" not in overall, overall  # raw weighted sum stays removed


def test_recommend_weighted_elo_is_a_weighted_average():
    card = {
        **CARD,
        "lanes": ["coding", "sql"],
        "lane_weights": [{"lane": "coding", "weight": 3.0},
                         {"lane": "sql", "weight": 1.0}],
    }
    out = _recommend({
        "coding": {"models": {"model/top": _model(1600), "model/close": _model(1500)}},
        "sql": {"models": {"model/top": _model(1500), "model/close": _model(1600)}},
    }, card=card)
    overall = out["overall"]
    # (3*1600 + 1*1500) / (3+1) = 1575, not the raw sum 6300.
    assert overall["pick"] == "model/top", overall
    assert overall["weighted_elo"] == 1575.0, overall
    assert overall["weighted_elo"] < 2000, overall
    assert overall["per_lane"]["coding"] == 4800.0, overall


def test_board_badge_short_prefixed_on_tie():
    import types

    class FakeLadder(types.ModuleType):
        def __init__(self):
            super().__init__("ladder")

        def recommend(self, data_dir, lane, candidates=None):
            return {
                "ok": True, "pick": "fake/model-a", "lane": lane, "margin": 4.0,
                "verdict": "no clear edge", "confidence": "low", "fights": 9,
                "cost_per_point": 0.001, "runner_up": "fake/model-b", "why": "x",
                "ranking": [],
            }

        def short_name(self, model_id):
            return "FM-A"

        def write_recommendations(self, data_dir, mapping):
            (Path(data_dir) / "recommendations.json").write_text(json.dumps(mapping))

    class FakeCtx:
        def __init__(self):
            self.commands = []

        def dispatch_tool(self, name, payload):
            command = str(payload.get("command") or "")
            if "boards list" in command:
                data = {"boards": [{"slug": "main", "is_current": True}]}
            elif " list " in command:
                data = {"tasks": [{"id": "t1", "status": "open"}]}
            elif " show t1 " in command:
                data = {"task": {"id": "t1", "status": "open",
                                 "title": "Fix the parser bug", "description": "traceback"}}
            else:
                data = {}
            return json.dumps({"output": json.dumps(data), "exit_code": 0})

        def get_config(self, key, default=None):
            return default

    prev = sys.modules.get("ladder")
    sys.modules["ladder"] = FakeLadder()
    try:
        with tempfile.TemporaryDirectory() as td:
            tools.set_data_dir_override(Path(td))
            tools.set_ctx(FakeCtx())
            try:
                out = json.loads(tools.kombat_recommend({"board": True}))
            finally:
                tools.set_ctx(None)
                tools.set_data_dir_override(None)
    finally:
        if prev is not None:
            sys.modules["ladder"] = prev
        else:
            sys.modules.pop("ladder", None)
    assert out["ok"], out
    assert out["mapping"]["t1"]["short"] == "≈FM-A", out["mapping"]
    assert out["mapping"]["t1"]["verdict"] == "no clear edge"


# ------------------------------------------------------------- sudden death


def test_sudden_death_picks_highest_discrimination_d3_left():
    pool = {tid: _task(tid, difficulty=3) for tid in ("a", "b", "c", "d")}
    picker = lanes._Picker(pool, __import__("random").Random("kombat:1"))
    picker.used.update({"a", "b"})  # already on the card
    stats = {
        "c": {"fights": 2, "split": 1, "mean_gap": 0.3, "both_perfect": 0},
        "d": {"fights": 2, "split": 2, "mean_gap": 0.9, "both_perfect": 0},
    }
    assert lanes._pick_sudden_death(picker, ["coding"], stats) == "d"


def test_sudden_death_is_seeded_when_no_stats():
    pool = {tid: _task(tid, difficulty=3) for tid in ("a", "b", "c")}
    import random
    p1 = lanes._Picker(pool, random.Random("kombat:1"))
    p2 = lanes._Picker(pool, random.Random("kombat:1"))
    s1 = lanes._pick_sudden_death(p1, ["coding"], {})
    s2 = lanes._pick_sudden_death(p2, ["coding"], {})
    assert s1 == s2 and s1 in pool


def test_sudden_death_on_built_card_is_not_a_round_task():
    pool = _pool()
    card = lanes.build_card(LANES, 21, pool=pool, intensity="gauntlet")
    ids = [tid for rnd in card["rounds"] for tid in rnd["tasks"]]
    assert card["sudden_death_task"] not in ids
    assert pool[card["sudden_death_task"]]["difficulty"] == 3


# ------------------------------------------------------------- discrimination level


def test_discrimination_levels_and_scout_field():
    card = {"rounds": [{"id": "R1", "tasks": ["a", "b", "c", "d"]}]}
    assert lanes.discrimination_level(card, {}) == "unknown"
    assert lanes.discrimination_level(card, {"x": {}}) == "unknown"
    high = {"a": {"fights": 1, "split": 1}, "b": {"fights": 1, "split": 1},
            "c": {"fights": 1, "split": 0}, "d": {"fights": 1, "split": 0}}
    assert lanes.discrimination_level(card, high) == "high"
    medium = {"a": {"fights": 1, "split": 1}, "b": {"fights": 1, "split": 0},
              "c": {"fights": 1, "split": 0}, "d": {"fights": 1, "split": 0}}
    assert lanes.discrimination_level(card, medium) == "medium"
    low = {tid: {"fights": 1, "split": 0} for tid in "abcd"}
    assert lanes.discrimination_level(card, low) == "low"
    # A read-only scout with no stats snapshot reports unknown.
    with tempfile.TemporaryDirectory() as td:
        tools.set_data_dir_override(Path(td))
        try:
            out = json.loads(tools.kombat_scout(
                {"input": "Write a Python function to sort a list.", "persist": False}
            ))
        finally:
            tools.set_data_dir_override(None)
    assert out["ok"], out
    assert out["expected_discrimination"] == "unknown", out
    assert out["card"]["intensity"] == "standard"


def main():
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - test harness
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} intensity tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
