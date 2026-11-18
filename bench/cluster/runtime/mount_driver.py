#!/usr/bin/env python3
"""Hash-free FUSE mount driver, cgroup runner, upload-drain waiter, and clean unmounter for run-001.

Operates locally on any of the 6 cluster hosts. Strictly enforces:
- Profile-specific MemoryMax, MemorySwapMax=0 per mount under ax-clients.slice
- Zero secrets on process cmdline (AWS/JuiceFS/Redis credentials via private 0600 files)
- Immediate post-mount verification via `bench.cluster.audit_mount`
- Upload-drain check requiring two zero-pending observations >= 1.0s apart
"""

from runtime_context import RUN_ROOT, REDIS_PORT

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

OPENER = build_opener(ProxyHandler({}))

BREWFS_PENDING_GAUGES = (
    "brewfs_writeback_dirty_bytes",
    "brewfs_writeback_live_dirty_bytes",
    "brewfs_writeback_pending_upload_bytes",
    "brewfs_writeback_recent_pending_upload_bytes",
    "brewfs_writeback_inflight_bytes",
    "brewfs_writeback_stage_inflight_bytes",
    "brewfs_writeback_remote_upload_inflight_bytes",
    "brewfs_writeback_metadata_inflight",
)

JUICEFS_PENDING_GAUGES = (
    "juicefs_staging_writing_blocks",
    "juicefs_staging_blocks",
    "juicefs_staging_block_bytes",
    "juicefs_object_request_uploading",
)


