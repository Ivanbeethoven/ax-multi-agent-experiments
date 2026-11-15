#!/usr/bin/env python3
"""Host-local parallel mount, replay, verify, loader, and directory-copy batch runner."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

SCRIPT_DIR = Path(__file__).resolve().parent
DRIVER_PATH = SCRIPT_DIR / "baseline_mount_driver.py"
PRIV_SPECS_DIR = Path("/srv/ax-cluster/private/baseline-mounts")
SHARED_BIN_DIR = Path("/srv/ax-cluster/direct/bin")


def _save_process_output(prefix: Path, proc: subprocess.CompletedProcess) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(prefix.suffix + ".stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    prefix.with_suffix(prefix.suffix + ".stderr.log").write_text(proc.stderr or "", encoding="utf-8")


def cmd_start_batch(args: argparse.Namespace) -> None:
    specs_by_id: dict[str, dict] = json.loads(args.specs.read_text(encoding="utf-8"))
    args.audit_dir.mkdir(parents=True, exist_ok=True)
    PRIV_SPECS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(PRIV_SPECS_DIR, 0o700)

    items = list(specs_by_id.items())

    def _one(item: tuple[str, dict]) -> dict:
        mid, spec = item
        spec_path = PRIV_SPECS_DIR / f"{mid}-spec.json"
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        os.chmod(spec_path, 0o600)
        audit_out = args.audit_dir / f"{mid}-audit.json"
        p = subprocess.run(
            [
                "/usr/bin/python3",
                str(DRIVER_PATH),
                "start",
                "--mounts",
                str(args.mounts),
                "--id",
                mid,
                "--spec",
                str(spec_path),
                "--audit-out",
                str(audit_out),
            ],
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        spec_path.unlink(missing_ok=True)
        if p.returncode != 0:
            raise RuntimeError(
                f"start failed for {mid}:\nSTDOUT:\n{p.stdout[-1500:]}\nSTDERR:\n{p.stderr[-1500:]}"
            )
        info = json.loads(p.stdout.strip().splitlines()[-1])
        info["slot"] = spec["slot"]
        info["host_name"] = spec["host_name"]
        info["host_ssh"] = spec["host_ssh"]
        return info

    workers = min(16, max(1, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(_one, items))
    print(json.dumps({"ok": True, "mounts": out}))


def cmd_stop_batch(args: argparse.Namespace) -> None:
    mounts_list: list[dict] = json.loads(args.mounts_info.read_text(encoding="utf-8"))
    args.stop_dir.mkdir(parents=True, exist_ok=True)

    def _one(m: dict) -> dict:
        mid = m["id"]
        out_p = args.stop_dir / f"{mid}-stop.json"
        cmd = [
            "/usr/bin/python3",
            str(DRIVER_PATH),
            "stop",
            "--id",
            mid,
            "--engine",
            m["engine"],
            "--mount-dir",
            m["mount_directory"],
            "--unit",
            m["unit"],
            "--cgroup",
            m["cgroup"],
            "--admin-socket",
            m.get("admin_socket") or "",
            "--cache-root",
            m.get("cache_root") or "",
            "--out",
            str(out_p),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        res = (
            json.loads(out_p.read_text(encoding="utf-8"))
            if out_p.is_file()
            else {"id": mid, "unmounted_cleanly": p.returncode == 0}
        )
        if args.remove_cache:
            cr = m.get("cache_root", "")
            if cr.startswith("/srv/ax-cluster/runs/baselines/"):
                shutil.rmtree(cr, ignore_errors=True)
        return res

    workers = min(16, max(1, len(mounts_list)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(_one, mounts_list))
    print(json.dumps({"ok": True, "stopped": out}))


def cmd_c2_replay_batch(args: argparse.Namespace) -> None:
    tasks: list[dict] = json.loads(args.tasks.read_text(encoding="utf-8"))

    def _run_task(t: dict) -> dict:
        target_ns = int(t["start_utc_ns"])
        now_ns = time.time_ns()
        if target_ns > now_ns:
            time.sleep((target_ns - now_ns) / 1e9)

        out_path = Path(t["out_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.unlink(missing_ok=True)

        shim_so = (t.get("hardlink_copy_shim_so") or "").strip()
        stats_path = out_path.with_name(out_path.stem + "-hardlink-copy.json")
        stats_path.unlink(missing_ok=True)

        replay_argv = [
            str(SHARED_BIN_DIR / "full-fuse-replay"),
            t["events_path"],
            t["blobs_dir"],
            t["mount_directory"],
            str(out_path),
            "--performance",
        ]
        if t.get("pacing_path"):
            replay_argv += ["--simulate-llm", t["pacing_path"]]

        if shim_so:
            exec_payload = [
                "/usr/bin/env",
                f"LD_PRELOAD={shim_so}",
                f"AX_HARDLINK_COPY_STATS_PATH={stats_path}",
                *replay_argv,
            ]
        else:
            exec_payload = replay_argv

        cmd = [
            "/usr/bin/python3",
            str(DRIVER_PATH),
            "exec-in-cgroup",
            "--cgroup",
            t["cgroup"],
            "--",
            *exec_payload,
        ]

        t0_mono = time.monotonic_ns()
        started_utc_ns = time.time_ns()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10800, check=False)
        app_done_mono = time.monotonic_ns()
        app_done_utc_ns = time.time_ns()
        _save_process_output(out_path, proc)
        if proc.returncode != 0:
            raise RuntimeError(
                f"full-fuse-replay failed for {t['task_id']} ({t['case']}):\n"
                f"STDOUT:\n{proc.stdout[-1500:]}\nSTDERR:\n{proc.stderr[-1500:]}"
            )

        up_proc = subprocess.run(
            [
                "/usr/bin/python3",
                str(DRIVER_PATH),
                "wait-upload-zero",
                "--engine",
                t["engine"],
                "--mount-dir",
                t["mount_directory"],
                "--admin-socket",
                t.get("admin_socket") or "",
                "--cache-root",
                t.get("cache_root") or "",
                "--timeout",
                "3600",
            ],
            capture_output=True,
            text=True,
            timeout=3630,
            check=False,
        )
        if up_proc.returncode != 0:
            raise RuntimeError(f"wait-upload-zero failed for {t['task_id']}:\n{up_proc.stderr[-1000:]}")
        up_info = json.loads(up_proc.stdout.strip().splitlines()[-1])
        upload_done_utc_ns = time.time_ns()
        rep_doc = json.loads(out_path.read_text(encoding="utf-8"))
        if stats_path.is_file():
            hl_cost = json.loads(stats_path.read_text(encoding="utf-8"))
        else:
            hl_cost = {
                "enabled": bool(shim_so),
                "fallback_count": 0,
                "copied_bytes": 0,
                "copy_seconds": 0.0,
            }
        rep_doc["hardlink_copy_cost"] = hl_cost
        out_path.write_text(json.dumps(rep_doc, indent=2) + "\n", encoding="utf-8")

        return {
            "task_id": t["task_id"],
            "case": t["case"],
            "host": t["host"],
            "slot": t["slot"],
            "planned_start_delay_ms": t["start_delay_ms"],
            "started_utc_ns": started_utc_ns,
            "app_done_utc_ns": app_done_utc_ns,
            "upload_done_utc_ns": upload_done_utc_ns,
            "wall_app_seconds": (app_done_mono - t0_mono) / 1e9,
            "upload_drain_seconds": float(up_info["upload_drain_seconds"]),
            "hardlink_copy_cost": hl_cost,
            "replay": rep_doc,
        }

    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        results = list(pool.map(_run_task, tasks))
    print(json.dumps({"ok": True, "tasks": results}))


def cmd_c2_verify_batch(args: argparse.Namespace) -> None:
    tasks: list[dict] = json.loads(args.tasks.read_text(encoding="utf-8"))

    def _ver_one(t: dict) -> dict:
        out_p = Path(t["out_path"])
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.unlink(missing_ok=True)
        t0 = time.monotonic_ns()
        p = subprocess.run(
            [
                str(SHARED_BIN_DIR / "trace-verify-and-index"),
                "verify-tree",
                "--mount-dir",
                t["mount_directory"],
                "--final-tree",
                t["final_tree_path"],
                "--replayed-dir",
                t["replayed_dir"],
                "--out",
                str(out_p),
            ],
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        t1 = time.monotonic_ns()
        _save_process_output(out_p, p)
        if p.returncode != 0:
            raise RuntimeError(
                f"verify-tree failed for {t['task_id']} ({t['case']}) on {t['verify_host']}:\n{p.stderr[-1500:]}"
            )
        vdoc = json.loads(out_p.read_text(encoding="utf-8"))
        return {
            "task_id": t["task_id"],
            "case": t["case"],
            "source_host": t["source_host"],
            "verify_host": t["verify_host"],
            "verify_seconds": (t1 - t0) / 1e9,
            "verified_utc_ns": time.time_ns(),
            "verify": vdoc,
        }

    with ThreadPoolExecutor(max_workers=min(16, max(1, len(tasks)))) as pool:
        results = list(pool.map(_ver_one, tasks))
    print(json.dumps({"ok": True, "verified": results}))


def cmd_c3_batch(args: argparse.Namespace) -> None:
    tasks: list[dict] = json.loads(args.tasks.read_text(encoding="utf-8"))

    def _run_one(t: dict) -> dict:
        out_p = Path(t["out_path"])
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.unlink(missing_ok=True)
        cmd = [
            "/usr/bin/python3",
            str(DRIVER_PATH),
            "exec-in-cgroup",
            "--cgroup",
            t["cgroup"],
            "--",
            *t["loader_argv"],
        ]
        t0 = time.monotonic_ns()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10800, check=False)
        t1 = time.monotonic_ns()
        _save_process_output(out_p, p)
        if p.returncode != 0:
            raise RuntimeError(
                f"c3-loader failed for {t['reader_id']}:\nSTDOUT:\n{p.stdout[-1500:]}\nSTDERR:\n{p.stderr[-1500:]}"
            )
        ldoc = json.loads(out_p.read_text(encoding="utf-8"))
        return {
            "reader_id": t["reader_id"],
            "host": t["host"],
            "slot": t["slot"],
            "wall_seconds": (t1 - t0) / 1e9,
            "loader": ldoc,
        }

    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        results = list(pool.map(_run_one, tasks))
    print(json.dumps({"ok": True, "results": results}))


def _copy_tree_parallel(src: Path, dst: Path, file_workers: int = 16) -> dict:
    """Copy a directory tree preserving symlinks and file modes via parallel file workers."""
    t0 = time.monotonic()
    dirs_to_make: list[tuple[Path, int]] = []
    symlinks: list[tuple[Path, Path]] = []
    regular_files: list[tuple[Path, Path, int]] = []

    for root_str, dirnames, filenames in os.walk(src, followlinks=False):
        rpath = Path(root_str)
        rel = rpath.relative_to(src)
        target_dir = dst if str(rel) == "." else (dst / rel)
        dirs_to_make.append((target_dir, rpath.lstat().st_mode & 0o777))
        for dname in dirnames:
            sp = rpath / dname
            if sp.is_symlink():
                symlinks.append((sp, target_dir / dname))
        for fname in filenames:
            sp = rpath / fname
            tp = target_dir / fname
            if sp.is_symlink():
                symlinks.append((sp, tp))
            else:
                regular_files.append((sp, tp, sp.stat().st_mode & 0o777))

    dirs_by_depth: dict[int, list[tuple[Path, int]]] = defaultdict(list)
    for dpath, dmode in dirs_to_make:
        dirs_by_depth[len(dpath.parts)].append((dpath, dmode))

    def _mkdir_one(entry: tuple[Path, int]) -> None:
        dpath, dmode = entry
        dpath.mkdir(parents=True, exist_ok=True)
        if os.name != "nt" and (dpath.lstat().st_mode & 0o777) != dmode:
            os.chmod(dpath, dmode)

    def _symlink_one(entry: tuple[Path, Path]) -> None:
        sp, tp = entry
        tp.unlink(missing_ok=True)
        os.symlink(os.readlink(sp), tp)

    with ThreadPoolExecutor(max_workers=file_workers) as pool:
        for depth in sorted(dirs_by_depth.keys()):
            list(pool.map(_mkdir_one, dirs_by_depth[depth]))
        if symlinks:
            list(pool.map(_symlink_one, symlinks))

    open_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    has_fchmod = hasattr(os, "fchmod")

    def _copy_one(entry: tuple[Path, Path, int]) -> int:
        sp, tp, fmode = entry
        written = 0
        with open(sp, "rb") as fsrc:
            fd = os.open(tp, open_flags, fmode)
            try:
                while True:
                    buf = fsrc.read(1024 * 1024)
                    if not buf:
                        break
                    mv = memoryview(buf)
                    off = 0
                    while off < len(buf):
                        off += os.write(fd, mv[off:])
                    written += len(buf)
                if fmode != 0o644 and has_fchmod:
                    os.fchmod(fd, fmode)
            finally:
                os.close(fd)
        if os.name != "nt" and fmode != 0o644 and (tp.lstat().st_mode & 0o777) != fmode:
            os.chmod(tp, fmode)
        return written

    with ThreadPoolExecutor(max_workers=file_workers) as pool:
        total_bytes = sum(pool.map(_copy_one, regular_files))

    if os.name != "nt":
        subprocess.run(["sync", "-f", str(dst)], capture_output=True, text=True, timeout=600, check=False)
    return {
        "files": len(regular_files),
        "symlinks": len(symlinks),
        "bytes": total_bytes,
        "seconds": round(time.monotonic() - t0, 6),
    }


def cmd_copy_tree_batch(args: argparse.Namespace) -> None:
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    pairs: list[dict] = spec["pairs"]
    outer_workers = min(8, max(1, int(spec.get("outer_workers", 4))))
    inner_workers = max(1, int(spec.get("inner_workers", 8)))

    t0 = time.monotonic()

    def _one(pair: dict) -> dict:
        return _copy_tree_parallel(Path(pair["src"]), Path(pair["dst"]), file_workers=inner_workers)

    with ThreadPoolExecutor(max_workers=outer_workers) as pool:
        items = list(pool.map(_one, pairs))
    print(json.dumps({"ok": True, "count": len(items), "wall_seconds": round(time.monotonic() - t0, 6), "items": items}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="subcmd", required=True)

    p_start = sub.add_parser("start-batch")
    p_start.add_argument("--mounts", type=Path, required=True)
    p_start.add_argument("--specs", type=Path, required=True)
    p_start.add_argument("--audit-dir", type=Path, required=True)
    p_start.set_defaults(func=cmd_start_batch)

    p_stop = sub.add_parser("stop-batch")
    p_stop.add_argument("--mounts-info", type=Path, required=True)
    p_stop.add_argument("--stop-dir", type=Path, required=True)
    p_stop.add_argument("--remove-cache", action="store_true")
    p_stop.set_defaults(func=cmd_stop_batch)

    p_rep = sub.add_parser("c2-replay-batch")
    p_rep.add_argument("--tasks", type=Path, required=True)
    p_rep.set_defaults(func=cmd_c2_replay_batch)

    p_ver = sub.add_parser("c2-verify-batch")
    p_ver.add_argument("--tasks", type=Path, required=True)
    p_ver.set_defaults(func=cmd_c2_verify_batch)

    p_c3 = sub.add_parser("c3-batch")
    p_c3.add_argument("--tasks", type=Path, required=True)
    p_c3.set_defaults(func=cmd_c3_batch)

    p_cp = sub.add_parser("copy-tree-batch")
    p_cp.add_argument("--spec", type=Path, required=True)
    p_cp.set_defaults(func=cmd_copy_tree_batch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
