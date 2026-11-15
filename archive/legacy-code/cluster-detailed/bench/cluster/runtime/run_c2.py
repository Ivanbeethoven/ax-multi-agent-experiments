#!/usr/bin/env python3
"""Complete hash-free C2 runner for run-001 (11 conditions / 81 system runs across 6 hosts).

Executes:
  - 10 live conditions (72 runs):
      * Main scale: C2-scale-01..04 (8, 32, 64, 128 agents; short pacing; remote-ready)
      * Pacing sensitivity: C2-pacing-01..02 (64 agents; none / long pacing)
      * Writeback sensitivity: C2-writeback-01 (64 agents; local-staged writes)
      * Ablation: C2-row-cache-01 (brewfs-native-overlay, row_cache_mib=0)
      * Ablation: C2-fork-cache-01 (juicefs-overlay, fork_cache_mib=0)
      * Ablation: C2-namespace-materialized-01 (brewfs-native-overlay, namespace_mode=materialized)
  - 1 pending-feature condition (9 runs):
      * C2-shared-host-cache-01 (requires host-shared-read-cache)
  - 1-pair 128-mount collector ON/OFF overhead check saved to hosts/collector-overhead-128mounts.json.

Strictly zero hashes/checksums. Uses host_batch_runner.py on all 6 hosts for parallel mount/replay/verify.
"""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import shutil
import statistics
import sys
import time

sys.path.insert(0, "/srv/ax-cluster/direct/scripts")
import cluster_lib as cl

REDIS_URL = f"redis://:{cl.SECRETS['redis_password']}@{cl.REDIS_HOST}:{cl.REDIS_PORT}/0"
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

# Exact bijection across the 6 hosts where every target host's active slot limit >= source host's max 128-agent load:
#   A366 (max 32) -> A601 (active 40)
#   A601 (max 27) -> A635 (active 40)
#   A635 (max 26) -> A355 (active 32)
#   A355 (max 21) -> A366 (active 48)
#   A672 (max 11) -> A368 (active 16)
#   A368 (max 11) -> A672 (active 16)
VERIFY_HOST_MAP = {
    "A366": "A601",
    "A601": "A635",
    "A635": "A355",
    "A355": "A366",
    "A672": "A368",
    "A368": "A672",
}
HOST_IDX = {h["name"]: idx for idx, h in enumerate(cl.HOSTS)}


def list_all_keys(bucket: str) -> set[str]:
    s3 = cl.make_s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    keys: set[str] = set()
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def delete_extra_keys(bucket: str, keep_keys: set[str]) -> int:
    cur = list_all_keys(bucket)
    extra = sorted(cur - keep_keys)
    if not extra:
        return 0
    cl.delete_keys_parallel(bucket, extra)
    return len(extra)


def make_req(
    mid: str,
    phase: str,
    engine: str,
    host_ssh: str,
    slot: int,
    ws: str,
    parent_ws: str,
    purpose: str,
    binary: str,
    cache_state: str = "cold",
    windows: list[str] | None = None,
    overrides: dict | None = None,
) -> dict:
    r = {
        "id": mid,
        "phase": phase,
        "engine": engine,
        "host": host_ssh,
        "slot": slot,
        "workspace": ws,
        "parent_workspace": parent_ws,
        "purpose": purpose,
        "binary": binary,
        "cache_state": cache_state,
        "windows": windows or [phase],
        "root": str(cl.RUN_ROOT),
    }
    if overrides:
        r["overrides"] = overrides
    return r


def prepare_run_dir(cond_id: str, system: str, rep: int) -> tuple[Path, int, bool]:
    status_doc = json.loads((cl.RUN_ROOT / "status.json").read_text(encoding="utf-8"))
    prev_state = (
        status_doc["groups"]["C2"]["conditions"]
        .get(cond_id, {})
        .get("runs", {})
        .get(f"{system}/r{rep}", {})
        .get("state", "planned")
    )
    base_dir = cl.RUN_ROOT / "C2" / cond_id / system / f"r{rep}"
    if prev_state in {"passed", "pending-feature"} and (base_dir / "result.json").is_file():
        return base_dir, 1, True

    if base_dir.exists() and any(base_dir.iterdir()):
        existing_attempts = sorted(p for p in base_dir.iterdir() if p.is_dir() and p.name.startswith("attempt-"))
        if not existing_attempts:
            att1 = base_dir / "attempt-01"
            att1.mkdir(parents=True, exist_ok=True)
            for item in list(base_dir.iterdir()):
                if item.name != "attempt-01":
                    shutil.move(str(item), str(att1 / item.name))
            attempt = 2
        else:
            attempt = len(existing_attempts) + 1
        att_dir = base_dir / f"attempt-{attempt:02d}"
        att_dir.mkdir(parents=True, exist_ok=True)
        return att_dir, attempt, False

    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir, 1, False


def finalize_run_result(cond_id: str, system: str, rep: int, work_dir: Path, result_obj: dict, state: str = "passed"):
    out_file = work_dir / "result.json"
    out_file.write_text(json.dumps(result_obj, indent=2) + "\n", encoding="utf-8")
    base_dir = cl.RUN_ROOT / "C2" / cond_id / system / f"r{rep}"
    if work_dir != base_dir:
        (base_dir / "result.json").write_text(json.dumps(result_obj, indent=2) + "\n", encoding="utf-8")
    details = {"result_path": str((base_dir / "result.json").relative_to(cl.RUN_ROOT))}
    if "timing" in result_obj:
        details["app_done_seconds"] = result_obj["timing"].get("app_done_seconds")
        details["remote_ready_seconds"] = result_obj["timing"].get("remote_ready_no_verify_seconds")
    cl.update_status("C2", cond_id, system, rep, state, details)


