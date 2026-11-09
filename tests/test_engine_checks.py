"""Tests for the v0.4 mechanical check-spec scorers and hardened code runner.

Run: python3 tests/test_engine_checks.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine_checks as ec  # noqa: E402


def run(check, answer, allow=False):
    return ec.score_check(check, answer, allow)


# ------------------------------------------------------------------ exact

def test_exact():
    check = {"type": "exact", "expected": "42", "normalize": True}
    assert run(check, "  42 \n")["score"] == 1.0
    assert run(check, "The answer is\n42")["score"] == 1.0
    assert run(check, "41")["score"] == 0.0
    assert run(check, "41")["status"] == "scored"
    # case-insensitive normalization
    assert run({"type": "exact", "expected": "Rose"}, "  rose  ")["score"] == 1.0
    # malformed spec -> errored, never raised
    bad = run({"type": "exact"}, "42")
    assert bad["status"] == "errored" and bad["score"] is None


# ------------------------------------------------------------------ numeric

def test_numeric():
    check = {"type": "numeric", "expected": [44.3, 5.7, 12.2], "tol": 0.01}
    assert run(check, "total $44.30, change $5.70, refund $12.20")["score"] == 1.0
    partial = run(check, "total 44.3 only")
    assert partial["score"] == round(1 / 3, 6) and partial["status"] == "scored"
    assert run(check, "nothing here")["score"] == 0.0
    # negative numbers + tolerance
    neg = {"type": "numeric", "expected": [-3.5], "tol": 0.1}
    assert run(neg, "delta -3.52")["score"] == 1.0
    assert run({"type": "numeric", "expected": "x"}, "1")["status"] == "errored"
    assert run({"type": "numeric", "expected": [], "tol": 0.1}, "1")["status"] == "errored"


# ------------------------------------------------------------------ phrases

def test_phrases():
    check = {"type": "phrases", "expected": [["sol 43"], ["tomas beck"],
                                             ["no injuries", "no one was injured"]]}
    answer = "Serviced  on SOL   43 by Tomas Beck. No one was injured."
    assert run(check, answer)["score"] == 1.0
    partial = run(check, "Sol 43 by Tomas Beck. One person was injured.")
    assert partial["score"] == round(2 / 3, 6)
    assert run(check, "")["score"] == 0.0
    assert run({"type": "phrases", "expected": []}, "x")["status"] == "errored"
    assert run({"type": "phrases", "expected": [[], "ok"]}, "ok")["status"] == "errored"


# ------------------------------------------------------------------ regex

def test_regex():
    check = {"type": "regex", "pattern": r"^\d{4}-\d{2}-\d{2}$", "flags": "im"}
    assert run(check, "2026-01-02")["score"] == 1.0
    assert run(check, "not a date")["score"] == 0.0
    assert run({"type": "regex", "pattern": "hello", "flags": "i"}, "HELLO there")["score"] == 1.0
    # must_match: false inverts the verdict
    assert run({"type": "regex", "pattern": "secret", "must_match": False}, "clean")["score"] == 1.0
    assert run({"type": "regex", "pattern": "secret", "must_match": False}, "secret")["score"] == 0.0
    assert run({"type": "regex", "pattern": "("}, "x")["status"] == "errored"
    assert run({"type": "regex"}, "x")["status"] == "errored"


# ------------------------------------------------------------------ constraints

def test_constraints():
    check = {
        "type": "constraints",
        "words": {"min": 25, "max": 25},
        "sentences": 3,
        "starts_with": "Stay",
        "ends_with": ".",
        "banned": ["amazing"],
        "contains": ["bottle"],
    }
    good = (
        "Stay hydrated with this reusable steel bottle for daily use. "
        "Double-wall insulation keeps drinks cold for hours. "
        "Simply refill, recap, and carry it anywhere daily."
    )
    assert run(check, good)["score"] == 1.0
    partial = run(check, "Stay amazing with this bottle. Second sentence. Third sentence.")
    assert partial["status"] == "scored"
    assert 0.0 < partial["score"] < 1.0
    assert partial["score"] == round(4 / 6, 6)
    assert run({"type": "constraints"}, "x")["status"] == "errored"
    assert run({"type": "constraints", "banned": "amazing"}, "x")["status"] == "errored"
    # Decimal points are not sentence boundaries.
    assert run({"type": "constraints", "sentences": 1}, "Pi is 3.14 today.")["score"] == 1.0
    assert run({"type": "constraints", "sentences": 3}, "One. Two. Three.")["score"] == 1.0


# ------------------------------------------------------------------ json_fields

def test_json_fields():
    check = {
        "type": "json_fields",
        "expected": {"vendor": "Acme", "total": 123.45, "dates": ["2026-01-02"],
                     "meta": {"ok": True}},
        "tol": 0.01,
    }
    answer = 'Sure:\n```json\n{"vendor": "Acme", "total": 123.46, "dates": ["2026-01-02"], "meta": {"ok": true}}\n```'
    assert run(check, answer)["score"] == 1.0
    bare = 'result {"vendor": "Acme", "total": 123.45, "dates": ["2026-01-02"], "meta": {"ok": true}} end'
    assert run(check, bare)["score"] == 1.0
    partial = run(check, '{"vendor": "Acme"}')
    assert partial["score"] == round(1 / 4, 6)
    no_json = run(check, "no object at all")
    assert no_json["status"] == "scored" and no_json["score"] == 0.0
    assert run({"type": "json_fields", "expected": "x"}, "{}")["status"] == "errored"
    # wrong type is not "close enough"
    wrong_type = run({"type": "json_fields", "expected": {"ok": True}},
                     '{"ok": 1}')
    assert wrong_type["score"] == 0.0


# ------------------------------------------------------------------ code_exec

ADD_CODE = "```python\ndef add(a, b):\n    return a + b\n```"

ADD_CHECK = {
    "type": "code_exec",
    "entrypoint": "add",
    "cases": [{"args": [1, 2], "expect": 3}, {"args": [-1, 1], "expect": 0}],
    "timeout_s": 5,
}


def test_code_exec_skipped_and_malformed():
    skipped = run(ADD_CHECK, ADD_CODE, allow=False)
    assert skipped["status"] == "skipped" and skipped["score"] is None
    assert run({"type": "code_exec", "entrypoint": "add"}, ADD_CODE, True)["status"] == "errored"
    assert run({"type": "code_exec", "cases": [{"expect": 1}]}, ADD_CODE, True)["status"] == "errored"
    assert run({"type": "code_exec", "entrypoint": "add", "cases": [{"args": [1]}]},
               ADD_CODE, True)["status"] == "errored"
    no_code = run(ADD_CHECK, "I refuse to write code", True)
    assert no_code["status"] == "scored" and no_code["score"] == 0.0


def test_code_exec_pass_partial_fail():
    passed = run(ADD_CHECK, ADD_CODE, True)
    assert passed["score"] == 1.0, passed

    partial_check = {
        "type": "code_exec",
        "entrypoint": "add",
        "cases": [{"args": [1, 2], "expect": 3},
                  {"args": [2, 2], "expect": 5},
                  {"args": [0, 0], "expect": 0}],
    }
    partial = run(partial_check, ADD_CODE, True)
    assert partial["score"] == round(2 / 3, 6), partial
    assert "failed" in partial["detail"]

    bad_code = "```python\ndef add(a, b):\n    return -999\n```"
    failed = run(ADD_CHECK, bad_code, True)
    assert failed["score"] == 0.0, failed

    raises_check = {
        "type": "code_exec",
        "entrypoint": "boom",
        "cases": [{"args": ["x"], "raises": "TypeError"}],
    }
    raises_code = "```python\ndef boom(x):\n    raise TypeError('nope')\n```"
    assert run(raises_check, raises_code, True)["score"] == 1.0
    no_raise = "```python\ndef boom(x):\n    return x\n```"
    assert run(raises_check, no_raise, True)["score"] == 0.0

    syntax = "```python\ndef add(:\n```"
    broken = run(ADD_CHECK, syntax, True)
    assert broken["status"] == "scored" and broken["score"] == 0.0
    assert "failed to load" in broken["detail"]


def test_runner_infinite_loop_times_out():
    check = {
        "type": "code_exec",
        "entrypoint": "spin",
        "cases": [{"args": [], "expect": None}],
        "timeout_s": 2,
    }
    code = "```python\ndef spin():\n    while True:\n        pass\n```"
    started = time.monotonic()
    out = run(check, code, True)
    elapsed = time.monotonic() - started
    assert out["status"] == "scored" and out["score"] == 0.0, out
    assert "timed out" in out["detail"], out
    assert elapsed < 9, f"runner took {elapsed:.1f}s"


def test_runner_env_is_empty():
    check = {
        "type": "code_exec",
        "entrypoint": "env_empty",
        "cases": [{"args": [], "expect": True}],
        "timeout_s": 5,
    }
    code = "```python\nimport os\ndef env_empty():\n    return len(os.environ) == 0\n```"
    out = run(check, code, True)
    assert out["score"] == 1.0, out


def test_runner_allows_writes_inside_tempdir():
    check = {
        "type": "code_exec",
        "entrypoint": "scratch",
        "cases": [{"args": [], "expect": True}],
        "timeout_s": 5,
    }
    code = (
        "```python\nimport os, tempfile\n"
        "def scratch():\n"
        "    with open('local.txt', 'w') as fh:\n"
        "        fh.write('ok')\n"
        "    fd, path = tempfile.mkstemp()\n"
        "    os.close(fd)\n"
        "    os.unlink(path)\n"
        "    return os.path.exists('local.txt')\n"
        "```"
    )
    assert run(check, code, True)["score"] == 1.0


def test_runner_blocks_writes_outside_tempdir():
    leak = Path(tempfile.gettempdir()) / f"kombat-leak-probe-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = (
        "```python\n"
        "def leak():\n"
        f"    with open({str(leak)!r}, 'w') as fh:\n"
        "        fh.write('should not leak')\n"
        "    return True\n"
        "```"
    )
    out = run(check, code, True)
    assert out["score"] == 1.0, out
    assert not leak.exists(), f"sandbox leaked a write to {leak}"


def test_runner_blocks_process_spawn():
    check = {
        "type": "code_exec",
        "entrypoint": "spawn",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = (
        "```python\nimport subprocess\n"
        "def spawn():\n"
        "    subprocess.run(['true'], check=False)\n"
        "    return True\n"
        "```"
    )
    assert run(check, code, True)["score"] == 1.0


# ------------------------------------------------------------------ sql_exec

SQL_SETUP = ["CREATE TABLE t (a INTEGER, b TEXT)",
             "INSERT INTO t VALUES (1, 'a'), (2, 'b')"]


def test_sql_exec_pass_fail():
    check = {"type": "sql_exec", "setup": SQL_SETUP,
             "expect_rows": [[1, "a"], [2, "b"]], "ordered": True}
    assert run(check, "```sql\nSELECT a, b FROM t ORDER BY a\n```")["score"] == 1.0
    wrong = run(check, "```sql\nSELECT a, b FROM t ORDER BY a DESC\n```")
    assert wrong["score"] == 0.0

    unordered = {"type": "sql_exec", "setup": SQL_SETUP,
                 "expect_rows": [[1, "a"], [2, "b"]], "ordered": False}
    assert run(unordered, "```sql\nSELECT a, b FROM t ORDER BY a DESC\n```")["score"] == 1.0
    assert run(unordered, "```sql\nSELECT a FROM t\n```")["score"] == 0.0


def test_sql_exec_cte_and_rejections():
    cte = {"type": "sql_exec", "setup": SQL_SETUP,
           "expect_rows": [[2]], "ordered": True}
    assert run(cte, "```sql\nWITH big AS (SELECT a FROM t WHERE a > 1) SELECT a FROM big\n```")["score"] == 1.0

    for sql in ("INSERT INTO t VALUES (3, 'c')",
                "UPDATE t SET b = 'z'",
                "DROP TABLE t",
                "PRAGMA query_only = OFF",
                "ATTACH DATABASE ':memory:' AS x"):
        out = run({"type": "sql_exec", "setup": SQL_SETUP, "expect_rows": []},
                  f"```sql\n{sql}\n```")
        assert out["status"] == "scored" and out["score"] == 0.0, (sql, out)
        assert "rejected" in out["detail"], (sql, out)


def test_sql_exec_malformed():
    no_sql = run({"type": "sql_exec", "setup": SQL_SETUP, "expect_rows": []}, "no code")
    assert no_sql["status"] == "scored" and no_sql["score"] == 0.0
    assert run({"type": "sql_exec", "setup": "bad", "expect_rows": []},
               "```sql\nSELECT 1\n```")["status"] == "errored"
    assert run({"type": "sql_exec", "setup": SQL_SETUP, "expect_rows": "bad"},
               "```sql\nSELECT 1\n```")["status"] == "errored"
    broken_setup = run({"type": "sql_exec", "setup": ["CREATE TABLE ("],
                        "expect_rows": []}, "```sql\nSELECT 1\n```")
    assert broken_setup["status"] == "errored"
    query_error = run({"type": "sql_exec", "setup": SQL_SETUP, "expect_rows": []},
                      "```sql\nSELECT nope FROM missing\n```")
    assert query_error["status"] == "scored" and query_error["score"] == 0.0


# ------------------------------------------------------------------ meta

def test_unknown_type_and_never_raises():
    assert run({"type": "made_up"}, "x")["status"] == "errored"
    assert run("not a dict", "x")["status"] == "errored"
    # ints, None, weird objects as answers must not explode
    for answer in (None, 123, b"bytes"):
        out = ec.score_check({"type": "exact", "expected": "x"}, answer, False)  # type: ignore[arg-type]
        assert out["status"] in ("scored", "errored")
    for check in ({"type": "exact", "expected": "x", "normalize": "yes"},
                  {"type": "numeric", "expected": [1], "tol": -1},
                  {"type": "json_fields", "expected": {}, "tol": 0},
                  {"type": "sql_exec", "setup": [], "expect_rows": [], "ordered": "no"}):
        out = ec.score_check(check, "x", False)
        assert out["status"] in ("scored", "errored")
        assert set(out) == {"score", "status", "detail"}


if __name__ == "__main__":
    test_exact()
    test_numeric()
    test_phrases()
    test_regex()
    test_constraints()
    test_json_fields()
    test_code_exec_skipped_and_malformed()
    test_code_exec_pass_partial_fail()
    test_runner_infinite_loop_times_out()
    test_runner_env_is_empty()
    test_runner_allows_writes_inside_tempdir()
    test_runner_blocks_writes_outside_tempdir()
    test_runner_blocks_process_spawn()
    test_sql_exec_pass_fail()
    test_sql_exec_cte_and_rejections()
    test_sql_exec_malformed()
    test_unknown_type_and_never_raises()
    print("all engine_checks tests passed")
