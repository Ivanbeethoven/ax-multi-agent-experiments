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


def load_module(name, path, replacements=None):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, replacements or {}):
        spec.loader.exec_module(module)
    return module


class ContinueSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.run_root = base / "run"
        self.repo_root = base / "repo"
        self.measured = self.run_root / "jobs" / "01-io-04-r1" / "attempt-01"
        write_json(self.run_root / "status.json", {
            "jobs": {"01-io-04-r1": {"output": str(self.measured)}},
        })
        write_json(self.measured / "result.json", {
            "timing": {"app_done_seconds": 12.5},
            "application": {"latency_us": {"p50": 100}},
        })
        write_json(self.repo_root / "configs" / "mount-profiles.json", {
            "brewfs_environment": {"TOKIO_WORKER_THREADS": "8"},
        })
        self.cluster_lib = types.ModuleType("cluster_lib")
        self.cluster_lib.RUN_ROOT = self.run_root
        self.cluster_lib.REPO_ROOT = self.repo_root
        self.run_suite = types.ModuleType("run_suite")
        self.run_suite.save = write_json
        self.module = load_module(
            "continue_suite_test_module",
            ROOT / "bench/cluster/runtime/continue_suite.py",
            {"cluster_lib": self.cluster_lib, "run_suite": self.run_suite},
        )

    def test_runs_fio_then_sampling_off_calibration_then_remaining_groups(self):
        calls = []

        def fake_run(command, check):
            calls.append(command)
            if any(str(part).endswith("run_fio_candidate.py") for part in command):
                output = Path(command[command.index("--out") + 1])
                write_json(output / "summary.json", {"results": [{
                    "timing": {"app_done_seconds": 10.0},
                    "application": {"latency_us": {"p50": 80}},
                }]})

        with patch.object(self.module.subprocess, "run", side_effect=fake_run) as run:
            self.module.main()

        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1:], [
            str(ROOT / "bench/cluster/runtime/run_suite.py"), "--group", "01-io",
        ])
        self.assertTrue(any(str(part).endswith("run_fio_candidate.py") for part in calls[1]))
        self.assertEqual(calls[1][calls[1].index("--job") + 1], "01-io-04-r1")
        self.assertEqual(calls[1][calls[1].index("--periodic") + 1], "off")
        self.assertEqual(calls[1][calls[1].index("--workers") + 1], "8")
        self.assertEqual(calls[2][1:], [str(ROOT / "bench/cluster/runtime/run_suite.py")])
        self.assertTrue(all(run.call_args_list[index].kwargs["check"] for index in range(3)))

        report = self.run_root / "calibration" / "sampling-attempt-01" / "comparison.json"
        comparison = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(comparison["on_seconds"], 12.5)
        self.assertEqual(comparison["off_seconds"], 10.0)
        self.assertEqual(comparison["on_latency_us"], {"p50": 100})
        self.assertEqual(comparison["off_latency_us"], {"p50": 80})

    def test_existing_comparison_skips_sampling_calibration(self):
        report = self.run_root / "calibration" / "sampling-attempt-01" / "comparison.json"
        write_json(report, {"completed": True})
        calls = []

        with patch.object(self.module.subprocess, "run", side_effect=lambda command, check: calls.append(command)):
            self.module.main()

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(not any(str(part).endswith("run_fio_candidate.py") for part in call)
                            for call in calls))
        self.assertEqual(json.loads(report.read_text(encoding="utf-8")), {"completed": True})


class StartSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module(
            "start_suite_test_module",
            ROOT / "infra/idcai/start_suite.py",
        )

    def test_calibrate_after_io_selects_continue_entrypoint(self):
        with patch.object(sys, "argv", ["start_suite.py", "--calibrate-after-io"]), \
                patch.object(self.module.subprocess, "run") as run:
            self.module.main()

        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0][:2], ["ssh", "idcai-60001"])
        self.assertTrue(kwargs["check"])
        remote_script = kwargs["input"].decode("utf-8")
        self.assertIn("continue_suite.py", remote_script)
        self.assertIn("group=None", remote_script)

    def test_calibration_flag_rejects_a_single_group(self):
        with patch.object(sys, "argv", [
            "start_suite.py", "--group", "01-io", "--calibrate-after-io",
        ]), patch.object(self.module.subprocess, "run") as run:
            with self.assertRaises(SystemExit) as raised:
                self.module.main()

        self.assertEqual(raised.exception.code, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
