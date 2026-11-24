#!/usr/bin/env python3
"""Host-local cgroup FUSE mount driver for CephFS (ceph-fuse) and s3fs."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_audit_mount as audit_mod
import baseline_context as ctx
import baseline_mounts as mounts_mod


def _load_secret_values() -> list[str]:
    rt = ctx.load_baseline_runtime()
    env_path = Path(rt["rustfs_env_path"])
    if not env_path.is_file():
        return []
    vals: list[str] = []
    for ln in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.startswith("#"):
            _, v = ln.split("=", 1)
            v = v.strip()
            if len(v) >= 6:
                vals.append(v)
    return vals


def _flatten_perf_dump(obj: object, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(obj, dict):
        # Ceph perf dump leaves sometimes use {"avgcount": N, "sum": X}
        if set(obj.keys()) >= {"avgcount", "sum"} and len(obj) <= 4:
            try:
                out[f"{prefix}.avgcount"] = float(obj["avgcount"])
                out[f"{prefix}.sum"] = float(obj["sum"])
            except (TypeError, ValueError):
                pass
            return out
        for k, v in obj.items():
            norm_k = k.split(".", 1)[0] if k.startswith(("client.", "objectcacher-client.")) else k
            sub = f"{prefix}.{norm_k}" if prefix else norm_k
            out.update(_flatten_perf_dump(v, sub))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out[prefix] = float(obj)
    return out


def read_ceph_fuse_metrics(asok_path: str | None) -> dict[str, float]:
    if not asok_path or not Path(asok_path).exists():
        return {}
    p = subprocess.run(
        ["/usr/bin/ceph", "--admin-daemon", asok_path, "perf", "dump"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if p.returncode != 0 or not p.stdout.strip():
        return {}
    try:
        return _flatten_perf_dump(json.loads(p.stdout))
    except Exception:
        return {}


def read_s3fs_local_metrics(cache_root: str) -> dict[str, float]:
    root = Path(cache_root)
    tmp_dir = root / "tmp"
    cache_dir = root / "cache"
    tmp_files = 0
    tmp_bytes = 0
    if tmp_dir.is_dir():
        for p in tmp_dir.rglob("*"):
            if p.is_file():
                try:
                    tmp_files += 1
                    tmp_bytes += p.stat().st_size
                except OSError:
                    pass
    cache_files = 0
    cache_bytes = 0
    if cache_dir.is_dir():
        for p in cache_dir.rglob("*"):
            if p.is_file():
                try:
                    cache_files += 1
                    cache_bytes += p.stat().st_size
                except OSError:
                    pass
    return {
        "s3fs_tmp_files": float(tmp_files),
        "s3fs_tmp_bytes": float(tmp_bytes),
        "s3fs_cache_files": float(cache_files),
        "s3fs_cache_bytes": float(cache_bytes),
    }


def read_cgroup_stats(cgroup_dir: str) -> dict:
    cg = Path(cgroup_dir)
    out: dict[str, object] = {}
    for fname, key in (
        ("memory.current", "memory_current"),
        ("memory.peak", "memory_peak"),
        ("memory.max", "memory_max"),
        ("memory.swap.current", "memory_swap_current"),
    ):
        fp = cg / fname
        if fp.is_file():
            raw = fp.read_text(encoding="utf-8").strip()
            out[key] = int(raw) if raw.isdigit() else raw
    cpu_fp = cg / "cpu.stat"
    if cpu_fp.is_file():
        for line in cpu_fp.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                out[f"cpu_{parts[0]}"] = int(parts[1])
    return out


def _unmount_if_needed(mount_dir: str) -> None:
    minfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
    if audit_mod.parse_mountinfo_for_target(minfo, mount_dir) is None:
        return
    for cmd in (
        ["fusermount3", "-u", mount_dir],
        ["fusermount", "-u", mount_dir],
        ["umount", mount_dir],
        ["umount", "-l", mount_dir],
    ):
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        minfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
        if audit_mod.parse_mountinfo_for_target(minfo, mount_dir) is None:
            return
        if p.returncode == 0:
            return


def cmd_start(args: argparse.Namespace) -> None:
    mounts_doc = json.loads(args.mounts.read_text(encoding="utf-8"))
    rec_map = {m["id"]: m for m in mounts_doc["mounts"]}
    if args.id not in rec_map:
        raise KeyError(f"Mount id {args.id!r} not in {args.mounts}")
    record = rec_map[args.id]
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    profiles = mounts_mod.load_profiles()
    inv = mounts_mod.render_mount_invocation(record, spec, profiles)

    _unmount_if_needed(record["mount_directory"])
    subprocess.run(["systemctl", "stop", record["unit"]], capture_output=True, check=False, timeout=15)
    subprocess.run(["systemctl", "reset-failed", record["unit"]], capture_output=True, check=False, timeout=10)
    if record.get("admin_socket"):
        Path(record["admin_socket"]).unlink(missing_ok=True)
    if record.get("cache_state") in ("cold", "cross-host-cold"):
        shutil.rmtree(record["cache_root"], ignore_errors=True)

    for d in inv["prepare_dirs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    p = subprocess.run(inv["systemd_argv"], capture_output=True, text=True, timeout=60, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"systemd-run failed for {record['id']}: {p.stderr.strip()}")

    deadline = time.monotonic() + 60.0
    mounted = False
    while time.monotonic() < deadline:
        minfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
        if audit_mod.parse_mountinfo_for_target(minfo, record["mount_directory"]) is not None:
            mounted = True
            break
        state_p = subprocess.run(
            ["systemctl", "show", "-p", "ActiveState", "--value", record["unit"]],
            capture_output=True,
            text=True,
            check=False,
        )
        if state_p.stdout.strip() in ("failed", "inactive"):
            j_p = subprocess.run(
                ["journalctl", "-u", record["unit"], "-n", "40", "--no-pager"],
                capture_output=True,
                text=True,
                check=False,
            )
            raise RuntimeError(
                f"Mount unit {record['unit']} exited early ({state_p.stdout.strip()}):\n{j_p.stdout[-1500:]}"
            )
        time.sleep(0.05)

    if not mounted:
        raise TimeoutError(f"Mount {record['mount_directory']} did not become ready within 60s")
    ready_seconds = time.monotonic() - t0

    t_rd0 = time.monotonic_ns()
    first_entries = len(os.listdir(record["mount_directory"]))
    first_readdir_seconds = (time.monotonic_ns() - t_rd0) / 1e9

    pid_p = subprocess.run(
        ["systemctl", "show", "-p", "MainPID", "--value", record["unit"]],
        capture_output=True,
        text=True,
        check=True,
    )
    pid = int(pid_p.stdout.strip() or "0")
    if pid <= 0:
        raise RuntimeError(f"Invalid MainPID for {record['unit']}")

    secrets = _load_secret_values()
    audit_res = audit_mod.audit_live_process(record, pid, secret_values=secrets)
    args.audit_out.parent.mkdir(parents=True, exist_ok=True)
    args.audit_out.write_text(json.dumps(audit_res, indent=2) + "\n", encoding="utf-8")
    if not audit_res["ok"]:
        _unmount_if_needed(record["mount_directory"])
        subprocess.run(["systemctl", "stop", record["unit"]], capture_output=True, check=False)
        raise RuntimeError(f"Mount audit failed for {record['id']}: {audit_res['errors']}")

    init_metrics = (
        read_ceph_fuse_metrics(record.get("admin_socket"))
        if record["engine"] == "cephfs"
        else read_s3fs_local_metrics(record["cache_root"])
    )
    out = {
        "id": record["id"],
        "engine": record["engine"],
        "unit": record["unit"],
        "pid": pid,
        "cgroup": record["cgroup"],
        "mount_directory": record["mount_directory"],
        "cache_root": record["cache_root"],
        "admin_socket": record.get("admin_socket"),
        "mount_ready_seconds": round(ready_seconds, 6),
        "first_readdir_seconds": round(first_readdir_seconds, 6),
        "first_readdir_entries": first_entries,
        "initial_metrics": init_metrics,
    }
    print(json.dumps(out))


def cmd_wait_upload_zero(args: argparse.Namespace) -> None:
    t0_ns = time.monotonic_ns()
    deadline = time.monotonic() + float(args.timeout)
    if args.mount_dir and Path(args.mount_dir).exists():
        subprocess.run(["sync", "-f", args.mount_dir], capture_output=True, text=True, timeout=1800, check=False)

    pending = 0.0
    first_zero_mono: int | None = None
    first_zero_utc: int | None = None
    while time.monotonic() < deadline:
        if args.engine == "cephfs":
            metrics = read_ceph_fuse_metrics(args.admin_socket)
            dirty = sum(v for k, v in metrics.items() if k.endswith(".bytes_dirty"))
            pending = dirty
            all_zero = dirty <= 0.0
        elif args.engine == "s3fs":
            metrics = read_s3fs_local_metrics(args.cache_root) if args.cache_root else {}
            pending = metrics.get("s3fs_tmp_bytes", 0.0)
            all_zero = metrics.get("s3fs_tmp_files", 0.0) <= 0.0
        else:
            all_zero = True

        now_mono = time.monotonic_ns()
        now_utc = time.time_ns()
        if all_zero:
            if first_zero_mono is None:
                first_zero_mono = now_mono
                first_zero_utc = now_utc
            elif (now_mono - first_zero_mono) >= 1_000_000_000:
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "engine": args.engine,
                            "upload_drain_seconds": round((first_zero_mono - t0_ns) / 1e9, 6),
                            "verified_with_1s_hold_seconds": round((now_mono - t0_ns) / 1e9, 6),
                            "check1_utc_ns": first_zero_utc,
                            "check2_utc_ns": now_utc,
                            "pending_bytes": pending,
                        }
                    )
                )
                return
        else:
            first_zero_mono = None
            first_zero_utc = None
        time.sleep(0.1)

    now_mono = time.monotonic_ns()
    print(
        json.dumps(
            {
                "ok": first_zero_mono is not None,
                "engine": args.engine,
                "upload_drain_seconds": round(((first_zero_mono or now_mono) - t0_ns) / 1e9, 6),
                "pending_bytes": pending,
            }
        )
    )


def cmd_metrics(args: argparse.Namespace) -> None:
    if args.engine == "cephfs":
        m = read_ceph_fuse_metrics(args.admin_socket)
    else:
        m = read_s3fs_local_metrics(args.cache_root or "")
    print(json.dumps({"ok": True, "metrics": m, "cgroup": read_cgroup_stats(args.cgroup) if args.cgroup else {}}))


def cmd_stop(args: argparse.Namespace) -> None:
    t0 = time.monotonic()
    final_cgroup = read_cgroup_stats(args.cgroup)
    final_metrics = (
        read_ceph_fuse_metrics(args.admin_socket)
        if args.engine == "cephfs"
        else read_s3fs_local_metrics(args.cache_root or "")
    )
    if Path(args.mount_dir).exists():
        subprocess.run(["sync", "-f", args.mount_dir], capture_output=True, text=True, timeout=300, check=False)
    _unmount_if_needed(args.mount_dir)
    subprocess.run(["systemctl", "stop", args.unit], capture_output=True, text=True, timeout=30, check=False)
    subprocess.run(["systemctl", "reset-failed", args.unit], capture_output=True, text=True, timeout=10, check=False)
    if args.admin_socket:
        Path(args.admin_socket).unlink(missing_ok=True)
    exit_s = time.monotonic() - t0

    minfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
    clean = audit_mod.parse_mountinfo_for_target(minfo, args.mount_dir) is None
    res = {
        "id": args.id,
        "engine": args.engine,
        "unmounted_cleanly": clean,
        "exit_seconds": round(exit_s, 6),
        "final_cgroup": final_cgroup,
        "final_metrics_summary": final_metrics,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(res))


def cmd_exec_in_cgroup(args: argparse.Namespace) -> None:
    cmd = list(args.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise ValueError("Missing command to execute inside cgroup")
    if os.name != "nt":
        import resource

        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (1048576, 1048576))
        except (ValueError, OSError):
            pass
    procs_file = Path(args.cgroup) / "cgroup.procs"
    if procs_file.is_file():
        procs_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    os.execvp(cmd[0], cmd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="subcmd", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--mounts", type=Path, required=True)
    p_start.add_argument("--id", required=True)
    p_start.add_argument("--spec", type=Path, required=True)
    p_start.add_argument("--audit-out", type=Path, required=True)
    p_start.set_defaults(func=cmd_start)

    p_wait = sub.add_parser("wait-upload-zero")
    p_wait.add_argument("--engine", required=True)
    p_wait.add_argument("--mount-dir", default="")
    p_wait.add_argument("--admin-socket", default="")
    p_wait.add_argument("--cache-root", default="")
    p_wait.add_argument("--timeout", type=float, default=3600.0)
    p_wait.set_defaults(func=cmd_wait_upload_zero)

    p_met = sub.add_parser("metrics")
    p_met.add_argument("--engine", required=True)
    p_met.add_argument("--admin-socket", default="")
    p_met.add_argument("--cache-root", default="")
    p_met.add_argument("--cgroup", default="")
    p_met.set_defaults(func=cmd_metrics)

    p_stop = sub.add_parser("stop")
    p_stop.add_argument("--id", required=True)
    p_stop.add_argument("--engine", required=True)
    p_stop.add_argument("--mount-dir", required=True)
    p_stop.add_argument("--unit", required=True)
    p_stop.add_argument("--cgroup", required=True)
    p_stop.add_argument("--admin-socket", default="")
    p_stop.add_argument("--cache-root", default="")
    p_stop.add_argument("--out", type=Path)
    p_stop.set_defaults(func=cmd_stop)

    p_exec = sub.add_parser("exec-in-cgroup")
    p_exec.add_argument("--cgroup", required=True)
    p_exec.add_argument("command", nargs=argparse.REMAINDER)
    p_exec.set_defaults(func=cmd_exec_in_cgroup)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
