"""Continue resource-interrupted CI against the same unchanged source tree."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import socket
import subprocess
import tarfile
import time

from run_checks import FEATURES, available_memory, now, processes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    args = parser.parse_args()
    prior = json.loads(args.prior.read_text())
    assert prior["role"] == "candidate" and prior.get("lock_released_utc")
    assert prior.get("redis", {}).get("exit_code") == 0
    diagnostics = [c for c in prior["commands"] if re.search(r"-[123]$", c["name"])]
    assert len(diagnostics) == 30 and all(c["exit_code"] == 0 for c in diagnostics)
    failed = [c for c in prior["commands"] if c["exit_code"] != 0]
    assert failed and all(c.get("guard_stop") for c in failed)
    args.out.mkdir(parents=True, exist_ok=False)
    source = Path(prior["source"]).resolve(strict=True)
    source_files = 0
    with tarfile.open(args.source_archive) as archive:
        for item in archive:
            relative = Path(item.name)
            if relative.parts[0] != source.name:
                continue
            assert item.isfile() and not relative.is_absolute() and ".." not in relative.parts
            destination = source.joinpath(*relative.parts[1:])
            assert destination.resolve().is_relative_to(source)
            assert destination.read_bytes() == archive.extractfile(item).read(), str(relative)
            source_files += 1
    assert source_files > 0
    report = {key: prior[key] for key in ("source", "base_commit", "role", "build_environment", "redis", "test_binary", "environment_limits", "isolation")}
    report.update(started_utc=now(), pid=os.getpid(), continued_from=str(args.prior),
                  commands=[], performance_measurement=False, peak_process_group_rss=0,
                  source_files_compared=source_files, source_content_matches_archive=True)
    for command in prior["commands"]:
        if command["exit_code"] == 0:
            copied = dict(command, from_report=str(args.prior))
            report["commands"].append(copied)
            shutil.copyfile(args.prior.parent / (command["name"] + ".log"), args.out / (command["name"] + ".log"))
    lock = None
    child = None

    def active_workloads(group=None):
        workloads = []
        for row in processes():
            if len(row) != 4 or int(row[1]) == group:
                continue
            if row[3] in ("cargo", "rustc"):
                key = row[0]
                report.setdefault("concurrent_compilers", {})[key] = dict(pid=int(row[0]), group=int(row[1]), name=row[3])
            elif row[3].startswith(("brewfs", "juicefs", "fio", "fs-probe", "fuse-replay", "smallfiles")):
                workloads.append(row)
        return workloads

    def save():
        path = args.out / "report.tmp"
        path.write_text(json.dumps(report, indent=2))
        path.replace(args.out / "report.json")

    def stop():
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=8)

    def interrupted(signum, frame):
        raise InterruptedError(f"signal {signum}")

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (64 << 30, 64 << 30))

    def run(name, command, timeout=5400):
        nonlocal child
        if any(row["name"] == name for row in report["commands"]):
            return args.out / (name + ".log")
        entry = dict(name=name, command=command, started_utc=now(), expected_exit=0)
        report["commands"].append(entry)
        save()
        with (args.out / (name + ".log")).open("xb") as log:
            child = subprocess.Popen(command, cwd=source, env=env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                     pass_fds=(lock.fileno(),), preexec_fn=limits)
            entry["pid"] = child.pid
            started = time.monotonic()
            while child.poll() is None:
                rss = sum(int(row[2]) * 1024 for row in processes() if len(row) == 4 and int(row[1]) == child.pid)
                report["peak_process_group_rss"] = max(report["peak_process_group_rss"], rss)
                other = active_workloads(child.pid)
                if rss > 48 << 30 or available_memory() < 128 << 30 or other or time.monotonic() - started > timeout:
                    entry["guard_stop"] = dict(rss=rss, available=available_memory(), foreign=other)
                    stop()
                    break
                save()
                time.sleep(1)
            entry.update(exit_code=child.wait(), seconds=time.monotonic() - started, finished_utc=now())
            child = None
        save()
        if "guard_stop" in entry:
            raise RuntimeError(name + " stopped by resource protection")
        return args.out / (name + ".log")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        save()
        assert socket.gethostname() == "A355"
        lock = Path("/run/lock/ax-overlay-open-diagnostic.lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report["lock_acquired_utc"] = now()
        assert not active_workloads() and available_memory() > 160 << 30
        assert os.readlink("/proc/self/ns/net") != os.environ["ENOENT_HOST_NETNS"]
        assert os.readlink("/proc/self/ns/mnt") != os.environ["ENOENT_HOST_MNTNS"]
        subprocess.run(["mount", "--make-rprivate", "/"], check=True)
        subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
        assert [name for _, name in socket.if_nameindex()] == ["lo"]
        assert json.loads(subprocess.check_output(["ip", "-j", "route"])) == []
        os.sched_setaffinity(0, report["environment_limits"]["cpus"])
        os.nice(19)
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], check=True)
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(("CARGO_", "RUST", "BREWFS_")):
                del env[key]
        env.update(report["build_environment"])
        toolchain = Path(env["RUSTC"]).parent
        env["PATH"] = str(toolchain) + ":" + env["PATH"]
        cargo = [str(toolchain / "cargo")]
        feature = ["--no-default-features", "--features", FEATURES]
        run("fmt", cargo + ["fmt", "--all", "--check"], 120)
        for flavor, options in (("default", []), ("features", feature)):
            for operation in ("check", "build"):
                run(flavor + "-" + operation, cargo + [operation, "--locked", "--offline", "--workspace", *options])
            run(flavor + "-all-tests", cargo + ["test", "--locked", "--offline", "--workspace", "--lib", "--bins", *options, "--", "--test-threads=4"])
            run(flavor + "-clippy", cargo + ["clippy", "--locked", "--offline", "--workspace", "--all-targets", *options, "--", "-D", "warnings"])
        for name in ("run_perf_in_container.sh", "run_redis_perf.sh", "run_juicefs_perf_in_container.sh", "run_juicefs_perf.sh"):
            run("syntax-" + name, ["bash", "-n", "docker/compose-xfstests/" + name], 30)
        for name in ("test_perf_report_delta.sh", "test_juicefs_direct_matrix.sh", "test_juicefs_perf_report.sh"):
            run(name, ["bash", "docker/compose-xfstests/" + name], 120)
        for runtime in ("fuse-tokio-runtime", "fuse-io-uring-runtime"):
            run(runtime, cargo + ["check", "--locked", "--offline", "-p", "brewfs", "--no-default-features", "--features", runtime])
        metadata = json.loads(run("cargo-metadata", cargo + ["metadata", "--no-deps", "--format-version", "1"], 30).read_text())
        report["rfuse3_workspace_member"] = any(package["name"] == "rfuse3" and package["id"] in metadata["workspace_members"] for package in metadata["packages"])
        if report["rfuse3_workspace_member"]:
            for runtime in ("tokio-runtime", "io-uring-runtime", "async-io-runtime"):
                run("rfuse3-" + runtime, cargo + ["check", "--locked", "--offline", "-p", "rfuse3", "--no-default-features", "--features", runtime])
        report["completed"] = True
        report["passed"] = all(command["exit_code"] == 0 for command in report["commands"])
    except BaseException as error:
        report["error"] = repr(error)
        stop()
    finally:
        report["finished_utc"] = now()
        report["mounts_after"] = subprocess.check_output(["findmnt", "-rn", "-o", "TARGET,FSTYPE"], text=True)
        if lock is not None:
            lock.close()
            report["lock_released_utc"] = now()
        save()
    if not report.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
