"""Recommend fixes: overall pick must agree with lane picks, and a draw must
carry a measured value edge (never a fake performance win).

Run: python3 tests/test_recommend_value.py
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TEST_HOME = tempfile.mkdtemp(prefix="kombat-test-home-")
os.environ["HERMES_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, True)

import ladder  # noqa: E402
import tools  # noqa: E402

CARD = {
    "id": "card-value-test",
    "title": "Kombat card — debugging · coding · sql",
    "lanes": ["debugging", "coding", "sql"],
    "lane_weights": [
        {"lane": "debugging", "weight": 10.0},
        {"lane": "coding", "weight": 6.9},
        {"lane": "sql", "weight": 5.6},
    ],
    "seed": 11,
    "rounds": [
        {"id": "R1", "name": "Warm-up", "points": 1, "tasks": ["debugging-001"]},
        {"id": "R2", "name": "Main Event", "points": 2, "tasks": ["coding-001"]},
        {"id": "R3", "name": "Championship", "points": 3, "tasks": ["sql-001"]},
    ],
    "sudden_death_task": "debugging-006",
}

# "aaa/expensive" sorts first alphabetically and is the slower, dearer model:
# the old overall tiebreak picked it purely by model id.
CHEAP = "zzz/nano-30b"
DEAR = "aaa/ultra-550b"


def _model(elo=1500.0, fights=4, points=8.0, cost=0.001, latency=4.0, scored=8):
    return {
        "elo": elo, "wins": 1, "losses": 1, "draws": 2, "fights": fights,
        "points": points, "cost_usd": cost, "latency_s": latency, "scored_tasks": scored,
    }


def _draw_lanes():
    """Every lane is a dead heat on Elo; CHEAP is ~18x cheaper and ~2.8x faster."""
    return {
        "debugging": {"models": {CHEAP: _model(cost=0.0025, latency=38.9),
                                 DEAR: _model(cost=0.0450, latency=109.4)}},
        "coding": {"models": {CHEAP: _model(cost=0.0025, latency=38.9),
                              DEAR: _model(cost=0.0450, latency=109.4)}},
        "sql": {"models": {CHEAP: _model(cost=0.0025, latency=38.9),
                           DEAR: _model(cost=0.0450, latency=109.4)}},
    }


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


def test_elo_tie_follows_lane_picks_not_alphabet():
    out = _recommend(_draw_lanes())
    assert out["ok"], out
    per_lane = {row["lane"]: row["pick"] for row in out["per_lane"]}
    assert set(per_lane.values()) == {CHEAP}, per_lane
    overall = out["overall"]
    assert overall["pick"] == CHEAP, overall
    assert overall["runner_up"] == DEAR, overall
    assert overall["lane_picks_won"] == 3, overall
    assert overall["lanes_considered"] == 3, overall
    # Still a tie on quality: the pick is marked, confidence stays low.
    assert overall["verdict"] == "no clear edge", overall
    assert overall["confidence"] == "low", overall
    assert overall["short"].startswith("≈"), overall


def test_draw_emits_measured_value_edge():
    out = _recommend(_draw_lanes())
    overall = out["overall"]
    value = overall["value_edge"]
    assert value, overall
    assert value["fighter"] == CHEAP, value
    assert value["other"] == DEAR, value
    # 0.0450 / 0.0025 = 18x cheaper, 109.4 / 38.9 = 2.81x faster.
    assert abs(value["cost_ratio"] - 18.0) < 0.2, value
    assert value["latency_ratio"] == 2.81, value
    assert value["cheaper"] is True and value["faster"] is True, value
    assert "× cheaper" in value["label"] and "× faster" in value["label"], value
    assert "not a performance win" in value["note"], value
    assert "Measured:" in overall["why"], overall["why"]
    assert out["value_edge"] == value, out["value_edge"]


def test_value_edge_none_when_cost_unknown():
    lanes = _draw_lanes()
    for lane in lanes.values():
        for model in lane["models"].values():
            model["cost_usd"] = None
            model["latency_s"] = 0.0
            model["scored_tasks"] = 0
            model["fights"] = 0
    out = _recommend(lanes)
    overall = out["overall"]
    assert overall["verdict"] == "no clear edge", overall
    assert overall["value_edge"] is None, overall
    assert "Measured:" not in overall["why"], overall["why"]


def test_thin_coverage_model_is_not_the_overall_pick():
    lanes = _draw_lanes()
    # A model with a great Elo in one lane only must not win the whole card.
    lanes["debugging"]["models"]["aaa/one-lane-king"] = _model(elo=1900.0)
    out = _recommend(lanes)
    overall = out["overall"]
    assert overall["pick"] != "aaa/one-lane-king", overall
    assert any("every card lane" in w for w in out["warnings"]), out["warnings"]


def test_lane_recommend_carries_value_edge():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        lanes = {"coding": {"models": {
            "zzz/nano": _model(cost=0.0025, latency=38.9),
            "aaa/ultra": _model(cost=0.0450, latency=109.4),
        }}}
        (data_dir / "ladder.json").write_text(
            json.dumps({"updated": None, "lanes": lanes, "applied": []}), encoding="utf-8"
        )
        out = ladder.recommend(data_dir, "coding")
    assert out["verdict"] == "no clear edge", out
    value = out["value_edge"]
    assert isinstance(value, dict), out
    assert value["fighter"] == "zzz/nano", value
    assert "efficiency tiebreak" in out["why"] and "not a performance win" in out["why"]
    assert out["why"].endswith("."), out["why"]


def test_value_edge_helper_ratios_flip_for_runner_winner():
    pick = {"model": "aaa/dear", "cost_per_point": 18.0, "latency_per_task": 2.8}
    runner = {"model": "zzz/cheap", "cost_per_point": 1.0, "latency_per_task": 1.0}
    value = ladder.value_edge(pick, runner)
    assert value["fighter"] == "zzz/cheap", value
    assert value["cost_ratio"] == 18.0 and value["latency_ratio"] == 2.8, value


def test_draw_edge_carries_value_ratios():
    usage = {
        "zzz/nano": {"latency_s": 38.92, "cost_usd": 0.0025, "output_tokens": 900},
        "aaa/ultra": {"latency_s": 109.442, "cost_usd": 0.0451, "output_tokens": 900},
    }
    edge = tools._efficiency_edge("zzz/nano", "aaa/ultra", usage)
    assert edge["metric"] == "latency", edge
    assert edge["latency_ratio"] == 2.81, edge
    assert edge["cost_ratio"] == 18.04, edge
    assert edge["value_fighter"] == "zzz/nano", edge
    assert "18.0× cheaper" in edge["value_summary"], edge["value_summary"]
    assert "not a win" in edge["reason"], edge


def test_draw_edge_value_summary_skips_unknown_cost():
    usage = {
        "zzz/nano": {"latency_s": 38.92, "cost_usd": None},
        "aaa/ultra": {"latency_s": 109.442, "cost_usd": 0.0451},
    }
    edge = tools._efficiency_edge("zzz/nano", "aaa/ultra", usage)
    assert edge["cost_ratio"] is None, edge
    assert edge["latency_ratio"] == 2.81, edge
    assert "cheaper" not in (edge["value_summary"] or ""), edge


def test_provisional_edge_suppresses_ratios():
    usage = {
        "zzz/nano": {"latency_s": 545.5, "cost_usd": 0.0002},
        "aaa/ultra": {"latency_s": 6.7, "cost_usd": 0.0007},
    }
    edge = tools._efficiency_edge("zzz/nano", "aaa/ultra", usage, has_errors=True)
    assert edge["provisional"] is True, edge
    assert edge["value_summary"] is None and edge["cost_ratio"] is None, edge
    assert edge["fighter"] == "aaa/ultra", edge
    # Without the flag the same totals would print an 81x "speedup".
    clean = tools._efficiency_edge("zzz/nano", "aaa/ultra", usage)
    assert clean["latency_ratio"] == 81.42, clean


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
    print(f"\n{len(tests) - failures}/{len(tests)} recommend-value tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
