"""Exercise mount budgets and configuration comparison before server use."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
                    self.assertEqual(result["disk_bytes"], 8 * 1024**3)
                    self.assertLessEqual(result["capacity_reservation_bytes"] + 2048 * 1024**2, result["memory_bytes"])

    def test_ssd_and_metadata_cache_allocations_match_the_eight_gib_profile(self):
        gib = 1024**3
        self.assertEqual(self.profiles["disk_mib"], 8192)

        native = self.record("brewfs-native-overlay")
        self.assertEqual(native["cache"]["read_ssd_bytes"], 7 * gib)
        self.assertEqual(native["cache"]["write_ssd_bytes"], 1 * gib)

        packed = self.record("brewfs-packed")
        self.assertEqual(packed["cache"]["read_ssd_bytes"], 3 * gib)
        self.assertEqual(packed["cache"]["write_ssd_bytes"], 1 * gib)
        self.assertEqual(int(packed["environment"]["BREWFS_PACKED_CACHE_BYTES"]), 4 * gib)

        juicefs = self.record("juicefs")
        self.assertEqual(juicefs["options"]["cache-size"], "7168M")
        self.assertEqual(self.profiles["juicefs_staging_reserve_mib"], 1024)

    def test_fio_resources_cover_each_phase_with_equal_read_cache(self):
        for engine in ("brewfs-native-overlay", "juicefs"):
            for phase in mounts.PHASES:
                record = self.record(engine, phase=phase, resource_profile="fio")
                self.assertEqual(record["memory_bytes"], 16 * 1024**3)
                self.assertEqual(record["disk_bytes"], 16 * 1024**3)
                if engine == "juicefs":
                    self.assertEqual(record["options"]["cache-size"], "15360M")
                else:
                    self.assertEqual(record["cache"]["read_ssd_bytes"], 15 * 1024**3)
                    self.assertEqual(record["cache"]["write_ssd_bytes"], 1024**3)
                self.assertEqual(len(mounts.validate_windows([record], self.design)), 1)

    def test_fio_audit_checks_the_effective_memory_limit(self):
        record = self.record(resource_profile="fio")
        actual = dict(binary=record["binary"], memory_max=16 * 1024**3, swap_max=0,
                      environment=copy.deepcopy(record["environment"]), cache=copy.deepcopy(record["cache"]),
                      argv=[record["binary"]], metrics={"brewfs_workspace_row_cache_capacity_bytes":64 * 1024**2})
        self.assertTrue(validate_effective(record, actual)["passed"])
        actual["memory_max"] = 4 * 1024**3
        with self.assertRaises(ValueError):
            validate_effective(record, actual)

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

    def test_audit_recovers_truncated_title_with_remaining_arguments(self):
        command='/srv/bin/juicefs --no-agent mount --dir-entry-cache=1 redis://server/14 /mnt/work'
        raw=b'/srv/bin/juicefs --no-agent mount\0remaining-original-argument\0'
        with patch.object(Path,'is_file',return_value=True), patch.object(Path,'read_text',return_value='ExecStart='+command+'\n'):
            parsed=parse_proc_cmdline(raw,Path('/sys/fs/cgroup/ax-mount.service'))
        self.assertEqual(parsed,command.split())

    @unittest.skipUnless(os.name == 'posix', 'Linux executable links')
    def test_audit_resolves_binary_link_and_rejects_other_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'juicefs-real'
            binary.write_bytes(b'test executable')
            link = root / 'juicefs'
            link.symlink_to(binary)
            record = self.record('juicefs', binary=str(link))
            argv = [str(link), *[f'--{k}={v}' for k, v in record['options'].items()]]
            actual = dict(binary=str(binary), memory_max=record['memory_bytes'], swap_max=0,
                          environment={}, cache={}, argv=argv, metrics={})
            self.assertTrue(validate_effective(record, actual)['passed'])
            actual['binary'] = str(root / 'different-program')
            with self.assertRaisesRegex(ValueError, 'executable'):
                validate_effective(record, actual)


if __name__ == "__main__":
    unittest.main()
