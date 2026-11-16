from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "bench" / "cluster" / "runtime"
RUNNER_PATH = ROOT / "bench/cluster/runtime/check_packed_candidate.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("packed_candidate_under_test", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackedCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        prepared = self.run_root / "prepared" / "brewfs-packed-on-100000.json"
        prepared.parent.mkdir(parents=True)
        prepared.write_text(json.dumps({
            "system": "brewfs-packed",
            "engine": "brewfs-packed",
            "bucket": "test-bucket",
            "namespace": "test-namespace",
            "meta_backend": "tikv",
            "files": 100000,
            "dynamic": "on",
            "manifest_key": "test-manifest-key",
            "data_config": str(self.root / "data.json"),
            "base": "packed-parent-workspace",
        }), encoding="utf-8")
        self.events = []
        self.remote_files = {}
        self.generated = []
        self.start_configs = []
        self.forbidden_calls = []
        self.hosts = {
            "A366": {"ssh": "idcai-60001", "slots": 52},
            "A355": {"ssh": "idcai-60525", "slots": 40},
        }
        self.cl = types.ModuleType("cluster_lib")
        self.cl.RUN_ROOT = self.run_root
        self.cl.BIN_DIR = self.root / "bin"
        self.cl.REPO_ROOT = ROOT
        self.cl.RUN_PREFIX = "test-run"
        self.cl.RUN_NAMESPACE = "test-run"
        self.cl.PD_ENDPOINT = "http://tikv.test:2379"
        self.cl.HOST_BY_NAME = self.hosts
        self.cl.HOST_BY_SSH = {record["ssh"]: {**record, "name": name} for name, record in self.hosts.items()}
        self.cl.ALL_HOST_NAMES = ["A366", "A355"]
        self.cl.RUSTFS_ENDPOINTS = ["http://object-a", "http://object-b"]
        self.cl.collector_calls = []
        self.cl.started = []
        self.cl.stop_calls = []

        def forbidden(name):
            def call(*args, **kwargs):
                self.forbidden_calls.append((name, args, kwargs))
                raise AssertionError(f"unexpected cluster operation: {name}")
            return call

        for name in (
            "collector_set_mounts", "collector_mark_all", "collector_pause_all",
            "delete_bucket_and_objects", "delete_bucket", "redis_flush_db",
            "redis_delete_pattern_db0",
        ):
            setattr(self.cl, name, forbidden(name))

        def endpoint_for_slot(slot, host_idx=0):
            return self.cl.RUSTFS_ENDPOINTS[(host_idx + slot - 1) % len(self.cl.RUSTFS_ENDPOINTS)]

        def generate_mounts_json(requests, out_path):
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            requests = list(requests)
            records = []
            for request in requests:
                host = next(name for name, info in self.hosts.items() if info["ssh"] == request["host"])
                records.append({
                    **request,
                    "host_name": host,
                    "metrics_port": 20000 + request["slot"],
                    "mount_directory": str(self.run_root / "mounts" / request["id"]),
                    "cache_root": str(self.run_root / "cache" / request["id"]),
                    "engine": request["engine"],
                })
            document = {"mounts": records, "host_windows": []}
            out_path.write_text(json.dumps(document), encoding="utf-8")
            self.generated.append((out_path, requests, document))
            self.events.append(("generate", out_path, requests))
            return document

        def start_mounts(mounts_path, specs_by_id, audit_dir, register_collector=True):
            document = json.loads(Path(mounts_path).read_text(encoding="utf-8"))
            self.start_configs.append((Path(mounts_path), document, dict(specs_by_id), register_collector))
            self.events.append(("start", tuple(specs_by_id), register_collector))
            if register_collector:
                self.cl.collector_calls.append("registered")
            if len(document["mounts"]) != 1 or len(specs_by_id) != 1:
                raise AssertionError("suite_volumes.mount must start from a one-mount config")
            started = []
            for record in document["mounts"]:
                if record["id"] not in specs_by_id:
                    continue
                item = {
                    **record,
                    "unit": "unit-" + record["id"],
                    "cgroup": "group-" + record["id"],
                    "pid": 1234,
                    "host_ssh": record["host"],
                }
                started.append(item)
                self.cl.started.append(item)
                audit_dir = Path(audit_dir)
                audit_dir.mkdir(parents=True, exist_ok=True)
                (audit_dir / f"{record['id']}-audit.json").write_text(
                    json.dumps({"id": record["id"], "purpose": record["purpose"]}),
                    encoding="utf-8",
                )
            return started

        def stop_mounts(mounts, stop_dir, clear_collector=True, remove_cache=False):
            self.cl.stop_calls.append((list(mounts), clear_collector, remove_cache))
            self.events.append(("stop", clear_collector, remove_cache))
            return [{"id": item["id"], "unmounted_cleanly": True} for item in mounts]

        def run_on_host(host, argv, **kwargs):
            argv = [str(value) for value in argv]
            self.events.append(("remote", host, argv))
            if argv[0] == "python3" and len(argv) >= 4 and argv[1] == "-c":
                port = int(argv[-1])
                return subprocess.CompletedProcess(argv, 0, f"port {port} free".encode(), b"")
            if argv[0] == "test":
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            if argv[0] == "cat":
                path = argv[1]
                if path not in self.remote_files:
                    return subprocess.CompletedProcess(argv, 1, b"", b"missing")
                return subprocess.CompletedProcess(argv, 0, self.remote_files[path], b"")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        self.cl.endpoint_for_slot = endpoint_for_slot
        self.cl.generate_mounts_json = generate_mounts_json
        self.cl.start_mounts = start_mounts
        self.cl.stop_mounts = stop_mounts
        self.cl.run_on_host = run_on_host
        self.cl.extract_json_from_cli = lambda raw: json.loads(raw.decode("utf-8"))

        self.support = types.ModuleType("c4_support")
        self.support.REDIS_URL = "redis://test"
        self.support.command_calls = []

        def command(argv, out, **kwargs):
            argv = [str(value) for value in argv]
            namespace = argv[argv.index("--workspace-namespace") + 1]
            self.support.command_calls.append(argv)
            self.events.append(("clone", namespace))
            payload = json.dumps({"workspace_id": "reader-" + namespace}).encode("utf-8")
            return subprocess.CompletedProcess(argv, 0, payload, b"")

        def save(path, value):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

        self.support.command = command
        self.support.save = save

        self.readers = types.ModuleType("run_c3")
        self.readers.batch_c3_loader = self._run_loaders
        self.volumes = self._load_suite_volumes()
        self.runtime = (self.cl, self.volumes, self.support, self.readers)
        self.runner = load_runner()

    def tearDown(self):
        self.temp.cleanup()

    def _load_suite_volumes(self):
        spec = importlib.util.spec_from_file_location("suite_volumes", RUNTIME_DIR / "suite_volumes.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"cluster_lib": self.cl, "c4_support": self.support}):
            sys.modules["suite_volumes"] = module
            spec.loader.exec_module(module)
        return module

    def _run_loaders(self, tasks_by_host, run_tag):
        self.events.append(("load", run_tag, tasks_by_host))
        results = []
        for tasks in tasks_by_host.values():
            for task in tasks:
                path = task["out_path"]
                doc = {
                    "passed": True,
                    "read_ops": 200000,
                    "actual_read_bytes": 819200000,
                }
                self.remote_files[path] = (json.dumps(doc) + "\n").encode()
                self.remote_files[path + ".stdout.log"] = b"loader stdout\n"
                self.remote_files[path + ".stderr.log"] = b"loader stderr\n"
                results.append({
                    "reader_id": task["reader_id"],
                    "host": task["host"],
                    "slot": task["slot"],
                    "wall_seconds": 1.25,
                    "loader": doc,
                })
        return results

    def _run(self, name):
        candidate = self.root / "candidate-brewfs"
        with patch.dict(os.environ, {"AX_PACKED_BREWFS_BINARY": str(candidate)}):
            return self.runner.run_candidate(name, self.runtime)

    def _assert_mount_helper_contract(self):
        self.assertEqual(len(self.generated), 5)
        preflight = self.generated[0]
        self.assertEqual(len(preflight[1]), 4)
        actual_mount_configs = self.generated[1:]
        self.assertEqual([len(item[1]) for item in actual_mount_configs], [1, 1, 1, 1])
        self.assertEqual(len(self.start_configs), 4)
        for _path, document, specs, register_collector in self.start_configs:
            self.assertEqual(len(document["mounts"]), 1)
            self.assertEqual(len(specs), 1)
            self.assertFalse(register_collector)
        self.assertEqual(self.cl.collector_calls, [])
        self.assertEqual(self.forbidden_calls, [])

    def test_success_uses_real_mount_helper_candidate_binary_and_byte_verified_reads(self):
        result = self._run("case-success")

        self.assertEqual(result["purpose"], "correctness-with-concurrent-suite")
        self.assertFalse(result["performance_comparison"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(self.cl.started), 4)
        self._assert_mount_helper_contract()

        candidate = str(self.root / "candidate-brewfs")
        for _path, requests, _document in self.generated:
            for request in requests:
                self.assertEqual(request["binary"], candidate)
        load_event = next(event for event in self.events if event[0] == "load")
        tasks_by_host = load_event[2]
        tasks = [task for host_tasks in tasks_by_host.values() for task in host_tasks]
        self.assertEqual(len(tasks), 4)
        self.assertEqual({task["host"] for task in tasks}, {"A366", "A355"})
        self.assertEqual({task["slot"] for task in tasks}, {35, 36, 45, 46})
        for task in tasks:
            argv = task["loader_argv"]
            self.assertEqual(argv[0], str(self.cl.BIN_DIR / "c3-loader"))
            self.assertNotEqual(argv[0], candidate)
            self.assertEqual(argv[argv.index("--files") + 1], "100000")
            self.assertEqual(argv[argv.index("--epochs") + 1], "2")
            self.assertEqual(argv[argv.index("--threads") + 1], "8")
            self.assertEqual(argv[argv.index("--verify-bytes") + 1], "1")
        barriers = {
            task["loader_argv"][task["loader_argv"].index("--start-unix-ns") + 1]
            for task in tasks
        }
        self.assertEqual(len(barriers), 1)

        out = self.run_root / "diagnostics" / "case-success"
        results = json.loads((out / "readers.json").read_text(encoding="utf-8"))
        self.assertEqual(len(results), 4)
        for row in results:
            self.assertTrue(row["loader"]["passed"])
            self.assertEqual(row["loader"]["read_ops"], 200000)
            self.assertEqual(row["loader"]["actual_read_bytes"], 819200000)
        manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "passed")
        self.assertEqual(len(manifest["stops"]), 4)
        self.assertTrue(all(row["unmounted_cleanly"] for row in manifest["stops"]))
        for mount in self.cl.started:
            self.assertTrue((out / f"{mount['id']}.json").is_file())
            self.assertEqual((out / f"{mount['id']}.json.stdout.log").read_text(encoding="utf-8"), "loader stdout\n")
            self.assertEqual((out / f"{mount['id']}.json.stderr.log").read_text(encoding="utf-8"), "loader stderr\n")
            self.assertTrue((out / f"{mount['id']}-audit.json").is_file())
        self.assertEqual(len(self.cl.stop_calls), 1)
        stopped, clear_collector, remove_cache = self.cl.stop_calls[0]
        self.assertEqual(len(stopped), 4)
        self.assertFalse(clear_collector)
        self.assertTrue(remove_cache)

    def test_occupied_metrics_port_prevents_reader_registration(self):
        original = self.cl.run_on_host

        def occupied_port(host, argv, **kwargs):
            argv = [str(value) for value in argv]
            if argv[0] == "python3" and len(argv) >= 4 and argv[1] == "-c":
                raise RuntimeError("mock metrics port is occupied")
            return original(host, argv, **kwargs)

        self.cl.run_on_host = occupied_port
        candidate = self.root / "candidate-brewfs"
        with patch.dict(os.environ, {"AX_PACKED_BREWFS_BINARY": str(candidate)}):
            with self.assertRaisesRegex(RuntimeError, "metrics port is occupied"):
                self.runner.run_candidate("case-busy-port", self.runtime)

        self.assertEqual(self.cl.started, [])
        self.assertEqual(self.start_configs, [])
        self.assertEqual(self.support.command_calls, [])
        self.assertEqual(self.cl.stop_calls, [])
        self.assertEqual(self.cl.collector_calls, [])
        self.assertEqual(self.forbidden_calls, [])

    def test_loader_failure_stops_four_owned_mounts_without_collector_or_s3_cleanup(self):
        self.readers.batch_c3_loader = lambda *_args: (_ for _ in ()).throw(RuntimeError("injected loader failure"))
        candidate = self.root / "candidate-brewfs"
        with patch.dict(os.environ, {"AX_PACKED_BREWFS_BINARY": str(candidate)}):
            with self.assertRaisesRegex(RuntimeError, "injected loader failure"):
                self.runner.run_candidate("case-failure", self.runtime)

        self.assertEqual(len(self.cl.started), 4)
        self._assert_mount_helper_contract()
        self.assertEqual(len(self.cl.stop_calls), 1)
        stopped, clear_collector, remove_cache = self.cl.stop_calls[0]
        self.assertEqual(len(stopped), 4)
        self.assertFalse(clear_collector)
        self.assertTrue(remove_cache)
        self.assertEqual(self.cl.collector_calls, [])
        self.assertEqual(self.forbidden_calls, [])
        manifest = json.loads((self.run_root / "diagnostics" / "case-failure" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(len(manifest["stops"]), 4)
        self.assertIn("injected loader failure", manifest["error"])


if __name__ == "__main__":
    unittest.main()
