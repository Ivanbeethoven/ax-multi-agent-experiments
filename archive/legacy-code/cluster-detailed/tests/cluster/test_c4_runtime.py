"""Unit tests for C4 runtime ordering, task association, and placement budgets."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "bench" / "cluster" / "runtime"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class C4RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

        cls.design = json.loads((ROOT / "configs" / "cluster.json").read_text(encoding="utf-8"))
        cls.profile = json.loads((ROOT / "configs" / "mount-profiles.json").read_text(encoding="utf-8"))
        cls.campaign = json.loads((ROOT / "configs" / "campaign-c4.json").read_text(encoding="utf-8"))

        cluster = ModuleType("cluster_lib")
        cluster.REPO_ROOT = ROOT
        cluster.RUN_ROOT = Path("/fake/run-root")
        cluster.BIN_DIR = Path("/fake/bin")
        cluster.SCRIPTS_DIR = Path("/fake/scripts")
        cluster.SECRETS = {"redis_password": "unit-test-secret"}
        cluster.REDIS_HOST = "127.0.0.1"
        cluster.REDIS_PORT = 16379
        cluster.ALL_HOST_NAMES = [host["name"] for host in cls.design["hosts"]]
        cluster.HOST_BY_NAME = {host["name"]: {"ssh": "fake-" + host["name"]}
                                for host in cls.design["hosts"]}
        cluster.RUSTFS_ENDPOINTS = ["http://fake-s3"]
        cluster.synced_files = {}
        cluster.generated_requests = []
        cluster.collector_mounts = None
        cluster.endpoint_for_slot = lambda slot: f"http://fake-s3/{slot}"
        cluster.generate_mounts_json = lambda requests, path: setattr(
            cluster, "generated_requests", list(requests))
        cluster.collector_set_mounts = lambda mounts: setattr(cluster, "collector_mounts", mounts)
        cluster.sync_file_to_host = lambda host, source, remote: cluster.synced_files.__setitem__(
            str(remote), Path(source))
        cls.cluster = cluster

        with patch.dict(sys.modules, {"cluster_lib": cluster}):
            support_impl = load_module("_c4_support_under_test", RUNTIME / "c4_support.py")
        support = ModuleType("c4_support")
        support.TAGS = support_impl.TAGS
        support.placements = support_impl.placements
        support.request = lambda parent, workspace, identifier, host, slot, phase="measure", row_cache_mib=64: {
            "id": identifier, "workspace": workspace, "host": host, "slot": slot, "phase": phase,
            "row_cache_mib": row_cache_mib,
        }
        support.spec = lambda parent, workspace, endpoint=None: {"workspace": workspace, "endpoint": endpoint}

        def save(path, value):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")

        support.save = save
        support.command = None
        cls.support = support

        run_c2 = ModuleType("run_c2")
        run_c2.VERIFY_HOST_MAP = {host: host for host in cluster.ALL_HOST_NAMES}

        def batch_start_mounts(config, specs, tag, register_collector=False):
            requests = cluster.generated_requests
            grouped = sorted(requests, key=lambda request: (request["host"], request["slot"]))
            return [{"id": request["id"], "workspace": request["workspace"],
                     "host_name": request["host"], "slot": request["slot"],
                     "mount_directory": "/fake/mounts/" + request["workspace"]}
                    for request in grouped]

        run_c2.batch_start_mounts = batch_start_mounts
        run_c2.batch_stop_mounts = lambda mounts, *args, **kwargs: mounts
        cls.run_c2 = run_c2

        cls.module_patch = patch.dict(sys.modules, {
            "cluster_lib": cluster,
            "c4_support": support,
            "run_c2": run_c2,
        })
        cls.module_patch.start()
        cls.runtime = load_module("_run_c4_under_test", RUNTIME / "run_c4.py")

    @classmethod
    def tearDownClass(cls):
        cls.module_patch.stop()
        sys.modules.pop("_c4_support_under_test", None)
        sys.modules.pop("_run_c4_under_test", None)

    def make_run(self, output):
        condition = {"id": "C4-unit", "parameters": {}, "system_parameters": {}}
        run = self.runtime.Run(condition, "brewfs-native-overlay", 1, output)
        run.parent = {"system": "brewfs-native-overlay", "namespace": "unit", "bucket": "unit",
                      "files": 100000, "file_bytes": 4096}
        return run

    def test_mount_restores_input_workspace_order_after_host_grouped_start(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.make_run(Path(directory))
            workspaces = ["workspace-b0", "workspace-a0", "workspace-b1", "workspace-a1"]
            places = [("A355", 1), ("A366", 3), ("A355", 2), ("A366", 4)]

            mounts = run.mount(workspaces, places, "children")

            self.assertEqual([mount["workspace"] for mount in mounts], workspaces)
            self.assertEqual([mount["slot"] for mount in mounts], [1, 3, 2, 4])
            self.assertEqual([mount["workspace"] for mount in run.active], workspaces)
            self.assertEqual(set(self.cluster.collector_mounts), {"A355", "A366"})
            self.assertEqual(sum(len(group) for group in self.cluster.collector_mounts.values()), 4)

    def test_batch_keeps_each_workspace_with_its_modification_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.make_run(Path(directory))
            mounts = [
                {"id": f"mount-{i}", "workspace": f"workspace-{i}",
                 "host_name": host, "mount_directory": f"/fake/mounts/workspace-{i}"}
                for i, host in enumerate(("A355", "A366", "A355", "A366"))
            ]
            changes = [{"marker": f"manifest-{i}"} for i in range(len(mounts))]

            def run_fake_batch(argv, output, host, **kwargs):
                remote_path = argv[argv.index("--tasks") + 1]
                tasks = json.loads(self.cluster.synced_files[remote_path].read_text(encoding="utf-8"))
                results = [{"workspace": task["workspace"], "changes": task["changes"]}
                           for task in tasks]
                return SimpleNamespace(stdout=json.dumps({"ok": True, "results": results}).encode())

            self.support.command = run_fake_batch
            try:
                results = run.batch(mounts, "apply", "manifest-check", changes)
            finally:
                self.support.command = None

            actual = {result["workspace"]: result["changes"] for result in results}
            expected = {mount["workspace"]: changes[index] for index, mount in enumerate(mounts)}
            self.assertEqual(actual, expected)

    def test_placements_respect_host_slots_and_campaign_resource_limits(self):
        host_slots = {host["name"]: host["slots"] for host in self.design["hosts"]}
        expected_counts = {
            1: {"A366": 1, "A355": 0, "A672": 0, "A601": 0, "A635": 0, "A368": 0},
            16: {"A366": 4, "A355": 3, "A672": 2, "A601": 3, "A635": 3, "A368": 1},
            64: {"A366": 16, "A355": 11, "A672": 6, "A601": 13, "A635": 13, "A368": 5},
        }
        for count, expected in expected_counts.items():
            with self.subTest(children=count):
                places = self.support.placements(count)
                self.assertEqual(len(places), count)
                counts = {host: 0 for host in host_slots}
                slots = set()
                for host, slot in places:
                    counts[host] += 1
                    self.assertIn(host, host_slots)
                    self.assertGreaterEqual(slot, 1)
                    self.assertLessEqual(slot, host_slots[host])
                    self.assertNotIn((host, slot), slots)
                    slots.add((host, slot))
                self.assertEqual(counts, expected)

        same_host = self.support.placements(16, same_host=True)
        self.assertEqual(same_host, [("A366", slot) for slot in range(3, 19)])
        self.assertLessEqual(len(same_host) + 2, host_slots["A366"])

        maximum_mounts = self.campaign["maximum_simultaneous_mounts"]
        self.assertEqual(self.campaign["maximum_children"], 64)
        self.assertEqual(maximum_mounts, 66)
        self.assertEqual(len(self.support.placements(64)) + 2, maximum_mounts)
        per_host = {host: 0 for host in host_slots}
        for host, _ in self.support.placements(64):
            per_host[host] += 1
        per_host["A366"] += 2  # base and target occupy slots 1 and 2
        for host, used in per_host.items():
            self.assertLessEqual(used, host_slots[host], host)
        self.assertEqual(self.design["workspace_memory_gib"], self.profile["memory_mib"] / 1024)
        self.assertEqual(self.design["workspace_disk_gib"], self.profile["disk_mib"] / 1024)
        self.assertEqual(maximum_mounts * self.profile["memory_mib"] / 1024, 264)
        self.assertEqual(maximum_mounts * self.profile["disk_mib"] / 1024, 528)


if __name__ == "__main__":
    unittest.main()
