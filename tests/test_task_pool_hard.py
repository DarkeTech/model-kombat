"""Hard task-pool validation: every task in coding/debugging/sql must be mechanically
sound and discriminating.

Run with:  python tests/test_task_pool_hard.py

Assertions per task:
  * `reference` scores exactly 1.0 with allow_exec=True
  * the empty answer scores < 1.0
  * `common_wrong` (when present) scores < 1.0
  * ids are unique across the pool, follow `<lane>-NNN`, and the task lane matches its file
  * check type is code_exec or sql_exec, difficulty is 3 for the hard ids
Stdlib only (unittest); no pytest dependency.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine_checks import score_check  # noqa: E402

LANES = ("coding", "debugging", "sql")
HARD_IDS = {
    "coding": ["coding-009", "coding-010", "coding-011", "coding-012"],
    "debugging": ["debugging-007", "debugging-008", "debugging-009", "debugging-010"],
    "sql": ["sql-007", "sql-008", "sql-009", "sql-010"],
}


def load_lane(lane):
    path = ROOT / "tasks" / f"{lane}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def all_tasks():
    for lane in LANES:
        data = load_lane(lane)
        for task in data["tasks"]:
            yield lane, data, task


class TestTaskPoolHard(unittest.TestCase):
    def test_files_declare_their_lane(self):
        for lane in LANES:
            data = load_lane(lane)
            self.assertEqual(data["lane"], lane, f"{lane}.json declares {data['lane']}")
            self.assertIsInstance(data["tasks"], list)
            self.assertTrue(data["tasks"])

    def test_hard_ids_present(self):
        for lane, ids in HARD_IDS.items():
            present = {t["id"] for t in load_lane(lane)["tasks"]}
            for task_id in ids:
                self.assertIn(task_id, present, f"{lane}.json missing {task_id}")

    def test_ids_unique_and_well_formed(self):
        seen = {}
        for lane, _data, task in all_tasks():
            task_id = task["id"]
            self.assertNotIn(task_id, seen,
                             f"duplicate id {task_id} in {lane}.json and {seen.get(task_id)}")
            seen[task_id] = f"{lane}.json"
            self.assertRegex(task_id, r"^[a-z]+-\d{3}$")
            self.assertTrue(task_id.startswith(lane + "-"),
                            f"{task_id} does not belong in {lane}.json")

    def test_required_fields(self):
        for lane, _data, task in all_tasks():
            with self.subTest(task=task["id"]):
                self.assertEqual(task["lane"], lane)
                self.assertIn(task["difficulty"], (1, 2, 3))
                self.assertTrue(task["title"].strip())
                self.assertTrue(task["prompt"].strip())
                self.assertIn(task["check"]["type"], ("code_exec", "sql_exec"))
                self.assertIsInstance(task["reference"], str)
                self.assertTrue(task["reference"].strip())

    def test_hard_tasks_are_difficulty_3(self):
        by_lane = {lane: {t["id"]: t for t in load_lane(lane)["tasks"]} for lane in LANES}
        for lane, ids in HARD_IDS.items():
            for task_id in ids:
                with self.subTest(task=task_id):
                    self.assertEqual(by_lane[lane][task_id]["difficulty"], 3)

    def test_reference_scores_one(self):
        for lane, _data, task in all_tasks():
            with self.subTest(task=task["id"]):
                result = score_check(task["check"], task["reference"], True)
                self.assertEqual(result["status"], "scored",
                                 f"{task['id']} reference status={result['status']}: {result['detail']}")
                self.assertEqual(result["score"], 1.0,
                                 f"{task['id']} reference={result['score']}: {result['detail']}")

    def test_empty_answer_scores_below_one(self):
        for lane, _data, task in all_tasks():
            with self.subTest(task=task["id"]):
                result = score_check(task["check"], "", True)
                self.assertIsNotNone(result["score"], f"{task['id']}: {result['detail']}")
                self.assertLess(result["score"], 1.0,
                                f"{task['id']} empty answer scored {result['score']}")

    def test_common_wrong_scores_below_one(self):
        for lane, _data, task in all_tasks():
            if "common_wrong" not in task:
                continue
            with self.subTest(task=task["id"]):
                result = score_check(task["check"], task["common_wrong"], True)
                self.assertIsNotNone(result["score"],
                                     f"{task['id']}: {result['detail']}")
                self.assertLess(result["score"], 1.0,
                                f"{task['id']} common_wrong scored {result['score']} "
                                f"(task does not discriminate)")

    def test_hard_tasks_have_many_cases(self):
        for lane, ids in HARD_IDS.items():
            by_id = {t["id"]: t for t in load_lane(lane)["tasks"]}
            for task_id in ids:
                check = by_id[task_id]["check"]
                with self.subTest(task=task_id):
                    if check["type"] == "code_exec":
                        self.assertGreaterEqual(len(check["cases"]), 10)
                    else:
                        self.assertGreaterEqual(len(check["setup"]), 2)
                        self.assertGreaterEqual(len(check["expect_rows"]), 2)
                        self.assertTrue(check.get("ordered", False))


def main():
    counts = {}
    print("task pool validation (allow_exec=True)")
    for lane in LANES:
        tasks = load_lane(lane)["tasks"]
        hard = [t for t in tasks if t["id"] in HARD_IDS[lane]]
        refs = sum(1 for t in tasks if score_check(t["check"], t["reference"], True)["score"] == 1.0)
        wrongs = 0
        wrong_total = 0
        for t in tasks:
            if "common_wrong" in t:
                wrong_total += 1
                score = score_check(t["check"], t["common_wrong"], True)["score"]
                if score is not None and score < 1.0:
                    wrongs += 1
        counts[lane] = (len(tasks), len(hard), refs, wrongs, wrong_total)
        print(f"  {lane:10s} tasks={len(tasks):2d} hard={len(hard)} "
              f"reference==1.0={refs}/{len(tasks)} "
              f"common_wrong<1.0={wrongs}/{wrong_total}")

    total = sum(c[0] for c in counts.values())
    hard_total = sum(c[1] for c in counts.values())
    refs = sum(c[2] for c in counts.values())
    wrongs = sum(c[3] for c in counts.values())
    wrong_total = sum(c[4] for c in counts.values())
    print(f"  TOTAL      tasks={total} hard={hard_total} refs={refs}/{total} "
          f"wrong={wrongs}/{wrong_total}")

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTaskPoolHard)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
