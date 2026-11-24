"""Fail-closed, single-process workspace syscall capture for mounted-fs-replay v1.

Uses strace rather than instrumenting Python file methods. Not a FUSE protocol or
physical-device recorder. Unsupported workspace operations invalidate the trace.
"""
import argparse
from collections import Counter
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

MAX_IO = 1024 * 1024


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def split_args(s):
    parts, start, depth, quoted, escaped = [], 0, 0, False, False
    for i, ch in enumerate(s):
        if quoted:
            if escaped: escaped = False
            elif ch == "\\": escaped = True
            elif ch == '"': quoted = False
        elif ch == '"': quoted = True
        elif ch in "([{<": depth += 1
        elif ch in ")]}>" : depth -= 1
        elif ch == "," and depth == 0:
            parts.append(s[start:i].strip()); start = i + 1
    parts.append(s[start:].strip())
    return parts


def unhex(s):
    if not re.fullmatch(r"(?:\\x[0-9a-f]{2})*", s):
        raise ValueError("Non-hex or truncated strace string")
    return bytes.fromhex(s.replace("\\x", ""))


def quoted(s):
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        raise ValueError("Missing or truncated syscall buffer")
    return unhex(s[1:-1])


def overlap(ranges, lo, hi):
    return sum(max(0, min(b, hi) - max(a, lo)) for a, b in ranges)


def insert(ranges, lo, hi):
    merged = []
    for a, b in sorted(ranges + ([(lo, hi)] if hi > lo else [])):
        if merged and a <= merged[-1][1]: merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else: merged.append((a, b))
    return merged


def invalidate(ranges, lo, hi):
    result = []
    for a, b in ranges:
        if b <= lo or a >= hi: result.append((a, b))
        else:
            if a < lo: result.append((a, lo))
            if b > hi: result.append((hi, b))
    return result


