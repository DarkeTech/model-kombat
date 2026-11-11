"""Tests for ladder Elo history + trends (v0.6.0 roadmap item). Plain stdlib asserts.

Run: python3 tests/test_ladder_trends.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ladder  # noqa: E402


def _side(score):
    return {"score": score, "status": "scored" if score is not None else "errored"}


def _record(fid, a_score, b_score, lane="coding", a="deepseek/x", b="stealth/y"):
    return {
        "fight_id": fid,
        "fighter_a": a,
        "fighter_b": b,
        "tasks": [{"lane": lane, "a": _side(a_score), "b": _side(b_score)}],
        "usage": {},
    }


def _fresh_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="kombat-trends-"))


def test_history_recorded_per_bout():
    d = _fresh_dir()
    ladder.update_ladder(d, _record("f1", 1.0, 0.0))
    raw = json.loads((d / "ladder.json").read_text())
    assert len(raw["history"]) == 2, raw["history"]
    a_entry = next(h for h in raw["history"] if h["model"] == "deepseek/x")
    assert a_entry["result"] == "win"
    assert a_entry["elo"] > 1500.0 and a_entry["delta"] > 0
    assert a_entry["lane"] == "coding"
    assert a_entry["opponent"] == "stealth/y"
    assert a_entry["fight_id"] == "f1"
    assert a_entry["ts"]
    b_entry = next(h for h in raw["history"] if h["model"] == "stealth/y")
    assert b_entry["result"] == "loss" and b_entry["delta"] < 0
    # bout_score is symmetric
    assert abs(a_entry["bout_score"] + b_entry["bout_score"]) < 1e-9
    shutil.rmtree(d, ignore_errors=True)


def test_history_idempotent_per_fight():
    d = _fresh_dir()
    ladder.update_ladder(d, _record("f1", 1.0, 0.0))
    ladder.update_ladder(d, _record("f1", 1.0, 0.0))  # replay
    raw = json.loads((d / "ladder.json").read_text())
    assert len(raw["history"]) == 2
    shutil.rmtree(d, ignore_errors=True)


def test_draw_and_unscored():
    d = _fresh_dir()
    ladder.update_ladder(d, _record("f1", 0.5, 0.5))  # draw bout
    ladder.update_ladder(d, _record("f2", None, None))  # nothing scored -> no bout
    raw = json.loads((d / "ladder.json").read_text())
    assert len(raw["history"]) == 2
    assert all(h["result"] == "draw" for h in raw["history"])
    assert all(h["delta"] == 0 for h in raw["history"])
    shutil.rmtree(d, ignore_errors=True)


def test_old_ladder_without_history_still_works():
    d = _fresh_dir()
    (d / "ladder.json").write_text(json.dumps({"updated": "x", "lanes": {}, "applied": []}))
    out = ladder.ladder_trends(d)
    assert out["ok"] is True and out["count"] == 0
    ladder.update_ladder(d, _record("f1", 1.0, 0.0))
    raw = json.loads((d / "ladder.json").read_text())
    assert len(raw["history"]) == 2
    shutil.rmtree(d, ignore_errors=True)


def test_corrupt_history_tolerated():
    d = _fresh_dir()
    (d / "ladder.json").write_text(json.dumps({"lanes": {}, "applied": [], "history": "nope"}))
    out = ladder.ladder_trends(d)
    assert out["count"] == 0
    ladder.update_ladder(d, _record("f1", 1.0, 0.0))
    raw = json.loads((d / "ladder.json").read_text())
    assert len(raw["history"]) == 2
    shutil.rmtree(d, ignore_errors=True)


def test_trends_filtering_and_series():
    d = _fresh_dir()
    ladder.update_ladder(d, _record("f1", 1.0, 0.0, lane="coding"))
    ladder.update_ladder(d, _record("f2", 0.0, 1.0, lane="coding"))
    ladder.update_ladder(d, _record("f3", 1.0, 0.0, lane="reasoning", a="openai/z"))

    all_t = ladder.ladder_trends(d)
    assert all_t["count"] == 4  # coding x2 + reasoning x2 (stealth/y fought in both)
    lanes = {(t["lane"], t["model"]) for t in all_t["trends"]}
    assert ("coding", "deepseek/x") in lanes and ("reasoning", "openai/z") in lanes
    assert ("reasoning", "stealth/y") in lanes

    coding = ladder.ladder_trends(d, lane="coding")
    assert coding["count"] == 2
    assert all(t["lane"] == "coding" for t in coding["trends"])

    one = ladder.ladder_trends(d, model="openai/z")
    assert one["count"] == 1 and one["trends"][0]["model"] == "openai/z"

    row = next(t for t in coding["trends"] if t["model"] == "deepseek/x")
    assert row["bouts"] == 2
    assert row["first_elo"] == 1512.0
    assert row["elo"] == 1499.17
    assert row["delta"] == round(1499.17 - 1512.0, 2)
    assert row["direction"] == "down"
    assert row["last_result"] == "loss"
    assert [p["fight_id"] for p in row["series"]] == ["f1", "f2"]
    assert row["series"][0]["elo"] == 1512.0 and row["series"][1]["elo"] == 1499.17

    no_series = ladder.ladder_trends(d, series_limit=0)
    assert all(t["series"] == [] for t in no_series["trends"])
    shutil.rmtree(d, ignore_errors=True)


def test_trend_direction_window():
    d = _fresh_dir()
    # 3 wins then a loss: recent window (last 5 bouts) still nets positive -> "up"
    ladder.update_ladder(d, _record("f1", 1.0, 0.0))
    ladder.update_ladder(d, _record("f2", 1.0, 0.0))
    ladder.update_ladder(d, _record("f3", 1.0, 0.0))
    row = ladder.ladder_trends(d, model="deepseek/x")["trends"][0]
    assert row["direction"] == "up" and row["bouts"] == 3
    assert row["min_elo"] == 1512.0 and row["elo"] == row["max_elo"]
    shutil.rmtree(d, ignore_errors=True)


def test_history_cap():
    d = _fresh_dir()
    original_cap = ladder.HISTORY_CAP
    ladder.HISTORY_CAP = 10  # shrink so the drop-oldest path is exercised quickly
    try:
        for i in range(12):
            ladder.update_ladder(d, _record(f"f{i}", 1.0, 0.0))
    finally:
        ladder.HISTORY_CAP = original_cap
    raw = json.loads((d / "ladder.json").read_text())
    assert len(raw["history"]) == 10
    # oldest dropped: first kept fight_id is not f0
    assert raw["history"][0]["fight_id"] != "f0"
    assert raw["history"][-1]["fight_id"] == "f11"
    shutil.rmtree(d, ignore_errors=True)


def test_recommend_rows_carry_trend():
    d = _fresh_dir()
    ladder.update_ladder(d, _record("f1", 1.0, 0.0))
    ladder.update_ladder(d, _record("f2", 1.0, 0.0))
    r = ladder.recommend(d, "coding")
    top = r["ranking"][0]
    assert top["model"] == "deepseek/x"
    assert top["trend"] is not None
    assert top["trend"]["direction"] == "up"
    assert r["ranking"][1]["trend"]["direction"] == "down"
    # a single bout has no direction yet (one point = flat)
    d1 = _fresh_dir()
    ladder.update_ladder(d1, _record("f1", 1.0, 0.0))
    r1 = ladder.recommend(d1, "coding")
    assert r1["ranking"][0]["trend"]["direction"] == "flat"
    # fresh ladder: no history -> trend None
    d2 = _fresh_dir()
    (d2 / "ladder.json").write_text(json.dumps({"updated": None, "lanes": {}, "applied": []}))
    r2 = ladder.recommend(d2, "coding")  # no data at all -> no pick
    assert r2["pick"] is None
    for p in (d, d1, d2):
        shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} trend tests passed")
