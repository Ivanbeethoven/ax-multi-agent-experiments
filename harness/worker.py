"""Deterministic local W1/W2/W3/W6 operations, separate from system adapters.

All results label their profile. A local flush/fsync is reported as such; the
coordinator must obtain the baseline-specific remote save confirmation. The
full profile never shrinks a requested mutation to fit a smaller fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import time

try:
    from .fixtures import (BLOCK_BYTES, PREFIX, SESSION_BYTES, REPARSE_POINT, _open_regular,
                           checked_root, external_path,
                           fixture_bytes, iter_fixture_files, profile_spec, record_bytes,
                           record_content_version, result_header, safe_path, sha256,
                           verify_expected)
except ImportError:  # Direct execution: python harness/worker.py ...
    from fixtures import (BLOCK_BYTES, PREFIX, SESSION_BYTES, REPARSE_POINT, _open_regular,
                          checked_root, external_path,
                          fixture_bytes, iter_fixture_files, profile_spec, record_bytes,
                          record_content_version, result_header, safe_path, sha256,
                          verify_expected)


def _integer(value, name, minimum=None):
    if not isinstance(value, int) or isinstance(value, bool) or (minimum is not None and value < minimum):
        raise ValueError(f"{name} must be an integer" + (f" >= {minimum}" if minimum is not None else ""))
    return value


def write_payload(workspace_id: int, round_id: int, write_id: int) -> bytes:
    _integer(workspace_id, "workspace_id")
    _integer(round_id, "round_id", 1)
    _integer(write_id, "write_id", 0)
    return hashlib.shake_256(
        f"{PREFIX}workspace={workspace_id}|round={round_id}|write={write_id}".encode()).digest(BLOCK_BYTES)


def session_payload(workspace_id: int, round_id: int) -> bytes:
    _integer(workspace_id, "workspace_id")
    _integer(round_id, "round_id", 1)
    return hashlib.shake_256(
        f"{PREFIX}workspace={workspace_id}|round={round_id}|session".encode()).digest(SESSION_BYTES)


def mutation_blocks(modified_bytes: int, shape: str, profile: str) -> list[tuple[int, int]]:
    _integer(modified_bytes, "modified_bytes", 0)
    spec = profile_spec(profile)
    pool_blocks = spec["large_count"] * spec["large_bytes"] // BLOCK_BYTES
    if modified_bytes % BLOCK_BYTES:
        raise ValueError("modified_bytes must be a multiple of 4096")
    if modified_bytes > pool_blocks * BLOCK_BYTES:
        raise ValueError(f"requested mutation {modified_bytes} exceeds {profile} pool {pool_blocks * BLOCK_BYTES}; no automatic downscaling")
    if shape not in ("contiguous", "scattered"):
        raise ValueError("shape must be contiguous or scattered")
    return [(j, j if shape == "contiguous" else (40503 * j + 17) % pool_blocks)
            for j in range(modified_bytes // BLOCK_BYTES)]


def _block_location(block: int, profile: str) -> tuple[str, int]:
    blocks_per_file = profile_spec(profile)["large_bytes"] // BLOCK_BYTES
    file_number, within_file = divmod(block, blocks_per_file)
    return f"large/l{file_number:03d}.bin", within_file * BLOCK_BYTES


def read_workload(root, profile: str = "full", version: int = 1) -> dict:
    """W1 reads the 256 small files from d000, exactly once per call."""
    root = checked_root(root)
    spec = profile_spec(profile)
    paths = [f"small/d000/s{entry:03d}.bin" for entry in range(256)]
    payloads = []
    started = time.perf_counter()
    for relative in paths:
        with _open_regular(safe_path(root, relative), "rb") as source:
            payloads.append((relative, source.read()))
    elapsed = time.perf_counter() - started
    verification_started = time.perf_counter()
    errors = [{"path": relative, "reason": "content_or_length"}
              for relative, payload in payloads
              if payload != fixture_bytes(relative, spec["small_bytes"], version)]
    return {**result_header(profile), "operation": "read", "workload": "W1",
            "content_version": version, "ok": not errors, "files_read": len(payloads),
            "logical_read_bytes": sum(len(payload) for _, payload in payloads),
            "read_elapsed_s": elapsed,
            "path_safety_checks_in_read_time": True,
            "verification_elapsed_s": time.perf_counter() - verification_started,
            "verification_in_workload_time": False, "errors": errors}


def mutate_workspace(root, workspace_id: int, round_id: int = 1,
                     modified_bytes: int = 1048576, shape: str = "contiguous",
                     namespace: bool = False, profile: str = "full") -> dict:
    """Apply exact W1/W2 block writes and session bytes; no remote save here.

    `write_elapsed_s` includes deterministic payload generation and all local
    open/write/fsync operations; `io_elapsed_s` is separately instrumented.
    Verification is a separate command so it cannot accidentally warm inputs
    before the measured read. Namespace edits are intentionally non-idempotent.
    """
    root = checked_root(root)
    spec = profile_spec(profile)
    _integer(workspace_id, "workspace_id")
    _integer(round_id, "round_id", 1)
    blocks = mutation_blocks(modified_bytes, shape, profile)
    # Preflight *all* paths before the first mutation, including namespace paths.
    locations = [(j, *_block_location(block, profile)) for j, block in blocks]
    paths = {}
    for _, relative, _ in locations:
        if relative not in paths:
            path = safe_path(root, relative)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size != spec["large_bytes"]:
                raise ValueError(f"large file is missing or has wrong size: {path}")
            paths[relative] = path
    state_dir = safe_path(root, "agent-state")
    if state_dir.exists() and not state_dir.is_dir():
        raise ValueError("agent-state must be a directory")
    session_path = safe_path(root, "agent-state/session.bin")
    if session_path.exists() and not session_path.is_file():
        raise ValueError("session path must be a regular file")
    deletes, renames = [], []
    if namespace:
        for entry in range(32):
            deletes.append(safe_path(root, f"small/d000/s{entry:03d}.bin"))
        for entry in range(32, 64):
            source = safe_path(root, f"small/d000/s{entry:03d}.bin")
            destination = safe_path(root, f"small/d000/r{entry:03d}.bin")
            if destination.exists():
                raise ValueError(f"rename destination exists: {destination}")
            renames.append((source, destination))
        for path in deletes + [source for source, _ in renames]:
            if not path.is_file() or path.stat().st_size != spec["small_bytes"]:
                raise ValueError(f"namespace source missing or wrong size: {path}")
    opened = {}
    io_s = 0.0
    payload_s = 0.0
    fsync_count = 0
    started = time.perf_counter()
    try:
        io_started = time.perf_counter()
        for relative, path in paths.items():
            opened[relative] = _open_regular(path, "r+b")
        io_s += time.perf_counter() - io_started
        for j, relative, offset in locations:
            generation_started = time.perf_counter()
            payload = write_payload(workspace_id, round_id, j)
            payload_s += time.perf_counter() - generation_started
            io_started = time.perf_counter()
            output = opened[relative]
            output.seek(offset)
            written = output.write(payload)
            if written != BLOCK_BYTES:
                raise OSError(f"short write: {relative}")
            io_s += time.perf_counter() - io_started
        io_started = time.perf_counter()
        for output in opened.values():
            output.flush()
            os.fsync(output.fileno())
            fsync_count += 1
        io_s += time.perf_counter() - io_started
    finally:
        io_started = time.perf_counter()
        for output in opened.values():
            output.close()
        io_s += time.perf_counter() - io_started
    io_started = time.perf_counter()
    for path in deletes:
        path.unlink()
    for source, destination in renames:
        source.rename(destination)
    state_dir.mkdir(mode=0o755, exist_ok=True)
    os.chmod(state_dir, 0o755)
    io_s += time.perf_counter() - io_started
    generation_started = time.perf_counter()
    session = session_payload(workspace_id, round_id)
    payload_s += time.perf_counter() - generation_started
    io_started = time.perf_counter()
    with _open_regular(session_path, "r+b" if session_path.exists() else "xb") as output:
        output.seek(0)
        written = output.write(session)
        if written != SESSION_BYTES:
            raise OSError("short session write")
        output.truncate(SESSION_BYTES)
        output.flush()
        os.fsync(output.fileno())
        fsync_count += 1
    os.chmod(session_path, 0o644)
    io_s += time.perf_counter() - io_started
    elapsed = time.perf_counter() - started
    return {**result_header(profile), "operation": "mutate", "ok": True,
            "workspace_id": workspace_id, "round_id": round_id, "shape": shape,
            "namespace": namespace, "unique_modified_bytes": modified_bytes,
            "logical_write_bytes": modified_bytes, "session_bytes": SESSION_BYTES,
            "session_sha256": sha256(session), "writes": len(blocks),
            "deleted_files": len(deletes), "renamed_files": len(renames),
            "write_elapsed_s": elapsed, "io_elapsed_s": io_s,
            "payload_generation_elapsed_s": payload_s,
            "content_verified": False, "local_file_fsync_calls": fsync_count,
            "local_file_fsync_completed": True,
            "namespace_durability_confirmed": False,
            "save_confirmation": "local_file_fsync_only; remote confirmation must come from adapter"}


def expected_state_files(workspace_id: int | None = None, round_id: int = 1,
                         modified_bytes: int = 1048576, shape: str = "contiguous",
                         namespace: bool = False, profile: str = "full", version: int = 1,
                         parent: bool = False):
    """Regenerate F1, then the saved parent, then the child's exact writes."""
    overrides = {}
    session = None
    if parent:
        for j, block in mutation_blocks(1048576, "contiguous", profile):
            overrides[block] = (-1, 1, j)
        session = session_payload(-1, 1)
    if workspace_id is not None:
        for j, block in mutation_blocks(modified_bytes, shape, profile):
            overrides[block] = (workspace_id, round_id, j)
        session = session_payload(workspace_id, round_id)
    elif namespace:
        raise ValueError("namespace edits require workspace_id")
    by_file = {}
    for block, identity in overrides.items():
        relative, offset = _block_location(block, profile)
        by_file.setdefault(relative, []).append((offset, identity))
    for relative, content in iter_fixture_files("F1", profile, version):
        if relative in by_file:
            mutable = bytearray(content)
            for offset, identity in by_file[relative]:
                mutable[offset:offset + BLOCK_BYTES] = write_payload(*identity)
            content = bytes(mutable)
        if namespace and relative.startswith("small/d000/s"):
            entry = int(Path(relative).stem[1:])
            if entry < 32:
                continue
            if entry < 64:
                relative = f"small/d000/r{entry:03d}.bin"
        yield relative, content
    if session is not None:
        yield "agent-state/session.bin", session


