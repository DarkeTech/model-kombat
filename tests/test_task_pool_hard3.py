"""Hard-pool (007-010) regression tests for instruction and recall.

Stdlib only. Uses the real scorer (engine_checks.score_check).
Run: python3 -m pytest tests/test_task_pool_hard3.py -v
  or: python3 tests/test_task_pool_hard3.py
"""
import json
import sys
from pathlib import Path

ROOT = Path("/home/themightyvh/src/model-kombat")
sys.path.insert(0, str(ROOT))
from engine_checks import score_check  # noqa: E402

LANES = ["instruction", "recall"]
NEW_IDS = {
    "instruction": ["instruction-007", "instruction-008", "instruction-009"],
    "recall": ["recall-007", "recall-008", "recall-009", "recall-010"],
}
EXPECTED_CHECK_TYPE = {
    "instruction": "constraints",
    "recall": "phrases",
}


def load_lane(lane):
    with open(ROOT / "tasks" / f"{lane}.json") as f:
        return json.load(f)


def new_tasks():
    out = []
    for lane in LANES:
        data = load_lane(lane)
        assert data["lane"] == lane, f"file lane mismatch: {data['lane']} != {lane}"
        by_id = {t["id"]: t for t in data["tasks"]}
        for task_id in NEW_IDS[lane]:
            assert task_id in by_id, f"{lane}.json missing {task_id}"
            out.append(by_id[task_id])
    return out


def score(task, answer):
    r = score_check(task["check"], answer, allow_exec=False)
    assert r["status"] == "scored", (task["id"], r)
    return r["score"]


def test_new_tasks_present_and_well_formed():
    tasks = new_tasks()
    assert len(tasks) == 7, f"expected 7 new tasks, found {len(tasks)}"
    ids = [t["id"] for t in tasks]
    assert len(set(ids)) == 7, f"duplicate ids: {ids}"
    for t in tasks:
        assert t["difficulty"] == 3, t["id"]
        assert t["lane"] in LANES, t["id"]
        data = load_lane(t["lane"])
        assert t["id"] in [x["id"] for x in data["tasks"]], t["id"]
        for key in ("id", "lane", "difficulty", "title", "prompt", "check", "reference"):
            assert key in t, (t["id"], key)
        assert t["prompt"].strip(), t["id"]
        assert t["reference"].strip(), t["id"]
        assert t["check"]["type"] == EXPECTED_CHECK_TYPE[t["lane"]], t["id"]
        if t["lane"] == "recall":
            assert "answer only from the passage" in t["prompt"], t["id"]
            passage = t["prompt"].split("---")[1]
            wc = len(passage.split())
            assert 250 <= wc <= 350, f"{t['id']} passage has {wc} words"
            assert 4 <= len(t["check"]["expected"]) <= 5, t["id"]
            for group in t["check"]["expected"]:
                assert len(group) >= 1 and all(g.strip() for g in group), t["id"]


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
    print("test_new_tasks_present_and_well_formed: PASS (7 tasks, unique ids, lane=file)")
    test_reference_scores_one()
    print("test_reference_scores_one: PASS (7/7 reference == 1.0)")
    test_empty_scores_below_one()
    print("test_empty_scores_below_one: PASS (7/7 empty < 1.0)")
    test_common_wrong_scores_below_one()
    print("test_common_wrong_scores_below_one: PASS (7/7 common_wrong < 1.0)")
    print("4 tests, 7 tasks checked, all passed")
