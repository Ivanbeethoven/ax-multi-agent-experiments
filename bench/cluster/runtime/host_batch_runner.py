#!/usr/bin/env python3
"""Host-local parallel mount & workload batch runner for C2 and C3 (up to 48 mounts/host).

Eliminates coordinator SSH fan-out bottlenecks and sshd MaxStartups drops by running
all per-mount start/stop/replay/verify/c3-load operations locally on each host via ThreadPoolExecutor.
Strictly zero hashes/checksums.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


def save_process_output(prefix,process):
    prefix=Path(prefix)
    prefix.with_suffix(prefix.suffix+'.stdout.log').write_text(process.stdout)
    prefix.with_suffix(prefix.suffix+'.stderr.log').write_text(process.stderr)


def cmd_start_batch(args):
    specs_by_id: dict[str, dict] = json.loads(args.specs.read_text(encoding="utf-8"))
    args.audit_dir.mkdir(parents=True, exist_ok=True)
    priv_dir = Path("/srv/ax-cluster/private/direct-mounts")
    priv_dir.mkdir(parents=True, exist_ok=True)

    items = list(specs_by_id.items())

    def _one(item: tuple[str, dict]) -> dict:
        mid, spec = item
        spec_path = priv_dir / f"{mid}-spec.json"
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        audit_out = args.audit_dir / f"{mid}-audit.json"
        p = subprocess.run(
            [
                "/usr/bin/python3",
                "/srv/ax-cluster/direct/scripts/mount_driver.py",
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
            timeout=180,
        )
        if p.returncode != 0:
            raise RuntimeError(f"start failed for {mid}:\nSTDOUT:\n{p.stdout[-1500:]}\nSTDERR:\n{p.stderr[-1500:]}")
        info = json.loads(p.stdout.strip().splitlines()[-1])
        info["slot"] = spec["slot"]
        info["host_name"] = spec["host_name"]
        info["host_ssh"] = spec["host_ssh"]
        return info

    workers = min(16, max(1, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(_one, items))
    print(json.dumps({"ok": True, "mounts": out}))


def cmd_stop_batch(args):
    mounts_list: list[dict] = json.loads(args.mounts_info.read_text(encoding="utf-8"))
    args.stop_dir.mkdir(parents=True, exist_ok=True)

    def _one(m: dict) -> dict:
        mid = m["id"]
        out_p = args.stop_dir / f"{mid}-stop.json"
        p = subprocess.run(
            [
                "/usr/bin/python3",
                "/srv/ax-cluster/direct/scripts/mount_driver.py",
                "stop",
                "--id",
                mid,
                "--engine",
                m["engine"],
                "--mount-dir",
                m["mount_directory"],
                "--unit",
                m["unit"],
                "--port",
                str(m["metrics_port"]),
                "--cgroup",
                m["cgroup"],
                "--out",
                str(out_p),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        res = json.loads(out_p.read_text(encoding="utf-8")) if out_p.is_file() else {"id": mid, "unmounted_cleanly": p.returncode == 0}
        if args.remove_cache:
            cr = m.get("cache_root", "")
            if cr.startswith("/srv/ax-cluster/runs/direct/run-001/cache/"):
                shutil.rmtree(cr, ignore_errors=True)
        return res

    workers = min(16, max(1, len(mounts_list)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(_one, mounts_list))
    print(json.dumps({"ok": True, "stopped": out}))


def cmd_c2_replay_batch(args):
    tasks: list[dict] = json.loads(args.tasks.read_text(encoding="utf-8"))

    def _run_task(t: dict) -> dict:
        target_ns = int(t["start_utc_ns"])
        now_ns = time.time_ns()
        if target_ns > now_ns:
            time.sleep((target_ns - now_ns) / 1e9)

        out_path = Path(t["out_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists():
            out_path.unlink()

        cmd = [
            "/usr/bin/python3",
            "/srv/ax-cluster/direct/scripts/mount_driver.py",
            "exec-in-cgroup",
            "--cgroup",
            t["cgroup"],
            "--",
            "/srv/ax-cluster/direct/bin/full-fuse-replay",
            t["events_path"],
            t["blobs_dir"],
            t["mount_directory"],
            str(out_path),
            "--performance",
        ]
        if t.get("pacing_path"):
            cmd += ["--simulate-llm", t["pacing_path"]]

        t0_mono = time.monotonic_ns()
        started_utc_ns = time.time_ns()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        app_done_mono = time.monotonic_ns()
        app_done_utc_ns = time.time_ns()
        save_process_output(out_path,proc)
        if proc.returncode != 0:
            raise RuntimeError(f"full-fuse-replay failed for {t['task_id']} ({t['case']}):\nSTDOUT:\n{proc.stdout[-1500:]}\nSTDERR:\n{proc.stderr[-1500:]}")

        up_proc = subprocess.run(
            [
                "/usr/bin/python3",
                "/srv/ax-cluster/direct/scripts/mount_driver.py",
                "wait-upload-zero",
                "--engine",
                t["engine"],
                "--port",
                str(t["metrics_port"]),
                "--timeout",
                "3600",
            ],
            capture_output=True,
            text=True,
            timeout=3630,
        )
        if up_proc.returncode != 0:
            raise RuntimeError(f"wait-upload-zero failed for {t['task_id']}:\n{up_proc.stderr[-1000:]}")
        up_info = json.loads(up_proc.stdout.strip().splitlines()[-1])
        upload_done_utc_ns = time.time_ns()
        rep_doc = json.loads(out_path.read_text(encoding="utf-8"))

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
            "replay": rep_doc,
        }

    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        results = list(pool.map(_run_task, tasks))
    print(json.dumps({"ok": True, "tasks": results}))


def cmd_c2_verify_batch(args):
    tasks: list[dict] = json.loads(args.tasks.read_text(encoding="utf-8"))

    def _ver_one(t: dict) -> dict:
        out_p = Path(t["out_path"])
        out_p.parent.mkdir(parents=True, exist_ok=True)
        if out_p.exists():
            out_p.unlink()
        t0 = time.monotonic_ns()
        p = subprocess.run(
            [
                "/srv/ax-cluster/direct/bin/trace-verify-and-index",
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
            timeout=1800,
        )
        t1 = time.monotonic_ns()
        save_process_output(out_p,p)
        if p.returncode != 0:
            raise RuntimeError(f"verify-tree failed for {t['task_id']} ({t['case']}) on {t['verify_host']}:\n{p.stderr[-1500:]}")
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


def cmd_c3_batch(args):
    tasks: list[dict] = json.loads(args.tasks.read_text(encoding="utf-8"))

    def _run_one(t: dict) -> dict:
        out_p = Path(t["out_path"])
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.unlink(missing_ok=True)
        state_p = Path(t["state_path"]) if t.get("state_path") else None
        if state_p and state_p.exists():
            state_p.unlink()

        cmd = [
            "/usr/bin/python3",
            "/srv/ax-cluster/direct/scripts/mount_driver.py",
            "exec-in-cgroup",
            "--cgroup",
            t["cgroup"],
            "--",
            *t["loader_argv"],
        ]
        t0 = time.monotonic_ns()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        t1 = time.monotonic_ns()
        save_process_output(out_p,p)
        if p.returncode != 0:
            raise RuntimeError(f"c3-loader failed for {t['reader_id']}:\nSTDOUT:\n{p.stdout[-1500:]}\nSTDERR:\n{p.stderr[-1500:]}")
        drain_s = 0.0
        if t.get("wait_upload"):
            up_p = subprocess.run(
                [
                    "/usr/bin/python3",
                    "/srv/ax-cluster/direct/scripts/mount_driver.py",
                    "wait-upload-zero",
                    "--engine",
                    t["engine"],
                    "--port",
                    str(t["metrics_port"]),
                    "--timeout",
                    "3600",
                ],
                capture_output=True,
                text=True,
                timeout=3630,
            )
            if up_p.returncode != 0:
                raise RuntimeError(f"wait-upload-zero failed for {t['reader_id']}:\n{up_p.stderr[-1000:]}")
            drain_s = float(json.loads(up_p.stdout.strip().splitlines()[-1])["upload_drain_seconds"])
        ldoc = json.loads(out_p.read_text(encoding="utf-8"))
        state_txt = state_p.read_text(encoding="utf-8") if (state_p and state_p.is_file()) else None
        return {
            "reader_id": t["reader_id"],
            "host": t["host"],
            "slot": t["slot"],
            "wall_seconds": (t1 - t0) / 1e9,
            "upload_drain_seconds": drain_s,
            "loader": ldoc,
            "state_text": state_txt,
        }

    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        results = list(pool.map(_run_one, tasks))
    print(json.dumps({"ok": True, "results": results}))


def cmd_c4_batch(args):
    tasks=json.loads(args.tasks.read_text())
    def one(task):
        out=Path(task['output']);out.mkdir(parents=True,exist_ok=True)
        changes=out/'changes.json'
        changes.write_text(json.dumps(task.get('changes',[]))+'\n')
        argv=['python3','-m','bench.cluster.c4_workload',task['action'],
              '--root',task['mount_directory'],'--files',str(task['files']),
              '--file-bytes',str(task['file_bytes']),'--changes',str(changes)]
        if task.get('full_file'):argv.append('--full-file')
        command=['python3','/srv/ax-cluster/direct/scripts/mount_driver.py',
                 'exec-in-cgroup','--cgroup',task['cgroup'],'--',*argv]
        start=time.monotonic();utc=time.time_ns()
        result=subprocess.run(command,capture_output=True,timeout=7200)
        elapsed=time.monotonic()-start
        (out/'stdout.json').write_bytes(result.stdout)
        (out/'stderr.log').write_bytes(result.stderr)
        record={'id':task['id'],'host':task['host_name'],'action':task['action'],
                'started_utc_ns':utc,'finished_utc_ns':time.time_ns(),
                'wall_seconds':elapsed,'returncode':result.returncode,'raw_directory':str(out)}
        if result.returncode==0:record['application']=json.loads(result.stdout)
        (out/'result.json').write_text(json.dumps(record,indent=2)+'\n')
        return record
    with ThreadPoolExecutor(max_workers=max(1,len(tasks))) as pool:results=list(pool.map(one,tasks))
    print(json.dumps({'ok':all(item['returncode']==0 for item in results),'results':results}))
    if any(item['returncode'] for item in results):raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="sub", required=True)

    p1 = sub.add_parser("start-batch")
    p1.add_argument("--mounts", type=Path, required=True)
    p1.add_argument("--specs", type=Path, required=True)
    p1.add_argument("--audit-dir", type=Path, required=True)
    p1.set_defaults(func=cmd_start_batch)

    p2 = sub.add_parser("stop-batch")
    p2.add_argument("--mounts-info", type=Path, required=True)
    p2.add_argument("--stop-dir", type=Path, required=True)
    p2.add_argument("--remove-cache", action="store_true")
    p2.set_defaults(func=cmd_stop_batch)

    p3 = sub.add_parser("c2-replay-batch")
    p3.add_argument("--tasks", type=Path, required=True)
    p3.set_defaults(func=cmd_c2_replay_batch)

    p4 = sub.add_parser("c2-verify-batch")
    p4.add_argument("--tasks", type=Path, required=True)
    p4.set_defaults(func=cmd_c2_verify_batch)

    p5 = sub.add_parser("c3-batch")
    p5.add_argument("--tasks", type=Path, required=True)
    p5.set_defaults(func=cmd_c3_batch)

    p6 = sub.add_parser('c4-batch')
    p6.add_argument('--tasks',type=Path,required=True)
    p6.set_defaults(func=cmd_c4_batch)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