def percentile_nearest_rank(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = max(0, min(len(s) - 1, math.ceil(len(s) * q) - 1))
    return round(float(s[idx]), 6)


def sum_mount_metrics(stop_records: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for rec in stop_records:
        for k, v in (rec.get("final_metrics_summary") or {}).items():
            totals[k] = totals.get(k, 0.0) + float(v)
    return totals


def extract_s3_and_amp(phase_delta: dict, read_bytes: int, write_bytes: int, mount_totals: dict[str, float]) -> dict:
    d = dict(phase_delta)
    brew_get = mount_totals.get("brewfs_s3_get_bytes_total", 0.0) + mount_totals.get("brewfs_packed_runtime_backend_received_bytes_total", 0.0)
    s3_get = int(
        brew_get
        or mount_totals.get("juicefs_object_request_data_bytes_GET", 0.0)
        or d.get("rustfs_s3_get_bytes", 0)
    )
    s3_put = int(
        mount_totals.get("brewfs_s3_put_bytes_total", 0.0)
        or mount_totals.get("juicefs_object_request_data_bytes_PUT", 0.0)
        or d.get("rustfs_s3_put_bytes", 0)
    )
    d["s3_get_bytes"] = s3_get
    d["s3_put_bytes"] = s3_put
    d["read_amplification_phys"] = round(d["backend_physical_read_bytes"] / read_bytes, 6) if read_bytes > 0 else None
    d["read_amplification_s3"] = round(s3_get / read_bytes, 6) if read_bytes > 0 else None
    d["upload_amplification_s3"] = round(s3_put / write_bytes, 6) if write_bytes > 0 else None
    d["upload_amplification_phys"] = round(d["backend_physical_write_bytes"] / write_bytes, 6) if write_bytes > 0 else None
    return d


# ==============================================================================
# Distributed Host Batch Dispatch Helpers (<= 6 SSH sessions simultaneously)
# ==============================================================================
def batch_start_mounts(mounts_path: Path, specs_by_id: dict[str, dict], run_tag: str, register_collector: bool = True) -> list[dict]:
    mounts_doc = json.loads(mounts_path.read_text(encoding="utf-8"))
    rec_by_id = {r["id"]: r for r in mounts_doc["mounts"] if r["id"] in specs_by_id}
    by_host: dict[str, dict[str, dict]] = defaultdict(dict)
    for mid, rec in rec_by_id.items():
        hname = cl.HOST_BY_SSH[rec["host"]]["name"]
        sp = dict(specs_by_id[mid])
        sp["host_name"] = hname
        sp["host_ssh"] = rec["host"]
        sp["slot"] = rec["slot"]
        by_host[hname][mid] = sp

    hosts = sorted(by_host.keys())

    def _start_host(hname: str) -> list[dict]:
        remote_mounts = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-mounts.json"
        remote_specs = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-specs.json"
        remote_audits = f"{cl.RUN_ROOT}/hosts/{hname}/audits/{run_tag}"
        local_specs_tmp = cl.RUN_ROOT / "mounts" / "tmp" / f"{run_tag}-{hname}-specs.json"
        local_specs_tmp.parent.mkdir(parents=True, exist_ok=True)
        local_specs_tmp.write_text(json.dumps(by_host[hname], indent=2) + "\n", encoding="utf-8")

        cl.sync_file_to_host(hname, mounts_path, remote_mounts)
        cl.sync_file_to_host(hname, local_specs_tmp, remote_specs)
        local_specs_tmp.unlink(missing_ok=True)

        p = cl.run_on_host(
            hname,
            [
                "/usr/bin/python3",
                "/srv/ax-cluster/direct/scripts/host_batch_runner.py",
                "start-batch",
                "--mounts",
                remote_mounts,
                "--specs",
                remote_specs,
                "--audit-dir",
                remote_audits,
            ],
            timeout=600.0,
        )
        cl.run_on_host(hname, ["rm", "-f", remote_mounts, remote_specs], check=False, log_cmd=False)
        out = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])
        return out["mounts"]

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        per_host = list(pool.map(_start_host, hosts))
    started = [m for sub in per_host for m in sub]

    if register_collector:
        col_map: dict[str, list[dict]] = {}
        for m in started:
            col_map.setdefault(m["host_name"], []).append(
                {
                    "id": m["id"],
                    "slot": m["slot"],
                    "engine": m["engine"],
                    "pid": m["pid"],
                    "cgroup": m["cgroup"],
                    "metrics_port": m["metrics_port"],
                    "mount_directory": m["mount_directory"],
                }
            )
        cl.collector_set_mounts(col_map)
    return started


def batch_stop_mounts(started_mounts: list[dict], run_tag: str, clear_collector: bool = True, remove_cache: bool = True) -> list[dict]:
    if clear_collector:
        cl.collector_set_mounts({})
    by_host: dict[str, list[dict]] = defaultdict(list)
    for m in started_mounts:
        by_host[m["host_name"]].append(m)
    hosts = sorted(by_host.keys())

    def _stop_host(hname: str) -> list[dict]:
        remote_info = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-stop-in.json"
        remote_stops = f"{cl.RUN_ROOT}/hosts/{hname}/audits/{run_tag}"
        local_tmp = cl.RUN_ROOT / "mounts" / "tmp" / f"{run_tag}-{hname}-stop.json"
        local_tmp.parent.mkdir(parents=True, exist_ok=True)
        local_tmp.write_text(json.dumps(by_host[hname], indent=2) + "\n", encoding="utf-8")
        cl.sync_file_to_host(hname, local_tmp, remote_info)
        local_tmp.unlink(missing_ok=True)

        cmd = [
            "/usr/bin/python3",
            "/srv/ax-cluster/direct/scripts/host_batch_runner.py",
            "stop-batch",
            "--mounts-info",
            remote_info,
            "--stop-dir",
            remote_stops,
        ]
        if remove_cache:
            cmd.append("--remove-cache")
        p = cl.run_on_host(hname, cmd, timeout=600.0, check=False)
        cl.run_on_host(hname, ["rm", "-f", remote_info], check=False, log_cmd=False)
        if p.returncode == 0 and p.stdout:
            return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["stopped"]
        return []

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        per_host = list(pool.map(_stop_host, hosts))
    return [r for sub in per_host for r in sub]


def batch_replay_cohort(tasks_by_host: dict[str, list[dict]], run_tag: str) -> list[dict]:
    hosts = sorted(tasks_by_host.keys())
    remote_paths: dict[str, str] = {}
    for hname in hosts:
        rp = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-replay-tasks.json"
        lt = cl.RUN_ROOT / "mounts" / "tmp" / f"{run_tag}-{hname}-replay-tasks.json"
        lt.parent.mkdir(parents=True, exist_ok=True)
        lt.write_text(json.dumps(tasks_by_host[hname], indent=2) + "\n", encoding="utf-8")
        cl.sync_file_to_host(hname, lt, rp)
        lt.unlink(missing_ok=True)
        remote_paths[hname] = rp

    def _run_host(hname: str) -> list[dict]:
        rp = remote_paths[hname]
        p = cl.run_on_host(
            hname,
            ["/usr/bin/python3", "/srv/ax-cluster/direct/scripts/host_batch_runner.py", "c2-replay-batch", "--tasks", rp],
            timeout=7200.0,
        )
        cl.run_on_host(hname, ["rm", "-f", rp], check=False, log_cmd=False)
        return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["tasks"]

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        per_host = list(pool.map(_run_host, hosts))
    return [t for sub in per_host for t in sub]


def batch_verify_cohort(vtasks_by_host: dict[str, list[dict]], run_tag: str) -> list[dict]:
    hosts = sorted(vtasks_by_host.keys())
    remote_paths: dict[str, str] = {}
    for hname in hosts:
        rp = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-verify-tasks.json"
        lt = cl.RUN_ROOT / "mounts" / "tmp" / f"{run_tag}-{hname}-verify-tasks.json"
        lt.parent.mkdir(parents=True, exist_ok=True)
        lt.write_text(json.dumps(vtasks_by_host[hname], indent=2) + "\n", encoding="utf-8")
        cl.sync_file_to_host(hname, lt, rp)
        lt.unlink(missing_ok=True)
        remote_paths[hname] = rp

    def _ver_host(hname: str) -> list[dict]:
        rp = remote_paths[hname]
        p = cl.run_on_host(
            hname,
            ["/usr/bin/python3", "/srv/ax-cluster/direct/scripts/host_batch_runner.py", "c2-verify-batch", "--tasks", rp],
            timeout=3600.0,
        )
        cl.run_on_host(hname, ["rm", "-f", rp], check=False, log_cmd=False)
        return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["verified"]

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        per_host = list(pool.map(_ver_host, hosts))
    return [v for sub in per_host for v in sub]


