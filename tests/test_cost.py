"""Cost/pricing + budget-cap tests (defect 1).

Run: python3 tests/test_cost.py
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


class _Usage:
    def __init__(self, cost=None):
        self.input_tokens = 10
        self.output_tokens = 20
        self.total_tokens = 30
        self.cost_usd = cost


class _Result:
    def __init__(self, text="answer", cost=None):
        self.text = text
        self.usage = _Usage(cost)


class _Llm:
    def complete(self, messages, **kwargs):
        return _Result()


class _State:
    def __init__(self):
        self.store = {}

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)


class _Ctx:
    def __init__(self, config=None):
        self.llm = _Llm()
        self.state = _State()
        self.config = config or {"show_on_kanban": False}

    def get_config(self, key, default=None):
        return self.config.get(key, default)


def _run(args, config=None):
    tools.set_ctx(_Ctx(config))
    try:
        with tempfile.TemporaryDirectory() as td:
            tools.set_data_dir_override(Path(td))
            try:
                return json.loads(tools.kombat_run_fight(args))
            finally:
                tools.set_data_dir_override(None)
    finally:
        tools.set_ctx(None)


def test_prices_loaded_and_estimated():
    usage = tools._usage_of(_Result(cost=None), "deepseek/deepseek-v4.1-flash-1017")
    # 10 in * 0.15/1M + 20 out * 0.60/1M
    assert usage["cost_source"] == "estimated", usage
    assert abs(usage["cost_usd"] - (10 * 0.15 + 20 * 0.60) / 1_000_000) < 1e-12, usage
    assert usage["input_tokens"] == 10 and usage["output_tokens"] == 20


def test_host_cost_wins():
    usage = tools._usage_of(_Result(cost=0.001), "deepseek/deepseek-v4.1-flash-1017")
    assert usage["cost_source"] == "host" and usage["cost_usd"] == 0.001, usage


def test_unknown_price_is_none_not_zero():
    usage = tools._usage_of(_Result(cost=None), "ghost/no-price")
    assert usage["cost_usd"] is None, usage
    assert usage["cost_source"] == "unknown", usage
    cost, source = tools._estimate_cost({"input_tokens": 1, "output_tokens": 2}, "ghost/no-price")
    assert cost is None and source == "unknown"


def test_model_prices_setting_overrides_shipped():
    tools.set_ctx(_Ctx({"show_on_kanban": False, "model_prices": {"ghost/x": {"in": 2.0, "out": 4.0}}}))
    try:
        cost, source = tools._estimate_cost(
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000}, "ghost/x"
        )
    finally:
        tools.set_ctx(None)
    assert source == "estimated" and cost == 6.0, (cost, source)


def test_token_budget_stops_and_skips_remaining_pairs():
    out = _run(
        {
            "fighter_a": "fake/a",
            "fighter_b": "fake/b",
            "tasks": ["T1"],
            "budget_tokens": 25,
            "budget_usd": 50,
        }
    )
    assert out["ok"], out
    assert out["budget_exceeded"] is True
    assert "token cap" in (out["budget"]["reason"] or ""), out["budget"]
    # First call (A) spent 30 tokens; B and sudden death were never attempted.
    assert out["usage"]["fake/a"]["tokens"] == 30
    assert out["usage"]["fake/a"]["cost_usd"] is None, out["usage"]
    assert out["usage"]["fake/a"]["cost_source"] == "unknown"
    assert out["budget"]["spent_tokens"] == 30
    assert out["usage"]["fake/b"]["tokens"] == 0
    assert out["meta_b"]["T1"]["status"] == "skipped", out["meta_b"]
    assert out["meta_a"]["T8"]["status"] == "skipped"
    assert out["meta_b"]["T8"]["status"] == "skipped"
    assert all(e.get("status") == "skipped" for e in out["errors"]), out["errors"]
    assert "failed" not in json.dumps(out["errors"]).lower()


def test_usd_budget_stops_with_estimated_cost():
    config = {
        "show_on_kanban": False,
        "model_prices": {
            "fake/priced-a": {"in": 1000.0, "out": 1000.0},
            "fake/priced-b": {"in": 1000.0, "out": 1000.0},
        },
    }
    out = _run(
        {
            "fighter_a": "fake/priced-a",
            "fighter_b": "fake/priced-b",
            "tasks": ["T1"],
            "budget_usd": 0.01,
            "budget_tokens": 10_000_000,
        },
        config,
    )
    assert out["ok"], out
    assert out["budget_exceeded"] is True
    assert "USD cap" in (out["budget"]["reason"] or ""), out["budget"]
    assert out["usage"]["fake/priced-a"]["cost_source"] == "estimated"
    assert abs(out["usage"]["fake/priced-a"]["cost_usd"] - 0.03) < 1e-9, out["usage"]
    assert out["meta_b"]["T1"]["status"] == "skipped"


def test_usage_has_input_output_split():
    out = _run({"fighter_a": "fake/a", "fighter_b": "fake/b", "tasks": ["T1"], "budget_usd": 50})
    assert out["ok"], out
    slot = out["usage"]["fake/a"]
    assert slot["input_tokens"] == 20 and slot["output_tokens"] == 40, slot
    assert slot["tokens"] == 60


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
    print(f"\n{len(tests) - failures}/{len(tests)} cost tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
