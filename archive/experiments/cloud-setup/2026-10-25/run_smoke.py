#!/usr/bin/env python3
"""Run sequential smoke cells, preserving commands, failures, metrics and rusage."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import threading
import time
import urllib.request

BASE = Path("/data/cloud-smoke/2026-10-25")
RESULTS = BASE / "results"
BIN = BASE / "target/release/fuse-replay"
DIAG = BASE / "target-latency/release/fuse-replay"
TRACE = BASE / "generated/workload.jsonl"
RUN_LABEL = ""


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def command(argv, timeout=60):
    started = time.time()
    try:
        p = subprocess.run([str(v) for v in argv], capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        result = {"argv": [str(v) for v in argv], "returncode": p.returncode,
                  "stdout": p.stdout, "stderr": p.stderr}
    except Exception as exc:
        result = {"argv": [str(v) for v in argv], "returncode": None, "error": repr(exc)}
    result.update(started_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                  wall_seconds=time.time() - started)
    with (RESULTS / "command-log.jsonl").open("a") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


def environment():
    commands = {
        "uname": ["uname", "-a"], "os": ["cat", "/etc/os-release"],
        "cpu": ["lscpu"], "memory": ["cat", "/proc/meminfo"],
        "disk": ["df", "-B1", "/", "/data"], "xfs": ["xfs_info", "/data"],
        "mounts": ["findmnt", "--json"], "ports": ["ss", "-lntp"],
        "docker": ["docker", "version"], "docker_data": ["docker", "info", "--format", "{{.DockerRootDir}}"],
        "juicefs": ["juicefs", "--version"], "brewfs": ["/data/bin/brewfs", "--version"],
        "rustfs": ["/data/bin/rustfs", "--version"], "redis": ["redis-server", "--version"],
        "redis_info": ["redis-cli", "-p", "6380", "info", "server"],
        "rustc": ["/root/.cargo/bin/rustc", "--version", "--verbose"],
        "cargo": ["/root/.cargo/bin/cargo", "--version"],
        "services": ["systemctl", "--no-pager", "--full", "status", "brewfs", "brewfs-redis", "brewfs-rustfs"],
        "brewfs_info_default_environment": ["/data/bin/brewfs", "info", "/mnt/brewfs"],
        "brewfs_info": ["env", "-u", "XDG_RUNTIME_DIR", "/data/bin/brewfs", "info", "/mnt/brewfs"],
        "brewfs_mountpoint": ["mountpoint", "/mnt/brewfs"],
        "jfs_mountpoint": ["mountpoint", "/mnt/jfs"],
        "brewfs_config": ["cat", "/data/brewfs-config/mount.yaml"],
        "systemd_units": ["systemctl", "cat", "brewfs", "brewfs-redis", "brewfs-rustfs"],
    }
    result = {"collected_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "python": platform.python_version(), "commands": {k: command(v) for k, v in commands.items()}}
    result["hashes"] = {}
    for path in (BIN, DIAG, Path("/data/bin/brewfs"), Path("/data/bin/rustfs"),
                 BASE / "install_brewfs_single_node.sh"):
        if path.exists():
            result["hashes"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    save(RESULTS / "environment.json", result)
    return result


def setup_jfs():
    results = {}
    results["format_help"] = command(["juicefs", "format", "--help"])
    results["mount_help"] = command(["juicefs", "mount", "--help"])
    results["format"] = command(["juicefs", "format", "--storage", "s3", "--bucket",
        "http://127.0.0.1:9000/jfs-smoke-20261025", "--access-key", "rustfsadmin", "--secret-key",
        "rustfsadmin", "--compress", "none", "--trash-days", "0", "redis://127.0.0.1:6380/1", "jfs-smoke-20261025"], 180)
    Path("/mnt/jfs").mkdir(exist_ok=True)
    results["mount"] = command(["juicefs", "mount", "-d", "--no-syslog", "--no-usage-report", "--no-bgjob",
        "--backup-meta", "0", "--cache-dir", "/data/jfs-cache", "--cache-size", "8192",
        "--buffer-size", "2048", "--log", "/data/jfs-mount.log", "--metrics", "127.0.0.1:9567",
        "redis://127.0.0.1:6380/1", "/mnt/jfs"], 120)
    results["mountpoint"] = command(["mountpoint", "/mnt/jfs"])
    save(RESULTS / "juicefs-setup.json", results)
    for name, mount in (("brewfs", "/mnt/brewfs"), ("jfs", "/mnt/jfs")):
        if command(["mountpoint", "-q", mount])["returncode"] != 0:
            continue
        path = Path(mount) / "cloud-smoke-canary-20261025"
        try:
            with path.open("xb") as f:
                f.write(b"cloud-smoke-20261025\n")
                f.flush()
                os.fsync(f.fileno())
            content = path.read_bytes()
            save(RESULTS / f"{name}-read-write-check.json", {"ok": content == b"cloud-smoke-20261025\n", "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        except Exception as exc:
            save(RESULTS / f"{name}-read-write-check.json", {"ok": False, "error": repr(exc)})
    environment()


def snapshot(label, name):
    snap = {"at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for source, path in (("brewfs_stats", Path("/mnt/brewfs/.stats")), ("jfs_stats", Path("/mnt/jfs/.stats"))):
        try:
            # Only regular control pseudo-files; no accesslog stream.
            snap[source] = path.read_text()
        except Exception as exc:
            snap[source] = {"error": repr(exc)}
    try:
        snap["jfs_metrics"] = urllib.request.urlopen("http://127.0.0.1:9567/metrics", timeout=3).read().decode()
    except Exception as exc:
        snap["jfs_metrics"] = {"error": repr(exc)}
    snap["redis_db0"] = command(["redis-cli", "-p", "6380", "-n", "0", "dbsize"])
    snap["redis_db1"] = command(["redis-cli", "-p", "6380", "-n", "1", "dbsize"])
    save(RESULTS / f"{name}-{label}-backend.json", snap)
    return snap


def process_ids():
    ids = {}
    for name in ("brewfs", "brewfs-rustfs", "brewfs-redis"):
        p = command(["systemctl", "show", "-p", "MainPID", "--value", name])
        try:
            ids[name] = int(p["stdout"].strip())
        except Exception:
            pass
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                cmd = (entry / "cmdline").read_bytes()
                argv = cmd.replace(b"\x00", b" ").split()
                if argv and argv[0].rsplit(b"/", 1)[-1] == b"juicefs" and b"mount" in argv[1:3]:
                    ids["juicefs"] = int(entry.name)
            except OSError:
                pass
    return {k: v for k, v in ids.items() if v}


def read_proc(ids):
    result = {}
    for name, pid in ids.items():
        try:
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            status = Path(f"/proc/{pid}/status").read_text().splitlines()
            fields = {x.split(":", 1)[0]: x.split(":", 1)[1].strip() for x in status if ":" in x}
            result[name] = {"pid": pid, "user_ticks": int(stat[11]), "system_ticks": int(stat[12]),
                            "rss_kib": int(fields.get("VmRSS", "0 kB").split()[0]),
                            "lifetime_hwm_kib": int(fields.get("VmHWM", "0 kB").split()[0])}
        except (OSError, ValueError) as exc:
            result[name] = {"pid": pid, "error": repr(exc)}
    return result


def fixture(root, spec):
    init = command([BIN, "init", "--root", root])
    if init["returncode"] != 0:
        raise RuntimeError(init)
    begin = time.monotonic()
    hashes = {}
    extent = spec["file_extent_bytes"]
    for s in range(spec["streams"]):
        directory = root / f"s{s}"
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
        for n in range(spec["files_per_stream"]):
            rel = f"s{s}/f{n}.bin"
            data = hashlib.shake_256(f"ai-statistical-smoke|{spec['seed']}|{rel}".encode()).digest(extent)
            with (root / rel).open("xb") as f:
                os.fchmod(f.fileno(), 0o644)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            hashes[rel] = hashlib.sha256(data).hexdigest()
    fp = command([BIN, "fingerprint", "--root", root], 240)
    if fp["returncode"] != 0:
        raise RuntimeError(fp)
    return {"fingerprint": json.loads(fp["stdout"])["initial_tree_sha256"],
            "bytes": spec["files"] * extent, "files": spec["files"], "file_sha256": hashes,
            "seconds": time.monotonic() - begin, "cache": "full fingerprint read warms data and metadata"}


def run_cell(name, mount, mode, spec):
    label = f"{name}-{mode}"
    root = Path(mount) / f"cloud-smoke-20261025-{RUN_LABEL}{mode}"
    result = {"filesystem": name, "mode": mode, "root": str(root)}
    try:
        if command(["mountpoint", "-q", mount])["returncode"] != 0:
            raise RuntimeError(f"not a mountpoint: {mount}")
        result["fixture"] = fixture(root, spec)
        header, rest = TRACE.read_text().split("\n", 1)
        header = json.loads(header)
        existing = header.get("initial_tree_sha256")
        if existing and existing != result["fixture"]["fingerprint"]:
            raise RuntimeError("initial tree differs between cells")
        if not existing:
            header["initial_tree_sha256"] = result["fixture"]["fingerprint"]
            TRACE.write_text(json.dumps(header, separators=(",", ":")) + "\n" + rest)
        result["trace_sha256"] = hashlib.sha256(TRACE.read_bytes()).hexdigest()
        result["validation"] = command([BIN, "validate", "--trace", TRACE])
        if result["validation"]["returncode"] != 0:
            raise RuntimeError(result["validation"])
        time.sleep(3)
        snapshot("before", label)
        ids = process_ids()
        before = read_proc(ids)
        samples = []
        stop = threading.Event()
        def monitor():
            while not stop.is_set():
                samples.append({"monotonic_seconds": time.monotonic(), "processes": read_proc(ids)})
                stop.wait(.1)
        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        argv = [str(BIN if mode == "off" else DIAG), "run", "--trace", str(TRACE), "--root", str(root),
                "--output", str(RESULTS / f"{label}.result.json"), "--workers", "4", "--pace", "asap",
                "--timing", mode, "--timeout-seconds", "180"]
        result["argv"] = argv
        begin = time.monotonic()
        with (RESULTS / f"{label}.stdout.log").open("xb") as stdout, (RESULTS / f"{label}.stderr.log").open("xb") as stderr:
            proc = subprocess.Popen(["timeout", "--signal=TERM", "--kill-after=5s", "300s"] + argv, stdout=stdout, stderr=stderr)
            _, status, usage = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
        elapsed = time.monotonic() - begin
        stop.set()
        monitor_thread.join()
        after = read_proc(ids)
        result["rusage"] = {"returncode": proc.returncode, "process_wall_seconds": elapsed,
            "user_seconds": usage.ru_utime, "system_seconds": usage.ru_stime,
            "cpu_percent_one_core": (usage.ru_utime + usage.ru_stime) / elapsed * 100,
            "max_rss_kib": usage.ru_maxrss, "voluntary_context_switches": usage.ru_nvcsw,
            "involuntary_context_switches": usage.ru_nivcsw,
            "scope": "timeout wrapper plus replay child, including parse, preparation and result writing"}
        result["daemon_processes"] = {"clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
            "before": before, "after": after,
            "sampled_peak_rss_kib": {n: max((x["processes"].get(n, {}).get("rss_kib", 0) for x in samples), default=0) for n in ids},
            "samples": len(samples), "sampling_interval_seconds": .1,
            "scope": "replay process lifetime, excludes fixture/fingerprint and post-run settle"}
        save(RESULTS / f"{label}.process-samples.json", samples)
        time.sleep(5)
        snapshot("after", label)
        result["post_tree"] = command([BIN, "fingerprint", "--root", root], 240)
        raw = json.loads((RESULTS / f"{label}.result.json").read_text())
        data = raw.get("totals", {}).get("diagnostic_latency_samples_ns")
        if data:
            data.sort()
            def q(p):
                return data[max(0, math.ceil(len(data) * p) - 1)] / 1e6
            result["diagnostic_latency"] = {"sample_count": len(data), "p50_ms": q(.5), "p99_ms": q(.99),
                "definition": "nearest rank across all logical operations, including scaffold; separate timing=op diagnostic; safe path resolution included, post-call content hashing excluded"}
        else:
            result["diagnostic_latency"] = None
    except Exception as exc:
        result["error"] = repr(exc)
    save(RESULTS / f"{label}.measurement.json", result)
    print(json.dumps({k: v for k, v in result.items() if k in ("filesystem", "mode", "error", "rusage", "diagnostic_latency")}), flush=True)
    return result


def main():
    global RESULTS, RUN_LABEL
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["setup-jfs", "environment", "run"])
    p.add_argument("--label", default="", help="new result subdirectory and fixture prefix, e.g. stable")
    p.add_argument("--timings", choices=("off", "op", "both"), default="both")
    a = p.parse_args()
    if a.label:
        if not a.label.isalnum():
            p.error("label must be alphanumeric")
        RESULTS = RESULTS / a.label
        RESULTS.mkdir(exist_ok=False)
        RUN_LABEL = a.label + "-"
    if a.action == "setup-jfs":
        setup_jfs()
    elif a.action == "environment":
        environment()
    else:
        spec = json.loads((BASE / "generated/synthesis.json").read_text())["fixture"]
        cells = []
        # One primary pair, followed by separate diagnostic pair, never concurrent.
        for mode in (("off", "op") if a.timings == "both" else (a.timings,)):
            for name, mount in (("brewfs", "/mnt/brewfs"), ("jfs", "/mnt/jfs")):
                cells.append(run_cell(name, mount, mode, spec))
        save(RESULTS / "measurements.json", cells)
        environment()


if __name__ == "__main__":
    main()