def verify_state(root, workspace_id: int | None = None, round_id: int = 1,
                 modified_bytes: int = 1048576, shape: str = "contiguous",
                 namespace: bool = False, profile: str = "full", version: int = 1,
                 parent: bool = False) -> dict:
    expected = expected_state_files(workspace_id, round_id, modified_bytes, shape,
                                    namespace, profile, version, parent)
    return {"operation": "verify-state", "workspace_id": workspace_id,
            "round_id": round_id, "parent_saved_state": parent,
            **verify_expected(root, expected, profile=profile)}


def query_metadata(root, profile: str = "full", order: str = "direct") -> dict:
    """W6: 256 positive/negative pairs; optionally list involved directories first."""
    root = checked_root(root)
    spec = profile_spec(profile)
    if order not in ("direct", "list-first"):
        raise ValueError("order must be direct or list-first")
    count = spec["small_dirs"] * spec["small_per_dir"]
    queries = []
    directories = set()
    for j in range(256):
        n = (40503 * j + 17) % count
        directory, entry = divmod(n, spec["small_per_dir"])
        directories.add(f"small/d{directory:03d}")
        for prefix, positive in (("s", True), ("m", False)):
            relative = f"small/d{directory:03d}/{prefix}{entry:03d}.bin"
            queries.append((relative, root / relative, positive))
    list_paths = [(relative, root / relative) for relative in sorted(directories)]
    observations = []
    directory_entries = {}
    checked_parents = {root}
    safety_calls = 0

    def check_parents(path):
        # Safety probes happen on first traversal *inside* the measured interval,
        # so they cannot silently warm a direct-query group before its timer.
        nonlocal safety_calls
        for parent in reversed(path.parents):
            if parent.is_relative_to(root) and parent not in checked_parents:
                info = parent.lstat()
                safety_calls += 1
                if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or
                        getattr(info, "st_file_attributes", 0) & REPARSE_POINT):
                    raise ValueError(f"unsafe query parent: {parent}")
                checked_parents.add(parent)

    started = time.perf_counter()
    if order == "list-first":
        for relative, path in list_paths:
            check_parents(path / "__safety_leaf__")
            directory_entries[relative] = sorted(os.listdir(path))
    list_finished = time.perf_counter()
    for relative, path, positive in queries:
        check_parents(path)
        try:
            info = path.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
                raise ValueError(f"symlink/reparse query path: {path}")
            observations.append((relative, positive, True, info))
        except FileNotFoundError:
            observations.append((relative, positive, False, None))
    ended = time.perf_counter()
    verification_started = time.perf_counter()
    errors = []
    for relative, positive, exists, info in observations:
        if positive != exists:
            errors.append({"path": relative, "reason": "expected_positive" if positive else "expected_negative"})
        elif positive and (not stat.S_ISREG(info.st_mode) or info.st_size != spec["small_bytes"]):
            errors.append({"path": relative, "reason": "type_or_length"})
    expected_entries = [f"s{entry:03d}.bin" for entry in range(spec["small_per_dir"])]
    for relative, entries in directory_entries.items():
        if entries != expected_entries:
            errors.append({"path": relative, "reason": "directory_listing"})
    return {**result_header(profile), "operation": "query", "workload": "W6",
            "order": order, "ok": not errors, "positive_queries": 256, "negative_queries": 256,
            "listed_directories": len(directory_entries), "query_elapsed_s": ended - started,
            "list_elapsed_s": list_finished - started, "stat_elapsed_s": ended - list_finished,
            "parent_safety_lstat_calls": safety_calls, "safety_checks_in_query_time": True,
            "verification_elapsed_s": time.perf_counter() - verification_started,
            "verification_in_workload_time": False, "errors": errors}


