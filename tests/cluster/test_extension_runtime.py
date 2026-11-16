"""Exercise the idle-suite extension coordinator without starting services."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from bench.cluster import suite


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "bench" / "cluster" / "runtime" / "extend_suite_when_idle.py"
RUN_SUITE_ENTRYPOINT = RUNTIME.with_name("run_suite.py")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def load_runtime_module(name, cluster_lib, run_suite, build, extend):
    spec = importlib.util.spec_from_file_location(name, RUNTIME)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    original_path = list(sys.path)
    try:
        with patch.dict(sys.modules, {"cluster_lib": cluster_lib, "run_suite": run_suite}), \
                patch.object(suite, "build", build), \
                patch.object(suite, "extend", extend):
            spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
    return module


class ExtendSuiteRuntimeTests(unittest.TestCase):
    added_ids = [f"01-io-{index:02d}-r1" for index in range(17, 21)]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.run_root = root / "run"
        self.repo_root = root / "repo"
        self.config = {"approval": "approved", "run_id": "run-test"}
        write_json(self.repo_root / "configs" / "experiment-suite.json", self.config)
        write_json(
            self.run_root / "status.json",
            {
                "run_id": "run-test",
                "completed_runs": 52,
                "total_runs": 52,
                "jobs": {
                    f"existing-{index:02d}": {"state": "passed"}
                    for index in range(1, 53)
                },
            },
        )

        self.cluster_lib = types.ModuleType("cluster_lib")
        self.cluster_lib.RUN_ROOT = self.run_root
        self.cluster_lib.REPO_ROOT = self.repo_root
        self.cluster_lib.RUN_ID = "run-test"
        self.plan = {"run_id": "run-test", "total_runs": 56}
        self.build = Mock(name="build", return_value=self.plan)
        self.extend = Mock(name="extend", return_value=list(self.added_ids))
        self.saved_states = []

        run_suite = types.ModuleType("run_suite")

        def save(path, value):
            self.saved_states.append(copy.deepcopy(value))
            write_json(path, value)

        run_suite.save = save
        self.module = load_runtime_module(
            "extend_suite_when_idle_test_module",
            self.cluster_lib,
            run_suite,
            self.build,
            self.extend,
        )

    def inactive_service(self, command, **_kwargs):
        return subprocess.CompletedProcess(command, 3, stdout="inactive\n", stderr="")

    def test_checks_all_added_jobs_before_starting_when_old_run_and_services_are_clear(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if command[0] == "systemctl":
                return self.inactive_service(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch.object(self.module.sys, "argv", [str(RUNTIME), "--wait-service", "diag.service"]), \
                patch.object(self.module.subprocess, "run", side_effect=run), \
                patch.object(self.module.time, "sleep") as sleep:
            self.module.main()

        commands = [command for command, _kwargs in calls]
        self.assertEqual(commands[:2], [
            ["systemctl", "is-active", "ax-run-test.service"],
            ["systemctl", "is-active", "diag.service"],
        ])
        checks = [command for command in commands if "--check" in command]
        self.assertEqual(
            checks,
            [
                [sys.executable, str(RUN_SUITE_ENTRYPOINT), "--check", "--job", job_id]
                for job_id in self.added_ids
            ],
        )
        start_index = next(index for index, command in enumerate(commands) if command[0] == "systemd-run")
        check_indices = [index for index, command in enumerate(commands) if "--check" in command]
        self.assertEqual(len(check_indices), 4)
        self.assertTrue(all(index < start_index for index in check_indices))
        self.assertEqual(start_index, len(commands) - 1)
        self.assertTrue(all(kwargs.get("check") is True for _, kwargs in calls[2:]))
        sleep.assert_not_called()

        self.build.assert_called_once_with(self.config)
        self.extend.assert_called_once_with(self.plan, self.run_root)
        self.assertEqual(self.saved_states[1], {"state": "checking", "added": self.added_ids})
        self.assertEqual(self.saved_states[2], {"state": "starting", "added": self.added_ids})
        self.assertEqual(self.saved_states[3], {"state": "started", "added": self.added_ids})
        state_path = self.run_root / "extensions" / "state.json"
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), self.saved_states[-1])

    def test_failed_job_check_prevents_formal_suite_start(self):
        calls = []
        failed_id = self.added_ids[2]

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if "--check" in command and command[-1] == failed_id:
                raise subprocess.CalledProcessError(2, command)
            if command[0] == "systemctl":
                return self.inactive_service(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch.object(self.module.sys, "argv", [str(RUNTIME)]), \
                patch.object(self.module.subprocess, "run", side_effect=run), \
                patch.object(self.module.time, "sleep") as sleep:
            with self.assertRaises(subprocess.CalledProcessError):
                self.module.main()

        commands = [command for command, _kwargs in calls]
        checks = [command for command in commands if "--check" in command]
        self.assertEqual(
            [command[-1] for command in checks],
            self.added_ids[:3],
        )
        self.assertFalse(any(command[0] == "systemd-run" for command in commands))
        self.assertTrue(all(kwargs.get("check") is True for _, kwargs in calls[2:]))
        sleep.assert_not_called()
        self.build.assert_called_once_with(self.config)
        self.extend.assert_called_once_with(self.plan, self.run_root)
        self.assertEqual(self.saved_states[-1], {"state": "checking", "added": self.added_ids})
        saved_state = json.loads(
            (self.run_root / "extensions" / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved_state, self.saved_states[-1])


if __name__ == "__main__":
    unittest.main()
