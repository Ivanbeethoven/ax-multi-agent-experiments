"""Exercise mount budgets and configuration comparison before server use."""

import copy
import json
from pathlib import Path
import unittest

from bench.cluster import mounts
from bench.cluster.audit_mount import parse_proc_cmdline, validate_effective

ROOT = Path(__file__).resolve().parents[2]


class MountTests(unittest.TestCase):
    def setUp(self):
        self.design = json.loads((ROOT / "configs/cluster.json").read_text(encoding="utf-8"))
        self.profiles = json.loads((ROOT / "configs/mount-profiles.json").read_text(encoding="utf-8"))

    def record(self, engine="brewfs-native-overlay", **changes):
        request = dict(id="test-1", phase="measure", engine=engine, host="idcai-60001",
                       slot=1, workspace="child", parent_workspace="base", purpose="test",
                       binary="/srv/ax-cluster/releases/test/brewfs", cache_state="cold",
                       windows=["measure"], root="/srv/ax-cluster/runs/test")
        request.update(changes)
        return mounts.render(request, self.profiles, self.design)

    def test_all_systems_and_phases_fit_the_same_total_limits(self):
        for engine in ("brewfs-native-overlay", "brewfs-packed", "juicefs", "juicefs-overlay"):
            for phase in mounts.PHASES:
                with self.subTest(engine=engine, phase=phase):
                    result = self.record(engine, phase=phase)
                    self.assertEqual(result["memory_bytes"], 4 * 1024**3)
                    self.assertEqual(result["disk_bytes"], 2 * 1024**3)
                    self.assertLessEqual(result["capacity_reservation_bytes"] + 2048 * 1024**2, result["memory_bytes"])

    def test_simultaneous_parent_and_child_have_separate_cache_and_slot(self):
        first = self.record()
        second = self.record(id="test-2", slot=2, phase="fork-child")
        rows = mounts.validate_windows([first, second], self.design)
        self.assertEqual(rows[0]["memory"], 8 * 1024**3)
        self.assertEqual(rows[0]["mounts"], 2)
        second["cache_root"] = first["cache_root"]
        with self.assertRaisesRegex(ValueError, "share a slot or cache"):
            mounts.validate_windows([first, second], self.design)

    def test_disk_cache_reuse_requires_nonoverlapping_processes(self):
        first = self.record(windows=["first"])
        second = self.record(id="test-2", windows=["second"], cache_state="disk-reuse",
                             previous_mount="test-1", reuse_cache_root=first["cache_root"])
        self.assertEqual(len(mounts.validate_windows([first, second], self.design)), 2)
        second["windows"] = first["windows"]
        with self.assertRaises(ValueError):
            mounts.validate_windows([first, second], self.design)

    def test_cache_ablation_and_writeback_change_effective_fields(self):
        result = self.record("brewfs-packed", overrides={"packed_cache":"off", "retained_payload_mib":0,
                                                       "metadata_cache_mib":0, "row_cache_mib":0,
                                                       "prefetch":"off", "writes":"local-staged"})
        self.assertEqual(result["environment"]["BREWFS_PACKED_CACHE"], "off")
        self.assertEqual(result["environment"]["BREWFS_PACKED_METADATA_CACHE_BYTES"], "0")
        self.assertEqual(result["download_workers"], 0)
        self.assertEqual(result["cache"]["writeback_mode"], "commit_before_upload")
        for overrides in ({"row_cache_mib":-1}, {"retained_payload_mib":65}, {"unknown":1}, {"prefetch":"other"}):
            with self.assertRaises(ValueError):
                self.record(overrides=overrides)

    def test_shared_cache_requires_a_real_adapter(self):
        with self.assertRaisesRegex(ValueError, "shared-read-cache adapter"):
            self.record(cache_sharing="host")

    def test_audit_detects_actual_capacity_and_config_mismatch(self):
        record = self.record()
        actual = dict(binary=record["binary"], memory_max=record["memory_bytes"], swap_max=0,
                      environment=copy.deepcopy(record["environment"]), cache=copy.deepcopy(record["cache"]),
                      argv=[record["binary"]], metrics={"brewfs_workspace_row_cache_capacity_bytes":64 * 1024**2})
        self.assertTrue(validate_effective(record, actual)["passed"])
        actual["cache"]["read_memory_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "cache.read_memory_bytes"):
            validate_effective(record, actual)
        actual["cache"] = record["cache"]
        actual["metrics"]["brewfs_workspace_row_cache_capacity_bytes"] = 0
        with self.assertRaisesRegex(ValueError, "Native capacity"):
            validate_effective(record, actual)

    def test_audit_parses_juicefs_setproctitle_cmdline(self):
        record = self.record("juicefs", binary="/srv/ax-cluster/releases/bin/juicefs")
        args = [record["binary"], "--no-agent", "mount", *[f"--{k}={v}" for k, v in record["options"].items()], "redis://10.77.13.12:16379/14", record["mount_directory"]]
        gspt_raw = (" ".join(args) + "\0" * 32).encode("utf-8")
        parsed = parse_proc_cmdline(gspt_raw)
        actual = dict(binary=record["binary"], memory_max=record["memory_bytes"], swap_max=0,
                      environment={}, cache={}, argv=parsed, metrics={})
        self.assertTrue(validate_effective(record, actual)["passed"])


if __name__ == "__main__":
    unittest.main()
