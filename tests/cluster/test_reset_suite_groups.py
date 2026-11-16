import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def load_reset_module():
    cluster_lib = types.ModuleType("cluster_lib")
    run_suite = types.ModuleType("run_suite")
    run_suite.save = write_json
    spec = importlib.util.spec_from_file_location(
        "reset_suite_groups_test_module",
        ROOT / "bench/cluster/runtime/reset_suite_groups.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"cluster_lib": cluster_lib, "run_suite": run_suite}):
        spec.loader.exec_module(module)
    return module


class ResetSuiteGroupsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reset_module = load_reset_module()

    def make_run(self, root):
        plan = {
            "jobs": [
                {"id": "selected-1", "group": "01-io", "output": "jobs/selected-1"},
                {"id": "other-1", "group": "02-fork", "output": "jobs/other-1"},
            ]
        }
        status = {
            "current": "selected-1",
            "completed_runs": 2,
            "jobs": {
                "selected-1": {"state": "passed", "attempt": 1},
                "other-1": {"state": "passed", "attempt": 3},
            },
        }
        write_json(root / "plan.json", plan)
        write_json(root / "status.json", status)
        write_json(root / "inventory.json", {"host": "test-host"})
        write_json(root / "config" / "settings.json", {"setting": "old"})
        write_json(root / "versions" / "runtime.json", {"version": "old"})

        for job_id in ("selected-1", "other-1"):
            output = root / "jobs" / job_id
            write_json(output / "result.json", {"job": job_id, "state": "passed"})
            write_json(output / "attempt-01" / "source-record.json", {"job": job_id})
        write_json(root / "01-io" / "summary.json", {"group": "01-io"})
        write_json(root / "02-fork" / "summary.json", {"group": "02-fork"})
        return plan, status

    def attempt_files(self, root):
        files = {}
        for job_id in ("selected-1", "other-1"):
            output = root / "jobs" / job_id
            for attempt_dir in output.glob("attempt-*"):
                for path in attempt_dir.rglob("*"):
                    if path.is_file():
                        files[path.relative_to(root).as_posix()] = path.read_bytes()
        return files

    def test_reset_archives_prior_state_and_preserves_attempt_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            original_plan, original_status = self.make_run(root)

            archive = self.reset_module.reset(root, ["01-io"], "retry-01")

            self.assertEqual(archive, root / "archive" / "results" / "retry-01")
            self.assertEqual(json.loads((archive / "plan.json").read_text(encoding="utf-8")),
                             original_plan)
            self.assertEqual(json.loads((archive / "status.json").read_text(encoding="utf-8")),
                             original_status)
            self.assertEqual(json.loads((archive / "config" / "settings.json").read_text(encoding="utf-8")),
                             {"setting": "old"})
            self.assertEqual(json.loads((archive / "jobs" / "selected-1" / "result.json").read_text(
                encoding="utf-8")), {"job": "selected-1", "state": "passed"})
            self.assertTrue((root / "config" / "settings.json").is_file())
            self.assertEqual(json.loads((root / "jobs" / "selected-1" / "attempt-01" /
                                         "source-record.json").read_text(encoding="utf-8")),
                             {"job": "selected-1"})
            self.assertFalse((root / "jobs" / "selected-1" / "result.json").exists())
            self.assertTrue((root / "jobs" / "other-1" / "result.json").is_file())
            self.assertFalse((root / "01-io" / "summary.json").exists())
            self.assertTrue((root / "02-fork" / "summary.json").is_file())

            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertIsNone(status["current"])
            self.assertEqual(status["jobs"]["selected-1"], {"state": "planned"})
            self.assertEqual(status["jobs"]["other-1"], original_status["jobs"]["other-1"])
            self.assertEqual(status["completed_runs"], 1)
            selection = json.loads((archive / "selection.json").read_text(encoding="utf-8"))
            self.assertEqual(selection["groups"], ["01-io"])
            self.assertEqual(selection["conditions"], [
                {"job": "selected-1", "previous": original_status["jobs"]["selected-1"]},
            ])

    def test_reset_rejects_running_jobs_without_changing_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            self.make_run(root)
            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            status["jobs"]["selected-1"] = {"state": "running"}
            write_json(root / "status.json", status)

            with self.assertRaisesRegex(ValueError, "Finish the current condition"):
                self.reset_module.reset(root, ["01-io"], "retry-01")

            self.assertFalse((root / "archive").exists())
            self.assertTrue((root / "jobs" / "selected-1" / "result.json").is_file())

    def test_reset_rejects_invalid_archive_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            self.make_run(root)

            with self.assertRaisesRegex(ValueError, "Archive label"):
                self.reset_module.reset(root, ["01-io"], "../invalid")

            self.assertFalse((root / "archive").exists())

    def test_reset_rejects_unknown_groups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            self.make_run(root)

            with self.assertRaisesRegex(ValueError, "Unknown experiment group"):
                self.reset_module.reset(root, ["missing-group"], "retry-01")

            self.assertFalse((root / "archive").exists())

    def test_reset_one_job_preserves_same_group_sibling_and_archives_group_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            plan, original_status = self.make_run(root)
            plan["jobs"][1]["group"] = "01-io"
            write_json(root / "plan.json", plan)

            for job_id in ("selected-1", "other-1"):
                output = root / "jobs" / job_id
                write_json(output / "attempt-02" / "loader.json", {"job": job_id})
                (output / "attempt-02" / "raw-output.bin").write_bytes(b"original-" + job_id.encode())
            original_attempt_files = self.attempt_files(root)

            archive = self.reset_module.reset(root, [], "retry-selected", jobs=["selected-1"])

            self.assertEqual(
                json.loads((archive / "selection.json").read_text(encoding="utf-8"))["groups"],
                ["01-io"],
            )
            self.assertEqual(
                json.loads((archive / "selection.json").read_text(encoding="utf-8"))["conditions"],
                [{"job": "selected-1", "previous": original_status["jobs"]["selected-1"]}],
            )
            self.assertEqual(
                json.loads((archive / "jobs" / "selected-1" / "result.json").read_text(
                    encoding="utf-8")),
                {"job": "selected-1", "state": "passed"},
            )
            self.assertFalse((root / "jobs" / "selected-1" / "result.json").exists())
            self.assertEqual(
                json.loads((root / "jobs" / "other-1" / "result.json").read_text(
                    encoding="utf-8")),
                {"job": "other-1", "state": "passed"},
            )
            self.assertTrue((archive / "01-io" / "summary.json").is_file())
            self.assertFalse((root / "01-io" / "summary.json").exists())
            self.assertTrue((root / "02-fork" / "summary.json").is_file())
            self.assertEqual(self.attempt_files(root), original_attempt_files)

            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["jobs"]["selected-1"], {"state": "planned"})
            self.assertEqual(status["jobs"]["other-1"], original_status["jobs"]["other-1"])
            self.assertEqual(status["completed_runs"], 1)

    def test_reset_rejects_unknown_job_without_changing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            self.make_run(root)
            before = {path.relative_to(root).as_posix(): path.read_bytes()
                      for path in root.rglob("*") if path.is_file()}

            with self.assertRaisesRegex(ValueError, "Unknown experiment condition"):
                self.reset_module.reset(root, [], "retry-01", jobs=["missing-job"])

            after = {path.relative_to(root).as_posix(): path.read_bytes()
                     for path in root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((root / "archive").exists())

    def test_reset_rejects_mixed_group_and_job_selection_without_changing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            self.make_run(root)
            before = {path.relative_to(root).as_posix(): path.read_bytes()
                      for path in root.rglob("*") if path.is_file()}

            with self.assertRaisesRegex(ValueError, "Select either groups or individual conditions"):
                self.reset_module.reset(root, ["01-io"], "retry-01", jobs=["selected-1"])

            after = {path.relative_to(root).as_posix(): path.read_bytes()
                     for path in root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((root / "archive").exists())


if __name__ == "__main__":
    unittest.main()
