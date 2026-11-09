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
