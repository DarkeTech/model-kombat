"""Tests for render.py winner-side HIT highlight (plain stdlib asserts).

Run: python3 tests/test_render.py  (or: python3 -m pytest tests/test_render.py)

The HIT highlight must follow the WINNING side: cyan (won-a) for fighter A,
magenta (won-b) for fighter B, and draws must be neutral (no won-* class).
No trademark-adjacent 'insert coin' / 'press start' strings may appear.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render import render_arena_preview, render_report  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 3 fixtures covering: A win + draw (mid), B win (wide), A win + KO (ko).
FIXTURE_NAMES = ("live_sample_mid.json", "live_sample_wide.json", "live_sample_ko.json")

# New engine-payload fixtures:
NEW_FIXTURES = {
    "draw_edge": "live_sample_draw_edge.json",
    "sudden_death": "live_sample_sudden_death.json",
    "budget_hit": "live_sample_budget_hit.json",
}
ALL_FIXTURE_NAMES = FIXTURE_NAMES + tuple(NEW_FIXTURES.values()) + ("live_sample_error.json",)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _rows(html: str) -> list[str]:
    return re.findall(r"<tr>.*?</tr>", html, flags=re.S)


def _row_for(html: str, task_id: str) -> str:
    for row in _rows(html):
        if f"<b>{task_id}</b>" in row:
            return row
    raise AssertionError(f"task row not found: {task_id}")


def test_arena_winner_side_classes():
    for name in FIXTURE_NAMES:
        html = render_arena_preview(_load(name))
        # CSS defines BOTH winner-side highlights with opposite colors.
        assert "td.won-a::before" in html and "var(--cyan)" in html, name
        assert "td.won-b::before" in html and "var(--magenta)" in html, name
        # No generic always-cyan class left.
        assert "td.won{" not in html and 'class="won hit"' not in html, name
        # Every fixture has an A win (coding-002: 1.0 vs 0.5).
        assert 'class="won-a hit"' in html, name
        # No trademark-adjacent strings.
        low = html.lower()
        assert "insert coin" not in low and "press start" not in low, name


def test_arena_b_win_and_draw_neutral():
    wide = render_arena_preview(_load("live_sample_wide.json"))
    # sql-002: A 0.5 vs B 1.0 -> B (magenta) wins.
    assert 'class="won-b hit"' in wide
    b_row = _row_for(wide, "sql-002")
    assert 'class="won-b hit"' in b_row and 'class="won-a hit"' not in b_row

    mid = render_arena_preview(_load("live_sample_mid.json"))
    # coding-002 / debugging-004: A wins -> cyan won-a, never won-b.
    for task_id in ("coding-002", "debugging-004"):
        row = _row_for(mid, task_id)
        assert 'class="won-a hit"' in row, task_id
        assert 'class="won-b hit"' not in row, task_id
    # coding-001: 1.0 vs 1.0 -> draw, neutral: no HIT highlight at all.
    draw_row = _row_for(mid, "coding-001")
    assert "won-a" not in draw_row and "won-b" not in draw_row and "HIT" not in draw_row


def test_report_winner_side_classes():
    rounds_out = [{
        "id": "R1", "name": "Warm-up", "points": 1,
        "tasks": [
            {"id": "coding-001", "title": "Draw",
             "a": {"score": 1, "max": 1, "details": []},
             "b": {"score": 1, "max": 1, "details": []}},
            {"id": "coding-002", "title": "A wins",
             "a": {"score": 1, "max": 1, "details": []},
             "b": {"score": 0, "max": 1, "details": []}},
            {"id": "coding-003", "title": "B wins",
             "a": {"score": 0, "max": 1, "details": []},
             "b": {"score": 1, "max": 1, "details": []}},
        ],
    }]
    html = render_report("Fighter A", "Fighter B", rounds_out, {}, "Fighter A", False)
    assert 'class="won-a hit"' in _row_for(html, "coding-002")
    assert 'class="won-b hit"' in _row_for(html, "coding-003")
    draw_row = _row_for(html, "coding-001")
    assert "won-a" not in draw_row and "won-b" not in draw_row
    low = html.lower()
    assert "insert coin" not in low and "press start" not in low


def test_null_cost_and_tokens():
    draw = render_arena_preview(_load(NEW_FIXTURES["draw_edge"]))
    # A: cost_usd null -> n/a (never $0.0000 for that fighter); B: estimated.
    assert "n/a" in draw
    assert "$0.0012 est." in draw
    assert "11000" in draw and "7000" in draw  # input / output tokens

    budget = render_arena_preview(_load(NEW_FIXTURES["budget_hit"]))
    assert "n/a" in budget and "$0.0011 est." in budget
    assert "7600" in budget and "4800" in budget

    # Scorecard path: same cost rules.
    usage = {
        "Fighter A": {"cost_usd": None, "cost_source": "unknown", "tokens": 10,
                      "input_tokens": 6, "output_tokens": 4, "latency_s": 1.0},
        "Fighter B": {"cost_usd": 0.002, "cost_source": "estimated", "tokens": 12,
                      "input_tokens": 7, "output_tokens": 5, "latency_s": 2.0},
    }
    html = render_report("Fighter A", "Fighter B", [], usage, "Fighter A", False)
    assert "n/a" in html and "$0.0020 est." in html


def test_draw_edge_only_on_draw():
    for name in ALL_FIXTURE_NAMES:
        html = render_arena_preview(_load(name))
        if name == NEW_FIXTURES["draw_edge"]:
            assert "efficiency edge" in html, name
            assert "efficiency edge: Crouton" in html, name
            assert '<div class="banner draw">DRAW</div>' in html, name
            # No winner card, no K.O. styling on a draw.
            assert "WINNER" not in html and '<div class="winner-card">' not in html, name
            assert '<span class="ko">' not in html, name
        else:
            assert "efficiency edge" not in html, name

    # Scorecard path: draw + edge, no winner card.
    edge = {"fighter": "Fighter A", "metric": "latency",
            "reason": "faster total latency (1.0s vs 2.0s)",
            "label": "efficiency edge (not a win)", "a": 1.0, "b": 2.0}
    html = render_report("Fighter A", "Fighter B", [], {}, "draw", False, edge=edge)
    assert "efficiency edge" in html and "DRAW" in html
    assert '<div class="winner-card">' not in html and "WINNER" not in html
    # Non-draw scorecard never shows the edge line.
    html2 = render_report("Fighter A", "Fighter B", [], {}, "Fighter A", False, edge=edge)
    assert "efficiency edge" not in html2


def test_sudden_death_only_on_sd_fixture():
    for name in ALL_FIXTURE_NAMES:
        html = render_arena_preview(_load(name))
        if name == NEW_FIXTURES["sudden_death"]:
            assert "SUDDEN DEATH" in html, name
            assert "debugging-006" in html, name  # the single SD task
            assert "Crouton takes the task" in html, name  # its outcome
        else:
            assert "SUDDEN DEATH" not in html, name

    # SD fought up-front but unused (waiting) must NOT appear.
    unused = _load(NEW_FIXTURES["sudden_death"])
    unused["sudden_death"] = {
        "id": "SD", "name": "Sudden Death", "points": 1, "state": "waiting",
        "tasks": [{"id": "debugging-006", "lane": "debugging", "title": "Repair the retry loop",
                   "a": {"state": "waiting", "score": None, "detail": "queued"},
                   "b": {"state": "waiting", "score": None, "detail": "queued"}}],
    }
    unused.pop("sudden_death_winner", None)
    assert "SUDDEN DEATH" not in render_arena_preview(unused)

    # Scorecard path: SD round + sudden_death_winner.
    rounds_out = [{"id": "SD", "name": "Sudden Death", "points": 0, "tasks": [
        {"id": "debugging-006", "title": "Repair the retry loop",
         "a": {"score": 1, "max": 1, "details": []},
         "b": {"score": 0, "max": 1, "details": []}}]}]
    html = render_report("Fighter A", "Fighter B", rounds_out, {}, "Fighter A", False,
                         sudden_death_winner="Fighter A")
    assert "SUDDEN DEATH" in html and "debugging-006" in html
    # No SD round / winner -> no banner.
    assert "SUDDEN DEATH" not in render_report("Fighter A", "Fighter B", [], {}, "Fighter A", False)


def test_budget_cap_only_on_budget_fixture():
    for name in ALL_FIXTURE_NAMES:
        html = render_arena_preview(_load(name))
        if name == NEW_FIXTURES["budget_hit"]:
            assert "BUDGET CAP HIT — remaining tasks skipped" in html, name
            assert "SKIPPED" in html, name
        else:
            assert "BUDGET CAP" not in html, name

    budget = {"exceeded": True, "reason": "USD cap $0.0020 exceeded",
              "cap_usd": 0.002, "cap_tokens": 400000, "spent_usd": 0.0023, "spent_tokens": 21000}
    html = render_report("Fighter A", "Fighter B", [], {}, "Fighter A", False, budget=budget)
    assert "BUDGET CAP HIT — remaining tasks skipped" in html
    assert "BUDGET CAP" not in render_report("Fighter A", "Fighter B", [], {}, "Fighter A", False)


def test_incomplete_asterisk_and_note():
    budget = render_arena_preview(_load(NEW_FIXTURES["budget_hit"]))
    assert "Main Event *" in budget
    assert "* round excluded from points (errored/skipped)" in budget
    for name in ALL_FIXTURE_NAMES:
        if name == NEW_FIXTURES["budget_hit"]:
            continue
        assert "* round excluded from points (errored/skipped)" not in render_arena_preview(_load(name)), name

    rounds_out = [{"id": "R2", "name": "Main Event", "points": 2, "incomplete": True,
                   "tasks": [{"id": "t1", "title": "T",
                              "a": {"score": 0, "max": 1, "details": [], "errored": True},
                              "b": {"score": 0, "max": 1, "details": [], "errored": True}}]}]
    html = render_report("Fighter A", "Fighter B", rounds_out, {}, "draw", False,
                         incomplete=True, incomplete_rounds=["R2"])
    assert "Main Event *" in html
    assert "* round excluded from points (errored/skipped)" in html


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
    print(f"\n{len(tests) - failures}/{len(tests)} render tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
