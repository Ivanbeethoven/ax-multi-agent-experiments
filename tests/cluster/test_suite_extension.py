"""Check that approved jobs can be appended without disturbing an existing run."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from bench.cluster import suite


ROOT = Path(__file__).resolve().parents[2]
SUITE_CONFIG = ROOT / "configs" / "experiment-suite.json"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class SuiteExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.new_suite = json.loads(SUITE_CONFIG.read_text(encoding="utf-8"))
        cls.new_plan = suite.build(cls.new_suite)
        cls.old_suite = copy.deepcopy(cls.new_suite)
        cls.old_suite["groups"]["01-io"]["configurations"] = (
            cls.old_suite["groups"]["01-io"]["configurations"][:16]
        )
        cls.old_suite["groups"]["01-io"]["runs"] = 16
        cls.old_plan = suite.build(cls.old_suite)

    def create_old_run(self, directory):
        write_json(directory / "plan.json", self.old_plan)
        status = {
            "run_id": self.old_plan["run_id"],
            "completed_runs": 0,
            "total_runs": self.old_plan["total_runs"],
            "jobs": {job["id"]: {"state": "planned"} for job in self.old_plan["jobs"]},
        }
        write_json(directory / "status.json", status)
        return status

    def assert_extension_rejected_without_changes(self, directory, plan, message):
        before_plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
        before_status = json.loads((directory / "status.json").read_text(encoding="utf-8"))

        with self.assertRaisesRegex(ValueError, message):
            suite.extend(plan, directory)

        self.assertEqual(
            json.loads((directory / "plan.json").read_text(encoding="utf-8")),
            before_plan,
        )
        self.assertEqual(
            json.loads((directory / "status.json").read_text(encoding="utf-8")),
            before_status,
        )
        self.assertFalse((directory / "plan.json.tmp").exists())
        self.assertFalse((directory / "status.json.tmp").exists())

    def test_extends_52_jobs_to_56_and_preserves_existing_jobs_results_and_status(self):
        self.assertEqual(self.old_plan["total_runs"], 52)
        self.assertEqual(self.new_plan["total_runs"], 56)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            status = self.create_old_run(directory)
            for job in self.old_plan["jobs"][:33]:
                status["jobs"][job["id"]] = {
                    "state": "passed",
                    "elapsed_seconds": 12.5,
                    "note": "retain this status row",
                }
            status["completed_runs"] = 33
            write_json(directory / "status.json", status)

            result_job = self.old_plan["jobs"][4]
            result_marker = {"state": "passed", "result_marker": "keep-existing-result"}
            result_path = directory / result_job["output"] / "result.json"
            write_json(result_path, result_marker)
            old_status_rows = copy.deepcopy(status["jobs"])

            added = suite.extend(self.new_plan, directory)

            expected_added = [f"01-io-{index:02d}-r1" for index in range(17, 21)]
            self.assertEqual(added, expected_added)
            expanded_plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
            expanded_status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(expanded_plan["total_runs"], 56)
            self.assertEqual(len(expanded_plan["jobs"]), 56)
            self.assertEqual(len({job["id"] for job in expanded_plan["jobs"]}), 56)
            jobs_by_id = {job["id"]: job for job in expanded_plan["jobs"]}
            self.assertEqual(
                [jobs_by_id[job["id"]] for job in self.old_plan["jobs"]],
                self.old_plan["jobs"],
            )
            self.assertEqual(expanded_status["run_id"], self.old_plan["run_id"])
            self.assertEqual(expanded_status["completed_runs"], 33)
            self.assertEqual(expanded_status["total_runs"], 56)
            for job_id, row in old_status_rows.items():
                self.assertEqual(expanded_status["jobs"][job_id], row)
            for job_id in expected_added:
                self.assertEqual(expanded_status["jobs"][job_id], {"state": "planned"})
                job = jobs_by_id[job_id]
                self.assertEqual(
                    json.loads((directory / job["output"] / "job.json").read_text(encoding="utf-8")),
                    job,
                )
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), result_marker)

    def test_rejects_modified_existing_job_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            self.create_old_run(directory)
            changed_plan = copy.deepcopy(self.new_plan)
            original_id = self.old_plan["jobs"][0]["id"]
            changed_job = next(job for job in changed_plan["jobs"] if job["id"] == original_id)
            changed_job["parameters"]["operation"] = "write"

            self.assert_extension_rejected_without_changes(
                directory, changed_plan, "Existing conditions must remain unchanged"
            )

    def test_rejects_removed_existing_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            self.create_old_run(directory)
            changed_plan = copy.deepcopy(self.new_plan)
            removed_id = self.old_plan["jobs"][0]["id"]
            changed_plan["jobs"] = [job for job in changed_plan["jobs"] if job["id"] != removed_id]

            self.assert_extension_rejected_without_changes(
                directory, changed_plan, "Existing conditions must remain unchanged"
            )

    def test_rejects_running_job_before_extending(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            status = self.create_old_run(directory)
            running_id = self.old_plan["jobs"][0]["id"]
            status["jobs"][running_id] = {"state": "running", "output": "current-attempt"}
            write_json(directory / "status.json", status)

            self.assert_extension_rejected_without_changes(
                directory, self.new_plan, "Wait for the running condition"
            )

    def test_extension_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            self.create_old_run(directory)

            first_added = suite.extend(self.new_plan, directory)
            second_added = suite.extend(self.new_plan, directory)

            self.assertEqual(first_added, [f"01-io-{index:02d}-r1" for index in range(17, 21)])
            self.assertEqual(second_added, [])
            extended_plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
            extended_status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(extended_plan["total_runs"], 56)
            self.assertEqual(len(extended_plan["jobs"]), 56)
            self.assertEqual(len({job["id"] for job in extended_plan["jobs"]}), 56)
            self.assertEqual(extended_status["total_runs"], 56)
            self.assertEqual(len(extended_status["jobs"]), 56)


if __name__ == "__main__":
    unittest.main()
