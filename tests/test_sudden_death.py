"""Sudden-death + draw-edge tests (defects 3 and 4).

Run: python3 tests/test_sudden_death.py
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

POOL = tools._task_lookup()
R1A, R1B = "reasoning-001", "reasoning-002"
R2A, R2B = "reasoning-003", "reasoning-004"
R3A, R3B = "reasoning-005", "reasoning-006"
SD = "recall-004"

CARD = {
    "id": "card-sd-test",
    "title": "Kombat card — reasoning",
    "lanes": ["reasoning"],
    "lane_weights": [{"lane": "reasoning", "weight": 2.0}],
    "seed": 42,
    "rounds": [
        {"id": "R1", "name": "Warm-up", "points": 1, "tasks": [R1A, R1B]},
        {"id": "R2", "name": "Main Event", "points": 2, "tasks": [R2A, R2B]},
        {"id": "R3", "name": "Championship", "points": 3, "tasks": [R3A, R3B]},
    ],
    "sudden_death_task": SD,
}

REFS = {tid: str(POOL[tid]["reference"]) for tid in (R1A, R1B, R2A, R2B, R3A, R3B, SD)}


def _score(answers_a, answers_b, usage=None):
    with tempfile.TemporaryDirectory() as td:
        tools.set_data_dir_override(Path(td))
        try:
            out = json.loads(
                tools.kombat_score_answers(
                    {
                        "fighter_a": "model/a",
                        "fighter_b": "model/b",
                        "answers_a": answers_a,
                        "answers_b": answers_b,
                        "card": CARD,
                        "usage": usage or {},
                        "allow_code_execution": False,
                        "render_report": False,
                    }
                )
            )
            ladder = tools._read_json_file(Path(td) / "ladder.json", {})
            live = tools._read_json_file(Path(td) / "live.json", {})
        finally:
            tools.set_data_dir_override(None)
    return out, ladder, live


def test_tied_card_sudden_death_decides():
    a = dict(REFS)
    b = {**REFS, SD: "no idea"}
    out, _ladder, live = _score(a, b)
    assert out["ok"], out
    assert out["points"] == {"model/a": 3.0, "model/b": 3.0}, out["points"]
    assert out["sudden_death_winner"] == "model/a", out
    assert out["winner"] == "model/a"
    assert out["rounds"][-1]["id"] == "SD"
    assert live.get("sudden_death"), live
    assert out["edge"] is None


def test_non_tied_card_ignores_sudden_death_even_when_answered():
    a = dict(REFS)
    b = {tid: "no idea" for tid in REFS}
    out, ladder, live = _score(a, b)
    assert out["ok"], out
    assert out["winner"] == "model/a"
    assert out["sudden_death_winner"] is None
    assert all(rnd["id"] != "SD" for rnd in out["rounds"]), out["rounds"]
    assert live.get("sudden_death") is None, live
    # SD is recall-004: if it had been scored it would create a recall Elo lane.
    assert "recall" not in (ladder.get("lanes") or {}), ladder


def test_draw_after_equal_sudden_death_gets_efficiency_edge():
    a = dict(REFS)
    b = dict(REFS)  # still tied after SD settles nothing
    usage = {
        "model/a": {"latency_s": 5.0, "output_tokens": 100, "cost_usd": 0.02, "tokens": 150},
        "model/b": {"latency_s": 9.0, "output_tokens": 80, "cost_usd": 0.01, "tokens": 130},
    }
    out, _ladder, _live = _score(a, b, usage)
    assert out["ok"], out
    assert out["winner"] == "draw"
    edge = out["edge"]
    assert edge and edge["fighter"] == "model/a", edge
    assert edge["metric"] == "latency"
    assert edge["label"] == "efficiency edge (not a win)"
    assert "not a win" in edge["reason"]


def test_draw_edge_uses_output_tokens_then_cost():
    # Equal latency: A wins on fewer output tokens.
    usage = {
        "model/a": {"latency_s": 5.0, "output_tokens": 80, "cost_usd": 0.02},
        "model/b": {"latency_s": 5.0, "output_tokens": 120, "cost_usd": 0.01},
    }
    out, _, _ = _score(dict(REFS), dict(REFS), usage)
    assert out["edge"]["fighter"] == "model/a" and out["edge"]["metric"] == "output_tokens"
    # Equal latency + tokens: cheaper wins.
    usage = {
        "model/a": {"latency_s": 5.0, "output_tokens": 80, "cost_usd": 0.02},
        "model/b": {"latency_s": 5.0, "output_tokens": 80, "cost_usd": 0.01},
    }
    out, _, _ = _score(dict(REFS), dict(REFS), usage)
    assert out["edge"]["fighter"] == "model/b" and out["edge"]["metric"] == "cost"


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
    print(f"\n{len(tests) - failures}/{len(tests)} sudden-death/edge tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
