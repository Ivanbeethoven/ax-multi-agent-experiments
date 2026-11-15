import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[2]
CHECK_SUITE_PATH = ROOT / "bench" / "cluster" / "runtime" / "check_suite.py"
SUITE_CONFIG_PATH = ROOT / "configs" / "experiment-suite.json"
SUITE_MODULE_PATH = ROOT / "bench" / "cluster" / "suite.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CheckSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.check_suite = load_module("cluster_check_suite_test_module", CHECK_SUITE_PATH)
        cls.suite_module = load_module("cluster_suite_plan_test_module", SUITE_MODULE_PATH)
        cls.suite_config = json.loads(SUITE_CONFIG_PATH.read_text(encoding="utf-8"))
        cls.plan_ids = {job["id"] for job in cls.suite_module.build(cls.suite_config)["jobs"]}

    def test_selected_jobs_are_39_current_plan_conditions(self):
        selected = self.check_suite.selected_jobs()
        self.assertEqual(len(selected), 39)
        self.assertEqual(len(set(selected)), 39)
        self.assertEqual(set(selected), self.plan_ids & set(selected))
        self.assertEqual(
            {group: len(self.check_suite.selected_jobs(group)) for group in self.check_suite.GROUPS},
            {"01-io": 16, "02-fork": 9, "03-packed": 11, "04-agent-replay": 3},
        )

    def test_group_selection_contains_only_that_groups_current_jobs(self):
        for group, indices in self.check_suite.GROUPS.items():
            with self.subTest(group=group):
                selected = self.check_suite.selected_jobs(group)
                expected = {f"{group}-{index:02d}-r1" for index in indices}
                self.assertEqual(set(selected), expected)
                self.assertTrue(set(selected) <= self.plan_ids)

    def test_group_argument_runs_only_selected_group_jobs(self):
        group = "02-fork"
        context = SimpleNamespace(RUN_ID="run-approved")
        inactive = subprocess.CompletedProcess(args=[], returncode=3, stdout="inactive\n", stderr="")
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        expected_jobs = self.check_suite.selected_jobs(group)
        with patch.object(
            self.check_suite.sys, "argv", [str(CHECK_SUITE_PATH), "--group", group]
        ), patch.dict(sys.modules, {"runtime_context": context}), patch.object(
            self.check_suite.subprocess, "run", side_effect=[inactive] + [completed] * len(expected_jobs)
        ) as run:
            self.check_suite.main()

        self.assertEqual(run.call_count, len(expected_jobs) + 1)
        actual_jobs = [call.args[0][-1] for call in run.call_args_list[1:]]
        self.assertEqual(actual_jobs, expected_jobs)

    def test_active_performance_service_prevents_any_check(self):
        context = SimpleNamespace(RUN_ID="run-approved")
        active = subprocess.CompletedProcess(args=[], returncode=0, stdout="active\n", stderr="")
        with patch.object(self.check_suite.sys, "argv", [str(CHECK_SUITE_PATH)]), \
             patch.dict(sys.modules, {"runtime_context": context}), \
             patch.object(self.check_suite.subprocess, "run", return_value=active) as run:
            with self.assertRaisesRegex(RuntimeError, "before the performance service starts"):
                self.check_suite.main()

        run.assert_called_once_with(
            ["systemctl", "is-active", "ax-run-approved"], capture_output=True, text=True
        )

    def test_failed_child_stops_before_the_next_job(self):
        context = SimpleNamespace(RUN_ID="run-approved")
        inactive = subprocess.CompletedProcess(args=[], returncode=3, stdout="inactive\n", stderr="")
        failure = subprocess.CalledProcessError(returncode=2, cmd=["python", "run_suite.py"])
        with patch.object(self.check_suite.sys, "argv", [str(CHECK_SUITE_PATH)]), \
             patch.dict(sys.modules, {"runtime_context": context}), \
             patch.object(self.check_suite.subprocess, "run", side_effect=[inactive, failure]) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                self.check_suite.main()

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[0],
            call(["systemctl", "is-active", "ax-run-approved"], capture_output=True, text=True),
        )
        self.assertEqual(run.call_args_list[1].args[0][-3:], ["--check", "--job", "01-io-01-r1"])


if __name__ == "__main__":
    unittest.main()
