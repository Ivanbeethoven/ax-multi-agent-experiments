#!/usr/bin/env python3
"""Bound one original cloud native phase; preserve live JSON stdout verbatim.

The supervisor stays in ax-slot-1 with the mount. Only its native worker and
descendants enter a fresh child cgroup, inheriting the original 2 CPU / 8 GiB.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import cgroup_limits, load, pinned, write_new

SLOT = Path("/sys/fs/cgroup/ax-slot-1")
BUDGET = dict(cpus=2, memory_bytes=8 * 1024**3, swap_bytes=0)
WORKER = Path(__file__).with_name("session-worker.py")
WORKER_SHA = "bc6030cb04942b18cf0987ff5e319a710db17c6af809c8c60eb8fed16d6415f0"
CLEANUP_SECONDS = 5


class Interrupted(Exception):
    pass


class Group:
    def __init__(self):
        if Path("/proc/self/cgroup").read_text().strip() != "0::/ax-slot-1":
            raise ValueError("Launch the supervisor in the original ax-slot-1")
        self.budget = cgroup_limits(BUDGET)
        self.path = SLOT / ("native-phase-" + uuid.uuid4().hex)
        self.path.mkdir(mode=0o700)
        if not (self.path / "cgroup.kill").is_file():
            self.path.rmdir()
            raise ValueError("A cgroup v2 child with cgroup.kill is required")
        # Ancestor limits charge all descendants. Do not enable controllers or
        # write child limits: the parent also contains the running FUSE mount.

    def enter(self):
        (self.path / "cgroup.procs").write_text(str(os.getpid()))

    def close(self):
        (self.path / "cgroup.kill").write_text("1")
        end = time.monotonic() + CLEANUP_SECONDS
        while "populated 0" not in (self.path / "cgroup.events").read_text().splitlines():
            if time.monotonic() >= end:
                raise RuntimeError("Native phase descendants remain; retain child cgroup")
            time.sleep(.05)
        self.path.rmdir()


def validate(request, output):
    timeout = request.get("total_timeout_seconds")
    if type(timeout) is not int or not 1 <= timeout <= 900:
        raise ValueError("Pinned phase total_timeout_seconds must be an integer in 1..900")
    if request.get("action") not in ("export", "import", "resume", "inspect-writer", "verify-stopped"):
        raise ValueError("One original native phase required")
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ValueError("New absolute native phase output required")
    if WORKER.is_symlink() or hashlib.sha256(WORKER.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != WORKER_SHA:
        raise ValueError("Original native worker SHA differs")
    if request["action"] == "resume":
        settings = load(request["config"])
        model = settings["provider"]["experiment"]["models"]["deepseek-v4-flash"]
        if model.get("limit") != {"context": 0, "output": 512}:
            raise ValueError("Prepare the qualified native 512-token model limit before handoff")
    return timeout


def run(request_ref, output):
    request_path = pinned(request_ref)
    request = load(request_path)
    output = Path(output)
    timeout = validate(request, output)
    group = Group()
    previous = {}
    process = None
    started = time.monotonic()
    result = dict(passed=False, total_timeout_seconds=timeout, cleanup_timeout_seconds=CLEANUP_SECONDS,
                  native_worker_sha256=WORKER_SHA, cgroup=str(group.path), inherited_budget=group.budget,
                  timed_out=False, descendants_cleared=False, automatic_retry=False,
                  native_stdout="inherited unchanged; coordinator observes original tool JSON")

    def interrupted(*_):
        raise Interrupted()

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, interrupted)
        argv = [sys.executable, "-B", str(WORKER), "--request", str(request_path),
                "--sha256", request_ref["sha256"], "--out", str(output)]
        # This supervisor is single-threaded. Membership is established before
        # exec, so OpenCode cannot fork outside the owned child cgroup.
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, preexec_fn=group.enter)
        result["native_worker_pid"] = process.pid
        try:
            result["exit_code"] = process.wait(timeout=max(.001, timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
    except BaseException as error:
        result["error_type"] = type(error).__name__
    finally:
        # Do not let repeated termination interrupt descendant cleanup.
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        try:
            group.close()
            result["descendants_cleared"] = True
        except Exception as error:
            result["cleanup_error_type"] = type(error).__name__
        if process is not None:
            try:
                result["exit_code"] = process.wait(timeout=CLEANUP_SECONDS)
            except subprocess.TimeoutExpired:
                result["reap_failed"] = True
        result["seconds"] = time.monotonic() - started
        result["passed"] = (result.get("exit_code") == 0 and result["descendants_cleared"] and
                            not result["timed_out"] and "error_type" not in result)
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_new(output / "bounded.json", result)
        for signum, previous_handler in previous.items():
            signal.signal(signum, previous_handler)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(dict(path=str(args.request), sha256=args.sha256), args.out)
    except Exception as error:
        # No request, configuration, argv, provider exception, or credentials.
        print(json.dumps(dict(passed=False, error_type=type(error).__name__, automatic_retry=False)), file=sys.stderr)
        return 1
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
