from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
RELEASE_PATH = ROOT / "infra" / "idcai" / "build_release.py"
ADMIN_PATH = ROOT / "infra" / "idcai" / "build_workspace_admin.py"


def load_module(path: Path, name: str, fcntl_module):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"fcntl": fcntl_module}):
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


class BuildSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake_fcntl = types.ModuleType("fcntl")
        self.fake_fcntl.LOCK_EX = 2
        self.fake_fcntl.LOCK_NB = 4
        self.fake_fcntl.flock_calls = []
        self.fake_fcntl.lock_streams = []
        def flock_without_locking(stream, flags):
            self.fake_fcntl.flock_calls.append(flags)
            self.fake_fcntl.lock_streams.append(stream)
        self.fake_fcntl.flock = flock_without_locking
        self.release = load_module(RELEASE_PATH, "build_release_under_test", self.fake_fcntl)
        self.admin = load_module(ADMIN_PATH, "build_workspace_admin_under_test", self.fake_fcntl)

    def tearDown(self):
        for stream in self.fake_fcntl.lock_streams:
            if not stream.closed:
                stream.close()
        self.temp.cleanup()

    def test_release_main_uses_explicit_target_for_cargo_and_artifacts(self):
        root = self.root / "release-root"
        repository = self.root / "base-repository"
        repository.mkdir()
        toolchain = self.root / "toolchain"
        cargo_home = self.root / "cargo-home"
        target = self.root / "target directory" / "release-target"
        lock = self.root / "locks" / "build.lock"
        lock.parent.mkdir()
        lock.write_text("", encoding="utf-8")
        jobs = 5
        revision = "0" * 40
        observed = {"build_env": None, "build_command": None, "objcopy": []}

        def fake_run(command, **kwargs):
            command = [str(value) for value in command]
            if command[:4] == ["git", "-C", str(root / "sources" / ("brewfs-" + revision[:8])), "cat-file"]:
                return subprocess.CompletedProcess(command, 0)
            if command[:2] == ["git", "clone"]:
                source = Path(command[-1])
                source.mkdir(parents=True, exist_ok=True)
                (source / "Cargo.lock").write_text("mock lockfile\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)
            if "build" in command and "--release" in command:
                observed["build_env"] = kwargs["env"].copy()
                observed["build_command"] = command
                cargo_target = Path(kwargs["env"]["CARGO_TARGET_DIR"])
                release_dir = cargo_target / "release"
                release_dir.mkdir(parents=True, exist_ok=True)
                for binary_name in ("brewfs", "packed_snapshot_fixture"):
                    (release_dir / binary_name).write_bytes((binary_name + " build output").encode())
                return subprocess.CompletedProcess(command, 0)
            if command and command[0] == "objcopy":
                destination = Path(command[-1])
                source = Path(command[-2])
                observed["objcopy"].append((source, destination))
                destination.write_bytes(source.read_bytes())
                return subprocess.CompletedProcess(command, 0)
            return subprocess.CompletedProcess(command, 0)

        argv = [
            "build_release.py", "--root", str(root),
            "--base-repository", str(repository),
            "--toolchain", str(toolchain), "--cargo-home", str(cargo_home),
            "--commit", revision, "--lock", str(lock), "--jobs", str(jobs),
            "--target-dir", str(target),
        ]
        with patch.object(sys, "argv", argv), \
                patch.object(self.release.subprocess, "run", side_effect=fake_run) as run, \
                patch.dict(sys.modules, {"fcntl": self.fake_fcntl}), \
                patch("sys.stdout", new=io.StringIO()):
            self.release.main()

        self.assertIsNotNone(observed["build_env"])
        env = observed["build_env"]
        self.assertEqual(env["CARGO_TARGET_DIR"], str(target.resolve()))
        self.assertEqual(env["CARGO_INCREMENTAL"], "1")
        self.assertEqual(env["CARGO_BUILD_JOBS"], str(jobs))
        self.assertIn("profile.release.package.brewfs.debug=0", observed["build_command"])
        self.assertEqual(
            {source for source, _destination in observed["objcopy"]},
            {target.resolve() / "release" / "brewfs",
             target.resolve() / "release" / "packed_snapshot_fixture"},
        )
        report_path = root / "releases" / revision[:8] / "build.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["target_directory"], str(target.resolve()))
        self.assertTrue(report["incremental"])
        self.assertFalse(report["brewfs_debug_info"])
        self.assertEqual(report["jobs"], jobs)
        self.assertGreaterEqual(run.call_count, 8)
        self.assertEqual(self.fake_fcntl.flock_calls, [self.fake_fcntl.LOCK_EX | self.fake_fcntl.LOCK_NB])

    def test_workspace_admin_main_uses_explicit_target_and_cargo_settings(self):
        source = self.root / "brewfs-source"
        template = self.root / "admin-template"
        out = self.root / "admin-output"
        target = self.root / "target admin" / "custom"
        (source / "src").mkdir(parents=True)
        template_src = template / "src"
        template_src.mkdir(parents=True)
        (source / "Cargo.toml").write_text("[profile.release]\ndebug = 2\n", encoding="utf-8")
        (source / "Cargo.lock").write_text("mock lockfile\n", encoding="utf-8")
        (template / "Cargo.toml").write_text(
            '[package]\nname = "ax-brewfs-admin"\nversion = "0.1.0"\nbrewfs = { path = "../brewfs" }\n',
            encoding="utf-8",
        )
        (template_src / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        jobs = 6
        candidate_bytes = b"mock admin executable"
        observed = {"command": None, "env": None}

        def fake_run(command, **kwargs):
            command = [str(value) for value in command]
            observed["command"] = command
            observed["env"] = kwargs["env"].copy()
            cargo_target = Path(kwargs["env"]["CARGO_TARGET_DIR"])
            binary = cargo_target / "release" / "ax-brewfs-admin"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(candidate_bytes)
            return subprocess.CompletedProcess(command, 0)

        argv = [
            "build_workspace_admin.py", "--source", str(source),
            "--template", str(template), "--out", str(out),
            "--target-dir", str(target), "--jobs", str(jobs),
        ]
        original_open = Path.open

        def open_with_mock_locks(path, *args, **kwargs):
            normalized = str(path).replace("\\", "/")
            if normalized.startswith("/srv/ax-idcai-20261113/") and Path(path).name in {
                "build.lock", "measurement.lock"
            }:
                return io.StringIO()
            return original_open(path, *args, **kwargs)

        with patch.object(sys, "argv", argv), \
                patch.object(self.admin.subprocess, "run", side_effect=fake_run) as run, \
                patch.object(Path, "open", new=open_with_mock_locks), \
                patch.dict(sys.modules, {"fcntl": self.fake_fcntl}), \
                patch("sys.stdout", new=io.StringIO()):
            self.admin.main()

        self.assertIsNotNone(observed["command"])
        self.assertEqual(observed["env"]["CARGO_TARGET_DIR"], str(target.resolve()))
        self.assertEqual(observed["env"]["CARGO_INCREMENTAL"], "1")
        self.assertEqual(observed["env"]["CARGO_BUILD_JOBS"], str(jobs))
        self.assertIn("profile.release.package.brewfs.debug=0", observed["command"])
        self.assertIn("-j", observed["command"])
        self.assertEqual(observed["command"][observed["command"].index("-j") + 1], str(jobs))
        copied_binary = out / "ax-brewfs-admin"
        self.assertEqual(copied_binary.read_bytes(), candidate_bytes)
        report = json.loads((out / "admin-build.json").read_text(encoding="utf-8"))
        self.assertEqual(report["target_directory"], str(target.resolve()))
        self.assertTrue(report["incremental"])
        self.assertEqual(report["jobs"], jobs)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.fake_fcntl.flock_calls, [self.fake_fcntl.LOCK_EX, self.fake_fcntl.LOCK_EX])


if __name__ == "__main__":
    unittest.main()
