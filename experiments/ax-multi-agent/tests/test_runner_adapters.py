"""Protocol and failure-containment tests; these are not filesystem benchmarks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.adapter import Adapter
from harness.run_cell import preflight, run, validate_cell
from harness import baseline_cli


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspaces"
        self.workspace.mkdir()
        self.script = self.root / "fake.py"

    def tearDown(self):
        self.temporary.cleanup()

    def adapter(self, code, extra_args=None):
        self.script.write_text(code, encoding="utf-8")
        config = {"schema_version": 1, "baseline_id": "B1", "execution_scope": "real",
                  "state": "configured", "operations": {"create": {"argv": [sys.executable, str(self.script)] + (extra_args or [])}},
                  "default_timeout_s": 5}
        return Adapter(config, log_dir=self.root / "logs", workspace_root=self.workspace)

    def test_unsupported_never_falls_back(self):
        adapter = self.adapter("raise AssertionError('must not execute')")
        result = adapter.call("migrate", {})
        self.assertEqual(result["status"], "unsupported")
        self.assertIsNone(result["telemetry"])
        self.assertIsNone(result["logs"])

    def test_disabled_adapter_never_executes(self):
        adapter = self.adapter("raise AssertionError('must not execute')")
        adapter.config["state"] = "not_configured"
        self.assertEqual(adapter.call("create", {})["status"], "unsupported")

    def test_argv_metacharacters_are_literal(self):
        literal = "literal & | ; $(whoami) `echo hello` with spaces"
        adapter = self.adapter("import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'status':'ok','scope':'real','data':{'arg':sys.argv[1],'value':r['payload']['value']},'evidence':{'test':True}}))", [literal])
        result = adapter.call("create", {"value": literal})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"], {"arg": literal, "value": literal})
        self.assertIsNone(result["telemetry"])

    def test_invalid_json_and_no_command_output_in_error(self):
        adapter = self.adapter("print('password=DO_NOT_ECHO')")
        result = adapter.call("create", {})
        self.assertEqual(result["error"], "adapter_invalid_json")
        self.assertNotIn("DO_NOT_ECHO", json.dumps(result))
        self.assertIn("DO_NOT_ECHO", Path(result["logs"]["stdout"]).read_text())

    def test_nonzero_exit_even_if_json_says_ok(self):
        adapter = self.adapter("import sys\nprint('{\"status\":\"ok\",\"scope\":\"real\",\"evidence\":{\"test\":true}}')\nsys.exit(3)")
        result = adapter.call("create", {})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["error"], "adapter_nonzero_exit")

    def test_timeout_kills_descendant(self):
        marker = self.root / "must-not-exist.txt"
        child = self.root / "child.py"
        child.write_text("import time,pathlib,sys\ntime.sleep(1.2)\npathlib.Path(sys.argv[1]).write_text('survived')", encoding="utf-8")
        adapter = self.adapter("import subprocess,sys,time\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\ntime.sleep(10)", [str(child), str(marker)])
        result = adapter.call("create", {}, timeout_s=0.4)
        self.assertEqual(result["error"], "adapter_timeout")
        time.sleep(1.3)
        self.assertFalse(marker.exists(), "descendant survived adapter timeout")

    def test_large_stderr_does_not_deadlock(self):
        adapter = self.adapter("import sys\nsys.stderr.write('x'*200000)\nprint('{\"status\":\"ok\",\"scope\":\"real\",\"evidence\":{\"test\":true}}')")
        result = adapter.call("create", {})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(Path(result["logs"]["stderr"]).stat().st_size, 200000)

    def test_output_escape_is_rejected_before_process_start(self):
        adapter = self.adapter("raise AssertionError('must not execute')")
        result = adapter.call("create", {"output_path": str(self.root / "elsewhere")})
        self.assertEqual(result["status"], "rejected")
        self.assertIsNone(result["logs"])

    def test_missing_evidence_or_wrong_scope_is_error(self):
        adapter = self.adapter("print('{\"status\":\"ok\",\"scope\":\"real\"}')")
        self.assertEqual(adapter.call("create", {})["error"], "adapter_missing_evidence")
        self.script.write_text("print('{\"status\":\"ok\",\"scope\":\"local_reference\",\"evidence\":{\"test\":true}}')", encoding="utf-8")
        self.assertEqual(adapter.call("create", {})["error"], "adapter_scope_mismatch")

    def test_nonfinite_json_is_rejected(self):
        adapter = self.adapter("print('{\"status\":\"ok\",\"scope\":\"real\",\"evidence\":{\"test\":true},\"data\":NaN}')")
        self.assertEqual(adapter.call("create", {})["error"], "adapter_invalid_json")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cell = {"schema_version": 1, "cell_id": "test-cell", "experiment_id": "T1", "rq": ["RQ1"],
                     "design_ids": ["D1"], "workload_id": "test", "baseline_id": "LOCAL_REFERENCE",
                     "variant": "default", "pair_id": "test-pair", "replicate": 1, "profile": "smoke",
                     "parameters": {}, "required_operations": ["discover"], "status": "planned"}
        self.cell_path = self.root / "cell.json"
        self.cell_path.write_text(json.dumps(self.cell), encoding="utf-8")
        script = self.root / "discover.py"
        script.write_text("print('{\"status\":\"ok\",\"scope\":\"local_reference\",\"evidence\":{\"test\":true},\"data\":{\"operations\":[\"discover\"]}}')", encoding="utf-8")
        self.config = {"schema_version": 1, "baseline_id": "LOCAL_REFERENCE", "execution_scope": "local_reference",
                       "state": "configured", "operations": {"discover": {"argv": [sys.executable, str(script)]}}}
        self.config_path = self.root / "adapter.json"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_existing_output_refused_and_kept(self):
        output = self.root / "output"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            run(self.cell_path, self.config_path, output)
        self.assertEqual(marker.read_text(), "keep")

    def test_preflight_does_not_execute_scenario(self):
        fake = types.ModuleType("harness.scenarios")
        fake.execute_cell = lambda *_: (_ for _ in ()).throw(AssertionError("must not execute"))
        with patch.dict(sys.modules, {"harness.scenarios": fake}):
            result = run(self.cell_path, self.config_path, self.root / "output")
        self.assertEqual(result["status"], "preflight_ok")
        self.assertFalse(result["performance_claims_allowed"])

    def test_failed_scenario_result_is_saved(self):
        fake = types.ModuleType("harness.scenarios")
        fake.execute_cell = lambda *_: {"status": "error", "measurement_valid": False, "measurements": None}
        output = self.root / "output"
        with patch.dict(sys.modules, {"harness.scenarios": fake}):
            result = run(self.cell_path, self.config_path, output, execute=True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(json.loads((output / "result.json").read_text())["status"], "error")
        self.assertTrue((output / "scenario.json").exists())

    def test_scenario_exception_is_saved(self):
        fake = types.ModuleType("harness.scenarios")
        fake.execute_cell = lambda *_: (_ for _ in ()).throw(RuntimeError("internal detail"))
        output = self.root / "output"
        with patch.dict(sys.modules, {"harness.scenarios": fake}):
            result = run(self.cell_path, self.config_path, output, execute=True)
        self.assertEqual(result["error"], "RuntimeError")
        self.assertTrue((output / "logs" / "runner-error.log").exists())
        self.assertTrue((output / "result.json").exists())

    def test_real_adapter_with_unobserved_runtime_rejected(self):
        self.cell["baseline_id"] = "B1"
        self.config.update(baseline_id="B1", execution_scope="real")
        adapter = Adapter(self.config, log_dir=self.root / "logs")
        result = preflight(self.cell, adapter)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("missing:runtime.versions", result["problems"])
        self.assertIsNone(result["discovery"])

    def test_local_reference_cannot_be_a_full_result(self):
        self.cell["profile"] = "full"
        with self.assertRaises(ValueError):
            validate_cell(self.cell)


class BaselineHookTests(unittest.TestCase):
    """Mock CLI contract tests, not claims of a deployed XFS/JuiceFS service."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.config = {"baseline_id": "B1", "state": "configured", "workspace_root": str(self.root),
                       "baseline_settings": {"enabled": True, "deployment_qualified": True}}
        self.mount = json.dumps({"filesystems": [{"fstype": "xfs", "target": str(self.root)}]})

    def tearDown(self):
        self.temporary.cleanup()

    def handle(self, operation, payload, outputs):
        with patch.object(baseline_cli.platform, "system", return_value="Linux"), \
             patch.object(baseline_cli, "command", side_effect=outputs) as command:
            result = baseline_cli.handle({"baseline_id": self.config["baseline_id"], "operation": operation, "payload": payload}, self.config)
        return result, command.call_args_list

    def test_create_uses_reflink_always_and_uniform_path(self):
        (data, evidence), calls = self.handle("create", {"workspace_id": "child", "source_path": str(self.source), "source_quiescent": True}, [self.mount, ""])
        self.assertEqual(data["path"], str(self.root / "child"))
        self.assertEqual(data["workspace_id"], "child")
        self.assertEqual(calls[1].args[0][:4], ["cp", "-a", "--reflink=always", "--"])
        self.assertEqual(evidence["operation"], "reflink_always")

    def test_save_records_exact_id_and_open_restores_subtree(self):
        snapshot_id = "a" * 64
        summary = json.dumps({"message_type": "summary", "snapshot_id": snapshot_id})
        with patch.dict(baseline_cli.os.environ, {"RESTIC_REPOSITORY": "s3:example.invalid/test"}):
            (data, _), calls = self.handle("save", {"workspace_id": "source", "path": str(self.source), "label": "round-1", "source_quiescent": True, "session_saved": True}, [self.mount, summary])
        self.assertEqual(data["version_id"], snapshot_id + ":" + str(self.source))
        self.assertFalse(data["checkpoint_acknowledged"])
        self.assertEqual(calls[1].args[0][-1], str(self.source))
        (opened, _), calls = self.handle("open", {"workspace_id": "restored", "version_id": data["version_id"]}, [self.mount, ""])
        self.assertEqual(calls[1].args[0][2], data["version_id"])
        self.assertEqual(opened["path"], str(self.root / "restored"))
        self.assertFalse(opened["ready_for_agent"])

    def test_running_source_not_cloned(self):
        with self.assertRaisesRegex(baseline_cli.Rejected, "quiescent_source_required"):
            self.handle("fork", {"workspace_id": "child", "parent_path": str(self.source)}, [self.mount])

    def test_juicefs_clone_and_checkpoint_return_contract(self):
        self.config["baseline_id"] = "B2"
        self.config["baseline_settings"]["writeback_disabled_verified"] = True
        self.mount = json.dumps({"filesystems": [{"fstype": "fuse.juicefs", "target": str(self.root)}]})
        with patch.object(baseline_cli, "sync_tree"):
            (data, _), calls = self.handle("save", {"workspace_id": "source", "path": str(self.source), "label": "round-1", "source_quiescent": True, "session_saved": True}, [self.mount, ""])
        self.assertEqual(data["version_id"], str(self.root / "source--checkpoint--round-1"))
        self.assertEqual(calls[1].args[0][:3], ["juicefs", "clone", "-p"])
        self.assertFalse(data["checkpoint_acknowledged"])

    def test_same_source_clones_serialize_across_processes(self):
        # Each process imports the real lock implementation; only the JuiceFS
        # binary is replaced by a timed stand-in. The namespace is truly shared.
        code = """import json,sys,time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness import baseline_cli
root,source,target=map(Path,sys.argv[2:5])
def simulated_clone(argv, timeout):
    start=time.monotonic()
    target.write_text(json.dumps({'start':start}))
    time.sleep(0.25)
    target.write_text(json.dumps({'start':start,'end':time.monotonic()}))
baseline_cli.command=simulated_clone
wait=baseline_cli.clone_with_source_lock(source,target,root,'juicefs',5)
print(json.dumps({'wait_s':wait}))
"""
        package = Path(__file__).resolve().parents[1]
        first_target, second_target = self.root / "first.json", self.root / "second.json"
        argv = [sys.executable, "-c", code, str(package), str(self.root), str(self.source)]
        first = subprocess.Popen(argv + [str(first_target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = None
        try:
            deadline = time.monotonic() + 5
            while not first_target.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(first_target.exists())
            second = subprocess.Popen(argv + [str(second_target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            first_stdout, first_stderr = first.communicate(timeout=5)
            second_stdout, second_stderr = second.communicate(timeout=5)
            self.assertEqual(first.returncode, 0, first_stderr)
            self.assertEqual(second.returncode, 0, second_stderr)
            first_result = json.loads(first_target.read_text())
            second_result = json.loads(second_target.read_text())
            self.assertLessEqual(first_result["end"], second_result["start"])
            self.assertGreater(json.loads(second_stdout)["wait_s"], 0.05)
            self.assertEqual(list((self.root / ".adapter-clone-locks").iterdir()), [])
        finally:
            for process in (first, second):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()

    def test_clone_failure_releases_only_its_own_lock(self):
        with patch.object(baseline_cli, "command", side_effect=baseline_cli.Rejected("simulated_failure")):
            with self.assertRaisesRegex(baseline_cli.Rejected, "simulated_failure"):
                baseline_cli.clone_with_source_lock(self.source, self.root / "target", self.root, "juicefs", 1)
        lock_root = self.root / ".adapter-clone-locks"
        self.assertEqual(list(lock_root.iterdir()), [])
        stale = lock_root / baseline_cli.hashlib.sha256(b"source").hexdigest()
        stale.mkdir()
        owner = stale / "owner-another-process"
        owner.write_text("retain")
        with patch.object(baseline_cli, "command", side_effect=AssertionError("locked command must not run")):
            with self.assertRaisesRegex(baseline_cli.Rejected, "clone_source_lock_timeout"):
                baseline_cli.clone_with_source_lock(self.source, self.root / "target", self.root, "juicefs", 0.03)
        self.assertEqual(owner.read_text(), "retain")


if __name__ == "__main__":
    unittest.main()
