"""Hard-pool (007-010) regression tests for extraction/summarization/reasoning.

Stdlib only. Uses the real scorer (engine_checks.score_check).
Run: python3 -m pytest tests/test_task_pool_hard2.py -v
  or: python3 tests/test_task_pool_hard2.py
"""
import json
import sys
from pathlib import Path

ROOT = Path("/home/themightyvh/src/model-kombat")
sys.path.insert(0, str(ROOT))
from engine_checks import score_check  # noqa: E402

LANES = ["extraction", "summarization", "reasoning"]
NEW_IDS = {f"{lane}-{n:03d}" for lane in LANES for n in range(7, 11)}
EXPECTED_CHECK_TYPE = {
    "extraction": "json_fields",
    "summarization": "constraints",
    "reasoning": "numeric",
}


def load_lane(lane):
    with open(ROOT / "tasks" / f"{lane}.json") as f:
        return json.load(f)


def new_tasks():
    out = []
    for lane in LANES:
        data = load_lane(lane)
        assert data["lane"] == lane, f"file lane mismatch: {data['lane']} != {lane}"
        for t in data["tasks"]:
            if t["id"] in NEW_IDS:
                out.append(t)
    return out


def score(task, answer):
    r = score_check(task["check"], answer, allow_exec=False)
    assert r["status"] == "scored", (task["id"], r)
    return r["score"]


def test_new_tasks_present_and_well_formed():
    tasks = new_tasks()
    assert len(tasks) == 12, f"expected 12 new tasks, found {len(tasks)}"
    ids = [t["id"] for t in tasks]
    assert len(set(ids)) == 12, f"duplicate ids: {ids}"
    assert set(ids) == NEW_IDS, f"id set mismatch: {sorted(set(ids) ^ NEW_IDS)}"
    for t in tasks:
        assert t["difficulty"] == 3, t["id"]
        assert t["lane"] in LANES, t["id"]
        # lane must match the file it lives in
        data = load_lane(t["lane"])
        assert t["id"] in [x["id"] for x in data["tasks"]], t["id"]
        for key in ("id", "lane", "difficulty", "title", "prompt", "check", "reference"):
            assert key in t, (t["id"], key)
        assert t["prompt"].strip(), t["id"]
        assert t["reference"].strip(), t["id"]
        assert t["check"]["type"] == EXPECTED_CHECK_TYPE[t["lane"]], t["id"]


def test_reference_scores_one():
    for t in new_tasks():
        s = score(t, t["reference"])
        assert s == 1.0, f"{t['id']} reference scored {s}"


def test_empty_scores_below_one():
    for t in new_tasks():
        s = score(t, "")
        assert s is not None and s < 1.0, f"{t['id']} empty scored {s}"


def test_common_wrong_scores_below_one():
    for t in new_tasks():
        assert "common_wrong" in t, t["id"]
        s = score(t, t["common_wrong"])
        assert s is not None and s < 1.0, f"{t['id']} common_wrong scored {s}"


if __name__ == "__main__":
    test_new_tasks_present_and_well_formed()
    print("test_new_tasks_present_and_well_formed: PASS (12 tasks, unique ids, lane=file)")
    test_reference_scores_one()
    print("test_reference_scores_one: PASS (12/12 reference == 1.0)")
    test_empty_scores_below_one()
    print("test_empty_scores_below_one: PASS (12/12 empty < 1.0)")
    test_common_wrong_scores_below_one()
    print("test_common_wrong_scores_below_one: PASS (12/12 common_wrong < 1.0)")
    print("4 tests, 12 tasks checked, all passed")
