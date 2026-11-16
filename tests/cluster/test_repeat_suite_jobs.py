"""Tests for repeat_suite_jobs readiness and default waiting behavior."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "bench" / "cluster" / "runtime" / "repeat_suite_jobs.py"
GIB = 1024 ** 3


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def load_module(run_root):
    cluster_lib = types.ModuleType("cluster_lib")
    cluster_lib.RUN_ROOT = run_root
    cluster_lib.RUN_ID = "test-run"
    cluster_lib.utc_iso = lambda: "2026-11-16T00:00:00+00:00"

    reset_suite_groups = types.ModuleType("reset_suite_groups")
    reset_suite_groups.reset = Mock(name="reset")

    run_suite = types.ModuleType("run_suite")
    run_suite.save = write_json

    spec = importlib.util.spec_from_file_location("repeat_suite_jobs_test_module", RUNTIME)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "cluster_lib": cluster_lib,
        "reset_suite_groups": reset_suite_groups,
        "run_suite": run_suite,
    }):
        spec.loader.exec_module(module)
    return module, reset_suite_groups.reset


class RepeatSuiteJobsBlockerTests(unittest.TestCase):
    def test_unfinished_current_condition_blocks_readiness(self):
        plan = {"jobs": [{"id": "current-job"}]}
        status = {"jobs": {"current-job": {"state": "running"}}}

        result = load_module(Path("."))[0].blockers(
            plan, status, [], [], types.SimpleNamespace(free=20 * GIB, total=100 * GIB)
        )

        self.assertEqual(result["unfinished"], ["current-job"])
        self.assertEqual(result["active_services"], [])
        self.assertTrue(result["disk_space_sufficient"])

    def test_missing_or_unfinished_required_jobs_block_readiness(self):
        plan = {"jobs": [{"id": "current-job"}]}
        status = {"jobs": {
            "current-job": {"state": "passed"},
            "unfinished-required": {"state": "failed"},
        }}

        result = load_module(Path("."))[0].blockers(
            plan,
            status,
            ["missing-required", "unfinished-required"],
            [],
            types.SimpleNamespace(free=20 * GIB, total=100 * GIB),
        )

        self.assertCountEqual(result["unfinished"], ["missing-required", "unfinished-required"])

    def test_active_services_block_readiness(self):
        plan = {"jobs": [{"id": "current-job"}]}
        status = {"jobs": {"current-job": {"state": "passed"}}}

        result = load_module(Path("."))[0].blockers(
            plan, status, [], ["diagnostic.service"],
            types.SimpleNamespace(free=20 * GIB, total=100 * GIB),
        )

        self.assertEqual(result["active_services"], ["diagnostic.service"])
        self.assertFalse(result["unfinished"])

    def test_insufficient_disk_blocks_readiness(self):
        plan = {"jobs": [{"id": "current-job"}]}
        status = {"jobs": {"current-job": {"state": "passed"}}}

        result = load_module(Path("."))[0].blockers(
            plan, status, [], [],
            types.SimpleNamespace(free=15 * GIB, total=100 * GIB),
        )

        self.assertFalse(result["disk_space_sufficient"])
        self.assertFalse(result["unfinished"])
        self.assertFalse(result["active_services"])

    def test_all_requirements_satisfied_allows_readiness(self):
        plan = {"jobs": [{"id": "current-job"}, {"id": "required-job"}]}
        status = {"jobs": {
            "current-job": {"state": "passed"},
            "required-job": {"state": "passed"},
        }}

        result = load_module(Path("."))[0].blockers(
            plan, status, ["required-job"], [],
            types.SimpleNamespace(free=20 * GIB, total=100 * GIB),
        )

        ready = (not result["unfinished"] and not result["active_services"]
                 and result["disk_space_sufficient"])
        self.assertTrue(ready)


class RepeatSuiteJobsMainTests(unittest.TestCase):
    def test_default_mode_writes_waiting_state_without_reset_or_service_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary) / "run"
            plan = {"jobs": [{"id": "selected-job", "group": "01-io"}]}
            status = {
                "current": None,
                "completed_runs": 0,
                "jobs": {"selected-job": {"state": "planned"}},
            }
            write_json(run_root / "plan.json", plan)
            write_json(run_root / "status.json", status)
            original_status = (run_root / "status.json").read_bytes()
            module, reset = load_module(run_root)
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 3, stdout="inactive\n", stderr="")

            with patch.object(module.sys, "argv", [str(RUNTIME), "--job", "selected-job",
                                                     "--label", "retry-01"]), \
                    patch.object(module.subprocess, "run", side_effect=fake_run), \
                    patch.object(module.shutil, "disk_usage",
                                 return_value=types.SimpleNamespace(free=20 * GIB, total=100 * GIB)):
                module.main()

            saved = json.loads(
                (run_root / "continuations" / "retry-01.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved, {
                "state": "waiting",
                "jobs": ["selected-job"],
                "unfinished": ["selected-job"],
                "active_services": [],
                "disk_space_sufficient": True,
            })
            self.assertEqual((run_root / "status.json").read_bytes(), original_status)
            self.assertFalse(any(command[0] == "systemd-run" for command in commands))
            self.assertEqual(commands, [["systemctl", "is-active", "ax-test-run.service"]])
            reset.assert_not_called()


if __name__ == "__main__":
    unittest.main()
