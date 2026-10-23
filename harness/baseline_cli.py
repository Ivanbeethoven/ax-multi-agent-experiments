"""Linux CLI hooks for B1 and B2; disabled until an operator supplies real paths.

No format, mount, repository initialization, deletion, cloud allocation, or cache
drop is performed here. Deployment and remote durability qualification precede use.
Credentials are inherited from the process environment, never command arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import uuid


class Rejected(Exception):
    pass


def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def path_argument(value, roots, *, exists=False):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise Rejected("absolute_path_required")
    path = Path(value).resolve()
    if not any(inside(path, root) and path != root for root in roots):
        raise Rejected("path_outside_dedicated_subdirectory")
    if exists and (not path.exists() or not path.is_dir()):
        raise Rejected("source_directory_missing")
    return path


def command(argv, timeout, *, cwd=None):
    # We stay in the adapter process group so the outer timeout kills the entire
    # hierarchy. Local timeouts still terminate the direct child.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=stderr, shell=False)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise Rejected("cli_timeout")
        stdout.seek(0)
        data = stdout.read(8 * 1024 * 1024 + 1)
        if proc.returncode != 0:
            raise Rejected("cli_nonzero_exit")
        if len(data) > 8 * 1024 * 1024:
            raise Rejected("cli_output_limit")
        return data.decode("utf-8")


def clone_with_source_lock(source, target, root, binary, timeout):
    """Serialize same-source JuiceFS clones across cooperating processes.

    The lock namespace must be on the shared experiment filesystem, with the
    same root-relative source layout on every client. Queueing consumes the
    timeout and remains inside the timed adapter operation. A killed owner can
    leave a stale lock; we never steal it or recursively remove anything.
    """
    started = time.monotonic()
    deadline = started + timeout
    lock_root = root / ".adapter-clone-locks"
    if lock_root.is_symlink():
        raise Rejected("clone_lock_root_symlink")
    lock_root.mkdir(exist_ok=True)
    source_key = os.path.relpath(source.resolve(), root.resolve()).replace(os.sep, "/")
    lock_path = lock_root / hashlib.sha256(source_key.encode()).hexdigest()
    token = uuid.uuid4().hex
    owner_path = lock_path / ("owner-" + token)
    while True:
        if time.monotonic() >= deadline:
            raise Rejected("clone_source_lock_timeout")
        if lock_path.is_symlink():
            raise Rejected("clone_source_lock_symlink")
        try:
            lock_path.mkdir()
            break
        except FileExistsError:
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    try:
        with owner_path.open("x", encoding="ascii") as owner:
            owner.write(token)
        wait_s = time.monotonic() - started
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Rejected("clone_source_lock_timeout")
        command([binary, "clone", "-p", str(source), str(target)], remaining)
        return wait_s
    finally:
        # Delete only the lock this invocation owns. Never clear another owner
        # or a directory containing unexpected files, even after a failed clone.
        if not lock_path.is_symlink() and owner_path.is_file() and owner_path.read_text(encoding="ascii") == token:
            owner_path.unlink()
            lock_path.rmdir()


def digest_tree(root):
    digest = hashlib.sha256()
    count = 0
    logical_bytes = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(json.dumps(["symlink", relative, os.readlink(path)]).encode())
        elif path.is_dir():
            digest.update(json.dumps(["directory", relative]).encode())
        elif path.is_file():
            content = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    content.update(chunk)
                    logical_bytes += len(chunk)
            digest.update(json.dumps(["file", relative, content.hexdigest()]).encode())
            count += 1
        else:
            raise Rejected("unsupported_special_file")
    return {"tree_sha256": digest.hexdigest(), "file_count": count, "logical_bytes": logical_bytes}


def sync_tree(root):
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    directories = sorted((p for p in root.rglob("*") if p.is_dir() and not p.is_symlink()), reverse=True)
    for path in directories + [root]:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def handle(request, config):
    settings = config.get("baseline_settings", {})
    if settings.get("enabled") is not True or config.get("state") != "configured":
        raise Rejected("baseline_not_enabled")
    if platform.system() != "Linux":
        raise Rejected("linux_required")
    baseline = config.get("baseline_id")
    if baseline not in {"B1", "B2"} or request.get("baseline_id") != baseline:
        raise Rejected("baseline_mismatch")
    root_value = config.get("workspace_root")
    if not root_value or not Path(root_value).is_absolute() or not Path(root_value).is_dir():
        raise Rejected("existing_dedicated_workspace_root_required")
    root = Path(root_value).resolve()
    # Refuse dangerously broad namespaces even though these hooks never delete.
    if root in {Path("/"), Path("/srv"), Path("/mnt"), Path("/tmp"), Path.home()}:
        raise Rejected("dedicated_workspace_root_required")
    input_roots = [root] + [Path(p).resolve() for p in config.get("allowed_input_roots", [])]
    payload = request.get("payload", {})
    operation = request.get("operation")
    binaries = settings.get("binaries", {})
    timeout = settings.get("command_timeout_s", 600)
    operations = ["discover", "create", "fork", "save", "open", "verify", "release"]
    fs = "xfs" if baseline == "B1" else "fuse.juicefs"
    mount = json.loads(command([binaries.get("findmnt", "findmnt"), "--json", "--output", "TARGET,FSTYPE,OPTIONS", "--target", str(root)], timeout))
    mounted = mount.get("filesystems", [])
    if len(mounted) != 1 or mounted[0].get("fstype") != fs:
        raise Rejected("required_filesystem_not_observed")
    if baseline == "B2" and settings.get("writeback_disabled_verified") is not True:
        raise Rejected("juicefs_writeback_must_be_disabled_and_verified")
    if operation == "discover":
        versions = {"linux": platform.release(), "python": platform.python_version()}
        if baseline == "B1":
            versions["cp"] = command([binaries.get("cp", "cp"), "--version"], timeout).splitlines()[0]
            versions["restic"] = command([binaries.get("restic", "restic"), "version"], timeout).splitlines()[0]
            info = command([binaries.get("xfs_info", "xfs_info"), str(root)], timeout)
            if not re.search(r"\breflink=1\b", info):
                raise Rejected("xfs_reflink_not_enabled")
        else:
            versions["juicefs"] = command([binaries.get("juicefs", "juicefs"), "version"], timeout).splitlines()[0]
        resources = {"visible_logical_cpus": os.cpu_count(), "physical_memory_bytes": os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")}
        # This is host evidence, not a claim that cgroups or cloud allocation have
        # been checked. Those remain deployment qualification fields.
        return {"operations": operations, "runtime": {"versions": versions, "resources": resources,
                "configuration": {"filesystem": fs, "mount": mounted[0], "deployment_qualified": settings.get("deployment_qualified") is True}}}, {"mount_type": fs}
    if operation not in operations:
        return None, None
    if settings.get("deployment_qualified") is not True:
        raise Rejected("deployment_and_durability_qualification_required")
    workspace_id = payload.get("workspace_id")
    if operation != "verify" and (not isinstance(workspace_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace_id)):
        raise Rejected("valid_workspace_id_required")
    if operation in {"create", "fork"}:
        source = path_argument(payload.get("source_path", payload.get("parent_path")), input_roots, exists=True)
        target = path_argument(payload.get("output_path", str(root / workspace_id)), [root])
        if target.exists() or not target.parent.is_dir():
            raise Rejected("target_must_be_new_with_existing_parent")
        if payload.get("source_quiescent") is not True:
            raise Rejected("quiescent_source_required")
        if baseline == "B1":
            command([binaries.get("cp", "cp"), "-a", "--reflink=always", "--", str(source), str(target)], timeout)
            lock_wait_s = None
        else:
            lock_wait_s = clone_with_source_lock(source, target, root, binaries.get("juicefs", "juicefs"), timeout)
        return {"workspace_id": workspace_id, "path": str(target)}, {"operation": "reflink_always" if baseline == "B1" else "native_clone", "exit_code": 0, "clone_source_lock_wait_s": lock_wait_s}
    if operation == "release":
        source = path_argument(payload.get("path"), [root], exists=True)
        return {"workspace_id": workspace_id, "path": str(source), "retained": True}, {"no_open_adapter_handle": True, "files_retained_for_audit": True, "gc_performed": False}
    if operation == "verify":
        source = path_argument(payload.get("source_path"), input_roots, exists=True)
        data = digest_tree(source)
        expected = payload.get("expected_tree_sha256")
        if expected is not None and data["tree_sha256"] != expected:
            raise Rejected("content_hash_mismatch")
        return data, {"hash_algorithm": "sha256", "scope": "contents_directories_symlinks"}
    if operation == "save":
        source = path_argument(payload.get("source_path", payload.get("path")), [root], exists=True)
        if payload.get("source_quiescent") is not True or payload.get("session_saved") is not True:
            raise Rejected("quiescent_source_and_saved_session_required")
        checkpoint_id = payload.get("checkpoint_id", payload.get("label"))
        workspace_id = payload.get("workspace_id")
        if not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v) for v in (checkpoint_id, workspace_id)):
            raise Rejected("stable_workspace_and_checkpoint_ids_required")
        if baseline == "B1":
            if not (os.environ.get("RESTIC_REPOSITORY") or os.environ.get("RESTIC_REPOSITORY_FILE")):
                raise Rejected("restic_repository_environment_missing")
            text = command([binaries.get("restic", "restic"), "backup", "--json", "--host", workspace_id,
                            "--tag", checkpoint_id, str(source)], timeout)
            summaries = [obj for line in text.splitlines() if line.strip() for obj in [json.loads(line)] if obj.get("message_type") == "summary"]
            if len(summaries) != 1 or not re.fullmatch(r"[a-f0-9]{8,64}", summaries[0].get("snapshot_id", "")):
                raise Rejected("exact_restic_snapshot_id_missing")
            return {"version_id": summaries[0]["snapshot_id"] + ":" + str(source), "snapshot_id": summaries[0]["snapshot_id"], "storage_save_complete": True,
                    "checkpoint_acknowledged": False}, {"restic_summary": summaries[0], "coordinator_record_still_required": True}
        target = path_argument(payload.get("output_path", str(root / (workspace_id + "--checkpoint--" + checkpoint_id))), [root])
        if target.exists() or not target.parent.is_dir():
            raise Rejected("checkpoint_target_must_be_new")
        sync_tree(source)
        lock_wait_s = clone_with_source_lock(source, target, root, binaries.get("juicefs", "juicefs"), timeout)
        sync_tree(target)
        return {"version_id": str(target), "checkpoint_path": str(target), "storage_save_complete": True, "checkpoint_acknowledged": False}, {"fsync_and_clone_complete": True, "coordinator_record_still_required": True, "clone_source_lock_wait_s": lock_wait_s}
    if operation == "open":
        target = path_argument(payload.get("output_path", str(root / workspace_id)), [root])
        if target.exists() or not target.parent.is_dir():
            raise Rejected("restore_target_must_be_new")
        if baseline == "B1":
            lock_wait_s = None
            version = payload.get("version_id", "")
            if not isinstance(version, str) or ":" not in version:
                raise Rejected("snapshot_id_and_subfolder_required")
            snapshot, source_path = version.split(":", 1)
            source = path_argument(source_path, input_roots)
            if not re.fullmatch(r"[a-f0-9]{8,64}", snapshot):
                raise Rejected("exact_snapshot_id_required")
            command([binaries.get("restic", "restic"), "restore", snapshot + ":" + str(source), "--target", str(target)], timeout)
        else:
            source = path_argument(payload.get("source_path", payload.get("version_id")), input_roots, exists=True)
            if payload.get("source_quiescent") is not True:
                raise Rejected("immutable_checkpoint_required")
            lock_wait_s = clone_with_source_lock(source, target, root, binaries.get("juicefs", "juicefs"), timeout)
        return {"workspace_id": workspace_id, "path": str(target), "ready_for_agent": False}, {"restore_complete": True, "content_and_session_verification_required": True, "clone_source_lock_wait_s": lock_wait_s}
    return None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    result = {"status": "error", "scope": "real", "data": None, "telemetry": None, "evidence": None}
    try:
        config = json.loads(Path(args.settings).read_text(encoding="utf-8"))
        request = json.load(sys.stdin)
        data, evidence = handle(request, config)
        result.update(status="unsupported" if evidence is None else "ok", data=data, evidence=evidence)
    except Rejected as exc:
        result.update(status="rejected", error=str(exc))
    except Exception as exc:
        result.update(status="error", error=type(exc).__name__)
    result["operation_elapsed_s"] = time.monotonic() - started
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
