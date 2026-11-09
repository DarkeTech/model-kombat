"""kombat_recommend scout-card resolution tests (defect 2).

Run: python3 tests/test_recommend_card.py
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

import tools  # noqa: E402

CARD = {
    "id": "card-recommend-test",
    "title": "Kombat card — debugging · coding · sql",
    "lanes": ["debugging", "coding", "sql"],
    "lane_weights": [
        {"lane": "debugging", "weight": 10.0},
        {"lane": "coding", "weight": 4.7},
        {"lane": "sql", "weight": 4.6},
    ],
    "seed": 7,
    "rounds": [
        {"id": "R1", "name": "Warm-up", "points": 1, "tasks": ["coding-001", "sql-001"]},
        {"id": "R2", "name": "Main Event", "points": 2, "tasks": ["debugging-001"]},
        {"id": "R3", "name": "Championship", "points": 3, "tasks": ["sql-002"]},
    ],
    "sudden_death_task": "debugging-006",
}


def _model(elo, fights=4, points=10, cost=1.0, latency=4.0, scored=4):
    return {
        "elo": elo, "wins": 2, "losses": 1, "draws": 1, "fights": fights,
        "points": points, "cost_usd": cost, "latency_s": latency, "scored_tasks": scored,
    }


def _write_ladder(data_dir):
    lanes = {
        "debugging": {"models": {
            "model/cheap": _model(1600), "model/slow": _model(1500),
            "model/tiny": _model(1400),
        }},
        "coding": {"models": {
            "model/cheap": _model(1580), "model/slow": _model(1500),
        }},
        "sql": {"models": {
            "model/slow": _model(1700), "model/cheap": _model(1570),
        }},
    }
    (data_dir / "ladder.json").write_text(
        json.dumps({"updated": "2026-01-01T00:00:00+00:00", "lanes": lanes, "applied": []}),
        encoding="utf-8",
    )


def _recommend(args, with_card=True):
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        _write_ladder(data_dir)
        if with_card:
            (data_dir / "cards.json").write_text(json.dumps({CARD["id"]: CARD}), encoding="utf-8")
        tools.set_data_dir_override(data_dir)
        try:
            return json.loads(tools.kombat_recommend(args))
        finally:
            tools.set_data_dir_override(None)


def test_stored_card_id_resolves_per_lane_not_reasoning():
    out = _recommend({"card": CARD["id"]})
    assert out["ok"], out
    assert out["mode"] == "card", out
    assert out["card_id"] == CARD["id"]
    assert out["lanes"] == ["debugging", "coding", "sql"], out["lanes"]
    assert "reasoning" not in out["lanes"]
    assert [row["lane"] for row in out["per_lane"]] == ["debugging", "coding", "sql"]
    picks = {row["lane"]: row["pick"] for row in out["per_lane"]}
    assert picks == {"debugging": "model/cheap", "coding": "model/cheap", "sql": "model/slow"}, picks
    # Overall = highest sum(lane_weight * elo): cheap 16000+7426+7222 > slow 15000+7050+7820.
    assert out["overall"]["pick"] == "model/cheap", out["overall"]
    assert out["overall"]["per_lane"]["debugging"] == 16000.0
    assert "sum(lane_weight * elo" in out["method"]


def test_card_object_also_resolves():
    out = _recommend({"card": CARD})
    assert out["ok"] and out["mode"] == "card"
    assert out["card_id"] == CARD["id"]
    assert out["overall"]["pick"] == "model/cheap"


def test_missing_card_id_errors_without_reasoning_fallback():
    out = _recommend({"card": "card-does-not-exist"})
    assert out["ok"] is False, out
    assert "not found" in out["error"]
    assert out.get("mode") == "card"


def test_lane_mode_still_works():
    out = _recommend({"lane": "sql"})
    assert out["ok"] and out["mode"] == "lane"
    assert out["lane"] == "sql"
    assert out["pick"] == "model/slow"


def test_cost_per_point_n_a_when_unknown():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        lanes = {"sql": {"models": {"model/unknown": {**_model(1500), "cost_usd": None}}}}
        (data_dir / "ladder.json").write_text(
            json.dumps({"updated": None, "lanes": lanes, "applied": []}), encoding="utf-8"
        )
        tools.set_data_dir_override(data_dir)
        try:
            out = json.loads(tools.kombat_recommend({"lane": "sql"}))
        finally:
            tools.set_data_dir_override(None)
    assert out["ok"], out
    assert out["cost_per_point"] is None
    assert out["cost_per_point_display"] == "n/a"
    assert out["ranking"][0]["cost_per_point_display"] == "n/a"
    assert "n/a per point" in out["why"]


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
    print(f"\n{len(tests) - failures}/{len(tests)} recommend-card tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
