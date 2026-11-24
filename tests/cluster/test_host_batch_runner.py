import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load_host_runner(run_root):
    context = types.ModuleType("runtime_context")
    context.RUN_ROOT = Path(run_root)
    path = ROOT / "bench/cluster/runtime/host_batch_runner.py"
    spec = importlib.util.spec_from_file_location("host_batch_runner_cache_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with patch.dict(sys.modules, {"runtime_context": context}):
        spec.loader.exec_module(module)
    return module


class HostBatchRunnerCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run_root = self.root / "runs" / "run-001"
        self.run_root.mkdir(parents=True)
        self.runner = load_host_runner(self.run_root)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def mount(mount_id, cache_root):
        return {
            "id": mount_id,
            "engine": "graftfs-native-overlay",
            "mount_directory": "/tmp/test-mount-" + mount_id,
            "unit": "test-unit-" + mount_id,
            "metrics_port": 19000,
            "cgroup": "/test/" + mount_id,
            "cache_root": str(cache_root),
        }

    def stop(self, mounts, results):
        mounts_path = self.root / "mounts-info.json"
        mounts_path.write_text(json.dumps(mounts), encoding="utf-8")
        calls = []

        def fake_run(argv, **kwargs):
            mount_id = argv[argv.index("--id") + 1]
            out_path = Path(argv[argv.index("--out") + 1])
            calls.append(mount_id)
            out_path.write_text(json.dumps({
                "id": mount_id,
                "unmounted_cleanly": results[mount_id],
            }), encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        self.runner.subprocess = types.SimpleNamespace(run=fake_run)
        args = types.SimpleNamespace(
            mounts_info=mounts_path,
            stop_dir=self.root / "stops",
            remove_cache=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.runner.cmd_stop_batch(args)
        return calls

    def test_successful_unmount_removes_only_mount_cache_child(self):
        cache_base = self.run_root / "cache"
        cache = cache_base / "mount-a"
        sibling = cache_base / "keep-sibling"
        cache.mkdir(parents=True)
        (cache / "marker").write_text("current run", encoding="utf-8")
        sibling.mkdir()
        (sibling / "marker").write_text("keep", encoding="utf-8")

        calls = self.stop([self.mount("mount-a", cache)], {"mount-a": True})

        self.assertEqual(calls, ["mount-a"])
        self.assertFalse(cache.exists())
        self.assertTrue(cache_base.is_dir())
        self.assertTrue((sibling / "marker").is_file())

    def test_successful_unmount_preserves_cache_outside_current_run(self):
        outside = self.root / "runs" / "run-previous" / "cache" / "mount-old"
        outside.mkdir(parents=True)
        marker = outside / "marker"
        marker.write_text("previous run", encoding="utf-8")

        self.stop([self.mount("mount-old", outside)], {"mount-old": True})

        self.assertTrue(marker.is_file())

    def test_failed_unmount_keeps_current_run_cache(self):
        cache = self.run_root / "cache" / "mount-failed"
        cache.mkdir(parents=True)
        marker = cache / "marker"
        marker.write_text("retain after failed unmount", encoding="utf-8")

        self.stop([self.mount("mount-failed", cache)], {"mount-failed": False})

        self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()