"""Tests for live.py (SPEC §6). Plain stdlib asserts.

Run: python3 tests/test_live.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import live  # noqa: E402

CARD = {
    "title": "Ship the thing",
    "lanes": ["coding", "debugging"],
    "rounds": [
        {"id": "R1", "name": "Warm-up", "points": 1, "tasks": ["coding-001", "coding-002"]},
        {"id": "R2", "name": "Main Event", "points": 2, "tasks": ["coding-003"]},
    ],
    "sudden_death_task": "coding-009",
}

TASK_META = {
    "coding-001": {"lane": "coding", "title": "Leap year"},
    "coding-002": {"lane": "coding", "title": "Second largest"},
    "coding-003": {"lane": "debugging", "title": "Fix the race"},
    "coding-009": {"lane": "coding", "title": "Boss fight"},
}

# Total possible = 2*1 (R1) + 1*2 (R2) + 1 (SD) = 5 points.


def _state(td, **kwargs):
    kwargs.setdefault("card", CARD)
    kwargs.setdefault("fighter_a", "stealth/crouton-alpha")
    kwargs.setdefault("fighter_b", "deepseek/deepseek-v4.1-flash-1017")
    kwargs.setdefault("task_meta", TASK_META)
    kwargs.setdefault("fight_id", "20261110-101500")
    kwargs.setdefault("broadcast", False)
    return live.LiveState(Path(td), **kwargs)


def _read(td) -> dict:
    return json.loads((Path(td) / "live.json").read_text(encoding="utf-8"))


def test_read_live_defaults_and_corrupt():
    with tempfile.TemporaryDirectory() as td:
        missing = live.read_live(Path(td))
        assert missing["status"] == "idle"
        assert missing["hp"] == {"a": 100, "b": 100}
        assert missing["rounds"] == [] and missing["sudden_death"] is None
        assert missing["points"] == {"a": 0, "b": 0}
        assert missing["winner"] is None and missing["ko"] is False

        (Path(td) / "live.json").write_text("{broken", encoding="utf-8")
        corrupt = live.read_live(Path(td))
        assert corrupt["status"] == "idle"
        assert corrupt["hp"] == {"a": 100, "b": 100}


def test_init_builds_and_writes_valid_state():
    with tempfile.TemporaryDirectory() as td:
        state = _state(td)
        payload = _read(td)
        assert payload["status"] == "idle"
        assert payload["fight_id"] == "20261110-101500"
        assert payload["card"] == {"title": "Ship the thing", "lanes": ["coding", "debugging"]}
        assert payload["fighters"]["a"]["id"] == "stealth/crouton-alpha"
        assert payload["fighters"]["a"]["short"] == "Crouton"
        assert payload["fighters"]["b"]["short"] == "DS-Flash"
        assert payload["hp"] == {"a": 100, "b": 100}
        assert [r["id"] for r in payload["rounds"]] == ["R1", "R2"]
        assert payload["rounds"][0]["tasks"][0] == {
            "id": "coding-001",
            "lane": "coding",
            "title": "Leap year",
            "a": {"state": "waiting", "score": None, "latency_s": None, "cost_usd": 0.0, "detail": ""},
            "b": {"state": "waiting", "score": None, "latency_s": None, "cost_usd": 0.0, "detail": ""},
        }
        assert payload["sudden_death"]["id"] == "SD"
        assert payload["sudden_death"]["tasks"][0]["id"] == "coding-009"
        assert payload["updated"]
        assert state.snapshot() == payload


def test_hp_math_and_points():
    with tempfile.TemporaryDirectory() as td:
        state = _state(td)
        state.start()
        assert _read(td)["status"] == "fighting"

        state.task_started("R1", "coding-001", "a")
        assert _read(td)["rounds"][0]["state"] == "fighting"

        state.task_done("R1", "coding-001", "a", score=1.0, latency_s=2.5, cost_usd=0.001, detail="1/1")
        payload = _read(td)
        assert payload["points"] == {"a": 1.0, "b": 0.0}
        assert payload["hp"] == {"a": 100.0, "b": 80.0}  # lead 1 / total 5 = 20 damage

        state.task_done("R1", "coding-001", "b", score=0.0, latency_s=1.0, cost_usd=0.0005)
        payload = _read(td)
        assert payload["points"] == {"a": 1.0, "b": 0.0}
        assert payload["hp"] == {"a": 100.0, "b": 80.0}

        state.task_done("R1", "coding-002", "b", score=1.0)
        payload = _read(td)
        assert payload["points"] == {"a": 1.0, "b": 1.0}
        assert payload["hp"] == {"a": 100, "b": 100}

        state.task_done("R2", "coding-003", "a", score=1.0)  # weight 2
        payload = _read(td)
        assert payload["points"] == {"a": 3.0, "b": 1.0}
        assert payload["hp"] == {"a": 100, "b": 60.0}  # lead 2 / 5 = 40 damage

        state.task_done("SD", "coding-009", "b", score=1.0)  # weight 1
        payload = _read(td)
        assert payload["points"] == {"a": 3.0, "b": 2.0}
        assert payload["hp"] == {"a": 100.0, "b": 80.0}  # b still trails by 1 -> 20 damage


def test_hp_floor_zero_and_round_done():
    with tempfile.TemporaryDirectory() as td:
        state = _state(td)
        for task_id in ("coding-001", "coding-002"):
            state.task_done("R1", task_id, "a", score=1.0)
            state.task_done("R1", task_id, "b", score=0.0)
        state.task_done("R2", "coding-003", "a", score=1.0)
        state.task_done("R2", "coding-003", "b", score=0.0)
        state.task_done("SD", "coding-009", "a", score=1.0)
        state.task_done("SD", "coding-009", "b", score=0.0)
        payload = _read(td)
        assert payload["points"] == {"a": 5.0, "b": 0.0}
        assert payload["hp"] == {"a": 100, "b": 0}
        assert payload["rounds"][0]["state"] == "done"
        assert payload["rounds"][1]["state"] == "done"
        assert payload["sudden_death"]["state"] == "done"


def test_ko_forces_loser_to_zero():
    with tempfile.TemporaryDirectory() as td:
        state = _state(td)
        state.task_done("R1", "coding-001", "a", score=0.6)
        assert _read(td)["hp"]["b"] < 100
        state.finish(winner="b", ko=True, report_path="/tmp/report.html")
        payload = _read(td)
        assert payload["status"] == "done"
        assert payload["winner"] == "b" and payload["ko"] is True
        assert payload["hp"]["a"] == 0
        assert payload["report_path"] == "/tmp/report.html"

        # KO identified by model id also maps to the loser side.
        state2 = _state(td)
        state2.finish(winner="stealth/crouton-alpha", ko=True)
        assert _read(td)["hp"]["b"] == 0


def test_atomic_file_validity_under_rapid_updates():
    with tempfile.TemporaryDirectory() as td:
        state = _state(td)
        for i in range(250):
            side = "a" if i % 2 == 0 else "b"
            state.update_task(
                "R1",
                "coding-001",
                side,
                state="done",
                score=(i % 11) / 10.0,
                latency_s=i / 100.0,
                cost_usd=0.00001 * i,
                detail=f"update {i}",
            )
            payload = json.loads((Path(td) / "live.json").read_text(encoding="utf-8"))
            assert payload["status"] in {"idle", "fighting", "scoring", "done", "error"}
            assert len(payload["rounds"]) == 2
            assert payload["rounds"][0]["tasks"][0]["id"] == "coding-001"
        leftovers = list(Path(td).glob("live.json.*.tmp"))
        assert leftovers == [], leftovers


def test_broadcast_success_payload_and_failure_swallowed():
    with tempfile.TemporaryDirectory() as td:
        calls = []
        package = types.ModuleType("hermes_cli")
        package.__path__ = []  # type: ignore[attr-defined]
        events = types.ModuleType("hermes_cli.plugin_events")
        setattr(events, "broadcast_plugin_event", lambda plugin, name, payload: calls.append((plugin, name, payload)))
        old_pkg = sys.modules.get("hermes_cli")
        old_events = sys.modules.get("hermes_cli.plugin_events")
        sys.modules["hermes_cli"] = package
        sys.modules["hermes_cli.plugin_events"] = events
        try:
            state = _state(td, broadcast=True)
            assert calls, "expected an initial broadcast"
            assert calls[-1][0] == "model-kombat"
            assert calls[-1][1] == "fight.updated"
            assert calls[-1][2]["fight_id"] == "20261110-101500"

            setattr(events, "broadcast_plugin_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bus down")))
            state.task_done("R1", "coding-001", "a", score=1.0)  # must not raise
            assert _read(td)["points"]["a"] == 1.0
        finally:
            if old_pkg is None:
                sys.modules.pop("hermes_cli", None)
            else:
                sys.modules["hermes_cli"] = old_pkg
            if old_events is None:
                sys.modules.pop("hermes_cli.plugin_events", None)
            else:
                sys.modules["hermes_cli.plugin_events"] = old_events


def test_never_raises_on_bad_input():
    with tempfile.TemporaryDirectory() as td:
        state = live.LiveState(
            Path(td),
            card={"title": None, "rounds": [None, {"id": "R1", "points": "x", "tasks": [None, {"id": "t", "lane": "sql"}]}]},
            fighter_a={"id": "a/x"},
            fighter_b=None,
            fight_id=None,
            task_meta=None,
        )
        assert _read(td)["rounds"][0]["tasks"][0]["id"] == "t"
        state.update_task("nope", "nope", "a", score=1.0)
        state.update_task("R1", "t", "z", score=1.0)
        state.task_done(None, "t", "a", score=1.0)
        state.add_usage("q", cost_usd=1)
        state.set_status("bogus")
        state.fail("boom")
        assert _read(td)["status"] == "error"


def test_usage_result_helper_and_statuses():
    with tempfile.TemporaryDirectory() as td:
        state = _state(td)
        state.start()
        state.task_started("R1", "coding-001", "a")
        payload = _read(td)
        assert payload["rounds"][0]["tasks"][0]["a"]["state"] == "fighting"
        state.apply_task_result(
            "R1",
            "coding-001",
            "a",
            {"status": "scored", "score": 0.75, "latency_s": 4.2, "cost_usd": 0.0003, "detail": "3/4 cases"},
        )
        slot = _read(td)["rounds"][0]["tasks"][0]["a"]
        assert slot["state"] == "done" and slot["score"] == 0.75
        assert slot["detail"] == "3/4 cases"
        state.task_error("R1", "coding-001", "b", detail="timeout")
        state.task_skipped("R1", "coding-002", "b", detail="exec off")
        state.apply_task_result("R1", "coding-002", "a", {"status": "errored", "detail": "boom"})
        payload = _read(td)
        assert payload["rounds"][0]["tasks"][0]["b"]["state"] == "error"
        assert payload["rounds"][0]["tasks"][1]["b"]["state"] == "skipped"
        assert payload["rounds"][0]["tasks"][1]["a"]["state"] == "error"

        state.add_usage("a", cost_usd=0.01, tokens=120, latency_s=4.2)
        state.add_usage("a", cost_usd=0.02, tokens=80, latency_s=1.0)
        assert _read(td)["usage"]["a"] == {"cost_usd": 0.03, "tokens": 200, "latency_s": 5.2}

        state.set_status("scoring")
        assert _read(td)["status"] == "scoring"


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
    print(f"\n{len(tests) - failures}/{len(tests)} live tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
