"""Run paired ENOENT regressions and complete Rust checks on an isolated A355."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import socket
import subprocess
import time


FEATURES = "fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability"
PREFIX = "workspace::meta_layer::tests::packed_open_preparation_tests::enoent_f24_tests::"
UNIT_TESTS = [
    ("enoent_f24_lookup_to_stat_interleaving", 101),
    ("enoent_f24_retired_inode_getattr_interleaving", 101),
    ("lookup_revalidates_lower_and_bounds_repeated_replacements", 101),
    ("enoent_f24_retained_handle_and_permission_controls", 0),
    ("lookup_and_getattr_preserve_backend_errors_with_live_handle", 0),
    ("lookup_dangling_dentry_is_not_hidden_by_revalidation", 0),
]
KERNEL_TESTS = [
    ("lookup_stat_kernel_revalidates_without_userspace_retry", 101),
    ("retired_getattr_kernel_revalidates_before_open_and_stat", 101),
    ("repeated_getattr_replacement_exhausts_kernel_retry_as_stale", 101),
    ("kernel_retained_handle_preserves_absence_permissions_and_backend_errors", 0),
]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def available_memory():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable missing")


def processes():
    return [line.split(None, 3) for line in subprocess.check_output(
        ["ps", "-eo", "pid=,pgid=,rss=,comm="], text=True
    ).splitlines()]


def foreign_jobs(group=None):
    return [row for row in processes() if len(row) == 4
            and row[3] in ("cargo", "rustc", "brewfs", "fio", "juicefs")
            and int(row[1]) != group]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--role", choices=("original", "candidate"), default="candidate")
    parser.add_argument("--base-commit", required=True)
    args = parser.parse_args()
    args.source = args.source.resolve(strict=True)
    args.out.mkdir(parents=True, exist_ok=False)
    report = dict(started_utc=now(), pid=os.getpid(), source=str(args.source),
                  base_commit=args.base_commit, role=args.role, commands=[],
                  performance_measurement=False, peak_process_group_rss=0)
    child = None
    lock = None
    redis = None
    redis_log = None

    def save():
        temporary = args.out / "report.tmp"
        temporary.write_text(json.dumps(report, indent=2))
        temporary.replace(args.out / "report.json")

    def stop_owned():
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=8)

    def interrupted(signum, frame):
        raise InterruptedError(f"signal {signum}")

    def limit_address_space():
        resource.setrlimit(resource.RLIMIT_AS, (64 << 30, 64 << 30))

    def run(name, command, timeout=5400, expected=0, required=False):
        nonlocal child
        entry = dict(name=name, command=[str(value) for value in command],
                     started_utc=now(), expected_exit=expected)
        report["commands"].append(entry)
        save()
        with (args.out / (name + ".log")).open("xb") as log:
            child = subprocess.Popen(command, cwd=args.source, env=env,
                                     stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True,
                                     preexec_fn=limit_address_space,
                                     pass_fds=(lock.fileno(),))
            entry["pid"] = child.pid
            started = time.monotonic()
            while child.poll() is None:
                rss = sum(int(row[2]) * 1024 for row in processes()
                          if len(row) == 4 and int(row[1]) == child.pid)
                report["peak_process_group_rss"] = max(report["peak_process_group_rss"], rss)
                other = foreign_jobs(child.pid)
                if rss > 48 << 30 or available_memory() < 128 << 30 or other or time.monotonic() - started > timeout:
                    entry["guard_stop"] = dict(rss=rss, available=available_memory(), foreign=other)
                    stop_owned()
                    break
                save()
                time.sleep(1)
            entry.update(exit_code=child.wait(), seconds=time.monotonic() - started, finished_utc=now())
            child = None
        save()
        if "guard_stop" in entry or (required and entry["exit_code"] != expected):
            raise RuntimeError(f"{name} failed; see its log")
        return args.out / (name + ".log")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        save()
        assert socket.gethostname() == "A355"
        lock = Path("/run/lock/ax-overlay-open-diagnostic.lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report["lock_acquired_utc"] = now()
        assert not foreign_jobs(), "another task is active"
        assert available_memory() > 160 << 30
        assert shutil.disk_usage(args.out).free > 100 << 30
        assert os.readlink("/proc/self/ns/net") != os.environ["ENOENT_HOST_NETNS"]
        assert os.readlink("/proc/self/ns/mnt") != os.environ["ENOENT_HOST_MNTNS"]
        subprocess.run(["mount", "--make-rprivate", "/"], check=True)
        subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
        assert [name for _, name in socket.if_nameindex()] == ["lo"]
        assert json.loads(subprocess.check_output(["ip", "-j", "route"])) == []
        cpus = sorted(os.sched_getaffinity(0))[:4]
        os.sched_setaffinity(0, cpus)
        os.nice(19)
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], check=True)
        report["environment_limits"] = dict(cpus=cpus, jobs=4, nice=19, io_class="idle", rss_limit=48 << 30)
        report["isolation"] = dict(network="loopback only", mount="private", kernel=os.uname().release)
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(("CARGO_PROFILE_", "BREWFS_")) or key in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER"):
                del env[key]
        temporary = args.out / "tmp"
        temporary.mkdir()
        env.update(PATH=str(args.toolchain) + ":" + env["PATH"], RUSTC=str(args.toolchain / "rustc"),
                   CARGO_HOME=str(args.cargo_home), CARGO_TARGET_DIR=str(args.target), TMPDIR=str(temporary),
                   CARGO_INCREMENTAL="0", CARGO_PROFILE_DEV_DEBUG="0", CARGO_PROFILE_TEST_DEBUG="0",
                   CARGO_BUILD_JOBS="4", CARGO_NET_OFFLINE="true", RUST_TEST_THREADS="1", CARGO_TERM_COLOR="never")
        report["build_environment"] = {key: value for key, value in env.items() if key.startswith(("CARGO_", "RUST")) or key == "TMPDIR"}
        run("toolchain", [args.toolchain / "rustc", "-Vv"], timeout=20, required=True)
        cargo = [str(args.toolchain / "cargo")]
        feature = ["--no-default-features", "--features", FEATURES]
        run("clean-package", cargo + ["clean", "--package", "brewfs"], timeout=120, required=True)
        log = run("compile-tests", cargo + ["test", "--locked", "--offline", *feature, "--lib", "--no-run", "--message-format=json-render-diagnostics"], required=True)
        artifacts = []
        for line in log.read_text().splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("reason") == "compiler-artifact" and item.get("executable") and item["target"]["name"] == "brewfs" and item["profile"]["test"]:
                assert not item.get("fresh"), "BrewFS must be compiled for this source tree"
                artifacts.append(item)
        assert len(artifacts) == 1
        binary = args.out / "brewfs-lib-tests"
        shutil.copy2(artifacts[0]["executable"], binary)
        report["test_binary"] = str(binary)
        run("inventory", [str(binary), "--list", "--color=never"], timeout=30, required=True)
        redis_directory = args.out / "redis"
        redis_directory.mkdir()
        redis_log = (args.out / "redis.log").open("xb")
        redis_command = ["/usr/bin/redis-server", "--bind", "127.0.0.1", "--port", "16379", "--save", "", "--appendonly", "no", "--maxmemory", "256mb", "--maxmemory-policy", "noeviction", "--dir", str(redis_directory)]
        redis = subprocess.Popen(redis_command, stdin=subprocess.DEVNULL, stdout=redis_log,
                                 stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(lock.fileno(),))
        report["redis"] = dict(command=redis_command, pid=redis.pid)
        for _ in range(50):
            assert redis.poll() is None
            ping = subprocess.run(["/usr/bin/redis-cli", "-p", "16379", "ping"], capture_output=True)
            if ping.returncode == 0 and ping.stdout.strip() == b"PONG":
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Redis did not become ready")
        env["BREWFS_ENOENT_REDIS_URL"] = "redis://127.0.0.1:16379/0"
        run("redis-before", ["/usr/bin/redis-cli", "-p", "16379", "info", "commandstats"], timeout=10, required=True)
        for iteration in range(1, 4):
            for mounted, tests in ((False, UNIT_TESTS), (True, KERNEL_TESTS)):
                for name, original_exit in tests:
                    fullname = PREFIX + ("kernel::" if mounted else "") + name
                    command = [str(binary), "--exact", fullname, "--nocapture", "--test-threads=1"] + (["--ignored"] if mounted else [])
                    expected = original_exit if args.role == "original" else 0
                    run(name + "-" + str(iteration), command, timeout=120, expected=expected)
        run("redis-after", ["/usr/bin/redis-cli", "-p", "16379", "info", "commandstats"], timeout=10, required=True)
        failures = [command["name"] for command in report["commands"] if command["exit_code"] != command["expected_exit"]]
        assert not failures, f"diagnostic assertions failed: {failures}"
        if args.role == "candidate":
            run("fmt", cargo + ["fmt", "--all", "--check"], timeout=120)
            for flavor, options in (("default", []), ("features", feature)):
                for operation in ("check", "build"):
                    run(flavor + "-" + operation, cargo + [operation, "--locked", "--offline", "--workspace", *options])
                run(flavor + "-all-tests", cargo + ["test", "--locked", "--offline", "--workspace", "--lib", "--bins", *options, "--", "--test-threads=4"])
                run(flavor + "-clippy", cargo + ["clippy", "--locked", "--offline", "--workspace", "--all-targets", *options, "--", "-D", "warnings"])
            for name in ("run_perf_in_container.sh", "run_redis_perf.sh", "run_juicefs_perf_in_container.sh", "run_juicefs_perf.sh"):
                run("syntax-" + name, ["bash", "-n", "docker/compose-xfstests/" + name], timeout=30)
            for name in ("test_perf_report_delta.sh", "test_juicefs_direct_matrix.sh", "test_juicefs_perf_report.sh"):
                run(name, ["bash", "docker/compose-xfstests/" + name], timeout=120)
            for runtime in ("fuse-tokio-runtime", "fuse-io-uring-runtime"):
                run(runtime, cargo + ["check", "--locked", "--offline", "-p", "brewfs", "--no-default-features", "--features", runtime])
            metadata_log = run("cargo-metadata", cargo + ["metadata", "--no-deps", "--format-version", "1"], timeout=30, required=True)
            metadata = json.loads(metadata_log.read_text())
            report["rfuse3_workspace_member"] = any(package["name"] == "rfuse3" and package["id"] in metadata["workspace_members"] for package in metadata["packages"])
            if report["rfuse3_workspace_member"]:
                for runtime in ("tokio-runtime", "io-uring-runtime", "async-io-runtime"):
                    run("rfuse3-" + runtime, cargo + ["check", "--locked", "--offline", "-p", "rfuse3", "--no-default-features", "--features", runtime])
        report["completed"] = True
        report["passed"] = all(command["exit_code"] == command["expected_exit"] for command in report["commands"])
    except BaseException as error:
        report["error"] = repr(error)
        stop_owned()
    finally:
        if redis is not None:
            redis.terminate()
            try:
                report["redis"]["exit_code"] = redis.wait(timeout=10)
            except subprocess.TimeoutExpired:
                redis.kill()
                report["redis"]["exit_code"] = redis.wait(timeout=10)
            redis_log.close()
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
