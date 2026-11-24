#!/usr/bin/env python3
"""All four baseline experiment groups (01-io, 02-fork, 03-packed, 04-agent-replay) for CephFS and s3fs."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import math
from pathlib import Path
import random
import shutil
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_cluster_lib as cl

for _cand in (cl.BASELINE_ROOT, Path(__file__).resolve().parents[3]):
    if (_cand / "bench" / "cluster" / "c4_workload.py").is_file() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

CASES = (
    "pytest-full-02",
    "sklearn",
    "dabench-39-01",
    "tb-access-logs-01",
    "office-excel-01",
    "bfcl-travel-01",
    "ml-vectorization-01",
    "appworld-export-01",
)

VERIFY_HOST_MAP = {
    "node-coord": "node-store-2",
    "node-store-2": "node-store-3",
    "node-store-3": "node-meta",
    "node-meta": "node-coord",
    "node-store-1": "node-store-4",
    "node-store-4": "node-store-1",
}
HOST_IDX = {h["name"]: idx for idx, h in enumerate(cl.HOSTS)}


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def select_backend_for_job(params: dict, out_dir: Path) -> str:
    if params["system"] == "cephfs":
        chk = cl.run_on_host("node-coord", ["/usr/bin/ceph", "--connect-timeout", "3", "-s", "-f", "json"], timeout=10.0, check=False, log_cmd=False)
        if chk.returncode != 0:
            cl.run_on_host("node-coord", ["/usr/local/bin/cephfs-cluster", "start"], timeout=300.0)
        for h in cl.ALL_HOST_NAMES:
            cl.run_on_host(h, ["sh", "-c", "mountpoint -q /mnt/cephfs && umount -l /mnt/cephfs || true"], check=False, log_cmd=False)
        p = cl.run_on_host("node-coord", ["/usr/bin/ceph", "--connect-timeout", "5", "-s", "-f", "json"], timeout=15.0, log_cmd=False)
        health = json.loads(p.stdout.decode("utf-8")).get("health", {})
        save_json(out_dir / "backend.json", {"backend": "cephfs", "ceph_health": health})
        return "cephfs"

    bname = "official" if params.get("dynamic") == "off" else "dynamic"
    cl.select_rustfs_backend(bname)
    bcfg = cl.RUSTFS_BACKENDS[bname]
    for ep in bcfg["endpoints"]:
        cl.make_s3_client(ep).list_buckets()
    sel = dict(bcfg, name=bname)
    save_json(out_dir / "backend.json", sel)
    tmp_sel = out_dir / "collector-backend.json"
    save_json(tmp_sel, sel)
    for host in bcfg["nodes"]:
        cl.sync_file_to_host(host, tmp_sel, "/srv/ax-cluster/baselines/runtime/rustfs-backend.json")
    tmp_sel.unlink(missing_ok=True)
    return bname


def _make_mount_req(
    mid: str,
    phase: str,
    system: str,
    host_ssh: str,
    slot: int,
    workspace: str,
    parent_ws: str,
    purpose: str,
    *,
    resource_profile: str = "default",
    cache_state: str = "cold",
    windows: list[str] | None = None,
    overrides: dict | None = None,
) -> dict:
    req = {
        "id": mid,
        "resource_profile": resource_profile,
        "phase": phase,
        "engine": system,
        "host": host_ssh,
        "slot": slot,
        "workspace": workspace,
        "parent_workspace": parent_ws,
        "purpose": purpose,
        "cache_state": cache_state,
        "windows": windows or [phase],
        "root": str(cl.RUN_ROOT),
    }
    if overrides:
        req["overrides"] = overrides
    return req


def _diff_numeric_map(m0: dict[str, float], m1: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v1 in m1.items():
        v0 = m0.get(k, 0.0)
        if v1 != v0:
            out[k] = round(v1 - v0, 6)
    return out


def _percentile_nearest_rank(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = max(0, min(len(s) - 1, math.ceil(len(s) * q) - 1))
    return round(float(s[idx]), 6)


def _resolve_scale(scale: str = "formal", small: bool = False) -> str:
    if scale != "formal":
        if scale not in ("formal", "check", "tiny"):
            raise ValueError(f"Unsupported scale: {scale!r}")
        return scale
    return "check" if small else "formal"


# ==============================================================================
# Group 1: 01-io (Basic fio 8 jobs, direct=1, cold + hot read, fsync write)
# ==============================================================================

def build_fio_jobfile(
    params: dict,
    mount_dir: str,
    seed: int,
    *,
    bytes_per_job: int = 256 * 1024**2,
    jobs: int = 8,
) -> tuple[str, bool, str, int]:
    """Generate the exact fio configuration matching bench/cluster/runtime/run_c1.py."""
    pattern = params["pattern"]
    op = params["operation"]
    rk = int(params["request_kib"])
    per_job = int(params["io_bytes"]) // jobs

    lines = [
        "[global]",
        "ioengine=psync",
        "iodepth=1",
        "direct=1",
        "do_verify=0",
        f"size={bytes_per_job}",
        "group_reporting=1",
        "randrepeat=1",
        "norandommap=0",
    ]
    is_pure_read = False
    verify_mode = "read"
    seq_written_per_job = 0

    if pattern == "random":
        if op == "read":
            lines += ["rw=randread", f"bs={rk}k", f"io_size={per_job}"]
            is_pure_read = True
        else:
            fsync_bytes = int(params.get("fsync_bytes_all_jobs", 64 * 1024**2))
            if fsync_bytes <= 0 or fsync_bytes % (jobs * rk * 1024):
                raise ValueError("Fsync interval must divide into whole requests per job")
            fsync_n = fsync_bytes // (jobs * rk * 1024)
            lines += [
                "rw=randwrite",
                f"bs={rk}k",
                f"io_size={per_job}",
                f"fsync={fsync_n}",
                "end_fsync=1",
                "buffer_pattern=0x5a3c96e1",
            ]
            verify_mode = "randwrite"
    elif pattern == "sequential":
        if op == "read":
            lines += ["rw=read", "bs=1024k", f"io_size={per_job}"]
            is_pure_read = True
        else:
            fsync_bytes = int(params.get("fsync_bytes_all_jobs", 64 * 1024**2))
            if fsync_bytes <= 0 or fsync_bytes % (jobs * 1024**2):
                raise ValueError("Fsync interval must divide into whole requests per job")
            fsync_n = fsync_bytes // (jobs * 1024**2)
            lines += [
                "rw=write",
                "bs=1024k",
                f"io_size={per_job}",
                f"fsync={fsync_n}",
                "end_fsync=1",
                "buffer_pattern=0x5a3c96e1",
            ]
            verify_mode = "seqwrite"
            seq_written_per_job = per_job
    else:
        raise ValueError(f"Unsupported fio pattern: {pattern}")

    for j in range(jobs):
        lines += [
            f"[job{j}]",
            f"filename={mount_dir}/job-{j}.dat",
            f"randseed={seed + j * 1009}",
        ]
    return "\n".join(lines) + "\n", is_pure_read, verify_mode, seq_written_per_job


def fio_json_to_metrics(fio_doc: dict) -> dict:
    read_ops = 0
    write_ops = 0
    read_bytes = 0
    write_bytes = 0
    buckets = [0] * 32
    max_us = 0.0
    min_us = None

    for job in fio_doc.get("jobs", []):
        for ddir, is_read in (("read", True), ("write", False)):
            sec = job.get(ddir) or {}
            ios = int(sec.get("total_ios", 0))
            ibytes = int(sec.get("io_bytes", 0))
            if is_read:
                read_ops += ios
                read_bytes += ibytes
            else:
                write_ops += ios
                write_bytes += ibytes
            clat = sec.get("clat_ns") or {}
            if ios > 0:
                mn = float(clat.get("min", 0)) / 1000.0
                mx = float(clat.get("max", 0)) / 1000.0
                max_us = max(max_us, mx)
                min_us = mn if min_us is None else min(min_us, mn)
            for ns_str, cnt in (clat.get("bins") or {}).items():
                us = int(ns_str) // 1000
                b = 0 if us <= 0 else min(31, us.bit_length())
                buckets[b] += int(cnt)

    j0 = (fio_doc.get("jobs") or [{}])[0]
    merged_hist = cl.merge_log2_histograms(
        [{"buckets": buckets, "max_us": max_us, "min_us": min_us or 0.0, "count": sum(buckets)}]
    )
    fio_pct: dict[str, dict[str, float]] = {}
    for ddir in ("read", "write"):
        p_map = ((j0.get(ddir) or {}).get("clat_ns") or {}).get("percentile") or {}
        if p_map:
            fio_pct[ddir] = {
                "p50_us": round(float(p_map.get("50.000000", 0.0)) / 1000.0, 3),
                "p95_us": round(float(p_map.get("95.000000", 0.0)) / 1000.0, 3),
                "p99_us": round(float(p_map.get("99.000000", 0.0)) / 1000.0, 3),
            }
    if read_ops > 0 and write_ops == 0 and "read" in fio_pct:
        merged_hist.update(fio_pct["read"])
    elif write_ops > 0 and read_ops == 0 and "write" in fio_pct:
        merged_hist.update(fio_pct["write"])

    return {
        "operations": read_ops + write_ops,
        "read_operations": read_ops,
        "write_operations": write_ops,
        "read_bytes": read_bytes,
        "write_bytes": write_bytes,
        "latency_us": merged_hist,
        "fio_direction_percentiles_us": fio_pct,
        "fio_read_throughput_mib_s": sum((j.get("read") or {}).get("bw_bytes", 0) for j in fio_doc.get("jobs", [])) / 1048576.0,
        "fio_write_throughput_mib_s": sum((j.get("write") or {}).get("bw_bytes", 0) for j in fio_doc.get("jobs", [])) / 1048576.0,
        "fio_io_seconds": max(
            [
                float((j.get(direction) or {}).get("runtime", 0)) / 1000.0
                for j in fio_doc.get("jobs", [])
                for direction in ("read", "write")
            ]
            or [0.0]
        ),
    }


def _prepare_io_workspace(
    system: str,
    ws_tag: str,
    bytes_per_job: int,
    jobs: int,
    out_dir: Path,
    *,
    reusable_read_pool: bool,
    backend_tag: str,
) -> dict:
    if reusable_read_pool:
        reg = cl.RUN_ROOT / "prepared" / f"io-read-{system}-{backend_tag}-{bytes_per_job}.json"
        if reg.is_file():
            doc = json.loads(reg.read_text(encoding="utf-8"))
            if doc.get("prepared"):
                return doc

    t0 = time.monotonic()
    prep_id = f"io-prep-{ws_tag}"
    overrides = {"disk_cache": "off"} if system == "s3fs" else {}
    if system == "cephfs":
        subdir = (
            f"{cl.CEPHFS_ROOT_PREFIX}/io-read-base-{bytes_per_job}"
            if reusable_read_pool
            else f"{cl.CEPHFS_ROOT_PREFIX}/io-runs/{ws_tag}"
        )
        cl.remove_cephfs_subdirs([subdir], out_dir, tag=f"clean-{ws_tag}")
        cl.ensure_cephfs_subdirs([subdir], out_dir, tag=f"mkdir-{ws_tag}")
        m_spec = {"subdir": subdir}
        v_spec = {"subdir": subdir}
        workspace = subdir
        bucket = None
    else:
        bucket = (
            f"{cl.BUCKET_PREFIX}-io-rbase-{backend_tag}-{bytes_per_job // (1024 * 1024)}m"
            if reusable_read_pool
            else f"{cl.BUCKET_PREFIX}-io-{ws_tag}"
        ).lower()[:62].rstrip("-")
        cl.delete_bucket_and_objects(bucket)
        cl.ensure_bucket(bucket)
        cl.ensure_s3fs_dir_marker(bucket, "data")
        m_spec = {"bucket": bucket, "prefix": "data", "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]}
        v_spec = {"bucket": bucket, "prefix": "data", "s3_endpoint": cl.RUSTFS_ENDPOINTS[1]}
        workspace = f"{bucket}:/data"

    mp_cfg = out_dir / f"{prep_id}-mounts.json"
    cl.generate_mounts_json(
        [
            _make_mount_req(
                prep_id,
                "initial",
                system,
                "cluster-node-1",
                50,
                workspace,
                "empty",
                "io-init",
                resource_profile="fio",
                overrides=overrides,
            )
        ],
        mp_cfg,
    )
    mounted = cl.batch_start_mounts(mp_cfg, {prep_id: m_spec}, f"{prep_id}-s", register_collector=False)
    try:
        cl.run_on_host(
            "node-coord",
            [
                str(cl.BIN_DIR / "c1-io-helper"),
                "init-files",
                "--root",
                mounted[0]["mount_directory"],
                "--jobs",
                str(jobs),
                "--bytes-per-job",
                str(bytes_per_job),
            ],
            timeout=14400.0,
        )
        cl.wait_uploads(mounted, timeout=7200.0)
    finally:
        cl.batch_stop_mounts(mounted, f"{prep_id}-x", clear_collector=False, remove_cache=True)
        mp_cfg.unlink(missing_ok=True)

    info = {
        "prepared": True,
        "system": system,
        "reusable_read_pool": reusable_read_pool,
        "workspace": workspace,
        "bucket": bucket,
        "measure_spec": m_spec,
        "verify_spec": v_spec,
        "prepare_seconds": round(time.monotonic() - t0, 6),
        "bytes": jobs * bytes_per_job,
    }
    if reusable_read_pool:
        save_json(cl.RUN_ROOT / "prepared" / f"io-read-{system}-{backend_tag}-{bytes_per_job}.json", info)
    return info


def _cleanup_io_workspace(ws_info: dict, out_dir: Path, ws_tag: str) -> None:
    if ws_info.get("reusable_read_pool"):
        return
    if ws_info["system"] == "cephfs":
        cl.remove_cephfs_subdirs([ws_info["workspace"]], out_dir, tag=f"rm-{ws_tag}")
    elif ws_info.get("bucket"):
        cl.delete_bucket_and_objects(ws_info["bucket"])


def _enrich_io_amplification(delta: dict, read_bytes: int, write_bytes: int, system: str) -> dict:
    d = dict(delta)
    if system == "s3fs":
        s3_get_b = int(d.get("rustfs_s3_get_bytes", 0))
        s3_put_b = int(d.get("rustfs_s3_put_bytes", 0))
        d["s3_get_bytes"] = s3_get_b
        d["s3_put_bytes"] = s3_put_b
        d["read_amplification_s3"] = round(s3_get_b / read_bytes, 6) if read_bytes > 0 else None
        d["write_amplification_s3"] = round(s3_put_b / write_bytes, 6) if write_bytes > 0 else None
    else:
        osd_r = int(d.get("ceph_osd_read_bytes_delta", 0))
        osd_w = int(d.get("ceph_osd_write_bytes_delta", 0))
        d["s3_get_bytes"] = None
        d["s3_put_bytes"] = None
        d["read_amplification_osd"] = round(osd_r / read_bytes, 6) if read_bytes > 0 else None
        d["write_amplification_osd"] = round(osd_w / write_bytes, 6) if write_bytes > 0 else None
    d["read_amplification_physical"] = round(d["backend_physical_read_bytes"] / read_bytes, 6) if read_bytes > 0 else None
    d["write_amplification_physical"] = round(d["backend_physical_write_bytes"] / write_bytes, 6) if write_bytes > 0 else None
    return d


def run_io(job: dict, out_dir: Path, small: bool = False, scale: str = "formal") -> dict:
    mode = _resolve_scale(scale, small)
    p = copy.deepcopy(job["parameters"])
    backend_tag = select_backend_for_job(p, out_dir)
    jobs = int(p.get("jobs", 8))
    if mode == "tiny":
        bytes_per_job = 2 * 1024 * 1024
        p["io_bytes"] = 8 * 1024 * 1024 if p["pattern"] == "sequential" else 512 * 1024
    elif mode == "check":
        bytes_per_job = 4 * 1024 * 1024
        p["io_bytes"] = 16 * 1024 * 1024
    else:
        ws_bytes = int(p.get("working_set_bytes", 2 * 1024**3))
        if ws_bytes <= 0 or ws_bytes % jobs:
            raise ValueError("Fio input must divide evenly across its jobs")
        bytes_per_job = ws_bytes // jobs

    _, is_pure_read, verify_mode, seq_written = build_fio_jobfile(
        p, "/tmp", int(job["seed"]), bytes_per_job=bytes_per_job, jobs=jobs
    )
    ws_tag = f"{job['id']}-{out_dir.name}".lower().replace("_", "-")
    pool_suffix = "-tiny" if mode == "tiny" else ("-chk" if mode == "check" else "")

    with cl.MeasurementLock():
        ws_info = _prepare_io_workspace(
            p["system"],
            ws_tag,
            bytes_per_job,
            jobs,
            out_dir,
            reusable_read_pool=is_pure_read,
            backend_tag=backend_tag + pool_suffix,
        )
        save_json(out_dir / "input.json", ws_info)

        measure_overrides = {"direct_io": "on"} if p["system"] == "s3fs" else {}
        verify_overrides = {"disk_cache": "off"} if p["system"] == "s3fs" else {}
        m_id = f"{ws_tag}-m"
        v_id = f"{ws_tag}-v"
        mp_path = out_dir / "mounts.json"
        cl.generate_mounts_json(
            [
                _make_mount_req(
                    m_id,
                    "measure",
                    p["system"],
                    "cluster-node-1",
                    1,
                    ws_info["workspace"],
                    "io-input",
                    job["id"],
                    resource_profile="fio",
                    windows=["measure"],
                    overrides=measure_overrides,
                ),
                _make_mount_req(
                    v_id,
                    "verify",
                    p["system"],
                    "cluster-node-2",
                    21,
                    ws_info["workspace"],
                    "io-input",
                    f"{job['id']}-verify",
                    resource_profile="fio",
                    cache_state="cross-host-cold",
                    windows=["verify"],
                    overrides=verify_overrides,
                ),
            ],
            mp_path,
        )

        if is_pure_read:
            cl.evict_storage_page_cache()

        started = cl.batch_start_mounts(mp_path, {m_id: ws_info["measure_spec"]}, f"{ws_tag}-ms", register_collector=True)
        m_info = started[0]
        job_txt, _, _, _ = build_fio_jobfile(
            p, m_info["mount_directory"], int(job["seed"]), bytes_per_job=bytes_per_job, jobs=jobs
        )
        job_file = out_dir / "workload.fio"
        job_file.write_text(job_txt, encoding="utf-8")

        hot_seconds = None
        hot_metrics = None
        hot_delta = None
        upload_drain_s = 0.0
        stop_info = {}
        try:
            if is_pure_read:
                fio_cold = out_dir / "fio-cold.json"
                met_c0 = cl.read_mount_metrics(m_info)["metrics"]
                snap_c0 = cl.collector_mark_all(job["id"], "cold_start")
                t_c0 = time.monotonic()
                cl.run_on_host(
                    "node-coord",
                    [
                        "/usr/bin/python3",
                        str(cl.SCRIPTS_DIR / "baseline_mount_driver.py"),
                        "exec-in-cgroup",
                        "--cgroup",
                        m_info["cgroup"],
                        "--",
                        "fio",
                        "--output-format=json+",
                        f"--output={fio_cold}",
                        str(job_file),
                    ],
                    timeout=14400.0,
                )
                app_done_s = time.monotonic() - t_c0
                snap_c1 = cl.collector_mark_all(job["id"], "cold_done")
                met_c1 = cl.read_mount_metrics(m_info)["metrics"]

                fio_hot = out_dir / "fio-hot.json"
                snap_h0 = cl.collector_mark_all(job["id"], "hot_start")
                t_h0 = time.monotonic()
                cl.run_on_host(
                    "node-coord",
                    [
                        "/usr/bin/python3",
                        str(cl.SCRIPTS_DIR / "baseline_mount_driver.py"),
                        "exec-in-cgroup",
                        "--cgroup",
                        m_info["cgroup"],
                        "--",
                        "fio",
                        "--output-format=json+",
                        f"--output={fio_hot}",
                        str(job_file),
                    ],
                    timeout=14400.0,
                )
                hot_seconds = time.monotonic() - t_h0
                snap_h1 = cl.collector_mark_all(job["id"], "hot_done")
                met_h1 = cl.read_mount_metrics(m_info)["metrics"]

                app_metrics = fio_json_to_metrics(json.loads(fio_cold.read_text(encoding="utf-8")))
                hot_metrics = fio_json_to_metrics(json.loads(fio_hot.read_text(encoding="utf-8")))
                mount_delta = _diff_numeric_map(met_c0, met_c1)
                backend_delta = _enrich_io_amplification(
                    cl.summarize_phase_delta(snap_c0, snap_c1),
                    app_metrics["read_bytes"],
                    app_metrics["write_bytes"],
                    p["system"],
                )
                hot_delta = _enrich_io_amplification(
                    cl.summarize_phase_delta(snap_h0, snap_h1),
                    hot_metrics["read_bytes"],
                    hot_metrics["write_bytes"],
                    p["system"],
                )
                hot_delta["mount_metrics_delta"] = _diff_numeric_map(met_c1, met_h1)
            else:
                fio_out = out_dir / "fio.json"
                met_0 = cl.read_mount_metrics(m_info)["metrics"]
                snap_0 = cl.collector_mark_all(job["id"], "write_start")
                t_w0 = time.monotonic()
                cl.run_on_host(
                    "node-coord",
                    [
                        "/usr/bin/python3",
                        str(cl.SCRIPTS_DIR / "baseline_mount_driver.py"),
                        "exec-in-cgroup",
                        "--cgroup",
                        m_info["cgroup"],
                        "--",
                        "fio",
                        "--output-format=json+",
                        f"--output={fio_out}",
                        str(job_file),
                    ],
                    timeout=14400.0,
                )
                app_done_s = time.monotonic() - t_w0
                cl.collector_mark_all(job["id"], "app_done")
                up_res = cl.wait_uploads(started, timeout=7200.0)[0]
                upload_drain_s = float(up_res["upload_drain_seconds"])
                snap_1 = cl.collector_mark_all(job["id"], "upload_done")
                met_1 = cl.read_mount_metrics(m_info)["metrics"]

                app_metrics = fio_json_to_metrics(json.loads(fio_out.read_text(encoding="utf-8")))
                mount_delta = _diff_numeric_map(met_0, met_1)
                backend_delta = _enrich_io_amplification(
                    cl.summarize_phase_delta(snap_0, snap_1),
                    app_metrics["read_bytes"],
                    app_metrics["write_bytes"],
                    p["system"],
                )
            expected = p.get("io_bytes")
            if expected is not None:
                for measured in (app_metrics, hot_metrics):
                    if measured is not None and measured["read_bytes"] + measured["write_bytes"] != int(expected):
                        raise RuntimeError("Fio completed fewer bytes than the configured workload")
        finally:
            stopped = cl.batch_stop_mounts(started, f"{ws_tag}-mx", clear_collector=True, remove_cache=True)
            stop_info = stopped[0] if stopped else {}

        # Cross-host cold remount verification on node-meta
        t_v0 = time.monotonic()
        v_started = cl.batch_start_mounts(mp_path, {v_id: ws_info["verify_spec"]}, f"{ws_tag}-vs", register_collector=False)
        try:
            v_cmd = [
                str(cl.BIN_DIR / "c1-io-helper"),
                "verify-files",
                "--root",
                v_started[0]["mount_directory"],
                "--jobs",
                str(jobs),
                "--bytes-per-job",
                str(bytes_per_job),
                "--mode",
                verify_mode,
                "--seed",
                str(job["seed"]),
            ]
            if verify_mode == "seqwrite":
                v_cmd += ["--seq-written-per-job", str(seq_written)]
            vp = cl.run_on_host("node-meta", v_cmd, timeout=3600.0)
            verify_doc = json.loads(vp.stdout.decode("utf-8").strip().splitlines()[-1])
            save_json(out_dir / "verify.json", verify_doc)
        finally:
            cl.batch_stop_mounts(v_started, f"{ws_tag}-vx", clear_collector=False, remove_cache=True)
        verify_s = time.monotonic() - t_v0

        save_json(
            out_dir / "measurement.json",
            {
                "phase": "verified",
                "measurement_completed_at": cl.utc_iso(),
                "app_done_seconds": app_done_s,
                "repeat_read_seconds": hot_seconds,
                "application": app_metrics,
                "repeat_read": hot_metrics,
                "backend_delta": backend_delta,
                "repeat_backend_delta": hot_delta,
                "verification": verify_doc,
            },
        )
        _cleanup_io_workspace(ws_info, out_dir, ws_tag)

    tot_bytes = app_metrics["read_bytes"] + app_metrics["write_bytes"]
    exit_s = float(stop_info.get("exit_seconds", 0.0))
    rr_no_ver = app_done_s + upload_drain_s
    return {
        "schema": "ax-baseline-io-result-v1",
        "scale": mode,
        "system": p["system"],
        "backend": backend_tag,
        "seed": job["seed"],
        "timing": {
            "input_prepare_seconds": ws_info["prepare_seconds"],
            "mount_ready_seconds": round(m_info["mount_ready_seconds"], 6),
            "app_done_seconds": round(app_done_s, 6),
            "hot_pass_seconds": round(hot_seconds, 6) if hot_seconds is not None else None,
            "upload_drain_seconds": round(upload_drain_s, 6),
            "remote_ready_no_verify_seconds": round(rr_no_ver, 6),
            "exit_seconds": round(exit_s, 6),
            "verify_seconds": round(verify_s, 6),
            "remote_ready_with_verify_seconds": round(rr_no_ver + exit_s + verify_s, 6),
        },
        "application": {
            **app_metrics,
            "throughput_mib_s": round((tot_bytes / 1048576.0) / max(1e-9, app_done_s), 6),
            "iops": round(app_metrics["operations"] / max(1e-9, app_done_s), 3),
            "hot_throughput_mib_s": (
                round(((hot_metrics["read_bytes"] + hot_metrics["write_bytes"]) / 1048576.0) / max(1e-9, hot_seconds), 6)
                if (hot_metrics and hot_seconds)
                else None
            ),
            "hot_iops": round(hot_metrics["operations"] / max(1e-9, hot_seconds), 3) if (hot_metrics and hot_seconds) else None,
            "hot_latency_us": hot_metrics["latency_us"] if hot_metrics else None,
        },
        "backend_delta": backend_delta,
        "hot_backend_delta": hot_delta,
        "mount_metrics_delta": mount_delta,
        "client_mount": {
            "measure_host": "node-coord",
            "verify_host": "node-meta",
            "peak_memory_bytes": (stop_info.get("final_cgroup") or {}).get("memory_peak"),
        },
        "verification": verify_doc,
    }


# ==============================================================================
# Group 2: 02-fork (Workspace creation at 1/16/64 & direct-parent comparison)
# ==============================================================================

def _c4_workload_on_mount(mount: dict, action: str, files: int, changes_file: Path | None = None) -> dict:
    argv = [
        "/usr/bin/python3",
        str(cl.SCRIPTS_DIR / "baseline_mount_driver.py"),
        "exec-in-cgroup",
        "--cgroup",
        mount["cgroup"],
        "--",
        "/usr/bin/python3",
        "-m",
        "bench.cluster.c4_workload",
        action,
        "--root",
        mount["mount_directory"],
        "--files",
        str(files),
        "--file-bytes",
        "4096",
    ]
    if changes_file is not None:
        remote_chg = f"{cl.RUN_ROOT}/hosts/{mount['host_name']}/batch/{mount['id']}-changes.json"
        cl.sync_file_to_host(mount["host_name"], changes_file, remote_chg)
        argv += ["--changes", remote_chg]
    p = cl.run_on_host(mount["host_name"], argv, timeout=7200.0)
    if changes_file is not None:
        cl.run_on_host(mount["host_name"], ["rm", "-f", remote_chg], check=False, log_cmd=False)
    return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])


def _prepare_fork_parent(system: str, files: int, file_bytes: int, direct: bool, out_dir: Path) -> dict:
    suffix = "-direct" if direct else ""
    tag = f"{system}-{files}-{file_bytes}{suffix}"
    state_file = cl.RUN_ROOT / "prepared" / "fork" / tag / "parent.json"
    if not direct and state_file.is_file():
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        if saved.get("prepared"):
            return saved

    t0 = time.monotonic()
    prep_id = f"fork-prep-{tag}"
    if system == "cephfs":
        base_sd = f"{cl.CEPHFS_ROOT_PREFIX}/fork/{tag}/base"
        cl.remove_cephfs_subdirs([f"{cl.CEPHFS_ROOT_PREFIX}/fork/{tag}"], out_dir, tag=f"rm-{tag}")
        cl.ensure_cephfs_subdirs([base_sd], out_dir, tag=f"mk-{tag}")
        spec = {"subdir": base_sd}
        workspace = base_sd
        bucket = None
    else:
        bucket = f"{cl.BUCKET_PREFIX}-fork-{files}-{file_bytes}{suffix}".lower()[:62]
        cl.delete_bucket_and_objects(bucket)
        cl.ensure_bucket(bucket)
        cl.ensure_s3fs_dir_marker(bucket, "base")
        spec = {"bucket": bucket, "prefix": "base", "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]}
        workspace = "base"

    mp_cfg = out_dir / f"{prep_id}-mounts.json"
    cl.generate_mounts_json(
        [_make_mount_req(prep_id, "initial", system, "cluster-node-1", 50, workspace, "none", "fork-parent-prep")],
        mp_cfg,
    )
    mounted = cl.batch_start_mounts(mp_cfg, {prep_id: spec}, f"{prep_id}-s", register_collector=False)
    try:
        pop_p = cl.run_on_host(
            "node-coord",
            [
                "/usr/bin/python3",
                "-m",
                "bench.cluster.c4_workload",
                "prepare",
                "--root",
                mounted[0]["mount_directory"],
                "--files",
                str(files),
                "--file-bytes",
                str(file_bytes),
                "--workers",
                "16",
            ],
            timeout=10800.0,
        )
        population = json.loads(pop_p.stdout.decode("utf-8").strip().splitlines()[-1])
        up = cl.wait_uploads(mounted, timeout=3600.0)
    finally:
        cl.batch_stop_mounts(mounted, f"{prep_id}-x", clear_collector=False, remove_cache=True)
        mp_cfg.unlink(missing_ok=True)

    keep_keys = sorted(cl.list_all_keys(bucket, prefix="base/")) if bucket else []
    parent = {
        "prepared": True,
        "system": system,
        "files": files,
        "file_bytes": file_bytes,
        "tag": tag,
        "bucket": bucket,
        "base": workspace,
        "keep_keys": keep_keys,
        "population": population,
        "upload": up,
        "prepare_seconds": round(time.monotonic() - t0, 6),
    }
    if not direct:
        save_json(state_file, parent)
    return parent


def run_fork(job: dict, out_dir: Path, small: bool = False, scale: str = "formal") -> dict:
    mode = _resolve_scale(scale, small)
    p = job["parameters"]
    system = p["system"]
    backend_tag = select_backend_for_job(p, out_dir)
    files = 16 if mode == "tiny" else (32 if mode == "check" else int(p["files"]))
    file_bytes = int(p["file_bytes"])
    direct = p["mode"] == "direct-parent"

    with cl.MeasurementLock():
        parent = _prepare_fork_parent(system, files, file_bytes, direct, out_dir)
        save_json(out_dir / "parent.json", parent)
        cl.evict_storage_page_cache()

        run_tag = f"{job['id']}-{out_dir.name}".lower().replace("_", "-")
        if direct:
            children = [parent["base"]]
            fork_copy_cost = cl.fuse_copy_workspaces(system, [], out_dir, tag=f"fk-{run_tag}")
            fork_copy_cost["mechanism"] = "direct-parent"
            fork_seconds = 0.0
            added_storage_bytes = 0
            storage_before = cl.ceph_df_summary() if system == "cephfs" else cl.bucket_usage_summary(parent["bucket"])
            storage_after = storage_before
            fork_backend_delta: dict = {}
        else:
            n_children = min(int(p["children"]), 2) if mode in ("tiny", "check") else int(p["children"])
            if system == "cephfs":
                children = [
                    f"{cl.CEPHFS_ROOT_PREFIX}/fork/{parent['tag']}/children/{run_tag}/c{i:03d}"
                    for i in range(n_children)
                ]
                copy_entries = [{"src": parent["base"], "dst": ch} for ch in children]
            else:
                children = [f"children/{run_tag}/c{i:03d}" for i in range(n_children)]
                copy_entries = [{"bucket": parent["bucket"], "src": parent["base"], "dst": ch} for ch in children]
            fork_copy_cost = cl.fuse_copy_workspaces(
                system,
                copy_entries,
                out_dir,
                tag=f"fk-{run_tag}",
                outer_workers=min(8, max(1, n_children)),
                inner_workers=12,
            )
            fork_seconds = fork_copy_cost["timing"]["total_copy_seconds"]
            added_storage_bytes = fork_copy_cost["storage_cost"]["added_storage_bytes"]
            storage_before = fork_copy_cost["storage_cost"].get("before")
            storage_after = fork_copy_cost["storage_cost"].get("after")
            fork_backend_delta = fork_copy_cost["backend_io_cost"]
        save_json(out_dir / "fork-copy-cost.json", fork_copy_cost)

        reqs, specs = [], {}
        for idx, (ws, (host, slot)) in enumerate(zip(children, cl.placements(len(children)))):
            mid = f"{run_tag}-c{idx:03d}"
            reqs.append(_make_mount_req(mid, "measure", system, cl.HOST_BY_NAME[host]["ssh"], slot, ws, parent["base"], "02-fork"))
            specs[mid] = (
                {"subdir": ws}
                if system == "cephfs"
                else {"bucket": parent["bucket"], "prefix": ws, "s3_endpoint": cl.endpoint_for_slot(slot, HOST_IDX[host])}
            )
        from bench.cluster.c4_workload import make_changes

        ow_cap = 4 if mode == "tiny" else files
        ow_count = min(ow_cap, files, int(p["overwrite_files"]))
        changes_file = out_dir / "changes.json"
        save_json(changes_file, make_changes(ow_count, kind="range-overwrite", seed=int(job["seed"])))

        mounts_cfg = out_dir / "mounts.json"
        cl.generate_mounts_json(reqs, mounts_cfg)
        mounted = cl.batch_start_mounts(mounts_cfg, specs, f"{run_tag}-m", register_collector=True)

        result: dict = {
            "schema": "ax-baseline-fork-result-v1",
            "scale": mode,
            "system": system,
            "backend": backend_tag,
            "mode": p["mode"],
            "fork_mechanism": fork_copy_cost["mechanism"],
            "children": len(children),
            "files": files,
            "file_bytes": file_bytes,
            "parent_prepare_seconds": parent["prepare_seconds"],
            "fork_seconds": fork_seconds,
            "fork_copy_cost": fork_copy_cost,
            "added_storage_bytes": added_storage_bytes,
            "storage_before": storage_before,
            "storage_after": storage_after,
            "mount_ready_seconds": [m["mount_ready_seconds"] for m in mounted],
            "fork_backend_delta": fork_backend_delta,
        }

        try:
            if int(p["read_files"]) > 0:
                s_r0 = cl.collector_mark_all(job["id"], "read-start")
                result["read"] = _c4_workload_on_mount(mounted[0], "read", min(files, int(p["read_files"])))
                s_r1 = cl.collector_mark_all(job["id"], "read-end")
                result["read_backend_delta"] = cl.summarize_phase_delta(s_r0, s_r1)
                result["write"] = _c4_workload_on_mount(mounted[0], "apply", files, changes_file)
                result["upload"] = cl.wait_uploads(mounted, timeout=3600.0)
                s_w1 = cl.collector_mark_all(job["id"], "write-end")
                result["write_backend_delta"] = cl.summarize_phase_delta(s_r1, s_w1)
            else:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    list(pool.map(lambda m: _c4_workload_on_mount(m, "first-read", files), mounted))
        finally:
            result["stops"] = cl.batch_stop_mounts(mounted, f"{run_tag}-mx", clear_collector=True, remove_cache=True)

        # Cross-host verification of child 0
        src_host = mounted[0]["host_name"]
        ver_host = "node-meta" if src_host != "node-meta" else "node-coord"
        ver_id = f"{run_tag}-ver"
        ver_cfg = out_dir / "verify-mounts.json"
        cl.generate_mounts_json(
            [
                _make_mount_req(
                    ver_id,
                    "verify",
                    system,
                    cl.HOST_BY_NAME[ver_host]["ssh"],
                    21,
                    children[0],
                    parent["base"],
                    "02-fork-verify",
                    cache_state="cross-host-cold",
                )
            ],
            ver_cfg,
        )
        ver_spec = (
            {"subdir": children[0]}
            if system == "cephfs"
            else {"bucket": parent["bucket"], "prefix": children[0], "s3_endpoint": cl.RUSTFS_ENDPOINTS[1]}
        )
        v_mounted = cl.batch_start_mounts(ver_cfg, {ver_id: ver_spec}, f"{run_tag}-vs", register_collector=False)
        try:
            result["verification"] = _c4_workload_on_mount(
                v_mounted[0],
                "verify" if int(p["read_files"]) > 0 else "first-read",
                files,
                changes_file if ow_count else None,
            )
        finally:
            cl.batch_stop_mounts(v_mounted, f"{run_tag}-vx", clear_collector=False, remove_cache=True)

        # Parent isolation check when child writes occurred
        if not direct and ow_count > 0:
            p_id = f"{run_tag}-pver"
            p_cfg = out_dir / "parent-verify-mounts.json"
            cl.generate_mounts_json(
                [_make_mount_req(p_id, "verify", system, "cluster-node-1", 22, parent["base"], "none", "02-fork-pver")],
                p_cfg,
            )
            p_spec = (
                {"subdir": parent["base"]}
                if system == "cephfs"
                else {"bucket": parent["bucket"], "prefix": parent["base"], "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]}
            )
            p_mounted = cl.batch_start_mounts(p_cfg, {p_id: p_spec}, f"{run_tag}-pvs", register_collector=False)
            try:
                result["parent_verification"] = _c4_workload_on_mount(p_mounted[0], "verify", files)
            finally:
                cl.batch_stop_mounts(p_mounted, f"{run_tag}-pvx", clear_collector=False, remove_cache=True)

        # Clean up ephemeral children or direct-parent copy
        if direct:
            if system == "cephfs":
                cl.remove_cephfs_subdirs([f"{cl.CEPHFS_ROOT_PREFIX}/fork/{parent['tag']}"], out_dir, tag=f"clean-{run_tag}")
            else:
                cl.delete_bucket_and_objects(parent["bucket"])
        else:
            if system == "cephfs":
                cl.remove_cephfs_subdirs([f"{cl.CEPHFS_ROOT_PREFIX}/fork/{parent['tag']}/children/{run_tag}"], out_dir, tag=f"clean-{run_tag}")
            else:
                child_keys = cl.list_all_keys(parent["bucket"], prefix=f"children/{run_tag}/")
                cl.delete_keys_parallel(parent["bucket"], child_keys)

    return result


# ==============================================================================
# Group 3: 03-packed (100k x 4 KiB small files prepare + 32 readers x 2 epochs)
# ==============================================================================

def ensure_smallfiles_corpus(files: int) -> Path:
    root = cl.INPUT_DIR / "smallfiles-v2" / str(files)
    marker = root / "ready.json"
    data = root / "data"
    if marker.is_file():
        return data
    for idx in range((files + 999) // 1000):
        (data / "small" / f"d{idx:04d}").mkdir(parents=True, exist_ok=True)

    def _write_one(index: int) -> None:
        state = index
        buf = bytearray(4096)
        mask = (1 << 64) - 1
        for offset in range(0, 4096, 8):
            state = (state + 0x9E3779B97F4A7C15) & mask
            val = ((state ^ (state >> 30)) * 0xBF58476D1CE4E5B9) & mask
            val = ((val ^ (val >> 27)) * 0x94D049BB133111EB) & mask
            struct.pack_into("<Q", buf, offset, val ^ (val >> 31))
        (data / "small" / f"d{index // 1000:04d}" / f"f{index:07d}").write_bytes(buf)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_write_one, range(files)))
    save_json(marker, {"files": files, "file_bytes": 4096, "files_per_directory": 1000})
    return data


def _prepare_packed_dataset(system: str, backend_tag: str, files: int, workers: int, seed: int, out_dir: Path) -> dict:
    tag = f"{system}-{backend_tag}-{files}"
    source = ensure_smallfiles_corpus(files)

    if system == "cephfs":
        subdir = f"{cl.CEPHFS_ROOT_PREFIX}/packed/{tag}"
        cl.remove_cephfs_subdirs([subdir], out_dir, tag=f"rm-{tag}")
        cl.ensure_cephfs_subdirs([subdir], out_dir, tag=f"mk-{tag}")
        m_spec = {"subdir": subdir}
        v_spec = {"subdir": subdir}
        workspace = subdir
        bucket = None
    else:
        bucket = f"{cl.BUCKET_PREFIX}-packed-{backend_tag}-{files}".lower()[:62]
        cl.delete_bucket_and_objects(bucket)
        cl.ensure_bucket(bucket)
        cl.ensure_s3fs_dir_marker(bucket, "data")
        m_spec = {"bucket": bucket, "prefix": "data", "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]}
        v_spec = {"bucket": bucket, "prefix": "data", "s3_endpoint": cl.RUSTFS_ENDPOINTS[1]}
        workspace = "data"

    prep_id = f"pk-prep-{tag}"
    mp_cfg = out_dir / f"{prep_id}-mounts.json"
    cl.generate_mounts_json(
        [_make_mount_req(prep_id, "measure", system, "cluster-node-1", 1, workspace, "none", "03-packed-prepare")],
        mp_cfg,
    )

    before = cl.collector_mark_all(tag, "prepare-start")
    t_begin = time.monotonic()
    mounted = cl.batch_start_mounts(mp_cfg, {prep_id: m_spec}, f"{prep_id}-s", register_collector=True)
    try:
        target = Path(mounted[0]["mount_directory"])
        dirs = sorted((source / "small").iterdir())
        for d in dirs:
            (target / "small" / d.name).mkdir(parents=True, exist_ok=True)

        def _copy_dir(d: Path) -> int:
            cnt = 0
            dst_d = target / "small" / d.name
            for fp in d.iterdir():
                shutil.copyfile(fp, dst_d / fp.name)
                cnt += 1
            return cnt

        with ThreadPoolExecutor(max_workers=workers) as pool:
            copied = sum(pool.map(_copy_dir, dirs))
        up = cl.wait_uploads(mounted, timeout=7200.0)
    finally:
        cl.batch_stop_mounts(mounted, f"{prep_id}-x", clear_collector=True, remove_cache=True)
    upload_ready_s = time.monotonic() - t_begin
    after = cl.collector_mark_all(tag, "prepare-end")
    save_json(out_dir / "prepare-start.json", before)
    save_json(out_dir / "prepare-end.json", after)

    # Cross-host readability & byte verification on node-meta (completes "time until readable on another host")
    ver_id = f"pk-ver-{tag}"
    ver_cfg = out_dir / f"{ver_id}-mounts.json"
    cl.generate_mounts_json(
        [_make_mount_req(ver_id, "verify", system, "cluster-node-2", 21, workspace, "none", "03-packed-verify", cache_state="cross-host-cold")],
        ver_cfg,
    )
    v_mounted = cl.batch_start_mounts(ver_cfg, {ver_id: v_spec}, f"{ver_id}-s", register_collector=False)
    try:
        remote_out = f"{cl.RUN_ROOT}/hosts/node-meta/{ver_id}.json"
        cl.run_on_host(
            "node-meta",
            [
                str(cl.BIN_DIR / "c3-loader"),
                "load",
                "--root",
                v_mounted[0]["mount_directory"],
                "--output",
                remote_out,
                "--files",
                str(files),
                "--epochs",
                "1",
                "--threads",
                "2",
                "--batch",
                str(min(64, files)),
                "--seed",
                str(seed),
                "--verify-bytes",
                "1",
            ],
            timeout=7200.0,
        )
        ver_doc = json.loads(cl.run_on_host("node-meta", ["cat", remote_out], log_cmd=False).stdout.decode("utf-8"))
        cl.run_on_host("node-meta", ["rm", "-f", remote_out], check=False, log_cmd=False)
    finally:
        cl.batch_stop_mounts(v_mounted, f"{ver_id}-x", clear_collector=False, remove_cache=True)

    remote_readable_s = time.monotonic() - t_begin
    return {
        "schema": "ax-baseline-packed-volume-v1",
        "prepared": True,
        "system": system,
        "backend": backend_tag,
        "files": files,
        "copied_files": copied,
        "workspace": workspace,
        "bucket": bucket,
        "upload": up,
        "prepare_upload_seconds": round(upload_ready_s, 6),
        "prepare_remote_readable_seconds": round(remote_readable_s, 6),
        "backend_delta": cl.summarize_phase_delta(before, after),
        "verification": ver_doc,
    }


def run_packed(job: dict, out_dir: Path, small: bool = False, scale: str = "formal") -> dict:
    mode = _resolve_scale(scale, small)
    p = job["parameters"]
    system = p["system"]
    backend_tag = select_backend_for_job(p, out_dir)
    files = 16 if mode == "tiny" else (32 if mode == "check" else int(p["files"]))
    workers = int(p.get("prepare_workers", 8))
    reg_path = cl.RUN_ROOT / "prepared" / f"packed-{system}-{backend_tag}-{files}.json"

    with cl.MeasurementLock():
        if p["phase"] == "prepare":
            vol = _prepare_packed_dataset(system, backend_tag, files, workers, int(job["seed"]), out_dir)
            vol["scale"] = mode
            save_json(reg_path, vol)
            return vol

        if not reg_path.is_file():
            vol = _prepare_packed_dataset(system, backend_tag, files, workers, int(job["seed"]), out_dir / "auto-prepare")
            vol["scale"] = mode
            save_json(reg_path, vol)
        else:
            vol = json.loads(reg_path.read_text(encoding="utf-8"))

        count = 2 if mode in ("tiny", "check") else int(p["readers"])
        epochs = 1 if mode == "tiny" else int(p["epochs"])
        cl.evict_storage_page_cache()

        run_tag = f"{job['id']}-{out_dir.name}".lower().replace("_", "-")
        reqs, specs = [], {}
        for idx, (host, slot) in enumerate(cl.placements(count)):
            mid = f"{run_tag}-r{idx:03d}"
            reqs.append(_make_mount_req(mid, "measure", system, cl.HOST_BY_NAME[host]["ssh"], slot, vol["workspace"], "none", "03-packed-read"))
            specs[mid] = (
                {"subdir": vol["workspace"]}
                if system == "cephfs"
                else {"bucket": vol["bucket"], "prefix": vol["workspace"], "s3_endpoint": cl.endpoint_for_slot(slot, HOST_IDX[host])}
            )
        mounts_cfg = out_dir / "mounts.json"
        cl.generate_mounts_json(reqs, mounts_cfg)
        mounted = cl.batch_start_mounts(mounts_cfg, specs, f"{run_tag}-m", register_collector=True)

        tasks_by_host: dict[str, list[dict]] = defaultdict(list)
        barrier_ns = 2_000_000_000 if mode in ("tiny", "check") else 5_000_000_000
        start_epoch_ns = time.time_ns() + barrier_ns
        for idx, m in enumerate(mounted):
            out_p = f"{cl.RUN_ROOT}/hosts/{m['host_name']}/{m['id']}-loader.json"
            tasks_by_host[m["host_name"]].append(
                {
                    "reader_id": m["id"],
                    "host": m["host_name"],
                    "slot": m["slot"],
                    "engine": m["engine"],
                    "cgroup": m["cgroup"],
                    "out_path": out_p,
                    "loader_argv": [
                        str(cl.BIN_DIR / "c3-loader"),
                        "load",
                        "--root",
                        m["mount_directory"],
                        "--output",
                        out_p,
                        "--files",
                        str(files),
                        "--epochs",
                        str(epochs),
                        "--threads",
                        "2",
                        "--batch",
                        str(min(64, files)),
                        "--seed",
                        str(int(job["seed"]) + idx * 1009),
                        "--verify-bytes",
                        "1",
                        "--start-unix-ns",
                        str(start_epoch_ns),
                    ],
                }
            )

        before = cl.collector_mark_all(job["id"], "read-start")
        t_r0 = time.monotonic()
        try:
            hosts = sorted(tasks_by_host.keys())

            def _dispatch_host(hname: str) -> list[dict]:
                rp = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-c3.json"
                lt = out_dir / f"{hname}-c3.json"
                save_json(lt, tasks_by_host[hname])
                cl.sync_file_to_host(hname, lt, rp)
                lt.unlink(missing_ok=True)
                proc = cl.run_on_host(
                    hname,
                    ["/usr/bin/python3", str(cl.SCRIPTS_DIR / "baseline_host_batch.py"), "c3-batch", "--tasks", rp],
                    timeout=10800.0,
                )
                cl.run_on_host(hname, ["rm", "-f", rp], check=False, log_cmd=False)
                return json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])["results"]

            with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
                per_host = list(pool.map(_dispatch_host, hosts))
            readers = [r for sub in per_host for r in sub]
            read_wall_s = time.monotonic() - t_r0
            after = cl.collector_mark_all(job["id"], "read-end")
        finally:
            stops = cl.batch_stop_mounts(mounted, f"{run_tag}-mx", clear_collector=True, remove_cache=True)

        save_json(out_dir / "read-start.json", before)
        save_json(out_dir / "read-end.json", after)
        save_json(out_dir / "readers.json", readers)
        return {
            "schema": "ax-baseline-packed-read-result-v1",
            "scale": mode,
            "system": system,
            "backend": backend_tag,
            "readers_count": count,
            "epochs": epochs,
            "files": files,
            "seconds": round(read_wall_s, 6),
            "readers": readers,
            "stops": stops,
            "backend_delta": cl.summarize_phase_delta(before, after),
            "volume": vol,
        }


# ==============================================================================
# Group 4: 04-agent-replay (1, 8, 16, 64 multi-agent full-fuse-replay)
# ==============================================================================

def build_think_plan(
    boundaries_doc: dict,
    agents: int,
    task_id: str,
    seed: int,
    *,
    min_ms: int = 1000,
    max_ms: int = 7000,
    mean_ms: int = 4000,
) -> dict:
    if boundaries_doc.get("reviewed") is not True:
        raise ValueError("Tool boundaries have not been reviewed")
    if min_ms <= 0 or max_ms > 15000 or not (min_ms <= mean_ms <= max_ms):
        raise ValueError(f"Invalid think interval bounds: min={min_ms}, mean={mean_ms}, max={max_ms}")
    rng = random.Random(seed)
    n_bounds = len(boundaries_doc["boundaries"])
    remaining = n_bounds * mean_ms
    waits: list[int] = []
    for idx in range(n_bounds):
        left = n_bounds - idx - 1
        delay = rng.randint(max(min_ms, remaining - left * max_ms), min(max_ms, remaining - left * min_ms))
        waits.append(delay)
        remaining -= delay
    rng.shuffle(waits)
    rows = [dict(row, delay_ms=delay) for row, delay in zip(boundaries_doc["boundaries"], waits)]
    return {
        "schema": "trace-think-plan-v2",
        "simulate_llm": True,
        "algorithm": "python-random-MT19937-feasible-allocation-shuffle-v2",
        "agent_count": agents,
        "agent_id": task_id,
        "seed": seed,
        "min_interval_ms": min_ms,
        "max_interval_ms": max_ms,
        "total_think_ms": sum(waits),
        "events_operations": boundaries_doc["events_operations"],
        "events_bytes": boundaries_doc["events_bytes"],
        "evidence": boundaries_doc["evidence"],
        "boundaries": rows,
    }


def _ensure_replay_bases(system: str, policy_tag: str, cases: tuple[str, ...], out_dir: Path) -> dict:
    reg_file = cl.RUN_ROOT / "prepared" / f"replay-bases-{system}-{policy_tag}.json"
    if reg_file.is_file():
        doc = json.loads(reg_file.read_text(encoding="utf-8"))
        if doc.get("completed") and all(c in doc.get("cases", {}) for c in cases):
            return doc

    cases_meta: dict[str, dict] = {}
    prep_root = out_dir / "base-prep"
    prep_root.mkdir(parents=True, exist_ok=True)

    for idx, case in enumerate(cases):
        t0 = time.monotonic()
        mid = f"c2-base-{system}-{case}"
        if system == "cephfs":
            base_sd = f"{cl.CEPHFS_ROOT_PREFIX}/replay-bases/{policy_tag}/{case}"
            cl.remove_cephfs_subdirs([base_sd], prep_root, tag=f"rm-{case}")
            cl.ensure_cephfs_subdirs([base_sd], prep_root, tag=f"mk-{case}")
            spec = {"subdir": base_sd}
            ws = base_sd
            bucket = None
        else:
            bucket = f"{cl.BUCKET_PREFIX}-c2-{policy_tag}-{case}".lower()[:62].rstrip("-")
            cl.delete_bucket_and_objects(bucket)
            cl.ensure_bucket(bucket)
            cl.ensure_s3fs_dir_marker(bucket, "base")
            spec = {"bucket": bucket, "prefix": "base", "s3_endpoint": cl.RUSTFS_ENDPOINTS[idx % 4]}
            ws = "base"

        mp = prep_root / f"{mid}-mounts.json"
        cl.generate_mounts_json([_make_mount_req(mid, "initial", system, "cluster-node-1", 1, ws, "none", f"c2-prep-{case}")], mp)
        mounted = cl.batch_start_mounts(mp, {mid: spec}, f"{mid}-s", register_collector=False)
        try:
            cl.run_on_host(
                "node-coord",
                ["cp", "-a", f"{cl.INPUT_DIR}/traces/{case}/initial/.", mounted[0]["mount_directory"].rstrip("/") + "/"],
                timeout=3600.0,
            )
            up = cl.wait_uploads(mounted, timeout=3600.0)[0]
        finally:
            cl.batch_stop_mounts(mounted, f"{mid}-x", clear_collector=False, remove_cache=True)
            mp.unlink(missing_ok=True)

        keep_keys = sorted(cl.list_all_keys(bucket, prefix="base/")) if bucket else []
        cases_meta[case] = {
            "case": case,
            "workspace": ws,
            "bucket": bucket,
            "keep_keys": keep_keys,
            "upload_drain_seconds": up["upload_drain_seconds"],
            "prepare_seconds": round(time.monotonic() - t0, 6),
        }

    doc = {"completed": True, "system": system, "policy": policy_tag, "cases": cases_meta}
    save_json(reg_file, doc)
    return doc


def _execute_replay_batch(
    job: dict,
    system: str,
    batch_tasks: list[dict],
    bases: dict,
    batch_dir: Path,
    batch_idx: int,
) -> dict:
    run_tag = f"{job['id']}-b{batch_idx:02d}".lower().replace("_", "-")
    if system == "cephfs":
        cl.remove_cephfs_subdirs([f"{cl.CEPHFS_ROOT_PREFIX}/replay-runs/{run_tag}"], batch_dir, tag=f"preclean-{run_tag}")
    else:
        for case in {t["case"] for t in batch_tasks}:
            bkt = bases["cases"][case]["bucket"]
            stale_keys = cl.list_all_keys(bkt, prefix=f"runs/{run_tag}/")
            if stale_keys:
                cl.delete_keys_parallel(bkt, stale_keys)

    snap_prep0 = cl.collector_mark_all(run_tag, "prepare_start")
    t_prep0 = time.monotonic()

    child_by_task: dict[str, dict] = {}
    copy_entries: list[dict] = []
    if system == "cephfs":
        for t in batch_tasks:
            base_sd = bases["cases"][t["case"]]["workspace"]
            dst_sd = f"{cl.CEPHFS_ROOT_PREFIX}/replay-runs/{run_tag}/{t['task_id']}"
            copy_entries.append({"src": base_sd, "dst": dst_sd})
            child_by_task[t["task_id"]] = {"workspace": dst_sd, "parent": base_sd, "bucket": None}
    else:
        for t in batch_tasks:
            binfo = bases["cases"][t["case"]]
            dst = f"runs/{run_tag}/{t['task_id']}"
            copy_entries.append({"bucket": binfo["bucket"], "src": "base", "dst": dst})
            child_by_task[t["task_id"]] = {"workspace": dst, "parent": "base", "bucket": binfo["bucket"]}

    fork_copy_cost = cl.fuse_copy_workspaces(
        system,
        copy_entries,
        batch_dir,
        tag=f"cp-{run_tag}",
        outer_workers=min(8, max(1, len(copy_entries))),
        inner_workers=8,
    )
    save_json(batch_dir / "fork-copy-cost.json", fork_copy_cost)

    mount_reqs: list[dict] = []
    m_specs: dict[str, dict] = {}
    v_specs: dict[str, dict] = {}
    v_target: dict[str, tuple[str, str, int]] = {}

    for t in batch_tasks:
        tid = t["task_id"]
        s_host, s_ssh, s_slot = t["host"], t["ssh"], int(t["slot"])
        v_host = VERIFY_HOST_MAP[s_host]
        v_ssh = cl.HOST_BY_NAME[v_host]["ssh"]
        v_slot = s_slot
        v_target[tid] = (v_host, v_ssh, v_slot)
        cm = child_by_task[tid]
        m_id = f"{run_tag}-{tid}"
        v_id = f"{run_tag}-{tid}-ver"

        mount_reqs.append(_make_mount_req(m_id, "measure", system, s_ssh, s_slot, cm["workspace"], cm["parent"], f"{run_tag}-m", windows=["measure"]))
        mount_reqs.append(
            _make_mount_req(v_id, "verify", system, v_ssh, v_slot, cm["workspace"], cm["parent"], f"{run_tag}-v", cache_state="cross-host-cold", windows=["verify"])
        )
        if system == "cephfs":
            m_specs[m_id] = {"subdir": cm["workspace"]}
            v_specs[v_id] = {"subdir": cm["workspace"]}
        else:
            m_specs[m_id] = {"bucket": cm["bucket"], "prefix": cm["workspace"], "s3_endpoint": cl.endpoint_for_slot(s_slot, HOST_IDX[s_host])}
            v_specs[v_id] = {"bucket": cm["bucket"], "prefix": cm["workspace"], "s3_endpoint": cl.endpoint_for_slot(v_slot, HOST_IDX[v_host])}

    mounts_cfg = batch_dir / "mounts.json"
    cl.generate_mounts_json(mount_reqs, mounts_cfg)
    started = cl.batch_start_mounts(mounts_cfg, m_specs, f"{run_tag}-ms", register_collector=True)
    workspace_prepare_seconds = round(time.monotonic() - t_prep0, 6)
    snap_prep1 = cl.collector_mark_all(run_tag, "prepare_done")

    m_by_id = {m["id"]: m for m in started}
    shim_so = (
        cl.ensure_hardlink_copy_shim(sorted({t["host"] for t in batch_tasks}))
        if system == "s3fs"
        else ""
    )
    cohort_epoch_ns = time.time_ns() + 3_000_000_000
    replay_by_host: dict[str, list[dict]] = defaultdict(list)
    for t in batch_tasks:
        tid = t["task_id"]
        case = t["case"]
        hname = t["host"]
        m = m_by_id[f"{run_tag}-{tid}"]
        replay_by_host[hname].append(
            {
                "task_id": tid,
                "case": case,
                "host": hname,
                "slot": int(t["slot"]),
                "engine": system,
                "cgroup": m["cgroup"],
                "mount_directory": m["mount_directory"],
                "admin_socket": m.get("admin_socket") or "",
                "cache_root": m.get("cache_root") or "",
                "events_path": f"{cl.INPUT_DIR}/traces/{case}/capture/events.jsonl",
                "blobs_dir": f"{cl.INPUT_DIR}/traces/{case}/capture/blobs",
                "pacing_path": t["pacing_path"],
                "hardlink_copy_shim_so": shim_so,
                "start_delay_ms": int(t["start_delay_ms"]),
                "start_utc_ns": cohort_epoch_ns + int(t["start_delay_ms"]) * 1_000_000,
                "out_path": f"{cl.RUN_ROOT}/hosts/{hname}/c2-tasks/{run_tag}/{tid}-replay.json",
            }
        )

    try:
        snap_m0 = cl.collector_mark_all(run_tag, "measure_start")
        hosts = sorted(replay_by_host.keys())

        def _run_host(hname: str) -> list[dict]:
            rp = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-replay.json"
            lt = batch_dir / f"{hname}-replay.json"
            save_json(lt, replay_by_host[hname])
            cl.sync_file_to_host(hname, lt, rp)
            lt.unlink(missing_ok=True)
            proc = cl.run_on_host(
                hname,
                ["/usr/bin/python3", str(cl.SCRIPTS_DIR / "baseline_host_batch.py"), "c2-replay-batch", "--tasks", rp],
                timeout=10800.0,
            )
            cl.run_on_host(hname, ["rm", "-f", rp], check=False, log_cmd=False)
            return json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])["tasks"]

        with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
            per_host = list(pool.map(_run_host, hosts))
        task_results = [r for sub in per_host for r in sub]
        snap_m1 = cl.collector_mark_all(run_tag, "upload_done")
    finally:
        stop_records = cl.batch_stop_mounts(started, f"{run_tag}-mx", clear_collector=True, remove_cache=True)

    # Cross-host 100% direct byte verification
    t_ver0 = time.monotonic()
    ver_mounts = cl.batch_start_mounts(mounts_cfg, v_specs, f"{run_tag}-vs", register_collector=False)
    v_by_id = {m["id"]: m for m in ver_mounts}
    try:
        vtasks_by_host: dict[str, list[dict]] = defaultdict(list)
        for t in batch_tasks:
            tid = t["task_id"]
            case = t["case"]
            v_host, _, _ = v_target[tid]
            vm = v_by_id[f"{run_tag}-{tid}-ver"]
            vtasks_by_host[v_host].append(
                {
                    "task_id": tid,
                    "case": case,
                    "source_host": t["host"],
                    "verify_host": v_host,
                    "mount_directory": vm["mount_directory"],
                    "final_tree_path": f"{cl.INPUT_DIR}/traces/{case}/final-tree.json",
                    "replayed_dir": f"{cl.INPUT_DIR}/traces/{case}/replayed",
                    "out_path": f"{cl.RUN_ROOT}/hosts/{v_host}/c2-tasks/{run_tag}/{tid}-verify.json",
                }
            )
        vhosts = sorted(vtasks_by_host.keys())

        def _ver_host(hname: str) -> list[dict]:
            rp = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-verify.json"
            lt = batch_dir / f"{hname}-verify.json"
            save_json(lt, vtasks_by_host[hname])
            cl.sync_file_to_host(hname, lt, rp)
            lt.unlink(missing_ok=True)
            proc = cl.run_on_host(
                hname,
                ["/usr/bin/python3", str(cl.SCRIPTS_DIR / "baseline_host_batch.py"), "c2-verify-batch", "--tasks", rp],
                timeout=3600.0,
            )
            cl.run_on_host(hname, ["rm", "-f", rp], check=False, log_cmd=False)
            return json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])["verified"]

        with ThreadPoolExecutor(max_workers=max(1, len(vhosts))) as pool:
            per_vhost = list(pool.map(_ver_host, vhosts))
        verified = [v for sub in per_vhost for v in sub]
    finally:
        cl.batch_stop_mounts(ver_mounts, f"{run_tag}-vx", clear_collector=False, remove_cache=True)
    verify_wall_s = round(time.monotonic() - t_ver0, 6)

    # Cleanup batch workspaces
    if system == "cephfs":
        cl.remove_cephfs_subdirs([f"{cl.CEPHFS_ROOT_PREFIX}/replay-runs/{run_tag}"], batch_dir, tag=f"rm-{run_tag}")
    else:
        for case in {t["case"] for t in batch_tasks}:
            bkt = bases["cases"][case]["bucket"]
            extra = cl.list_all_keys(bkt, prefix=f"runs/{run_tag}/")
            cl.delete_keys_parallel(bkt, extra)

    task_results.sort(key=lambda x: x["task_id"])
    ver_by_id = {v["task_id"]: v for v in verified}
    for tr in task_results:
        v = ver_by_id[tr["task_id"]]
        if not v["verify"].get("passed", False):
            raise RuntimeError(f"Cross-host verify failed for {tr['task_id']}: {v}")
        tr["cross_host_verify"] = {
            "source_host": v["source_host"],
            "verify_host": v["verify_host"],
            "verify_seconds": round(v["verify_seconds"], 6),
            "passed": True,
        }
    save_json(batch_dir / "tasks.json", task_results)

    max_app_ns = max(int(t["app_done_utc_ns"]) for t in task_results)
    max_up_ns = max(int(t["upload_done_utc_ns"]) for t in task_results)
    cohort_app_s = max(0.001, (max_app_ns - cohort_epoch_ns) / 1e9)
    cohort_up_s = max(cohort_app_s, (max_up_ns - cohort_epoch_ns) / 1e9)

    wall_vals = [float(t["wall_app_seconds"]) for t in task_results]
    exec_vals = [float(t["replay"].get("execution_seconds", t["wall_app_seconds"])) for t in task_results]
    active_vals = [float(t["replay"].get("active_replay_seconds", 0.0)) for t in task_results]
    fs_vals = [float(t["replay"].get("fs_operation_seconds", 0.0)) for t in task_results]
    drain_vals = [float(t.get("upload_drain_seconds", 0.0)) for t in task_results]
    act_read = sum(
        int(t["replay"].get("actual_read_bytes", (t["replay"].get("bytes") or {}).get("actual_read", 0)))
        for t in task_results
    )
    act_write = sum(
        int(t["replay"].get("actual_write_bytes", (t["replay"].get("bytes") or {}).get("actual_write", 0)))
        for t in task_results
    )
    uniq_read = sum(int(t["replay"].get("unique_read_bytes", 0)) for t in task_results)
    rep_read = sum(int(t["replay"].get("repeated_read_bytes", 0)) for t in task_results)
    repeated_ratio = round(1.0 - (uniq_read / act_read), 6) if act_read > 0 else None
    trace_ops = sum(int(t["replay"].get("operations", t["replay"].get("total_events", 0))) for t in task_results)
    wait_s_sum = sum(
        float(
            (t["replay"].get("think_time") or {}).get(
                "actual_sleep_seconds",
                (t["replay"].get("pacing") or {}).get("simulated_think_seconds", 0.0),
            )
        )
        for t in task_results
    )

    merged_all = cl.merge_log2_histograms([(t["replay"].get("latency_us") or {}).get("all") or {} for t in task_results])
    merged_read = cl.merge_log2_histograms([(t["replay"].get("latency_us") or {}).get("read") or {} for t in task_results])
    merged_write = cl.merge_log2_histograms([(t["replay"].get("latency_us") or {}).get("write") or {} for t in task_results])
    merged_meta = cl.merge_log2_histograms([(t["replay"].get("latency_us") or {}).get("meta") or {} for t in task_results])

    per_case_summary: dict[str, dict] = {}
    for case in CASES:
        ct = [t for t in task_results if t["case"] == case]
        if not ct:
            continue
        c_exec = [float(t["replay"].get("execution_seconds", t["wall_app_seconds"])) for t in ct]
        c_act = [float(t["replay"].get("active_replay_seconds", 0.0)) for t in ct]
        c_fs = [float(t["replay"].get("fs_operation_seconds", 0.0)) for t in ct]
        c_rr = [
            float(t["replay"].get("execution_seconds", t["wall_app_seconds"])) + float(t.get("upload_drain_seconds", 0.0))
            for t in ct
        ]
        c_hist = cl.merge_log2_histograms([(t["replay"].get("latency_us") or {}).get("all") or {} for t in ct])
        per_case_summary[case] = {
            "tasks": len(ct),
            "execution_p50_seconds": _percentile_nearest_rank(c_exec, 0.50),
            "execution_p95_seconds": _percentile_nearest_rank(c_exec, 0.95),
            "active_replay_p50_seconds": _percentile_nearest_rank(c_act, 0.50),
            "active_replay_p95_seconds": _percentile_nearest_rank(c_act, 0.95),
            "fs_operation_p50_seconds": _percentile_nearest_rank(c_fs, 0.50),
            "fs_operation_p95_seconds": _percentile_nearest_rank(c_fs, 0.95),
            "remote_ready_p50_seconds": _percentile_nearest_rank(c_rr, 0.50),
            "remote_ready_p95_seconds": _percentile_nearest_rank(c_rr, 0.95),
            "op_latency_p50_us": c_hist["p50_us"],
            "op_latency_p95_us": c_hist["p95_us"],
            "op_latency_p99_us": c_hist["p99_us"],
        }

    cgroup_mem_peak_sum = sum(int((sr.get("final_cgroup") or {}).get("memory_peak", 0)) for sr in stop_records)
    cgroup_cpu_usec_sum = sum(int((sr.get("final_cgroup") or {}).get("cpu_usage_usec", 0)) for sr in stop_records)
    total_with_prep_s = workspace_prepare_seconds + cohort_up_s

    hardlink_copy_cost = {
        "enabled": bool(shim_so),
        "native_hardlink_supported": system == "cephfs",
        "fallback_count": sum(int((t.get("hardlink_copy_cost") or {}).get("fallback_count", 0)) for t in task_results),
        "copied_bytes": sum(int((t.get("hardlink_copy_cost") or {}).get("copied_bytes", 0)) for t in task_results),
        "copy_seconds": round(
            sum(float((t.get("hardlink_copy_cost") or {}).get("copy_seconds", 0.0)) for t in task_results), 6
        ),
    }

    return {
        "batch_index": batch_idx,
        "tasks_count": len(task_results),
        "fork_copy_cost": fork_copy_cost,
        "hardlink_copy_cost": hardlink_copy_cost,
        "timing": {
            "fork_copy_seconds": fork_copy_cost["timing"]["total_copy_seconds"],
            "hardlink_copy_seconds": hardlink_copy_cost["copy_seconds"],
            "workspace_prepare_seconds": workspace_prepare_seconds,
            "app_done_seconds": round(cohort_app_s, 6),
            "upload_drain_max_seconds": round(max(drain_vals, default=0.0), 6),
            "remote_ready_no_verify_seconds": round(cohort_up_s, 6),
            "total_with_prepare_seconds": round(total_with_prep_s, 6),
            "verify_wall_seconds": verify_wall_s,
            "remote_ready_with_verify_seconds": round(cohort_up_s + verify_wall_s, 6),
            "task_wall_p50_seconds": _percentile_nearest_rank(wall_vals, 0.50),
            "task_wall_p95_seconds": _percentile_nearest_rank(wall_vals, 0.95),
            "total_simulated_wait_seconds": round(wait_s_sum, 6),
        },
        "metrics": {
            "tasks_per_hour": round((len(task_results) * 3600.0) / cohort_up_s, 3),
            "tasks_per_hour_app_done": round((len(task_results) * 3600.0) / cohort_app_s, 4),
            "tasks_per_hour_remote_ready": round((len(task_results) * 3600.0) / max(1e-6, total_with_prep_s), 4),
            "task_p50_seconds": _percentile_nearest_rank(exec_vals, 0.50),
            "task_p95_seconds": _percentile_nearest_rank(exec_vals, 0.95),
            "active_replay_mean_seconds": round(sum(active_vals) / max(1, len(active_vals)), 6),
            "fs_operation_mean_seconds": round(sum(fs_vals) / max(1, len(fs_vals)), 6),
            "trace_operations": trace_ops,
            "actual_read_bytes": act_read,
            "actual_write_bytes": act_write,
            "unique_read_bytes": uniq_read,
            "repeated_read_bytes": rep_read,
            "repeated_read_ratio": repeated_ratio,
            "client_cpu_seconds": round(cgroup_cpu_usec_sum / 1e6, 6),
            "client_peak_memory_bytes_sum": cgroup_mem_peak_sum,
            "latency_us": merged_all,
            "read_latency_us": merged_read,
            "write_latency_us": merged_write,
            "meta_latency_us": merged_meta,
        },
        "per_case": per_case_summary,
        "prepare_backend_delta": cl.summarize_phase_delta(snap_prep0, snap_prep1),
        "replay_backend_delta": cl.summarize_phase_delta(snap_m0, snap_m1),
        "stops": stop_records,
    }


def run_replay(job: dict, out_dir: Path, small: bool = False, scale: str = "formal") -> dict:
    mode = _resolve_scale(scale, small)
    p = job["parameters"]
    system = p["system"]
    backend_tag = select_backend_for_job(p, out_dir)

    if mode == "tiny":
        cases_tuple = ("office-excel-01",)
        policy_tag = backend_tag + "-tiny"
        agents = 1 if int(p["agents"]) == 1 else min(2, int(p["agents"]))
        min_ms, max_ms, mean_ms = 50, 150, 100
        max_start_delay_ms = 200
    elif mode == "check":
        cases_tuple = ("office-excel-01",)
        policy_tag = backend_tag + "-chk"
        agents = 1
        min_ms = int(p.get("wait_min_ms", 1000))
        max_ms = int(p.get("wait_max_ms", 7000))
        mean_ms = int(p.get("wait_mean_ms", 4000))
        max_start_delay_ms = 30000
    else:
        cases_tuple = CASES
        policy_tag = backend_tag
        agents = int(p["agents"])
        min_ms = int(p.get("wait_min_ms", 1000))
        max_ms = int(p.get("wait_max_ms", 7000))
        mean_ms = int(p.get("wait_mean_ms", 4000))
        max_start_delay_ms = 30000

    with cl.MeasurementLock():
        bases = _ensure_replay_bases(system, policy_tag, cases_tuple, out_dir)
        cases = list(cases_tuple)
        rng = random.Random(int(job["seed"]))
        rng.shuffle(cases)
        total = agents
        chosen_placements = [("node-coord", 1)] if agents == 1 else cl.placements(agents)

        tasks: list[dict] = []
        for idx, (host, slot) in enumerate(chosen_placements):
            tid = f"a{idx + 1:03d}"
            case = cases[idx % len(cases)]
            bounds_doc = json.loads((cl.INPUT_DIR / "traces" / case / "boundaries.json").read_text(encoding="utf-8"))
            plan_doc = build_think_plan(
                bounds_doc,
                agents,
                tid,
                int(job["seed"]) + 100003 * (idx + 1),
                min_ms=min_ms,
                max_ms=max_ms,
                mean_ms=mean_ms,
            )
            pacing_file = out_dir / "pacing" / f"{tid}.json"
            save_json(pacing_file, plan_doc)
            cl.sync_file_to_host(host, pacing_file, str(pacing_file))
            tasks.append(
                {
                    "task_id": tid,
                    "case": case,
                    "host": host,
                    "ssh": cl.HOST_BY_NAME[host]["ssh"],
                    "slot": slot,
                    "start_delay_ms": 0 if agents == 1 else rng.randrange(max_start_delay_ms + 1),
                    "pacing_path": str(pacing_file),
                }
            )
        save_json(out_dir / "tasks-plan.json", tasks)

        b_dir = out_dir / "batch-01"
        b_dir.mkdir(parents=True, exist_ok=True)
        result = _execute_replay_batch(job, system, tasks, bases, b_dir, 1)
        save_json(b_dir / "result.json", result)
        batch_results = [result]

        tot_app_s = sum(r["timing"]["app_done_seconds"] for r in batch_results)
        tot_up_s = sum(r["timing"]["remote_ready_no_verify_seconds"] for r in batch_results)
        tot_with_prep_s = sum(r["timing"]["total_with_prepare_seconds"] for r in batch_results)
        tot_read = sum(r["metrics"]["actual_read_bytes"] for r in batch_results)
        tot_write = sum(r["metrics"]["actual_write_bytes"] for r in batch_results)
        tot_uniq_read = sum(r["metrics"]["unique_read_bytes"] for r in batch_results)
        tot_rep_read = sum(r["metrics"]["repeated_read_bytes"] for r in batch_results)
        fork_copy_summary = {
            "mechanism": "full-fuse-data-copy",
            "native_fork_supported": False,
            "workspaces_copied": sum(r["fork_copy_cost"]["children_count"] for r in batch_results),
            "copied_files": sum(r["fork_copy_cost"]["copied_files"] for r in batch_results),
            "copied_symlinks": sum(r["fork_copy_cost"]["copied_symlinks"] for r in batch_results),
            "copied_logical_bytes": sum(r["fork_copy_cost"]["copied_logical_bytes"] for r in batch_results),
            "total_copy_seconds": round(sum(r["fork_copy_cost"]["timing"]["total_copy_seconds"] for r in batch_results), 6),
            "added_storage_bytes": sum(r["fork_copy_cost"]["storage_cost"]["added_storage_bytes"] for r in batch_results),
            "added_objects": sum(r["fork_copy_cost"]["storage_cost"]["added_objects"] for r in batch_results),
            "client_cpu_seconds": round(sum(r["fork_copy_cost"]["client_resources"]["cpu_seconds"] for r in batch_results), 6),
            "peak_memory_bytes": max(
                (r["fork_copy_cost"]["client_resources"]["peak_memory_bytes"] for r in batch_results),
                default=0,
            ),
        }
        hardlink_copy_summary = {
            "native_hardlink_supported": system == "cephfs",
            "fallback_mechanism": "none" if system == "cephfs" else "full-fuse-file-copy",
            "fallback_count": sum(r["hardlink_copy_cost"]["fallback_count"] for r in batch_results),
            "copied_bytes": sum(r["hardlink_copy_cost"]["copied_bytes"] for r in batch_results),
            "copy_seconds": round(sum(r["hardlink_copy_cost"]["copy_seconds"] for r in batch_results), 6),
        }
        return {
            "schema": "ax-baseline-replay-result-v1",
            "scale": mode,
            "system": system,
            "backend": backend_tag,
            "agents": agents,
            "completed_tasks": total,
            "fork_copy_summary": fork_copy_summary,
            "hardlink_copy_summary": hardlink_copy_summary,
            "replay_seconds": round(tot_app_s, 6),
            "remote_ready_seconds": round(tot_up_s, 6),
            "total_with_prepare_seconds": round(tot_with_prep_s, 6),
            "tasks_per_hour": round((total * 3600.0) / max(1e-6, tot_up_s), 3),
            "tasks_per_hour_app_done": round((total * 3600.0) / max(1e-6, tot_app_s), 4),
            "tasks_per_hour_remote_ready": round((total * 3600.0) / max(1e-6, tot_with_prep_s), 4),
            "actual_read_bytes": tot_read,
            "actual_write_bytes": tot_write,
            "unique_read_bytes": tot_uniq_read,
            "repeated_read_bytes": tot_rep_read,
            "repeated_read_ratio": round(1.0 - (tot_uniq_read / tot_read), 6) if tot_read > 0 else None,
            "operations": sum(r["metrics"]["trace_operations"] for r in batch_results),
            "batches": batch_results,
        }
