import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest


REPO = Path(__file__).resolve().parents[2]
RUN_C2 = REPO / "bench" / "cluster" / "runtime" / "run_c2.py"


def load_run_c2():
    cluster_lib = ModuleType("cluster_lib")
    cluster_lib.SECRETS = {"redis_password": "test"}
    cluster_lib.REDIS_HOST = "127.0.0.1"
    cluster_lib.REDIS_PORT = 6379
    cluster_lib.HOSTS = []

    previous = sys.modules.get("cluster_lib")
    sys.modules["cluster_lib"] = cluster_lib
    try:
        spec = importlib.util.spec_from_file_location("run_c2_trace_tree_test", RUN_C2)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            del sys.modules["cluster_lib"]
        else:
            sys.modules["cluster_lib"] = previous
    return module


class TraceTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_c2 = load_run_c2()

    def test_overlay_reuses_registered_revision_and_requires_existing_input(self):
        with tempfile.TemporaryDirectory() as temp:
            self.run_c2.cl.RUN_ROOT = Path(temp)
            registry = Path(temp) / "prepared" / "trace-bases-dynamic.json"
            with self.assertRaises(FileNotFoundError):
                self.run_c2.ensure_c2_bases("dynamic", "graftfs-native-overlay")
            self.assertFalse(registry.parent.exists())
            registry.parent.mkdir()
            entry = dict(base_ws="parent", revision="original", namespace="volume", bucket="objects")
            record = dict(completed=True, graftfs_native_overlay={case: entry for case in self.run_c2.CASES})
            registry.write_text(json.dumps(record))
            self.assertEqual(self.run_c2.ensure_c2_bases("dynamic"), record)
            del entry["revision"]
            registry.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "parent"):
                self.run_c2.ensure_c2_bases("dynamic")

    def test_replay_root_selects_layout_from_base_schema(self):
        mount = Path("/tmp/c2-mount")

        self.assertEqual(
            self.run_c2.replay_root(mount, {"schema": "ax-c2-base-preparation-v1"}),
            mount,
        )
        self.assertEqual(
            self.run_c2.replay_root(mount, {"schema": "ax-c2-base-preparation-v2"}),
            mount / "trace-root",
        )

    def test_replay_root_rejects_unknown_schema(self):
        with self.assertRaisesRegex(ValueError, "Unknown trace base schema"):
            self.run_c2.replay_root(Path("/tmp/c2-mount"), {"schema": "unknown"})

    def test_install_trace_tree_preserves_mount_directory_and_copies_contents(self):
        case = "sample-case"
        with tempfile.TemporaryDirectory() as temporary_directory:
            temp = Path(temporary_directory)
            source = temp / "capture" / "initial"
            nested = source / "nested"
            nested.mkdir(parents=True)
            payload = nested / "payload.bin"
            payload.write_bytes(b"captured trace bytes\x00\xff")
            os.chmod(payload, 0o640)
            (source / "payload-link").symlink_to("nested/payload.bin")
            os.chmod(source, 0o751)
            source_mode = source.stat().st_mode & 0o777

            mount = temp / "existing-mount"
            mount.mkdir()
            os.chmod(mount, 0o751)
            original_mount_mode = mount.stat().st_mode & 0o777

            def copy_from_local_fixture(host, argv):
                self.assertEqual(host, "node-coord")
                self.assertEqual(argv[:3], ["cp", "-a", "--"])
                self.assertEqual(
                    argv[3],
                    f"/srv/ax-cluster/input/traces/{case}/initial/.",
                )
                self.assertEqual(argv[4], str(mount / "trace-root") + "/")
                command = list(argv)
                command[3] = str(source) + "/."
                subprocess.run(command, check=True)

            original_runner = getattr(self.run_c2.cl, "run_on_host", None)
            self.run_c2.cl.run_on_host = copy_from_local_fixture
            try:
                self.run_c2.install_trace_tree(case, mount)
            finally:
                if original_runner is None:
                    del self.run_c2.cl.run_on_host
                else:
                    self.run_c2.cl.run_on_host = original_runner

            copied_root = mount / "trace-root"
            self.assertEqual(mount.stat().st_mode & 0o777, original_mount_mode)
            self.assertEqual(copied_root.stat().st_mode & 0o777, source_mode)
            self.assertEqual((copied_root / "nested" / "payload.bin").read_bytes(), payload.read_bytes())
            self.assertEqual((copied_root / "nested" / "payload.bin").stat().st_mode & 0o777, 0o640)
            copied_link = copied_root / "payload-link"
            self.assertTrue(copied_link.is_symlink())
            self.assertEqual(os.readlink(copied_link), "nested/payload.bin")


if __name__ == "__main__":
    unittest.main()