def consumer_keys(consumer_id: int = 0, mode: str = "shared", profile: str = "full"):
    """Return the exact read order; scattered reads are not sorted for execution."""
    spec = profile_spec(profile)
    _integer(consumer_id, "consumer_id", 0)
    if mode not in ("shared", "disjoint", "scattered", "sequential"):
        raise ValueError("mode must be shared, disjoint, scattered, or sequential")
    if mode == "disjoint" and consumer_id >= 64:
        raise ValueError("disjoint workload supports consumer_id 0..63")
    total_records = spec["shards"] * spec["records_per_shard"]
    count = total_records if mode == "sequential" else spec["query_records"]
    if mode == "scattered":
        return [(40503 * j + 17) % total_records for j in range(count)]
    first = consumer_id * spec["query_records"] if mode == "disjoint" else 0
    if first + count > total_records:
        raise ValueError("requested keys exceed the selected profile")
    return range(first, first + count)


def consume_records(root, consumer_id: int = 0, mode: str = "shared", version: int = 1,
                    profile: str = "full", output=None) -> dict:
    """W3 reads fixed keys, validates outside read timing, emits external JSONL.

    Full: shared/disjoint/scattered read 1024 records, sequential reads all 262144.
    Smoke: shared/disjoint/scattered read 16 records, sequential reads all 1024; 64
    disjoint consumers still fit. Version binding is the caller's responsibility.
    """
    root = checked_root(root)
    spec = profile_spec(profile)
    keys = consumer_keys(consumer_id, mode, profile)
    record_content_version(0, version)
    total_records = spec["shards"] * spec["records_per_shard"]
    count = len(keys)
    first, last = (min(keys), max(keys)) if mode == "scattered" else (keys[0], keys[-1])
    output_path = external_path(root, output) if output is not None else None
    if output_path is not None and output_path.exists():
        raise ValueError(f"refusing to overwrite output: {output_path}")
    shards = (sorted({key // spec["records_per_shard"] for key in keys}) if mode == "scattered"
              else range(first // spec["records_per_shard"], last // spec["records_per_shard"] + 1))
    paths = {shard: f"shards/p{shard:02d}.bin" for shard in shards}
    observations = []
    opened = {}
    io_s = 0.0
    first_read_elapsed = None
    started = time.perf_counter()
    try:
        for key in keys:
            shard, within = divmod(key, spec["records_per_shard"])
            io_started = time.perf_counter()
            if shard not in opened:
                opened[shard] = _open_regular(safe_path(root, paths[shard]), "rb")
            source = opened[shard]
            source.seek(within * BLOCK_BYTES)
            payload = source.read(BLOCK_BYTES)
            io_s += time.perf_counter() - io_started
            # Record digests avoid retaining 1 GiB for the sequential case.
            observations.append((key, len(payload), sha256(payload)))
            if first_read_elapsed is None:
                first_read_elapsed = time.perf_counter() - started
    finally:
        io_started = time.perf_counter()
        for source in opened.values():
            source.close()
        io_s += time.perf_counter() - io_started
    read_elapsed = time.perf_counter() - started
    verification_started = time.perf_counter()
    errors = []
    rows = []
    for key, length, digest in observations:
        if length != BLOCK_BYTES or digest != sha256(record_bytes(key, version)):
            errors.append({"key": key, "reason": "content_or_length"})
        rows.append({"profile": profile, "paper_eligible": profile == "full",
                     "key": key, "dataset_version": f"V{version}",
                     "record_content_version": record_content_version(key, version), "sha256": digest})
    if mode == "scattered":
        rows.sort(key=lambda row: row["key"])
    verification_s = time.perf_counter() - verification_started
    first_verified_elapsed = time.perf_counter() - started if not errors else None
    output_started = time.perf_counter()
    output_digest = hashlib.sha256()
    output_stream = _open_regular(output_path, "xb") if output_path else None
    try:
        for row in rows:
            line = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            output_digest.update(line)
            if output_stream:
                output_stream.write(line)
    finally:
        if output_stream:
            output_stream.close()
    output_s = time.perf_counter() - output_started
    return {**result_header(profile), "operation": "consume", "workload": "W3",
            "consumer_id": consumer_id, "mode": mode, "dataset_version": f"V{version}",
            "ok": not errors, "records_read": count, "logical_read_bytes": sum(length for _, length, _ in observations),
            "first_key": first, "last_key": last,
            "read_order": {"formula": "key=(40503*j+17)%total_records" if mode == "scattered" else "key=first_key+j",
                           "j_start": 0, "count": count, "total_records": total_records,
                           "first_read_key": keys[0], "last_read_key": keys[-1]},
            "shards_touched": len(paths), "output_order": "key_ascending",
            "first_read_elapsed_s": first_read_elapsed,
            "read_elapsed_s": read_elapsed, "io_elapsed_s": io_s,
            "read_time_includes_sha256": True,
            "path_safety_checks_in_read_time": True,
            "verification_elapsed_s": verification_s, "verification_in_workload_time": False,
            "output_elapsed_s": output_s, "output_path": str(output_path) if output_path else None,
            "output_sha256": output_digest.hexdigest(), "errors": errors,
            "first_verified_result_elapsed_s": first_verified_elapsed,
            "first_verified_result_rule": "this worker validates after the read phase; first_read is not a correct-result timestamp"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("read", "mutate", "verify-state", "query", "consume"):
        action = sub.add_parser(name)
        action.add_argument("--root", required=True)
        action.add_argument("--profile", choices=("full", "smoke"), default="full")
        if name in ("read", "verify-state", "consume"):
            action.add_argument("--version", type=int, default=1)
        if name in ("mutate", "verify-state"):
            action.add_argument("--workspace-id", type=int, required=name == "mutate")
            action.add_argument("--round-id", type=int, default=1)
            action.add_argument("--modified-bytes", type=int, default=1048576)
            action.add_argument("--shape", choices=("contiguous", "scattered"), default="contiguous")
            action.add_argument("--namespace", action="store_true")
            if name == "verify-state":
                action.add_argument("--parent", action="store_true")
        if name == "query":
            action.add_argument("--order", choices=("direct", "list-first"), default="direct")
        if name == "consume":
            action.add_argument("--consumer-id", type=int, default=0)
            action.add_argument("--mode", choices=("shared", "disjoint", "scattered", "sequential"), default="shared")
            action.add_argument("--output", help="external JSONL path, must not already exist")
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    actions = {"read": read_workload, "mutate": mutate_workspace,
               "verify-state": verify_state, "query": query_metadata, "consume": consume_records}
    try:
        result = actions[command](**args)
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
