"""Tests for Model Kombat solvers/tools (no Hermes host needed).

Run: python -m pytest tests/ -q   (or: python tests/test_scoring.py)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools  # noqa: E402

CROUTON_T4 = (
    "Stay hydrated with this reusable steel bottle for daily use. "
    "Double-wall insulation keeps drinks cold for hours. "
    "Simply refill, recap, and carry it anywhere daily."
)
CROUTON_T5 = (
    "On 14 March, the council voted 7-3 to approve the Riverwalk project. "
    "Led by Hartwell Builders, it costs $2.4 million over 18 months for 1.2 km "
    "of path. Mayor Lucia Chen said construction begins in June; Councillor "
    "Raj Patel opposed cost and consultation. Public consultation will be "
    "held in April."
)
CROUTON_T6 = '''```python
def is_leap_year(year: int) -> bool:
    """Return True if year is a Gregorian leap year."""
    if isinstance(year, bool) or not isinstance(year, int):
        raise TypeError("year must be an int (bool is not accepted)")
    if year <= 0:
        raise ValueError("year must be positive")
    return year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)
```'''
BAD_T6 = "```python\ndef is_leap_year(year):\n    return year % 4 == 0\n```"

TASKS = {t["id"]: t for t in tools._load_tasks()}


def test_list_tasks():
    out = json.loads(tools.kombat_list_tasks({}))
    assert out["ok"] and out["count"] == 6
    assert [t["id"] for t in out["tasks"]] == ["T1", "T2", "T3", "T4", "T5", "T6"]


def test_money_scorer():
    good = tools._score_one("money", "a) $44.30, b) $5.70, c) $12.20", TASKS["T1"], False)
    assert good["passed"] and good["score"] == 3
    bad = tools._score_one("money", "total $44.30", TASKS["T1"], False)
    assert not bad["passed"] and bad["score"] == 1


def test_format25_scorer():
    out = tools._score_one("format25", CROUTON_T4, TASKS["T4"], False)
    assert out["passed"], out["details"]
    wrong = tools._score_one("format25", "Stay amazing and best!", TASKS["T4"], False)
    assert not wrong["passed"]


def test_summary50_scorer():
    out = tools._score_one("summary50", CROUTON_T5, TASKS["T5"], False)
    assert out["passed"], out["details"]
    dropped = tools._score_one("summary50", "The council approved a project in March.", TASKS["T5"], False)
    assert not dropped["passed"]


def test_code_exec_passes_and_fails():
    good = tools._score_one("code_exec", CROUTON_T6, TASKS["T6"], True)
    assert good["passed"], good["details"]
    bad = tools._score_one("code_exec", BAD_T6, TASKS["T6"], True)
    assert not bad["passed"]
    skipped = tools._score_one("code_exec", CROUTON_T6, TASKS["T6"], False)
    assert skipped.get("skipped") is True


def test_score_answers_end_to_end(tmp_path=None):
    # Fighter A: perfect on T4/T5/T6; Fighter B: sloppy. A must win + K.O. on T6.
    out = json.loads(
        tools.kombat_score_answers(
            {
                "fighter_a": "crouton-alpha",
                "fighter_b": "sloppy-model",
                "answers_a": {"T4": CROUTON_T4, "T5": CROUTON_T5, "T6": CROUTON_T6},
                "answers_b": {"T4": "Stay amazing!", "T5": "A project was approved.", "T6": BAD_T6},
                "allow_code_execution": True,
                "render_report": False,
            }
        )
    )
    assert out["ok"], out
    assert out["winner"] == "crouton-alpha", out["points"]
    assert out["points"]["crouton-alpha"] > out["points"]["sloppy-model"]
    t6 = next(r for r in out["rounds"] if r["id"] == "T6")
    assert t6["a"]["passed"] and not t6["b"]["passed"]


def test_run_fight_without_host_ctx():
    out = json.loads(
        tools.kombat_run_fight({"fighter_a": "a/b", "fighter_b": "c/d"})
    )
    assert out["ok"] is False  # no ctx bound outside Hermes — must degrade, not crash


if __name__ == "__main__":
    test_list_tasks()
    test_money_scorer()
    test_format25_scorer()
    test_summary50_scorer()
    test_code_exec_passes_and_fails()
    test_score_answers_end_to_end()
    test_run_fight_without_host_ctx()
    print("all Model Kombat tests passed")
