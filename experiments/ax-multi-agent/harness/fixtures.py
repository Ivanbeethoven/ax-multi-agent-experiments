"""Generate F1/F2 exactly as workloads.json specifies, with independent checking.

The full profile is the default and contains exactly 1 GiB of data. Smoke is
explicitly smaller and cannot produce paper measurements. Manifests always live
outside the data tree. This module does not assert remote persistence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import time
from typing import Iterator

PREFIX = "brewfs-ax-v2|20261023|"
BLOCK_BYTES = 4096
SESSION_BYTES = 65536
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def profile_spec(profile: str) -> dict:
    if profile == "full":
        return {"small_dirs": 128, "small_per_dir": 256, "small_bytes": 16384,
                "large_count": 128, "large_bytes": 4194304,
                "shards": 64, "records_per_shard": 4096, "query_records": 1024}
    if profile == "smoke":
        return {"small_dirs": 1, "small_per_dir": 256, "small_bytes": 4096,
                "large_count": 4, "large_bytes": 1048576,
                "shards": 4, "records_per_shard": 256, "query_records": 16}
    raise ValueError("profile must be full or smoke; size is never reduced automatically")


def result_header(profile: str) -> dict:
    profile_spec(profile)
    return {"profile": profile, "paper_eligible": profile == "full",
            "measurement_scope": "local_workload_only",
            "remote_durability_confirmed": False}


def _check_not_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
        raise ValueError(f"symlink/reparse point is forbidden: {path}")


def checked_path(path: os.PathLike | str, *, must_exist: bool = False) -> Path:
    """Reject lexical traversal and links in every existing path component."""
    raw = Path(path)
    if ".." in raw.parts:
        raise ValueError(f"parent traversal is forbidden: {path}")
    absolute = Path(os.path.abspath(raw))
    for component in reversed((absolute, *absolute.parents)):
        _check_not_link(component)
    if must_exist and not absolute.exists():
        raise FileNotFoundError(absolute)
    return absolute


def checked_root(root: os.PathLike | str) -> Path:
    root = checked_path(root, must_exist=True)
    if not root.is_dir():
        raise ValueError(f"workspace root is not a directory: {root}")
    return root


def safe_path(root: os.PathLike | str, relative: str) -> Path:
    root = checked_root(root)
    relative_path = PurePosixPath(relative)
    if (not relative or relative_path.is_absolute() or
            any(part in ("..", ".") for part in relative.split("/")) or
            "\\" in relative or ":" in relative):
        raise ValueError(f"unsafe relative path: {relative}")
    candidate = checked_path(root.joinpath(*relative_path.parts))
    if not candidate.is_relative_to(root):
        raise ValueError(f"path escapes workspace: {relative}")
    return candidate


def external_path(root: os.PathLike | str, destination: os.PathLike | str) -> Path:
    root = checked_path(root)
    destination = checked_path(destination)
    if destination.is_relative_to(root):
        raise ValueError("manifest/output must be outside the measured data tree")
    if not destination.parent.is_dir():
        raise ValueError("output parent directory must already exist")
    return destination


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_bytes(relative: str, length: int, version: int = 1) -> bytes:
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("F1 content version must be a positive integer")
    return hashlib.shake_256(f"{PREFIX}{version}|{relative}".encode()).digest(length)


def record_content_version(key: int, version: int) -> int:
    if version not in (1, 2):
        raise ValueError("F2 dataset version must be 1 or 2")
    return 2 if version == 2 and key % 100 == 0 else 1


def record_bytes(key: int, version: int = 1) -> bytes:
    if key < 0:
        raise ValueError("key must be non-negative")
    content_version = record_content_version(key, version)
    return hashlib.shake_256(f"{PREFIX}{content_version}|key={key}".encode()).digest(BLOCK_BYTES)


def iter_fixture_files(kind: str, profile: str = "full", version: int = 1) -> Iterator[tuple[str, bytes]]:
    spec = profile_spec(profile)
    if kind == "F1":
        # Check the version even if a future profile contains no files.
        fixture_bytes("validation", 0, version)
        for directory in range(spec["small_dirs"]):
            for entry in range(spec["small_per_dir"]):
                relative = f"small/d{directory:03d}/s{entry:03d}.bin"
                yield relative, fixture_bytes(relative, spec["small_bytes"], version)
        for number in range(spec["large_count"]):
            relative = f"large/l{number:03d}.bin"
            yield relative, fixture_bytes(relative, spec["large_bytes"], version)
    elif kind == "F2":
        record_content_version(0, version)
        for shard in range(spec["shards"]):
            start = shard * spec["records_per_shard"]
            yield f"shards/p{shard:02d}.bin", b"".join(
                record_bytes(key, version)
                for key in range(start, start + spec["records_per_shard"]))
    else:
        raise ValueError("fixture kind must be F1 or F2")


def expected_layout(kind: str, profile: str = "full") -> dict[str, int]:
    spec = profile_spec(profile)
    if kind == "F1":
        files = {f"small/d{directory:03d}/s{entry:03d}.bin": spec["small_bytes"]
                 for directory in range(spec["small_dirs"])
                 for entry in range(spec["small_per_dir"])}
        files.update({f"large/l{number:03d}.bin": spec["large_bytes"]
                      for number in range(spec["large_count"])})
        return files
    if kind == "F2":
        return {f"shards/p{shard:02d}.bin": spec["records_per_shard"] * BLOCK_BYTES
                for shard in range(spec["shards"])}
    raise ValueError("fixture kind must be F1 or F2")


def directory_set(paths) -> set[str]:
    return {str(parent) for relative in paths
            for parent in PurePosixPath(relative).parents if str(parent) != "."}


def inventory(root: os.PathLike | str) -> dict[str, os.stat_result]:
    root = checked_root(root)
    entries = {}
    pending = [root]
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            _check_not_link(child)
            info = child.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError(f"only regular files and directories are permitted: {child}")
            entries[child.relative_to(root).as_posix()] = info
            if stat.S_ISDIR(info.st_mode):
                pending.append(child)
    return entries


def _open_regular(path: Path, mode: str):
    _check_not_link(path)
    # Leaf O_NOFOLLOW additionally prevents a replaced symlink on POSIX. Callers
    # must not allow an adversary to rename parents while the workload is active.
    flags = getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    if mode == "rb":
        flags |= os.O_RDONLY
    elif mode == "r+b":
        flags |= os.O_RDWR
    elif mode == "xb":
        flags |= os.O_WRONLY | os.O_CREAT | os.O_EXCL
    else:
        raise ValueError("unsupported file mode")
    fd = os.open(path, flags, 0o644)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError(f"not a regular file: {path}")
    return os.fdopen(fd, mode)


def create_fixture(kind: str, target: os.PathLike | str, profile: str = "full",
                   version: int = 1, manifest_path: os.PathLike | str | None = None) -> dict:
    spec = profile_spec(profile)
    layout = expected_layout(kind, profile)
    if kind == "F1":
        fixture_bytes("validation", 0, version)
    else:
        record_content_version(0, version)
    target = checked_path(target)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("refusing to overwrite an existing nonempty fixture directory")
    manifest = external_path(target, manifest_path or target.with_name(target.name + ".manifest.json"))
    if manifest.exists():
        raise ValueError(f"refusing to overwrite manifest: {manifest}")
    target.mkdir(parents=True, exist_ok=True)
    entries = []
    total = 0
    started = time.perf_counter()
    for relative, payload in iter_fixture_files(kind, profile, version):
        path = safe_path(target, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _open_regular(path, "xb") as output:
            output.write(payload)
        os.chmod(path, 0o644)
        os.utime(path, ns=(0, 0))
        entries.append({"path": relative, "type": "file", "bytes": len(payload),
                        "mode": "0644", "sha256": sha256(payload)})
        total += len(payload)
    for relative in sorted(directory_set(layout)):
        path = safe_path(target, relative)
        os.chmod(path, 0o755)
        os.utime(path, ns=(0, 0))
        entries.append({"path": relative, "type": "directory", "mode": "0755"})
    os.chmod(target, 0o755)
    os.utime(target, ns=(0, 0))
    entries.sort(key=lambda item: item["path"])
    result = {**result_header(profile), "schema_version": 1, "kind": kind,
              "content_version": version, "dataset_version": f"V{version}" if kind == "F2" else None,
              "data_root": str(target), "manifest_path": str(manifest),
              "logical_data_bytes": total, "entries": entries,
              "data_manifest_sha256": sha256(_json_bytes(entries)),
              "generation_elapsed_s": time.perf_counter() - started,
              "generation_is_benchmark": False,
              "permission_check_supported": os.name != "nt",
              "mtime_ns": 0, "spec": spec}
    if kind == "F2":
        result["index"] = {"record_bytes": BLOCK_BYTES,
                           "records_per_shard": spec["records_per_shard"],
                           "key_count": spec["shards"] * spec["records_per_shard"],
                           "mapping": "shard=key//records_per_shard; offset=(key%records_per_shard)*4096",
                           "changed_records": sum(1 for key in range(spec["shards"] * spec["records_per_shard"])
                                                  if record_content_version(key, version) != 1)}
    with _open_regular(manifest, "xb") as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    return result


def load_manifest(path: os.PathLike | str) -> dict:
    path = checked_path(path, must_exist=True)
    with _open_regular(path, "rb") as source:
        manifest = json.load(source)
    if manifest.get("schema_version") != 1 or manifest.get("kind") not in ("F1", "F2"):
        raise ValueError("unsupported fixture manifest")
    profile_spec(manifest["profile"])
    if sha256(_json_bytes(manifest["entries"])) != manifest["data_manifest_sha256"]:
        raise ValueError("manifest checksum mismatch")
    return manifest


def verify_expected(root: os.PathLike | str, expected_files, *, profile: str) -> dict:
    """Compare bytes independently derived from inputs, never manifest hashes."""
    root = checked_root(root)
    started = time.perf_counter()
    observed = inventory(root)
    errors = []
    seen = set()
    total = 0
    count = 0
    for relative, expected in expected_files:
        seen.add(relative)
        count += 1
        total += len(expected)
        info = observed.get(relative)
        if info is None or not stat.S_ISREG(info.st_mode):
            errors.append({"path": relative, "reason": "missing_or_not_regular"})
            continue
        if info.st_size != len(expected):
            errors.append({"path": relative, "reason": "length", "expected": len(expected), "actual": info.st_size})
            continue
        with _open_regular(safe_path(root, relative), "rb") as source:
            actual = source.read()
        if actual != expected:
            errors.append({"path": relative, "reason": "content", "expected_sha256": sha256(expected),
                           "actual_sha256": sha256(actual)})
        if os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o644:
            errors.append({"path": relative, "reason": "mode", "actual": oct(stat.S_IMODE(info.st_mode))})
    expected_dirs = directory_set(seen)
    expected_paths = seen | expected_dirs
    for relative in sorted(set(observed) - expected_paths):
        errors.append({"path": relative, "reason": "unexpected_entry"})
    for relative in sorted(expected_dirs):
        info = observed.get(relative)
        if info is None or not stat.S_ISDIR(info.st_mode):
            errors.append({"path": relative, "reason": "missing_or_not_directory"})
        elif os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o755:
            errors.append({"path": relative, "reason": "directory_mode", "actual": oct(stat.S_IMODE(info.st_mode))})
    return {**result_header(profile), "ok": not errors, "files_checked": count,
            "logical_bytes_checked": total, "errors": errors,
            "verification_elapsed_s": time.perf_counter() - started,
            "verification_in_workload_time": False,
            "oracle": "independent_SHAKE256_generation_plus_exact_directory_tree",
            "permission_check_supported": os.name != "nt"}


def verify_fixture(root: os.PathLike | str, kind: str, profile: str = "full", version: int = 1) -> dict:
    return {"kind": kind, "content_version": version,
            **verify_expected(root, iter_fixture_files(kind, profile, version), profile=profile)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "verify"):
        action = sub.add_parser(command)
        action.add_argument("--kind", required=True, choices=("F1", "F2"))
        action.add_argument("--root", required=True)
        action.add_argument("--profile", choices=("full", "smoke"), default="full")
        action.add_argument("--version", type=int, default=1)
        if command == "create":
            action.add_argument("--manifest")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_fixture(args.kind, args.root, args.profile, args.version, args.manifest)
            result = {key: value for key, value in result.items() if key != "entries"}
        else:
            result = verify_fixture(args.root, args.kind, args.profile, args.version)
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
