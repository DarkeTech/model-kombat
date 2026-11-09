"""Slash-command tests: /kombat fight flags + /kombat scout hand-off (defect 5).

Run: python3 tests/test_slash.py
"""

import atexit
import importlib.util
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


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "model_kombat_plugin", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["model_kombat_plugin"] = module
    spec.loader.exec_module(module)
    return module


PLUGIN = _load_plugin()


class _Ctx:
    def get_config(self, key, default=None):
        return default

    def spawn_task(self, coro, name=None):
        raise RuntimeError("no loop in tests")

    def state_get(self, key, default=None):
        return default


def _capture(payload):
    def fake_start(ctx, kind, fn, args):
        payload.update(args)
        return "captured"

    return fake_start


def test_split_fight_flags():
    phrase, flags = PLUGIN._split_fight_flags(
        "stealth/crouton-alpha vs deepseek/deepseek-v4.1-flash-1017 --card card-15b9 --lanes coding,sql --seed 9"
    )
    assert phrase == "stealth/crouton-alpha vs deepseek/deepseek-v4.1-flash-1017", phrase
    assert flags == {"card": "card-15b9", "lanes": "coding,sql", "seed": "9"}, flags


def test_fight_with_card_flag():
    captured = {}
    original = PLUGIN._start_job
    PLUGIN._start_job = _capture(captured)
    try:
        msg = PLUGIN._handle_kombat(_Ctx(), "fight a/model vs b/model --card card-15b9f4edc0")
    finally:
        PLUGIN._start_job = original
    assert msg == "captured"
    assert captured["fighter_a"] == "a/model" and captured["fighter_b"] == "b/model"
    assert captured["card"] == "card-15b9f4edc0", captured


def test_fight_with_lanes_and_seed_flags():
    captured = {}
    original = PLUGIN._start_job
    PLUGIN._start_job = _capture(captured)
    try:
        PLUGIN._handle_kombat(_Ctx(), "fight a/model vs b/model --lanes coding,sql --seed 7")
    finally:
        PLUGIN._start_job = original
    assert captured["card"] == {"lanes": ["coding", "sql"], "seed": 7}, captured


def test_fight_with_bad_seed_is_rejected():
    captured = {}
    original = PLUGIN._start_job
    PLUGIN._start_job = _capture(captured)
    try:
        msg = PLUGIN._handle_kombat(_Ctx(), "fight a/model vs b/model --lanes coding --seed nope")
    finally:
        PLUGIN._start_job = original
    assert "seed must be an integer" in msg
    assert captured == {}


def test_scout_prints_card_id_and_next_command():
    raw = json.dumps(
        {
            "ok": True,
            "card": {
                "id": "card-abc123",
                "title": "Kombat card — sql · coding",
                "lanes": ["sql", "coding"],
                "lane_weights": [{"lane": "sql", "weight": 3.0}, {"lane": "coding", "weight": 1.0}],
                "rounds": [{"id": "R1", "name": "Warm-up", "points": 1, "tasks": ["sql-001"]}],
                "sudden_death_task": "sql-002",
            },
        }
    )
    text = PLUGIN._scout_summary(raw)
    assert "card-abc123" in text
    assert "R1 Warm-up" in text and "sql-001" in text
    assert "Next: /kombat fight " in text
    assert "--card card-abc123" in text


def test_scout_task_id_heuristic():
    assert PLUGIN._looks_like_task_id("t1")
    assert PLUGIN._looks_like_task_id("t_ab12cd")
    assert PLUGIN._looks_like_task_id("task-1234")
    assert PLUGIN._looks_like_task_id("0123abcd-4567")
    assert not PLUGIN._looks_like_task_id("fix the parser bug")
    assert not PLUGIN._looks_like_task_id("debugging")


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
    print(f"\n{len(tests) - failures}/{len(tests)} slash tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