# ==============================================================================
# Base Trace Workspace Preparation (8 cases x 3 engines, sealed once & cached)
# ==============================================================================
def ensure_c2_bases(policy="on") -> dict:
    meta_file = cl.RUN_ROOT / "prepared" / f"trace-bases-{policy}.json"
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    if meta_file.is_file():
        doc = json.loads(meta_file.read_text(encoding="utf-8"))
        if doc.get("completed"):
            return doc

    print("=== Preparing & sealing 8 immutable C2 base workspaces across all 3 engines ===", flush=True)
    prep_dir = cl.RUN_ROOT / "mounts" / ("trace-base-prep-" + policy)
    prep_dir.mkdir(parents=True, exist_ok=True)

    brew_bases: dict[str, dict] = {}
    jov_bases: dict[str, dict] = {}

    # 1. BrewFS Native Overlay bases (8 cases)
    for idx, case in enumerate(CASES):
        bkt = f"{cl.RUN_PREFIX}-c2-{policy}-brew-{case}"
        ns = f"{cl.RUN_NAMESPACE}_c2_{policy}_brew_{case.replace('-', '_')}"
        cl.delete_bucket_and_objects(bkt)
        cl.ensure_bucket(bkt)
        cl.redis_delete_pattern_db0(f"*{ns}*")

        t0 = time.monotonic()
        init_rec = cl.extract_json_from_cli(
            cl.run_on_host(
                "A366",
                [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns, "init-shared-volume"],
                with_secrets=True,
            ).stdout
        )
        base_ws = init_rec["workspace_id"]
        mp = prep_dir / f"brew-{case}-mounts.json"
        mid = f"c2-prep-brew-{case}"
        cl.generate_mounts_json(
            [make_req(mid, "initial", "brewfs-native-overlay", "idcai-60001", 1, base_ws, "none", f"c2-prep-{case}", str(cl.BIN_DIR / "brewfs"))],
            mp,
        )
        m = cl.start_mounts(mp, {mid: {"workspace_uuid": base_ws, "workspace_namespace": ns, "s3_bucket": bkt, "s3_endpoint": cl.RUSTFS_ENDPOINTS[idx % 4]}}, prep_dir)
        try:
            cl.run_on_host("A366", ["cp", "-a", f"/srv/ax-cluster/input/traces/{case}/initial/.", m[0]["mount_directory"].rstrip("/") + "/"])
            up = cl.wait_uploads(m)[0]
        finally:
            cl.stop_mounts(m, prep_dir, remove_cache=True)
        seal_rec = cl.extract_json_from_cli(
            cl.run_on_host(
                "A366",
                [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns, "seal-native-base", "--workspace", base_ws],
                with_secrets=True,
            ).stdout
        )
        keep_keys = sorted(list_all_keys(bkt))
        brew_bases[case] = {
            "bucket": bkt,
            "namespace": ns,
            "base_ws": base_ws,
            "revision": seal_rec["revision"],
            "seal_stats": seal_rec,
            "upload_drain_seconds": up["upload_drain_seconds"],
            "prepare_wall_seconds": round(time.monotonic() - t0, 6),
            "keep_keys": keep_keys,
        }
        print(f"  [BrewFS base] {case}: rows={seal_rec['total_rows']} objects={len(keep_keys)} ({brew_bases[case]['prepare_wall_seconds']:.2f}s)", flush=True)

    # 2. Official JuiceFS shared volume on DB 3 with /bases/<case>
    bkt_jfs = f"{cl.RUN_PREFIX}-c2-{policy}-jfs"
    vol_jfs = f"{cl.RUN_PREFIX}-c2-{policy}-jfs"
    cl.delete_bucket_and_objects(bkt_jfs)
    cl.ensure_bucket(bkt_jfs)
    cl.redis_flush_db(3)
    t_j0 = time.monotonic()
    cl.run_on_host(
        "A366",
        [
            str(cl.BIN_DIR / "juicefs"), "--no-agent", "format",
            "--storage=s3", f"--bucket={cl.RUSTFS_ENDPOINTS[0]}/{bkt_jfs}",
            "--block-size=4096", "--compress=none", "--trash-days=0",
            f"redis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/3", vol_jfs,
        ],
        with_secrets=True,
    )
    mp_j = prep_dir / "jfs-base-mounts.json"
    cl.generate_mounts_json(
        [make_req("c2-prep-jfs-root", "initial", "juicefs", "idcai-60001", 1, "root", "none", "c2-prep-jfs", str(cl.BIN_DIR / "juicefs"))],
        mp_j,
    )
    mj = cl.start_mounts(mp_j, {"c2-prep-jfs-root": {"redis_db": 3, "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt_jfs}"}}, prep_dir)
    jfs_case_seconds: dict[str, float] = {}
    try:
        rdir = Path(mj[0]["mount_directory"])
        (rdir / "runs").mkdir(parents=True, exist_ok=True)
        for case in CASES:
            tc0 = time.monotonic()
            dst = rdir / "bases" / case
            dst.mkdir(parents=True, exist_ok=True)
            cl.run_on_host("A366", ["cp", "-a", f"/srv/ax-cluster/input/traces/{case}/initial/.", str(dst) + "/"])
            jfs_case_seconds[case] = round(time.monotonic() - tc0, 6)
        up_j = cl.wait_uploads(mj)[0]
    finally:
        cl.stop_mounts(mj, prep_dir, remove_cache=True)
    jfs_keep_keys = sorted(list_all_keys(bkt_jfs))
    jfs_base = {
        "bucket": bkt_jfs,
        "volume": vol_jfs,
        "redis_db": 3,
        "case_copy_seconds": jfs_case_seconds,
        "upload_drain_seconds": up_j["upload_drain_seconds"],
        "prepare_wall_seconds": round(time.monotonic() - t_j0, 6),
        "keep_keys": jfs_keep_keys,
    }
    print(f"  [JuiceFS base] all 8 cases populated: objects={len(jfs_keep_keys)} ({jfs_base['prepare_wall_seconds']:.2f}s)", flush=True)

    # 3. JuiceFS Overlay (forkredis) bases (8 cases, sealed `main` branch)
    for idx, case in enumerate(CASES):
        bkt = f"{cl.RUN_PREFIX}-c2-{policy}-jov-{case}"
        vol = f"{cl.RUN_PREFIX}-c2-{policy}-jov-{case}"
        cl.delete_bucket_and_objects(bkt)
        cl.ensure_bucket(bkt)
        cl.redis_delete_pattern_db0(f"{{jfs-fork:{vol}}}:*")
        base_url = f"forkredis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/0?volume={vol}&branch=main"

        t0 = time.monotonic()
        cl.run_on_host(
            "A366",
            [
                str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "format",
                "--storage=s3", f"--bucket={cl.RUSTFS_ENDPOINTS[idx % 4]}/{bkt}",
                "--block-size=4096", "--compress=none", "--trash-days=0",
                base_url, vol,
            ],
            with_secrets=True,
        )
        mp = prep_dir / f"jov-{case}-mounts.json"
        mid = f"c2-prep-jov-{case}"
        cl.generate_mounts_json(
            [make_req(mid, "initial", "juicefs-overlay", "idcai-60001", 1, "main", "none", f"c2-prep-jov-{case}", str(cl.BIN_DIR / "juicefs-overlay"))],
            mp,
        )
        mo = cl.start_mounts(mp, {mid: {"volume": vol, "branch": "main", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[idx % 4]}/{bkt}"}}, prep_dir)
        try:
            cl.run_on_host("A366", ["cp", "-a", f"/srv/ax-cluster/input/traces/{case}/initial/.", mo[0]["mount_directory"].rstrip("/") + "/"])
            up_o = cl.wait_uploads(mo)[0]
        finally:
            cl.stop_mounts(mo, prep_dir, remove_cache=True)
        cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--seal", base_url], with_secrets=True)
        keep_keys = sorted(list_all_keys(bkt))
        jov_bases[case] = {
            "bucket": bkt,
            "volume": vol,
            "base_url": base_url,
            "upload_drain_seconds": up_o["upload_drain_seconds"],
            "prepare_wall_seconds": round(time.monotonic() - t0, 6),
            "keep_keys": keep_keys,
        }
        print(f"  [JuiceFS-Overlay base] {case}: objects={len(keep_keys)} ({jov_bases[case]['prepare_wall_seconds']:.2f}s)", flush=True)

    doc = {
        "schema": "ax-c2-base-preparation-v1",
        "completed": True,
        "recorded_at": cl.utc_iso(),
        "brewfs_native_overlay": brew_bases,
        "juicefs": jfs_base,
        "juicefs_overlay": jov_bases,
    }
    meta_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


# ==============================================================================
# Cohort Execution Engine
# ==============================================================================
def execute_c2_cohort_arm(
    cond: dict,
    system: str,
    rep: int,
    cohort: dict,
    bases: dict,
    work_dir: Path,
    *,
    collector_enabled: bool = True,
    arm_suffix: str = "",
) -> dict:
    cond_id = cond["id"]
    params = cond["parameters"]
    agents = int(params["agents"])
    pacing = params["pacing"]
    writes = params["writes"]
    row_cache_mib = int(params.get("row_cache_mib", 64))
    fork_cache_mib = int(params.get("fork_cache_mib", 256))
    namespace_mode = params.get("namespace_mode", "shared")

    short_sys = {"brewfs-native-overlay": "brew", "juicefs": "jfs", "juicefs-overlay": "jov"}[system]
    attempt_tag=work_dir.name if work_dir.name.startswith('attempt-') else 'attempt-01'
    run_tag = f"{cond_id}-{short_sys}-r{rep}-{attempt_tag}{arm_suffix}"
    run_id_mark = f"C2/{cond_id}/{system}/r{rep}{arm_suffix}"

    overrides: dict = {}
    if writes != "remote-ready":
        overrides["writes"] = writes
    if system == "brewfs-native-overlay" and row_cache_mib != 64:
        overrides["row_cache_mib"] = row_cache_mib
    if system == "juicefs-overlay" and fork_cache_mib != 256:
        overrides["fork_cache_mib"] = fork_cache_mib

    tasks = cohort["shared_tasks"]
    tasks_by_case: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        tasks_by_case[t["case"]].append(t)

    # 1. Fork/clone N child workspaces & measure workspace_prepare
    if not collector_enabled:
        cl.collector_pause_all(True)
    else:
        cl.collector_pause_all(False)

    snap_prep0 = cl.collector_mark_all(run_id_mark, "prepare_start") if collector_enabled else {}
    t_prep_mono0 = time.monotonic()

    child_meta_by_task: dict[str, dict] = {}
    fork_stats = {"created_rows": 0, "created_bytes": 0, "fork_cli_seconds": 0.0}
    ctl_mounts_jfs = None

    if system == "brewfs-native-overlay":
        t_fk0 = time.monotonic()
        for case, c_tasks in sorted(tasks_by_case.items()):
            binfo = bases["brewfs_native_overlay"][case]
            fk_out = cl.extract_json_from_cli(
                cl.run_on_host(
                    "A366",
                    [
                        str(cl.BIN_DIR / "ax-brewfs-admin"),
                        "--meta-url", REDIS_URL,
                        "--workspace-namespace", binfo["namespace"],
                        "fork-shared-bucket",
                        "--revision", binfo["revision"],
                        "--count", str(len(c_tasks)),
                        "--mode", namespace_mode,
                    ],
                    with_secrets=True,
                ).stdout
            )
            fork_stats["created_rows"] += int(fk_out.get("created_rows", 0))
            fork_stats["created_bytes"] += int(fk_out.get("created_bytes", 0))
            for t, ws_uuid in zip(c_tasks, fk_out["workspaces"]):
                child_meta_by_task[t["task_id"]] = {
                    "workspace": ws_uuid,
                    "parent_workspace": binfo["base_ws"],
                    "namespace": binfo["namespace"],
                    "bucket": binfo["bucket"],
                }
        fork_stats["fork_cli_seconds"] = round(time.monotonic() - t_fk0, 6)

    elif system == "juicefs":
        binfo = bases["juicefs"]
        # Mount temporary control slot 50 on A366 (outside active 1..48) to execute juicefs clone -p
        mp_ctl = work_dir / f"{run_tag}-jfs-ctl-mounts.json"
        ctl_id = f"{run_tag}-jfs-ctl"
        cl.generate_mounts_json(
            [make_req(ctl_id, "initial", "juicefs", "idcai-60001", 50, "root", "none", f"{run_tag}-ctl", str(cl.BIN_DIR / "juicefs"))],
            mp_ctl,
        )
        ctl_mounts_jfs = cl.start_mounts(
            mp_ctl,
            {ctl_id: {"redis_db": 3, "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{binfo['bucket']}"}},
            work_dir,
            register_collector=False,
        )
        try:
            ctl_root = Path(ctl_mounts_jfs[0]["mount_directory"])
            run_dir_root = ctl_root / "runs" / run_tag
            run_dir_root.mkdir(parents=True, exist_ok=True)
            t_fk0 = time.monotonic()

            def _clone_one(t: dict):
                src = ctl_root / "bases" / t["case"]
                dst = run_dir_root / t["task_id"]
                cl.run_on_host(
                    "A366",
                    [str(cl.BIN_DIR / "juicefs"), "--no-agent", "clone", "-p", str(src), str(dst)],
                    with_secrets=True,
                )

            with ThreadPoolExecutor(max_workers=min(16, len(tasks))) as pool:
                list(pool.map(_clone_one, tasks))
            fork_stats["fork_cli_seconds"] = round(time.monotonic() - t_fk0, 6)
        finally:
            cl.stop_mounts(ctl_mounts_jfs, work_dir, clear_collector=False, remove_cache=True)

        for t in tasks:
            child_meta_by_task[t["task_id"]] = {
                "workspace": f"{run_tag}-{t['task_id']}",
                "parent_workspace": f"base-{t['case']}",
                "subdir": f"/runs/{run_tag}/{t['task_id']}",
                "bucket": binfo["bucket"],
            }

    elif system == "juicefs-overlay":
        t_fk0 = time.monotonic()

        def _clone_lazy_one(t: dict):
            binfo = bases["juicefs_overlay"][t["case"]]
            branch = f"{run_tag}-{t['task_id']}".replace("_", "-")
            cl.run_on_host(
                "A366",
                [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--lazy", binfo["base_url"], branch],
                with_secrets=True,
            )
            return t["task_id"], branch, binfo["volume"], binfo["bucket"]

        with ThreadPoolExecutor(max_workers=min(16, len(tasks))) as pool:
            cloned = list(pool.map(_clone_lazy_one, tasks))
        fork_stats["fork_cli_seconds"] = round(time.monotonic() - t_fk0, 6)
        for tid, branch, vol, bkt in cloned:
            child_meta_by_task[tid] = {
                "workspace": branch,
                "parent_workspace": "main",
                "volume": vol,
                "branch": branch,
                "bucket": bkt,
            }

    # 2. Generate mounts.json for BOTH measure window AND cross-host verify window
    bin_path = str(cl.BIN_DIR / ("brewfs" if system.startswith("brewfs") else system))
    mount_reqs: list[dict] = []
    measure_specs: dict[str, dict] = {}
    verify_specs: dict[str, dict] = {}
    verify_target_by_task: dict[str, tuple[str, str, int]] = {}

    for t in tasks:
        tid = t["task_id"]
        src_host = t["host"]
        src_ssh = t["ssh"]
        src_slot = int(t["slot"])
        v_host = VERIFY_HOST_MAP[src_host]
        v_ssh = cl.HOST_BY_NAME[v_host]["ssh"]
        v_slot = src_slot  # Bijection preserves unique 1..k slot numbering per host!
        verify_target_by_task[tid] = (v_host, v_ssh, v_slot)

        cm = child_meta_by_task[tid]
        m_id = f"{run_tag}-{tid}"
        v_id = f"{run_tag}-{tid}-ver"

        mount_reqs.append(
            make_req(
                m_id,
                "measure",
                system,
                src_ssh,
                src_slot,
                cm["workspace"],
                cm["parent_workspace"],
                f"{cond_id}-measure",
                bin_path,
                cache_state="cold",
                windows=["measure"],
                overrides=overrides,
            )
        )
        mount_reqs.append(
            make_req(
                v_id,
                "verify",
                system,
                v_ssh,
                v_slot,
                cm["workspace"],
                cm["parent_workspace"],
                f"{cond_id}-verify",
                bin_path,
                cache_state="cross-host-cold",
                windows=["verify"],
                overrides=overrides,
            )
        )

        ep_m = cl.endpoint_for_slot(src_slot, HOST_IDX[src_host])
        ep_v = cl.endpoint_for_slot(v_slot, HOST_IDX[v_host])
        if system == "brewfs-native-overlay":
            measure_specs[m_id] = {
                "workspace_uuid": cm["workspace"],
                "workspace_namespace": cm["namespace"],
                "s3_bucket": cm["bucket"],
                "s3_endpoint": ep_m,
            }
            verify_specs[v_id] = {
                "workspace_uuid": cm["workspace"],
                "workspace_namespace": cm["namespace"],
                "s3_bucket": cm["bucket"],
                "s3_endpoint": ep_v,
            }
        elif system == "juicefs":
            measure_specs[m_id] = {"redis_db": 3, "subdir": cm["subdir"], "s3_bucket_url": f"{ep_m}/{cm['bucket']}"}
            verify_specs[v_id] = {"redis_db": 3, "subdir": cm["subdir"], "s3_bucket_url": f"{ep_v}/{cm['bucket']}"}
        elif system == "juicefs-overlay":
            measure_specs[m_id] = {"volume": cm["volume"], "branch": cm["branch"], "s3_bucket_url": f"{ep_m}/{cm['bucket']}"}
            verify_specs[v_id] = {"volume": cm["volume"], "branch": cm["branch"], "s3_bucket_url": f"{ep_v}/{cm['bucket']}"}

    mounts_cfg_path = work_dir / "mounts.json"
    cl.generate_mounts_json(mount_reqs, mounts_cfg_path)

    # 3. Start all N measure mounts across the 6 hosts
    started_mounts = batch_start_mounts(mounts_cfg_path, measure_specs, f"{run_tag}-m", register_collector=collector_enabled)
    t_prep_done = time.monotonic()
    workspace_prepare_seconds = round(t_prep_done - t_prep_mono0, 6)
    snap_prep1 = cl.collector_mark_all(run_id_mark, "prepare_done") if collector_enabled else {}
    prep_delta = cl.summarize_phase_delta(snap_prep0, snap_prep1) if collector_enabled else {}

    m_by_id = {m["id"]: m for m in started_mounts}
    stop_records: list[dict] = []
    task_results: list[dict] = []
    cohort_epoch_utc_ns = 0
    snap_m0: dict = {}
    snap_m1: dict = {}

    try:
        # Schedule synchronized cohort launch 3.0s ahead so all 6 hosts receive their batch file cleanly
        cohort_epoch_utc_ns = time.time_ns() + 3_000_000_000
        replay_by_host: dict[str, list[dict]] = defaultdict(list)
        for t in tasks:
            tid = t["task_id"]
            case = t["case"]
            hname = t["host"]
            m_info = m_by_id[f"{run_tag}-{tid}"]
            pacing_file = (
                f"/srv/ax-cluster/input/pacing/agents_{agents}_r{rep}_{pacing}/{tid}.json"
                if pacing in {"short", "long"}
                else None
            )
            replay_by_host[hname].append(
                {
                    "task_id": tid,
                    "case": case,
                    "host": hname,
                    "slot": int(t["slot"]),
                    "engine": system,
                    "cgroup": m_info["cgroup"],
                    "mount_directory": m_info["mount_directory"],
                    "metrics_port": m_info["metrics_port"],
                    "events_path": f"/srv/ax-cluster/input/traces/{case}/capture/events.jsonl",
                    "blobs_dir": f"/srv/ax-cluster/input/traces/{case}/capture/blobs",
                    "pacing_path": t.get("pacing_path", pacing_file),
                    "start_delay_ms": int(t["start_delay_ms"]),
                    "start_utc_ns": cohort_epoch_utc_ns + int(t["start_delay_ms"]) * 1_000_000,
                    "out_path": f"{cl.RUN_ROOT}/hosts/{hname}/c2-tasks/{run_tag}/{tid}-replay.json",
                }
            )

        if collector_enabled:
            snap_m0 = cl.collector_mark_all(run_id_mark, "measure_start")
        task_results = batch_replay_cohort(replay_by_host, run_tag)
        if collector_enabled:
            snap_m1 = cl.collector_mark_all(run_id_mark, "upload_done")
    finally:
        stop_records = batch_stop_mounts(started_mounts, f"{run_tag}-m", clear_collector=collector_enabled, remove_cache=True)
        if not collector_enabled:
            cl.collector_pause_all(False)

    # 4. Cross-host cold-remount verification for 100% of tasks (`verify_host != source_host`)
    t_ver0 = time.monotonic()
    ver_mounts = batch_start_mounts(mounts_cfg_path, verify_specs, f"{run_tag}-v", register_collector=False)
    v_by_id = {m["id"]: m for m in ver_mounts}
    verified_results: list[dict] = []
    try:
        vtasks_by_host: dict[str, list[dict]] = defaultdict(list)
        for t in tasks:
            tid = t["task_id"]
            case = t["case"]
            v_host, _, _ = verify_target_by_task[tid]
            vm = v_by_id[f"{run_tag}-{tid}-ver"]
            vtasks_by_host[v_host].append(
                {
                    "task_id": tid,
                    "case": case,
                    "source_host": t["host"],
                    "verify_host": v_host,
                    "mount_directory": vm["mount_directory"],
                    "final_tree_path": f"/srv/ax-cluster/input/traces/{case}/final-tree.json",
                    "replayed_dir": f"/srv/ax-cluster/input/traces/{case}/replayed",
                    "out_path": f"{cl.RUN_ROOT}/hosts/{v_host}/c2-tasks/{run_tag}/{tid}-verify.json",
                }
            )
        verified_results = batch_verify_cohort(vtasks_by_host, run_tag)
    finally:
        batch_stop_mounts(ver_mounts, f"{run_tag}-v", clear_collector=False, remove_cache=True)
    verify_wall_seconds = round(time.monotonic() - t_ver0, 6)

    # Clean up per-run child data in S3 (and JuiceFS /runs/<run_tag>) so buckets remain compact across runs
    if system == "brewfs-native-overlay":
        for case in tasks_by_case:
            binfo = bases["brewfs_native_overlay"][case]
            delete_extra_keys(binfo["bucket"], set(binfo["keep_keys"]))
    elif system == "juicefs":
        binfo = bases["juicefs"]
        mp_clean = work_dir / f"{run_tag}-jfs-clean-mounts.json"
        cid = f"{run_tag}-jfs-clean"
        cl.generate_mounts_json(
            [make_req(cid, "initial", "juicefs", "idcai-60001", 50, "root", "none", f"{run_tag}-clean", str(cl.BIN_DIR / "juicefs"))],
            mp_clean,
        )
        cmnt = cl.start_mounts(mp_clean, {cid: {"redis_db": 3, "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{binfo['bucket']}"}}, work_dir, register_collector=False)
        try:
            cl.run_on_host("A366", ["rm", "-rf", str(Path(cmnt[0]["mount_directory"]) / "runs" / run_tag)], log_cmd=False)
        finally:
            cl.stop_mounts(cmnt, work_dir, clear_collector=False, remove_cache=True)
            mp_clean.unlink(missing_ok=True)
        delete_extra_keys(binfo["bucket"], set(binfo["keep_keys"]))
    elif system == "juicefs-overlay":
        for case in tasks_by_case:
            binfo = bases["juicefs_overlay"][case]
            delete_extra_keys(binfo["bucket"], set(binfo["keep_keys"]))

    # 5. Aggregate cohort, per-case, latency histograms, amplification, and resource metrics
    task_results.sort(key=lambda x: x["task_id"])
    verified_by_id = {v["task_id"]: v for v in verified_results}
    for tr in task_results:
        v = verified_by_id[tr["task_id"]]
        if not v["verify"].get("passed", False):
            raise RuntimeError(f"Cross-host verify failed for {tr['task_id']}: {v}")
        tr["cross_host_verify"] = {
            "source_host": v["source_host"],
            "verify_host": v["verify_host"],
            "verify_seconds": round(v["verify_seconds"], 6),
            "passed": True,
        }

    (work_dir / f"tasks{arm_suffix}.json").write_text(json.dumps(task_results, indent=2) + "\n", encoding="utf-8")

    # Cohort window from cohort_epoch_utc_ns (when launch schedule t=0 began)
    max_app_done_ns = max(int(t["app_done_utc_ns"]) for t in task_results)
    max_upload_done_ns = max(int(t["upload_done_utc_ns"]) for t in task_results)
    cohort_app_done_s = max(0.001, (max_app_done_ns - cohort_epoch_utc_ns) / 1e9)
    cohort_upload_done_s = max(cohort_app_done_s, (max_upload_done_ns - cohort_epoch_utc_ns) / 1e9)

    total_read_bytes = sum(int(t["replay"]["actual_read_bytes"]) for t in task_results)
    total_write_bytes = sum(int(t["replay"]["actual_write_bytes"]) for t in task_results)
    total_unique_read = sum(int(t["replay"]["unique_read_bytes"]) for t in task_results)
    total_repeated_read = sum(int(t["replay"]["repeated_read_bytes"]) for t in task_results)
    cohort_repeated_ratio = round(1.0 - (total_unique_read / total_read_bytes), 6) if total_read_bytes > 0 else None
    total_trace_ops = sum(int(t["replay"]["operations"]) for t in task_results)

    exec_secs = [float(t["replay"]["execution_seconds"]) for t in task_results]
    active_secs = [float(t["replay"]["active_replay_seconds"]) for t in task_results]
    fs_secs = [float(t["replay"]["fs_operation_seconds"]) for t in task_results]
    drain_secs = [float(t["upload_drain_seconds"]) for t in task_results]
    task_rr_secs = [float(t["replay"]["execution_seconds"]) + float(t["upload_drain_seconds"]) for t in task_results]

    merged_all = cl.merge_log2_histograms([t["replay"]["latency_us"]["all"] for t in task_results])
    merged_read = cl.merge_log2_histograms([t["replay"]["latency_us"]["read"] for t in task_results])
    merged_write = cl.merge_log2_histograms([t["replay"]["latency_us"]["write"] for t in task_results])
    merged_meta = cl.merge_log2_histograms([t["replay"]["latency_us"]["meta"] for t in task_results])

    per_case_summary: dict[str, dict] = {}
    for case in CASES:
        ct = [t for t in task_results if t["case"] == case]
        if not ct:
            continue
        c_exec = [float(t["replay"]["execution_seconds"]) for t in ct]
        c_act = [float(t["replay"]["active_replay_seconds"]) for t in ct]
        c_fs = [float(t["replay"]["fs_operation_seconds"]) for t in ct]
        c_rr = [float(t["replay"]["execution_seconds"]) + float(t["upload_drain_seconds"]) for t in ct]
        c_hist = cl.merge_log2_histograms([t["replay"]["latency_us"]["all"] for t in ct])
        per_case_summary[case] = {
            "tasks": len(ct),
            "execution_p50_seconds": percentile_nearest_rank(c_exec, 0.50),
            "execution_p95_seconds": percentile_nearest_rank(c_exec, 0.95),
            "active_replay_p50_seconds": percentile_nearest_rank(c_act, 0.50),
            "active_replay_p95_seconds": percentile_nearest_rank(c_act, 0.95),
            "fs_operation_p50_seconds": percentile_nearest_rank(c_fs, 0.50),
            "fs_operation_p95_seconds": percentile_nearest_rank(c_fs, 0.95),
            "remote_ready_p50_seconds": percentile_nearest_rank(c_rr, 0.50),
            "remote_ready_p95_seconds": percentile_nearest_rank(c_rr, 0.95),
            "op_latency_p50_us": c_hist["p50_us"],
            "op_latency_p95_us": c_hist["p95_us"],
            "op_latency_p99_us": c_hist["p99_us"],
        }

    mount_totals = sum_mount_metrics(stop_records)
    fuse_requests_total = int(
        mount_totals.get("brewfs_fuse_op_total", 0.0)
        or mount_totals.get("brewfs_fuse_ops_total", 0.0)
        or mount_totals.get("juicefs_fuse_ops_durations_histogram_seconds_count", 0.0)
    )
    cgroup_memory_peak_sum = sum(int((sr.get("final_cgroup") or {}).get("memory_peak", 0)) for sr in stop_records)
    cgroup_cpu_usec_sum = sum(int(((sr.get("final_cgroup") or {}).get("cpu_stat") or {}).get("usage_usec", 0)) for sr in stop_records)

    raw_m_delta = cl.summarize_phase_delta(snap_m0, snap_m1) if collector_enabled else {
        "backend_physical_read_bytes": 0,
        "backend_physical_write_bytes": 0,
        "rustfs_cpu_seconds": 0.0,
        "rustfs_metrics_delta": {},
        "redis_commands_delta": 0,
        "redis_lua_calls_delta": 0,
        "redis_net_input_bytes_delta": 0,
        "redis_net_output_bytes_delta": 0,
        "redis_used_memory_end": 0,
        "redis_evicted_keys_end": 0,
    }
    phase_delta = extract_s3_and_amp(raw_m_delta, total_read_bytes, total_write_bytes, mount_totals)

    prep_redis_bytes = int(prep_delta.get("redis_net_input_bytes_delta", 0)) + int(prep_delta.get("redis_net_output_bytes_delta", 0))
    meas_redis_bytes = int(phase_delta.get("redis_net_input_bytes_delta", 0)) + int(phase_delta.get("redis_net_output_bytes_delta", 0))
    metadata_bytes_total = max(int(fork_stats["created_bytes"]), prep_redis_bytes) + meas_redis_bytes
    metadata_requests_total = int(prep_delta.get("redis_commands_delta", 0)) + int(phase_delta.get("redis_commands_delta", 0))

    total_with_prep_s = workspace_prepare_seconds + cohort_upload_done_s

    return {
        "schema": "ax-c2-run-result-v1",
        "group": "C2",
        "condition": cond_id,
        "family": cond["family"],
        "kind": cond["kind"],
        "system": system,
        "repetition": rep,
        "seed": cohort["seed"],
        "collector_enabled": collector_enabled,
        "parameters": params,
        "recorded_at": cl.utc_iso(),
        "timing": {
            "workspace_prepare_seconds": workspace_prepare_seconds,
            "fork_cli_seconds": fork_stats["fork_cli_seconds"],
            "app_done_seconds": round(cohort_app_done_s, 6),
            "upload_drain_max_seconds": round(max(drain_secs), 6),
            "remote_ready_no_verify_seconds": round(cohort_upload_done_s, 6),
            "total_with_prepare_seconds": round(total_with_prep_s, 6),
            "verify_seconds": verify_wall_seconds,
            "remote_ready_with_verify_seconds": round(cohort_upload_done_s + verify_wall_seconds, 6),
        },
        "metrics": {
            "agents": agents,
            "tasks_completed": len(task_results),
            "tasks_per_hour_app_done": round(agents * 3600.0 / cohort_app_done_s, 4),
            "tasks_per_hour_remote_ready": round(agents * 3600.0 / total_with_prep_s, 4),
            "task_seconds": round(statistics.mean(exec_secs), 6),
            "task_p50_seconds": percentile_nearest_rank(exec_secs, 0.50),
            "task_p95_seconds": percentile_nearest_rank(exec_secs, 0.95),
            "task_remote_ready_mean_seconds": round(statistics.mean(task_rr_secs), 6),
            "active_replay_mean_seconds": round(statistics.mean(active_secs), 6),
            "fs_operation_mean_seconds": round(statistics.mean(fs_secs), 6),
            "trace_operations": total_trace_ops,
            "fuse_requests": fuse_requests_total,
            "actual_read_bytes": total_read_bytes,
            "actual_write_bytes": total_write_bytes,
            "unique_read_bytes": total_unique_read,
            "repeated_read_bytes": total_repeated_read,
            "repeated_read_ratio": cohort_repeated_ratio,
            "metadata_requests": metadata_requests_total,
            "metadata_bytes": metadata_bytes_total,
            "workspace_created_rows": fork_stats["created_rows"],
            "workspace_created_bytes": fork_stats["created_bytes"],
            "s3_get_bytes": phase_delta["s3_get_bytes"],
            "s3_put_bytes": phase_delta["s3_put_bytes"],
            "backend_read_bytes": phase_delta["backend_physical_read_bytes"],
            "backend_write_bytes": phase_delta["backend_physical_write_bytes"],
            "client_cpu_seconds": round(cgroup_cpu_usec_sum / 1e6, 6),
            "client_peak_memory_bytes_sum": cgroup_memory_peak_sum,
            "latency_us": merged_all,
            "read_latency_us": merged_read,
            "write_latency_us": merged_write,
            "meta_latency_us": merged_meta,
        },
        "per_case": per_case_summary,
        "prepare_phase_delta": prep_delta,
        "phase_delta": phase_delta,
        "mount_metrics_totals": mount_totals,
        "verification": {
            "passed": True,
            "cross_host_tasks_verified": len(verified_results),
            "host_mapping": VERIFY_HOST_MAP,
        },
    }


def cleanup_all_c2_bases(policy="on"):
    print("=== Cleaning up all C2 base buckets and Redis keys ===", flush=True)
    for case in CASES:
        cl.delete_bucket_and_objects(f"{cl.RUN_PREFIX}-c2-{policy}-brew-{case}")
        cl.delete_bucket_and_objects(f"{cl.RUN_PREFIX}-c2-{policy}-jov-{case}")
    cl.delete_bucket_and_objects(f"{cl.RUN_PREFIX}-c2-{policy}-jfs")
    cl.redis_delete_pattern_db0("*axd1_c2_brew_*")
    cl.redis_delete_pattern_db0("{jfs-fork:axd1-c2-jov-*}:*")
    cl.redis_flush_db(3)
    meta_file = cl.RUN_ROOT / "prepared" / f"trace-bases-{policy}.json"
    if meta_file.is_file():
        doc = json.loads(meta_file.read_text(encoding="utf-8"))
        doc["cleaned_up_at"] = cl.utc_iso()
        doc["completed"] = False
        meta_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="all", help="Run a specific C2 condition ID or 'all'")
    ap.add_argument("--cleanup-bases-after", action="store_true", help="Remove C2 S3/Redis bases after all 81 C2 runs complete")
    args = ap.parse_args()

    c2_plan = json.loads((cl.RUN_ROOT / "plan/C2.json").read_text(encoding="utf-8"))
    cond_by_id = {c["id"]: c for c in c2_plan["conditions"]}
    cohort_by_key = {(int(c["agents"]), int(c["repetition"])): c for c in c2_plan["cohorts"]}

    with cl.MeasurementLock():
        # Handle pending-feature condition(s) first if requested
        for job in c2_plan["jobs"]:
            cid = job["condition"]
            if args.condition != "all" and cid != args.condition:
                continue
            cond = cond_by_id[cid]
            reqs = cond["parameters"].get("requires", [])
            if "host-shared-read-cache" in reqs:
                sys_name = job["system"]
                rep = int(job["repetition"])
                wdir, _, done = prepare_run_dir(cid, sys_name, rep)
                if not done:
                    pf_doc = {
                        "schema": "ax-c2-run-result-v1",
                        "group": "C2",
                        "condition": cid,
                        "family": cond["family"],
                        "kind": cond["kind"],
                        "system": sys_name,
                        "repetition": rep,
                        "status": "pending-feature",
                        "requires": reqs,
                        "parameters": cond["parameters"],
                        "reason": "Host-shared read-only cache group service is not implemented in current BrewFS/JuiceFS binaries (per docs/MOUNTS.md and configs/cache-sharing.json).",
                        "recorded_at": cl.utc_iso(),
                    }
                    finalize_run_result(cid, sys_name, rep, wdir, pf_doc, state="pending-feature")
                    print(f"  [PENDING-FEATURE] {cid} {sys_name} r{rep}", flush=True)

        # Check if any live jobs remain to execute
        live_jobs = [
            j
            for j in c2_plan["jobs"]
            if (args.condition == "all" or j["condition"] == args.condition)
            and "host-shared-read-cache" not in cond_by_id[j["condition"]]["parameters"].get("requires", [])
        ]
        need_any = False
        for j in live_jobs:
            _, _, done = prepare_run_dir(j["condition"], j["system"], int(j["repetition"]))
            if not done:
                need_any = True
                break

        cal128_path = cl.RUN_ROOT / "hosts/collector-overhead-128mounts.json"
        if need_any or not cal128_path.is_file():
            bases = ensure_c2_bases()
        else:
            bases = {}

        for job in live_jobs:
            cid = job["condition"]
            sys_name = job["system"]
            rep = int(job["repetition"])
            cond = cond_by_id[cid]
            wdir, attempt, done = prepare_run_dir(cid, sys_name, rep)
            if done:
                print(f"  [SKIP passed] {cid} {sys_name} r{rep}", flush=True)
            else:
                cl.update_status("C2", cid, sys_name, rep, "running", {"attempt": attempt})
                cohort = cohort_by_key[(int(cond["parameters"]["agents"]), rep)]
                print(f">>> Running {cid} | {sys_name} | r{rep} (agents={cond['parameters']['agents']}, pacing={cond['parameters']['pacing']}, writes={cond['parameters']['writes']}) ...", flush=True)
                res = execute_c2_cohort_arm(cond, sys_name, rep, cohort, bases, wdir, collector_enabled=True)
                finalize_run_result(cid, sys_name, rep, wdir, res, state="passed")
                print(
                    f"  [PASS] {cid} {sys_name} r{rep}: prep={res['timing']['workspace_prepare_seconds']:.2f}s "
                    f"app_done={res['timing']['app_done_seconds']:.2f}s rr={res['timing']['remote_ready_no_verify_seconds']:.2f}s "
                    f"tasks/h={res['metrics']['tasks_per_hour_remote_ready']:.1f} p99={res['metrics']['latency_us']['p99_us']:.0f}us",
                    flush=True,
                )

            # Run 1-pair 128-mount collector ON/OFF calibration check right after C2-scale-04 brewfs-native-overlay r1
            if cid == "C2-scale-04" and sys_name == "brewfs-native-overlay" and rep == 1 and not cal128_path.is_file():
                on_doc = json.loads((cl.RUN_ROOT / "C2/C2-scale-04/brewfs-native-overlay/r1/result.json").read_text(encoding="utf-8"))
                cohort_128 = cohort_by_key[(128, 1)]
                cal_dir = cl.RUN_ROOT / "mounts/cal-128-off"
                cal_dir.mkdir(parents=True, exist_ok=True)
                print(">>> Running 128-mount collector OFF arm for 1-pair overhead verification ...", flush=True)
                off_doc = execute_c2_cohort_arm(
                    cond,
                    "brewfs-native-overlay",
                    1,
                    cohort_128,
                    bases,
                    cal_dir,
                    collector_enabled=False,
                    arm_suffix="-caloff",
                )
                (cal_dir/'result.json').write_text(json.dumps(off_doc,indent=2)+'\n')
                on_tot = float(on_doc["timing"]["total_with_prepare_seconds"])
                off_tot = float(off_doc["timing"]["total_with_prepare_seconds"])
                on_task = float(on_doc["metrics"]["task_seconds"])
                off_task = float(off_doc["metrics"]["task_seconds"])
                on_p99 = float(on_doc["metrics"]["latency_us"]["p99_us"])
                off_p99 = float(off_doc["metrics"]["latency_us"]["p99_us"])
                rel_tot = (on_tot - off_tot) / max(0.001, off_tot)
                rel_task = (on_task - off_task) / max(0.001, off_task)
                rel_p99 = (on_p99 - off_p99) / max(1.0, off_p99)
                cal128 = {
                    "schema": "ax-collector-overhead-calibration-128mounts-v1",
                    "mounts": 128,
                    "condition_reference": "C2-scale-04/brewfs-native-overlay/r1",
                    "on": {
                        "total_with_prepare_seconds": on_tot,
                        "mean_task_seconds": on_task,
                        "merged_op_p99_us": on_p99,
                    },
                    "off": {
                        "total_with_prepare_seconds": off_tot,
                        "mean_task_seconds": off_task,
                        "merged_op_p99_us": off_p99,
                    },
                    "relative_total_diff": round(rel_tot, 6),
                    "relative_task_seconds_diff": round(rel_task, 6),
                    "relative_p99_diff": round(rel_p99, 6),
                    "threshold": 0.05,
                    "passed": abs(rel_tot) <= 0.05 and rel_p99 <= 0.05,
                    "recorded_at": cl.utc_iso(),
                }
                cal128_path.write_text(json.dumps(cal128, indent=2) + "\n", encoding="utf-8")
                print(f"  [CALIBRATION] passed={cal128['passed']}: rel_total={rel_tot:+.4f}, rel_p99={rel_p99:+.4f}", flush=True)
                if not cal128['passed']:raise RuntimeError('128-mount collector overhead requires inspection')

        if args.cleanup_bases_after and args.condition == "all":
            status_doc = json.loads((cl.RUN_ROOT / "status.json").read_text(encoding="utf-8"))
            c2_conds = status_doc["groups"]["C2"]["conditions"].values()
            if all(c["state"] in {"passed", "pending-feature"} for c in c2_conds):
                cleanup_all_c2_bases()

    print("=== C2 RUNNER FINISHED ===", flush=True)


if __name__ == "__main__":
    main()