def safe_unit_name(mount_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", mount_id)
    return f"ax-direct-mnt-{cleaned}.service"


def load_redis_pw() -> str:
    p = Path("/srv/ax-cluster/private/redis.env")
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.startswith("REDIS_PASSWORD="):
            return ln.split("=", 1)[1].strip()
    raise RuntimeError("REDIS_PASSWORD missing in /srv/ax-cluster/private/redis.env")


def scrape_metrics(port: int, timeout: float = 2.0, mount_dir: Path | None = None, engine: str = "") -> tuple[str, dict[str, float]]:
    url = f"http://127.0.0.1:{port}/metrics"
    with OPENER.open(Request(url), timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} from {url}")
        raw = resp.read(8 * 1024 * 1024).decode("utf-8", errors="replace")
    if mount_dir is not None and engine.startswith("brewfs"):
        vstats = mount_dir / ".stats"
        try:
            extra = vstats.read_text(encoding="utf-8", errors="replace")
            if extra:
                raw = raw + "\n# --- .stats ---\n" + extra
        except OSError:
            pass
    parsed: dict[str, float] = {}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        if len(toks) < 2:
            continue
        key = toks[0]
        base_key = key.split("{", 1)[0]
        try:
            val = float(toks[1])
        except ValueError:
            continue
        parsed[key] = val
        if "{" not in key:
            parsed[base_key] = val
        else:
            parsed[base_key] = parsed.get(base_key, 0.0) + val
            if 'method="' in key:
                meth = key.split('method="', 1)[1].split('"', 1)[0]
                if base_key in ("juicefs_object_request_data_bytes", "juicefs_object_request_durations_histogram_seconds_count"):
                    syn = f"{base_key}_{meth}"
                    parsed[syn] = parsed.get(syn, 0.0) + val
                    if base_key == "juicefs_object_request_durations_histogram_seconds_count":
                        cnt_key = f"juicefs_object_request_count_{meth}"
                        parsed[cnt_key] = parsed.get(cnt_key, 0.0) + val
            if (
                base_key == "brewfs_object_read_started_total"
                and 'layer="backend_body"' in key
                and any(kd in key for kd in ('kind="group_index"', 'kind="group_metadata"', 'kind="manifest"'))
            ):
                syn_m = "brewfs_packed_metadata_backend_requests_total"
                parsed[syn_m] = parsed.get(syn_m, 0.0) + val
    return raw, parsed


def read_cgroup_snapshot(cg: Path) -> dict:
    if not cg.is_dir():
        return {}
    out = {}
    for name in ("memory.current", "memory.peak", "memory.swap.current", "pids.current"):
        p = cg / name
        if p.is_file():
            s = p.read_text(encoding="utf-8", errors="replace").strip()
            if s.isdigit():
                out[name.replace(".", "_")] = int(s)
    for name in ("cpu.stat", "memory.events", "memory.stat"):
        p = cg / name
        if p.is_file():
            kv = {}
            for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = ln.split()
                if len(parts) == 2 and parts[1].isdigit():
                    kv[parts[0]] = int(parts[1])
            out[name.replace(".", "_")] = kv
    io_p = cg / "io.stat"
    if io_p.is_file():
        out["io_stat"] = io_p.read_text(encoding="utf-8", errors="replace").strip()
    return out


def ensure_unmounted(mount_dir: Path):
    if mount_dir.exists() and os.path.ismount(mount_dir):
        subprocess.run(["fusermount3", "-u", str(mount_dir)], capture_output=True)
        if os.path.ismount(mount_dir):
            subprocess.run(["umount", str(mount_dir)], capture_output=True)
        if os.path.ismount(mount_dir):
            subprocess.run(["umount", "-l", str(mount_dir)], capture_output=True)


def cmd_start(args):
    mounts_doc = json.loads(args.mounts.read_text(encoding="utf-8"))
    record = next((r for r in mounts_doc["mounts"] if r["id"] == args.id), None)
    if record is None:
        raise ValueError(f"Mount id {args.id} not found in {args.mounts}")
    spec = json.loads(args.spec.read_text(encoding="utf-8"))

    mount_id = record["id"]
    unit = safe_unit_name(mount_id)
    mount_dir = Path(record["mount_directory"])
    cache_root = Path(record["cache_root"])
    port = int(record["metrics_port"])
    engine = record["engine"]
    if engine == "brewfs-native-overlay" and spec.get("packed_manifest_key"):
        raise ValueError("A Native Overlay mount cannot use a Packed manifest")

    # Clean up any stale unit with the exact same name before starting
    subprocess.run(["systemctl", "stop", unit], capture_output=True)
    subprocess.run(["systemctl", "reset-failed", unit], capture_output=True)
    ensure_unmounted(mount_dir)
    mount_dir.mkdir(parents=True, exist_ok=True)

    allowed_cache_prefix = str(RUN_ROOT / "cache") + "/"
    if not str(cache_root).startswith(allowed_cache_prefix):
        raise ValueError(f"Unsafe cache_root outside run-001 cache dir: {cache_root}")

    if record["cache_state"] in {"cold", "cross-host-cold"}:
        if cache_root.exists():
            shutil.rmtree(cache_root)
        cache_root.mkdir(parents=True, exist_ok=True)
    elif record["cache_state"] == "disk-reuse":
        cache_root.mkdir(parents=True, exist_ok=True)
    else:
        raise ValueError(f"start should not be called for cache_state={record['cache_state']}")

    priv_dir = Path("/srv/ax-cluster/private/direct-mounts")
    priv_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(priv_dir, 0o700)

    log_dir = (RUN_ROOT / "hosts") / spec["host_name"] / "mount-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{mount_id}.log"

    if engine in {"brewfs-native-overlay", "brewfs-packed", "brewfs-flat"}:
        redis_pw = load_redis_pw()
        cfg_path = priv_dir / f"{mount_id}.json"
        cfg_obj = {
            "mount_point": str(mount_dir),
            "volume_format": record["volume_format"],
            "workspace": spec.get("workspace_uuid"),
            "workspace_namespace": spec.get("workspace_namespace"),
            "workspace_operator_managed": engine != "brewfs-packed",
            "layout": {
                "chunk_size": 4194304,
                "block_size": 4194304,
            },
            "data": {
                "backend": "s3",
                "s3": {
                    "bucket": spec["s3_bucket"],
                    "endpoint": spec["s3_endpoint"],
                    "region": "us-east-1",
                    "max_concurrency": 16,
                    "force_path_style": True,
                    "disable_payload_checksum": True,
                },
            },
            "meta": {
                "backend": "redis",
                "redis": {
                    "url": f"redis://:{redis_pw}@175.168.3.55:{REDIS_PORT}/0",
                },
            },
            "cache": record["cache"],
        }
        if engine == "brewfs-flat":
            for key in ("workspace", "workspace_namespace", "workspace_operator_managed"):
                cfg_obj.pop(key)
            database = int(spec["redis_db"])
            if not 1 <= database <= 15:
                raise ValueError("Flat volumes require a dedicated Redis database")
            cfg_obj["meta"]["redis"]["url"] = f"redis://:{redis_pw}@175.168.3.55:{REDIS_PORT}/{database}"
        if spec.get("meta_backend") == "tikv":
            cfg_obj["meta"] = {"backend": "tikv", "tikv": {"pd_endpoints": spec["pd_endpoints"], "namespace": spec["workspace_namespace"]}}
        if spec.get("packed_manifest_key"):
            cfg_obj["packed_manifest_key"] = spec["packed_manifest_key"]
        cfg_path.write_text(json.dumps(cfg_obj, indent=2) + "\n", encoding="utf-8")
        os.chmod(cfg_path, 0o600)

        hint = str(spec.get("rustfs_ec_block_size_hint", "auto"))
        argv = [
            record["binary"],
            "mount",
            "--config",
            str(cfg_path),
            "--metrics",
            "--metrics-addr",
            f"127.0.0.1:{port}",
            "--s3-rustfs-ec-block-size-hint",
            hint,
            str(mount_dir),
        ]
    elif engine in {"juicefs", "juicefs-overlay"}:
        extra_flags = []
        if spec.get("subdir"):
            extra_flags.append(f"--subdir={spec['subdir']}")
        if spec.get("s3_bucket_url"):
            extra_flags.append(f"--bucket={spec['s3_bucket_url']}")
        opt_flags = [f"--{k}={v}" for k, v in record["options"].items()]
        if engine == "juicefs":
            meta_url = f"redis://175.168.3.55:{REDIS_PORT}/{spec['redis_db']}"
        else:
            meta_url = (
                f"forkredis://175.168.3.55:{REDIS_PORT}/0"
                f"?volume={spec['volume']}&branch={spec['branch']}"
                f"&fork_cache_bytes={record['fork_cache_bytes']}"
            )
        meta_url = spec.get("meta_url", meta_url)
        argv = [
            record["binary"],
            "--no-agent",
            "mount",
            *extra_flags,
            *opt_flags,
            meta_url,
            str(mount_dir),
        ]
    else:
        raise ValueError(f"Unsupported engine {engine}")

    systemd_cmd = [
        "systemd-run",
        f"--unit={unit}",
        "--slice=ax-clients.slice",
        "--collect",
        "-p",
        f"MemoryMax={record['memory_bytes']}",
        "-p",
        "MemorySwapMax=0",
        "-p",
        "MemoryAccounting=yes",
        "-p",
        "CPUAccounting=yes",
        "-p",
        "IOAccounting=yes",
        "-p",
        "TasksMax=16384",
        "-p",
        "LimitNOFILE=1048576",
        "-p",
        "EnvironmentFile=/srv/ax-cluster/private/aws.env",
        *[f"--setenv={k}={v}" for k, v in sorted(record["environment"].items())],
        "-p",
        f"StandardOutput=append:{log_path}",
        "-p",
        f"StandardError=append:{log_path}",
        "--",
        *argv,
    ]

    t_start_ns = time.monotonic_ns()
    t_utc_start_ns = time.time_ns()
    subprocess.run(systemd_cmd, check=True, capture_output=True, text=True)

    deadline = time.monotonic() + float(spec.get("mount_timeout_seconds", 90.0))
    raw_metrics = ""
    mount_ready_ns = None
    last_err = ""
    while time.monotonic() < deadline:
        if os.path.ismount(mount_dir):
            try:
                raw_metrics, _ = scrape_metrics(port, timeout=1.5)
                mount_ready_ns = time.monotonic_ns()
                break
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
        else:
            st = subprocess.run(
                ["systemctl", "show", "-p", "ActiveState,Result", "--value", unit],
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            if st and st[0] in {"failed", "inactive"}:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:] if log_path.exists() else ""
                raise RuntimeError(f"Mount unit {unit} exited early ({st}):\n{tail}")
        time.sleep(0.05)

    if mount_ready_ns is None:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:] if log_path.exists() else ""
        raise RuntimeError(f"Timed out waiting for mount {mount_id} ({last_err}):\n{tail}")

    t_rd0 = time.monotonic_ns()
    first_entries = len(os.listdir(mount_dir))
    t_rd1 = time.monotonic_ns()

    pid_str = subprocess.run(
        ["systemctl", "show", "-p", "MainPID", "--value", unit],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    pid = int(pid_str)
    if pid <= 0:
        raise RuntimeError(f"Invalid MainPID={pid} for unit {unit}")

    cgroup_path = Path("/sys/fs/cgroup/ax-clients.slice") / unit
    if not cgroup_path.is_dir():
        # Resolve exact cgroup path from /proc/<pid>/cgroup
        rel = ""
        for ln in Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines():
            if ln.startswith("0::/"):
                rel = ln.split("0::/", 1)[1].strip()
                break
        if rel:
            cgroup_path = Path("/sys/fs/cgroup") / rel

    # In foreground mode on Linux, JuiceFS launches a watchdog MainPID + FUSE worker child inside the same cgroup.
    # Prefer the FUSE worker child PID so /proc/<pid>/status tracks the active worker RSS and thread count.
    if engine in {"juicefs", "juicefs-overlay"} and (cgroup_path / "cgroup.procs").is_file():
        cg_pids = [int(x) for x in (cgroup_path / "cgroup.procs").read_text(encoding="utf-8").splitlines() if x.strip().isdigit()]
        for cand in cg_pids:
            if cand != pid and Path(f"/proc/{cand}/exe").exists():
                try:
                    if Path(f"/proc/{cand}/exe").resolve() == Path(record["binary"]).resolve():
                        pid = cand
                        break
                except OSError:
                    pass

    # Re-scrape metrics + virtual .stats after first_readdir so native capacity gauges are guaranteed populated
    raw_metrics, parsed_metrics = scrape_metrics(port, timeout=2.0, mount_dir=mount_dir, engine=engine)

    args.audit_out.parent.mkdir(parents=True, exist_ok=True)
    stats_tmp = args.audit_out.with_suffix(".prom.txt")
    stats_tmp.write_text(raw_metrics, encoding="utf-8")
    if args.audit_out.exists():
        args.audit_out.unlink()

    env = os.environ.copy()
    env["PYTHONPATH"] = "/srv/ax-cluster/direct/repo"
    audit_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bench.cluster.audit_mount",
            "--mounts",
            str(args.mounts),
            "--id",
            mount_id,
            "--pid",
            str(pid),
            "--cgroup",
            str(cgroup_path),
            "--stats",
            str(stats_tmp),
            "--out",
            str(args.audit_out),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    if audit_proc.returncode != 0:
        ensure_unmounted(mount_dir)
        subprocess.run(["systemctl", "stop", unit], capture_output=True)
        subprocess.run(["systemctl", "reset-failed", unit], capture_output=True)
        raise RuntimeError(f"audit_mount failed for {mount_id}:\n{audit_proc.stderr}\n{audit_proc.stdout}")

    res = {
        "id": mount_id,
        "engine": engine,
        "unit": unit,
        "pid": pid,
        "cgroup": str(cgroup_path),
        "mount_directory": str(mount_dir),
        "cache_root": str(cache_root),
        "metrics_port": port,
        "started_utc_ns": t_utc_start_ns,
        "mount_ready_seconds": (mount_ready_ns - t_start_ns) / 1e9,
        "first_readdir_seconds": (t_rd1 - t_rd0) / 1e9,
        "first_readdir_entries": first_entries,
        "log_path": str(log_path),
        "audit_path": str(args.audit_out),
        "initial_cgroup": read_cgroup_snapshot(cgroup_path),
        "initial_metrics_count": len(parsed_metrics),
    }
    print(json.dumps(res))


def cmd_exec_in_cgroup(args):
    cg_procs = args.cgroup / "cgroup.procs"
    cg_procs.write_text(f"{os.getpid()}\n", encoding="utf-8")
    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise ValueError("Missing command to exec")
    os.execvp(cmd[0], cmd)


def pending_values(engine: str, parsed: dict[str, float]) -> dict[str, float]:
    keys = BREWFS_PENDING_GAUGES if engine.startswith("brewfs") else JUICEFS_PENDING_GAUGES
    return {k: parsed.get(k, 0.0) for k in keys if k in parsed}


def cmd_wait_upload_zero(args):
    t0 = time.monotonic_ns()
    deadline = time.monotonic() + args.timeout
    first_zero_mono: int | None = None
    first_zero_utc: int | None = None
    last_vals: dict[str, float] = {}

    while time.monotonic() < deadline:
        _, parsed = scrape_metrics(args.port, timeout=3.0)
        vals = pending_values(args.engine, parsed)
        last_vals = vals
        all_zero = all(abs(v) < 0.5 for v in vals.values())
        now_mono = time.monotonic_ns()
        now_utc = time.time_ns()
        if all_zero:
            if first_zero_mono is None:
                first_zero_mono = now_mono
                first_zero_utc = now_utc
            elif (now_mono - first_zero_mono) >= 1_000_000_000:
                res = {
                    "ok": True,
                    "upload_drain_seconds": (first_zero_mono - t0) / 1e9,
                    "verified_with_1s_hold_seconds": (now_mono - t0) / 1e9,
                    "check1_utc_ns": first_zero_utc,
                    "check2_utc_ns": now_utc,
                    "gauges": vals,
                }
                print(json.dumps(res))
                return
        else:
            first_zero_mono = None
            first_zero_utc = None
        time.sleep(0.2)

    raise RuntimeError(f"Timed out waiting for upload zero on port {args.port}; last={last_vals}")


def cmd_stop(args):
    t0 = time.monotonic_ns()
    final_cgroup = read_cgroup_snapshot(args.cgroup) if args.cgroup else {}
    final_raw = ""
    final_parsed: dict[str, float] = {}
    try:
        final_raw, final_parsed = scrape_metrics(args.port, timeout=2.0, mount_dir=args.mount_dir, engine=args.engine)
    except Exception:
        pass

    pending_at_exit = pending_values(args.engine, final_parsed) if final_parsed else {}

    # Sync and unmount cleanly so the FUSE daemon flushes and exits naturally
    mount_dir = args.mount_dir
    unmounted = False
    for attempt in range(30):
        if not os.path.ismount(mount_dir):
            unmounted = True
            break
        r = subprocess.run(["fusermount3", "-u", str(mount_dir)], capture_output=True, text=True)
        if r.returncode == 0 or not os.path.ismount(mount_dir):
            unmounted = True
            break
        r2 = subprocess.run(["umount", str(mount_dir)], capture_output=True, text=True)
        if r2.returncode == 0 or not os.path.ismount(mount_dir):
            unmounted = True
            break
        time.sleep(0.2)

    if not unmounted and os.path.ismount(mount_dir):
        subprocess.run(["umount", "-l", str(mount_dir)], capture_output=True)

    # Wait up to 30s for the unit to stop naturally, then stop via systemctl
    unit_Wait_deadline = time.monotonic() + 30.0
    active_state = "unknown"
    while time.monotonic() < unit_Wait_deadline:
        active_state = subprocess.run(
            ["systemctl", "show", "-p", "ActiveState", "--value", args.unit],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if active_state in {"inactive", "failed", ""}:
            break
        time.sleep(0.1)

    if active_state not in {"inactive", ""}:
        subprocess.run(["systemctl", "stop", args.unit], capture_output=True)
        subprocess.run(["systemctl", "reset-failed", args.unit], capture_output=True)

    t1 = time.monotonic_ns()
    # Remove private per-mount JSON if present so we don't accumulate temp files
    priv_cfg = Path("/srv/ax-cluster/private/direct-mounts") / f"{args.id}.json"
    if priv_cfg.exists():
        priv_cfg.unlink()

    res = {
        "id": args.id,
        "unit": args.unit,
        "unmounted_cleanly": unmounted,
        "exit_seconds": (t1 - t0) / 1e9,
        "stopped_utc_ns": time.time_ns(),
        "pending_at_exit": pending_at_exit,
        "final_cgroup": final_cgroup,
        "final_metrics_summary": {
            k: v
            for k, v in final_parsed.items()
            if "{" not in k and (k.startswith("brewfs_") or k.startswith("juicefs_") or k.startswith("forkredis_"))
        },
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
        if final_raw:
            args.out.with_suffix(".final.prom.txt").write_text(final_raw, encoding="utf-8")
    print(json.dumps({"id": args.id, "exit_seconds": res["exit_seconds"], "unmounted_cleanly": unmounted}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="subcmd", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--mounts", type=Path, required=True)
    p_start.add_argument("--id", required=True)
    p_start.add_argument("--spec", type=Path, required=True)
    p_start.add_argument("--audit-out", type=Path, required=True)
    p_start.set_defaults(func=cmd_start)

    p_exec = sub.add_parser("exec-in-cgroup")
    p_exec.add_argument("--cgroup", type=Path, required=True)
    p_exec.add_argument("cmd", nargs=argparse.REMAINDER)
    p_exec.set_defaults(func=cmd_exec_in_cgroup)

    p_wait = sub.add_parser("wait-upload-zero")
    p_wait.add_argument("--engine", required=True)
    p_wait.add_argument("--port", type=int, required=True)
    p_wait.add_argument("--timeout", type=float, default=3600.0)
    p_wait.set_defaults(func=cmd_wait_upload_zero)

    p_stop = sub.add_parser("stop")
    p_stop.add_argument("--id", required=True)
    p_stop.add_argument("--engine", required=True)
    p_stop.add_argument("--mount-dir", type=Path, required=True)
    p_stop.add_argument("--unit", required=True)
    p_stop.add_argument("--port", type=int, required=True)
    p_stop.add_argument("--cgroup", type=Path, default=None)
    p_stop.add_argument("--out", type=Path, default=None)
    p_stop.set_defaults(func=cmd_stop)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