class Converter:
    def __init__(self, root):
        self.root = str(Path(root).resolve())
        self.fds, self.paths, self.seen = {}, {}, {}
        self.next_fd = self.next_object = 0
        self.records, self.events = [], []
        self.counts, self.excluded = Counter(), Counter()
        self.read_bytes = self.write_bytes = self.repeat_bytes = 0
        self.requested_read = self.requested_write = 0
        self.pid = None

    def relative(self, path):
        path = os.path.normpath(path)
        if path == self.root: return "."
        if path.startswith(self.root + "/"): return path[len(self.root)+1:]
        return None

    def path(self, arg, dirarg=None):
        value = quoted(arg).decode("utf-8")
        if not value: return None
        if not value.startswith("/"):
            base = self.root
            if dirarg:
                m = re.search(r"<(.*?)>", dirarg)
                if not m: raise ValueError("Cannot resolve directory FD")
                base = unhex(m[1]).decode()
            value = os.path.join(base, value)
        return self.relative(value)

    def object(self, path):
        if path not in self.paths:
            self.next_object += 1
            self.paths[path] = self.next_object
            self.seen[self.next_object] = []
        return self.paths[path]

    def consume(self, line, number):
        if "+++ exited with 0 +++" in line: return
        if re.match(r"\d+\s+\d+\.\d+\s+exit_group\(0\)\s+=\s+\?", line): return
        m = re.match(r"(\d+)\s+(\d+\.\d+)\s+(\w+)\((.*)\)\s+=\s+(-?\d+)(.*?)(?: <([\d.]+)>)?$", line)
        if not m:
            raise ValueError(f"Unparsed/unfinished syscall or signal at line {number}")
        pid, stamp, name, raw, ret, tail, duration = m.groups()
        if self.pid is not None and pid != self.pid: raise ValueError("Multiple processes not supported")
        self.pid = pid
        a, ret = split_args(raw), int(ret)
        if name in {"clone", "clone3", "fork", "vfork", "chdir", "fchdir", "chroot"}:
            raise ValueError("Process/CWD changes unsupported: " + name)
        err = 0
        if ret < 0:
            match = re.search(r"\b(E[A-Z0-9]+)\b", tail)
            if not match: raise ValueError("Missing errno")
            err = getattr(errno, match[1])
        fd_match = re.match(r"(\d+)", a[0]) if a else None
        oldfd = int(fd_match[1]) if fd_match else None
        handle = self.fds.get(oldfd)
        paths = []
        if name in {"openat", "newfstatat", "mkdirat", "unlinkat", "readlinkat", "faccessat", "faccessat2", "statx"}:
            paths = [self.path(a[1], a[0])]
        elif name in {"open", "stat", "lstat", "mkdir", "rmdir", "unlink", "readlink", "access", "chmod", "truncate"}:
            paths = [self.path(a[0])]
        elif name in {"rename", "link", "symlink"}: paths = [self.path(a[0]), self.path(a[1])]
        elif name in {"renameat", "renameat2", "linkat"}: paths = [self.path(a[1], a[0]), self.path(a[3], a[2])]
        elif a and a[0].startswith('"') and name not in {"getrandom", "getcwd"}:
            # Fail closed for unimplemented path syscalls such as creat/chown/xattr.
            paths = [self.path(a[0])]
        elif len(a) > 1 and (a[0].startswith("AT_FDCWD") or "<" in a[0]) and a[1].startswith('"') and name not in {"read", "write", "pread64", "pwrite64"}:
            paths = [self.path(a[1], a[0])]
        # Inspect FD annotations on ANY syscall, including mmap, readv and sendfile.
        annotated = [self.relative(unhex(x).decode()) for x in re.findall(r"<((?:\\x[0-9a-f]{2})+)>", raw)]
        relevant = handle is not None or any(p is not None for p in paths)
        if not relevant:
            # AT_FDCWD points to workspace but does not mean an absolute external path is in scope.
            fd_annotations = annotated[1:] if a and a[0].startswith("AT_FDCWD") else annotated
            relevant = any(p is not None for p in fd_annotations)
        if not relevant:
            self.excluded[name] += 1
            return
        record = {"phase": 0, "stream": 0, "expected_errno": err}
        event = {"line": number, "pid": int(pid), "timestamp_realtime_s": stamp,
                 "duration_s": duration, "syscall": name, "return": ret, "errno": err}
        if name in {"open", "openat"}:
            path = paths[0]
            if path is None: raise ValueError("Ambiguous open path")
            flags = a[2] if name == "openat" else a[1]
            allowed = {"O_RDONLY", "O_WRONLY", "O_RDWR", "O_CREAT", "O_EXCL", "O_TRUNC", "O_CLOEXEC", "O_LARGEFILE"}
            if set(flags.split("|")) - allowed: raise ValueError("Unsupported open flags: " + flags)
            self.next_fd += 1
            logical = self.next_fd
            record.update(op="open", fd=logical, path=path,
                access="read_write" if "O_RDWR" in flags else "write" if "O_WRONLY" in flags else "read",
                create="O_CREAT" in flags, exclusive="O_EXCL" in flags, truncate="O_TRUNC" in flags)
            if "O_CREAT" in flags: record["mode"] = int(a[-1], 8)
            if ret >= 0:
                if ret in self.fds: raise ValueError("FD overwritten without close")
                obj = self.object(path)
                if "O_TRUNC" in flags: self.seen[obj] = []
                self.fds[ret] = {"fd": logical, "offset": 0, "object": obj, "path": path}
        elif name in {"read", "pread64", "write", "pwrite64"}:
            if handle is None: raise ValueError("I/O on unrecorded workspace FD")
            length = int(a[2])
            if not 0 < length <= MAX_IO: raise ValueError("Unsupported I/O length")
            offset = int(a[3]) if name in {"pread64", "pwrite64"} else handle["offset"]
            reading = name in {"read", "pread64"}
            actual = max(ret, 0)
            data = quoted(a[1]) if ret >= 0 or not reading else b""
            if ret >= 0 and len(data) != (actual if reading else length): raise ValueError("Incomplete buffer capture")
            record.update(op="pread" if reading else "pwrite", fd=handle["fd"], offset=offset, len=length, expected_bytes=actual)
            event.update(object=handle["object"], path=handle["path"], offset=offset, requested_bytes=length, actual_bytes=actual)
            obj = handle["object"]
            if reading:
                if ret >= 0: record["expected_sha256"] = hashlib.sha256(data).hexdigest()
                self.requested_read += length; self.read_bytes += actual
                repeated = overlap(self.seen[obj], offset, offset+actual)
                self.repeat_bytes += repeated
                event["repeated_bytes"] = repeated
                self.seen[obj] = insert(self.seen[obj], offset, offset+actual)
            else:
                record["data_hex"] = data.hex()
                self.requested_write += length; self.write_bytes += actual
                self.seen[obj] = invalidate(self.seen[obj], offset, offset+actual)
            if name in {"read", "write"} and ret >= 0: handle["offset"] += actual
        elif name == "close":
            if handle is None or err: raise ValueError("Unknown/failed workspace close")
            record.update(op="close", fd=handle["fd"]); del self.fds[oldfd]
        elif name == "lseek":
            if handle is None or err: raise ValueError("Unknown/failed seek")
            handle["offset"] = ret
            record = None  # Converted read/write use explicit offsets; retain seek in audit/counts.
        elif name in {"fstat", "newfstatat", "stat", "lstat"}:
            if name == "fstat" or (name == "newfstatat" and quoted(a[1]) == b""):
                if handle is None: raise ValueError("Unknown stat FD")
                record.update(op="fstat", fd=handle["fd"])
            else:
                record.update(op="lstat" if name == "lstat" or "AT_SYMLINK_NOFOLLOW" in raw else "stat", path=paths[0])
        elif name in {"fsync", "fdatasync", "ftruncate"}:
            if handle is None: raise ValueError("Unknown sync/truncate FD")
            record.update(op="truncate" if name == "ftruncate" else name, fd=handle["fd"])
            if name == "ftruncate":
                length = int(a[1]); record["len"] = length
                if not err: self.seen[handle["object"]] = invalidate(self.seen[handle["object"]], length, 2**63)
        elif name in {"rename", "renameat", "renameat2"}:
            if any(p is None for p in paths): raise ValueError("Cross-boundary rename")
            if name == "renameat2" and a[4] != "0": raise ValueError("renameat2 flags unsupported")
            record.update(op="rename", **{"from": paths[0], "to": paths[1]})
            if not err:
                obj = self.paths.pop(paths[0], None)
                self.paths.pop(paths[1], None)
                if obj is not None: self.paths[paths[1]] = obj
                for h in self.fds.values():
                    if h["path"] == paths[0]: h["path"] = paths[1]
        elif name in {"unlink", "rmdir", "mkdir"}:
            record.update(op=name, path=paths[0])
            if name == "mkdir": record["mode"] = int(a[1], 8)
            elif not err: self.paths.pop(paths[0], None)
        else:
            raise ValueError("Unsupported workspace syscall: " + name)
        self.counts[name] += 1
        event["replay_operation_index"] = len(self.records) if record else None
        self.events.append(event)
        if record: self.records.append(record)

    def convert(self, raw, initial_hash, output):
        for number, line in enumerate(raw.read_text().splitlines(), 1):
            self.consume(line, number)
        if self.fds: raise ValueError("Unclosed workspace FDs")
        if not self.records: raise ValueError("No workspace operations captured")
        header = {"format": "mounted-fs-replay", "version": 1, "payload_provenance": "captured",
                  "description": "Single-process strace capture; read/write normalized to positioned I/O; no physical/FUSE I/O claim",
                  "initial_tree_sha256": initial_hash}
        (output / "trace.jsonl").write_text("".join(json.dumps(x)+"\n" for x in [header]+self.records))
        (output / "syscalls.jsonl").write_text("".join(json.dumps(x)+"\n" for x in self.events))
        metrics = {"scope": "workspace application syscalls; single process; excludes runtime outside root",
            "workspace_syscalls": sum(self.counts.values()), "syscalls_by_name": dict(self.counts),
            "replay_operations": len(self.records), "seek_operations_normalized": self.counts["lseek"],
            "actual_read_bytes": self.read_bytes, "actual_write_bytes": self.write_bytes,
            "requested_read_bytes": self.requested_read, "requested_write_bytes": self.requested_write,
            "repeated_read_bytes": self.repeat_bytes,
            "repeated_read_ratio": self.repeat_bytes/self.read_bytes if self.read_bytes else None,
            "repeat_definition": "Bytes previously read from same file identity, excluding ranges overwritten/truncated since that read; rename preserves identity",
            "excluded_syscalls_by_name": dict(self.excluded), "unsupported_workspace_operations": 0,
            "all_workspace_fds_closed": True, "physical_device_bytes": None, "fuse_protocol_operations": None}
        dump(output / "metrics.json", metrics)
        return metrics


def capture(root, output, initial_hash, argv):
    output.mkdir(parents=True, exist_ok=False)
    raw = output / "strace.log"
    command = ["strace", "-f", "-qq", "-ttt", "-T", "-yy", "-xx", "-s", str(MAX_IO),
               "-o", str(raw), "--"] + argv
    started = time.monotonic()
    try:
        with (output / "stdout.txt").open("wb") as out, (output / "stderr.txt").open("wb") as err:
            proc = subprocess.run(command, cwd=root, stdout=out, stderr=err, timeout=120,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        if proc.returncode: raise RuntimeError(f"Workload exited {proc.returncode}")
        metrics = Converter(root).convert(raw, initial_hash, output)
        dump(output / "capture-status.json", {"valid": True, "elapsed_s": time.monotonic()-started,
             "argv": argv, "root": str(root), "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest()})
        return metrics
    except Exception as exc:
        dump(output / "capture-status.json", {"valid": False, "error": str(exc), "argv": argv})
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--initial-hash", required=True)
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args()
    print(json.dumps(capture(args.root.resolve(), args.output.resolve(), args.initial_hash,
                             args.command[1:] if args.command[:1] == ["--"] else args.command)))
