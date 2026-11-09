"""Tests for ladder.py (SPEC §5). Plain stdlib asserts.

Run: python3 tests/test_ladder.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ladder  # noqa: E402


def _side(score=None, status="scored", latency=0.0, cost=0.0):
    return {"score": score, "status": status, "latency_s": latency, "cost_usd": cost}


def _record(fid="fight-1", a="deepseek/x", b="stealth/y", tasks=None, usage=None):
    return {
        "fight_id": fid,
        "fighter_a": a,
        "fighter_b": b,
        "tasks": tasks or [],
        "usage": usage or {},
    }


def _model(elo=1500.0, wins=0, losses=0, draws=0, fights=0, points=0.0, cost=0.0, latency=0.0):
    return {
        "elo": elo,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "fights": fights,
        "points": points,
        "cost_usd": cost,
        "latency_s": latency,
    }


def _write_ladder(data_dir: Path, lanes: dict, applied=None) -> None:
    (data_dir / "ladder.json").write_text(
        json.dumps({"updated": "2026-01-01T00:00:00+00:00", "lanes": lanes, "applied": applied or []}),
        encoding="utf-8",
    )


def test_short_names():
    assert ladder.short_name("deepseek/deepseek-v4.1-flash-1017") == "DS-Flash"
    assert ladder.short_name("stealth/crouton-alpha") == "Crouton"
    assert ladder.short_name("nvidia/nemotron-3-ultra-550b-a55b") == "Nemo-Ultra"
    generic = ladder.short_name("vendor/some-very-long-model-name-xyz")
    assert generic == "Some-Very-Long", generic
    assert len(generic) <= 14
    assert ladder.short_name("vendor/thing") == "Thing"
    assert ladder.short_name("") == "Model"


def test_elo_math_win():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        tasks = [{"id": "coding-001", "lane": "coding", "a": _side(1.0), "b": _side(0.0)}]
        usage = {
            "deepseek/x": {"cost_usd": 0.01, "tokens": 100, "latency_s": 4.0},
            "stealth/y": {"cost_usd": 0.02, "tokens": 200, "latency_s": 6.0},
        }
        out = ladder.update_ladder(data_dir, _record(tasks=tasks, usage=usage))
        a = out["lanes"]["coding"]["models"]["deepseek/x"]
        b = out["lanes"]["coding"]["models"]["stealth/y"]
        # Expected 0.5 each, K=24 -> +/- 12 exactly.
        assert abs(a["elo"] - 1512.0) < 1e-9, a["elo"]
        assert abs(b["elo"] - 1488.0) < 1e-9, b["elo"]
        assert (a["wins"], a["losses"], a["draws"], a["fights"]) == (1, 0, 0, 1)
        assert (b["wins"], b["losses"], b["draws"], b["fights"]) == (0, 1, 0, 1)
        assert a["points"] == 1.0 and b["points"] == 0.0
        assert abs(a["cost_usd"] - 0.01) < 1e-12
        assert abs(a["latency_s"] - 4.0) < 1e-12
        assert out["applied"] == ["fight-1"]
        assert json.loads((data_dir / "ladder.json").read_text())["lanes"]["coding"]["models"]["deepseek/x"]["elo"] == 1512.0


def test_draw_rule_and_threshold():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        # diff 0.1 < 0.25 * 2 -> draw
        tasks = [
            {"id": "c1", "lane": "coding", "a": _side(1.0), "b": _side(1.0)},
            {"id": "c2", "lane": "coding", "a": _side(1.0), "b": _side(0.9)},
        ]
        out = ladder.update_ladder(data_dir, _record(tasks=tasks))
        a = out["lanes"]["coding"]["models"]["deepseek/x"]
        b = out["lanes"]["coding"]["models"]["stealth/y"]
        assert a["elo"] == 1500.0 and b["elo"] == 1500.0
        assert a["draws"] == 1 and b["draws"] == 1
        assert a["points"] == 2.0 and abs(b["points"] - 1.9) < 1e-9

        # diff 0.4 < 0.5 still a draw (threshold is per scored task)
        out = ladder.update_ladder(
            data_dir,
            _record(
                fid="fight-2",
                tasks=[
                    {"id": "c3", "lane": "coding", "a": _side(1.0), "b": _side(0.8)},
                    {"id": "c4", "lane": "coding", "a": _side(1.0), "b": _side(0.8)},
                ],
            ),
        )
        assert out["lanes"]["coding"]["models"]["deepseek/x"]["draws"] == 2

        # diff 0.5 >= 0.5 -> win
        out = ladder.update_ladder(
            data_dir,
            _record(
                fid="fight-3",
                tasks=[
                    {"id": "c5", "lane": "coding", "a": _side(1.0), "b": _side(0.75)},
                    {"id": "c6", "lane": "coding", "a": _side(1.0), "b": _side(0.75)},
                ],
            ),
        )
        a = out["lanes"]["coding"]["models"]["deepseek/x"]
        assert a["wins"] == 1 and a["fights"] == 3


def test_idempotent_repeat():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        rec = _record(
            fid="same-fight",
            tasks=[{"id": "c1", "lane": "coding", "a": _side(1.0), "b": _side(0.0)}],
        )
        first = ladder.update_ladder(data_dir, rec)
        snapshot = json.dumps(first, sort_keys=True)
        second = ladder.update_ladder(data_dir, rec)
        assert json.dumps(second, sort_keys=True) == snapshot
        assert second["applied"] == ["same-fight"]
        assert second["lanes"]["coding"]["models"]["deepseek/x"]["fights"] == 1


def test_applied_cap_500():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        applied = [f"old-{i}" for i in range(510)]
        _write_ladder(data_dir, {}, applied=applied)
        out = ladder.update_ladder(
            data_dir,
            _record(fid="new", tasks=[{"id": "c1", "lane": "coding", "a": _side(1.0), "b": _side(0.0)}]),
        )
        assert len(out["applied"]) == 500
        assert out["applied"][0] == "old-11"
        assert out["applied"][-1] == "new"


def test_corrupt_file_recovery():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        (data_dir / "ladder.json").write_text("{not valid json!!", encoding="utf-8")
        out = ladder.update_ladder(
            data_dir,
            _record(tasks=[{"id": "c1", "lane": "coding", "a": _side(1.0), "b": _side(0.0)}]),
        )
        assert out["lanes"]["coding"]["models"]["deepseek/x"]["elo"] == 1512.0
        backup = data_dir / "ladder.json.bak"
        assert backup.is_file()
        assert backup.read_text(encoding="utf-8") == "{not valid json!!"
        # The rewritten ladder is valid JSON with the required top-level keys.
        reloaded = json.loads((data_dir / "ladder.json").read_text())
        assert set(reloaded) == {"updated", "lanes", "applied"}


def test_multi_lane_one_bout_each():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        tasks = [
            {"id": "c1", "lane": "coding", "a": _side(1.0), "b": _side(0.0)},
            {"id": "d1", "lane": "debugging", "a": _side(0.0), "b": _side(1.0)},
        ]
        out = ladder.update_ladder(data_dir, _record(fid="ml", tasks=tasks))
        assert out["lanes"]["coding"]["models"]["deepseek/x"]["elo"] == 1512.0
        assert out["lanes"]["debugging"]["models"]["deepseek/x"]["elo"] == 1488.0
        # One-sided scored tasks do not form a bout for a lane.
        out = ladder.update_ladder(
            data_dir,
            _record(
                fid="one-sided",
                tasks=[{"id": "r1", "lane": "reasoning", "a": _side(1.0), "b": _side(None, "errored")}],
            ),
        )
        assert "reasoning" not in out["lanes"]
        assert "one-sided" in out["applied"]


def test_confidence_rules():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        lanes = {
            "coding": {
                "models": {
                    "a/x": _model(elo=1700, wins=2, fights=2, points=10, cost=1),
                    "b/y": _model(elo=1400, wins=0, fights=2, points=4, cost=1),
                }
            }
        }
        _write_ladder(data_dir, lanes)
        assert ladder.recommend(data_dir, "coding")["confidence"] == "low"

        for fights, margin, expected, lowered in [
            (4, 40.0, "medium", "medium"),
            (4, 10.0, "low", "low"),
            (9, 40.0, "high", "high"),
            (9, 10.0, "low", "low"),  # margin < 15 is always low, regardless of fights
            (9, 15.0, "high", "high"),  # margin == 15 is not a downgrade
        ]:
            lanes["coding"]["models"]["a/x"] = _model(elo=1500 + margin, fights=fights, points=10, cost=1)
            lanes["coding"]["models"]["b/y"] = _model(elo=1500, fights=fights, points=4, cost=1)
            _write_ladder(data_dir, lanes)
            out = ladder.recommend(data_dir, "coding")
            assert out["confidence"] == expected, (fights, margin, out["confidence"], expected)
            assert out["verdict"] == ("no clear edge" if margin < 15 else "clear edge"), out


def test_confidence_margin_downgrade_uses_real_margin():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        _write_ladder(
            data_dir,
            {
                "coding": {
                    "models": {
                        "a/x": _model(elo=1510, fights=9, points=10, cost=1),
                        "b/y": _model(elo=1500, fights=9, points=4, cost=1),
                    }
                }
            },
        )
        out = ladder.recommend(data_dir, "coding")
        assert out["margin"] == 10.0
        assert out["confidence"] == "low"  # margin < 15 is always low
        assert out["verdict"] == "no clear edge"
        assert "efficiency tiebreak" in out["why"]
        assert "not a performance win" in out["why"]


def test_no_data_recommend():
    with tempfile.TemporaryDirectory() as td:
        out = ladder.recommend(Path(td), "coding")
        expected = {
            "ok": True,
            "pick": None,
            "lane": "coding",
            "confidence": "none",
            "why": "no fights recorded for this lane yet — run /kombat fight with a card that includes it",
        }
        assert out == expected, out
        assert ladder.recommend(Path(td), "coding", candidates=[]) == expected


def test_unknown_candidates_defaults():
    with tempfile.TemporaryDirectory() as td:
        out = ladder.recommend(Path(td), "coding", candidates=["ghost/one", "ghost/two"])
        assert out["ok"] is True
        assert out["fights"] == 0
        assert out["cost_per_point"] is None
        assert {row["model"] for row in out["ranking"]} == {"ghost/one", "ghost/two"}
        assert all(row["elo"] == 1500 and row["fights"] == 0 for row in out["ranking"])
        assert out["pick"] in ("ghost/one", "ghost/two")


def test_cost_per_point_and_tiebreaks():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        _write_ladder(
            data_dir,
            {
                "coding": {
                    "models": {
                        "cheap/x": _model(elo=1500, fights=4, points=10, cost=0.5),
                        "pricey/y": _model(elo=1500, fights=4, points=10, cost=2.0),
                        "vet/z": _model(elo=1500, fights=8, points=0, cost=0.0),
                    }
                }
            },
        )
        out = ladder.recommend(data_dir, "coding")
        # Equal Elo -> lower cost per point wins (cheap 0.05 vs pricey 0.20;
        # vet has points==0 so cost_per_point is None and ranks last).
        assert out["pick"] == "cheap/x", out["pick"]
        assert out["cost_per_point"] == 0.05
        assert out["runner_up"] == "pricey/y"
        assert out["ranking"][-1]["model"] == "vet/z"
        assert out["ranking"][-1]["cost_per_point"] is None
        assert out["fights"] == 4

        # Equal Elo and equal cost per point -> more fights wins.
        _write_ladder(
            data_dir,
            {
                "coding": {
                    "models": {
                        "rookie/x": _model(elo=1500, fights=2, points=10, cost=1),
                        "vet/y": _model(elo=1500, fights=7, points=10, cost=1),
                    }
                }
            },
        )
        assert ladder.recommend(data_dir, "coding")["pick"] == "vet/y"


def test_why_sentence_has_numbers():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        _write_ladder(
            data_dir,
            {
                "coding": {
                    "models": {
                        "a/x": _model(elo=1550, fights=9, points=10, cost=1),
                        "b/y": _model(elo=1500, fights=9, points=10, cost=1),
                    }
                }
            },
        )
        out = ladder.recommend(data_dir, "coding")
        assert out["why"].endswith(".")
        assert "1550" in out["why"] and "+50" in out["why"] and "9" in out["why"]
        assert out["ranking"][0]["model"] == "a/x"


def test_write_recommendations_merges_and_stamps():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        ladder.write_recommendations(
            data_dir,
            {"t_abc": {"lane": "coding", "pick": "a/x", "short": "AX", "confidence": "medium", "elo": 1512}},
        )
        first = json.loads((data_dir / "recommendations.json").read_text())
        assert set(first) == {"t_abc"}
        assert first["t_abc"]["updated"]
        assert first["t_abc"]["elo"] == 1512

        ladder.write_recommendations(
            data_dir,
            {"t_def": {"lane": "sql", "pick": "b/y", "short": "BY", "confidence": "low", "elo": 1490}},
        )
        merged = json.loads((data_dir / "recommendations.json").read_text())
        assert set(merged) == {"t_abc", "t_def"}
        assert merged["t_abc"]["pick"] == "a/x"
        assert merged["t_def"]["updated"]

        # Corrupt recommendations recover to a fresh mergeable file, corrupt copy kept.
        (data_dir / "recommendations.json").write_text("[[[", encoding="utf-8")
        ladder.write_recommendations(data_dir, {"t_new": {"lane": "coding", "pick": "a/x"}})
        recovered = json.loads((data_dir / "recommendations.json").read_text())
        assert set(recovered) == {"t_new"}
        assert (data_dir / "recommendations.json.bak").read_text() == "[[["


def test_exact_elo_tie_breaks_on_latency_then_cost():
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        fast = _model(elo=1500, fights=4, points=10, cost=1.0, latency=8.0)
        fast["scored_tasks"] = 4  # 2.0s per scored task
        slow = _model(elo=1500, fights=4, points=10, cost=1.0, latency=20.0)
        slow["scored_tasks"] = 4  # 5.0s per scored task
        _write_ladder(data_dir, {"coding": {"models": {"fast/x": fast, "slow/y": slow}}})
        out = ladder.recommend(data_dir, "coding")
        assert out["pick"] == "fast/x", out
        assert out["ranking"][0]["latency_per_task"] == 2.0
        assert out["ranking"][1]["latency_per_task"] == 5.0

        # Equal latency -> lower cost per point still decides.
        fast["latency_s"] = slow["latency_s"] = 8.0
        fast["cost_usd"], slow["cost_usd"] = 0.5, 2.0
        _write_ladder(data_dir, {"coding": {"models": {"fast/x": fast, "slow/y": slow}}})
        assert ladder.recommend(data_dir, "coding")["pick"] == "fast/x"


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - test harness
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} ladder tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
