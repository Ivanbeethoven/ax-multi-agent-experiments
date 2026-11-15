#!/usr/bin/env python3
"""Complete hash-free C3 runner for run-001 (15 conditions / 105 system runs across 6 hosts).

Executes:
  - 12 live conditions (90 runs):
      * Main prepare: C3-prepare-01 (10k), C3-prepare-02 (100k), C3-prepare-03 (1M) [27 runs]
      * Main load: C3-load-01 (1 reader), C3-load-02 (32 readers), C3-load-03 (128 readers) [27 runs]
      * Main fork: C3-fork-01 (10k, 16 children, 1% dirty), C3-fork-02 (100k, 16 children, 1% dirty) [18 runs]
      * Main reuse: C3-reuse-01 (100k, 32 readers, 16 waves; cumulative prefixes 1, 4, 16) [9 runs]
      * Packed ablations (100k, 32 readers):
          - C3-whole-pack-cache-01 (packed_cache="off") [3 runs]
          - C3-retained-payload-01 (retained_payload_mib=0) [3 runs]
          - C3-metadata-cache-01 (metadata_cache_mib=0) [3 runs]
  - 3 pending-feature conditions (15 runs):
      * C3-metadata-representation-01 (requires same-payload-kv-adapter) [3 runs]
      * C3-shared-host-cache-01 (requires host-shared-read-cache) [9 runs]
      * C3-kv-no-metadata-cache-01 (requires same-payload-kv-adapter) [3 runs]

Strictly zero hashes/checksums. Uses SplitMix64 direct byte verification (`smallfiles-verify` and `c3-loader --verify-bytes 1`).
"""

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import struct
import sys
import time

sys.path.insert(0, "/srv/ax-cluster/direct/scripts")
import cluster_lib as cl

REDIS_URL = f"redis://:{cl.SECRETS['redis_password']}@{cl.REDIS_HOST}:{cl.REDIS_PORT}/0"
CORPUS_ROOT = Path("/srv/ax-cluster/input/c3-corpora")
HOST_IDX = {h["name"]: idx for idx, h in enumerate(cl.HOSTS)}

VERIFY_HOST_MAP = {
    "A366": "A601",
    "A601": "A635",
    "A635": "A355",
    "A355": "A366",
    "A672": "A368",
    "A368": "A672",
}


def size_tag(files: int) -> str:
    return {10_000: "10k", 100_000: "100k", 1_000_000: "1m"}[files]


def jfs_db_for(files: int, rep: int) -> int:
    if files == 1_000_000:
        return 4
    if files == 100_000:
        return 4 + rep  # DB 5, 6, 7
    if files == 10_000:
        return 7 + rep  # DB 8, 9, 10
    raise ValueError(f"Unexpected C3 files={files}")


def list_all_objects_with_sizes(bucket: str) -> dict[str, int]:
    s3 = cl.make_s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    out: dict[str, int] = {}
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = int(obj.get("Size", 0))
    return out


def delete_extra_keys(bucket: str, keep_keys: set[str]) -> int:
    cur = set(list_all_objects_with_sizes(bucket).keys())
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
        status_doc["groups"]["C3"]["conditions"]
        .get(cond_id, {})
        .get("runs", {})
        .get(f"{system}/r{rep}", {})
        .get("state", "planned")
    )
    base_dir = cl.RUN_ROOT / "C3" / cond_id / system / f"r{rep}"
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
    base_dir = cl.RUN_ROOT / "C3" / cond_id / system / f"r{rep}"
    if work_dir != base_dir:
        (base_dir / "result.json").write_text(json.dumps(result_obj, indent=2) + "\n", encoding="utf-8")
    details = {"result_path": str((base_dir / "result.json").relative_to(cl.RUN_ROOT))}
    if "timing" in result_obj:
        details["app_done_seconds"] = result_obj["timing"].get("app_done_seconds")
        details["remote_ready_seconds"] = result_obj["timing"].get("remote_ready_no_verify_seconds")
    cl.update_status("C3", cond_id, system, rep, state, details)


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
# Untimed Local NVMe Corpus Staging (/srv/ax-cluster/input/c3-corpora/{10k,100k,1m})
# ==============================================================================
def _stage_worker(args: tuple[str, int, int, int]):
    root_s, files, worker_idx, num_workers = args
    small = Path(root_s) / "small"
    m = 0xFFFFFFFFFFFFFFFF
    buf = bytearray(4096)
    for i in range(worker_idx, files, num_workers):
        s = i
        for off in range(0, 4096, 8):
            s = (s + 0x9E3779B97F4A7C15) & m
            z = ((s ^ (s >> 30)) * 0xBF58476D1CE4E5B9) & m
            z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & m
            struct.pack_into("<Q", buf, off, z ^ (z >> 31))
        fp = small / f"d{i % 1024:04d}" / f"f{i:07d}"
        fp.write_bytes(buf)
        os.chmod(fp, 0o644)


def ensure_local_corpus(files: int) -> Path:
    tag = size_tag(files)
    cdir = CORPUS_ROOT / tag
    marker = CORPUS_ROOT / f".verified-{tag}.json"
    if marker.is_file() and (cdir / "small").is_dir():
        return cdir

    print(f"=== Staging untimed local NVMe C3 source corpus: {tag} ({files} x 4 KiB) ===", flush=True)
    if cdir.exists():
        shutil.rmtree(cdir)
    small = cdir / "small"
    small.mkdir(parents=True, exist_ok=True)
    os.chmod(cdir, 0o755)
    os.chmod(small, 0o755)
    for d in range(min(files, 1024)):
        dp = small / f"d{d:04d}"
        dp.mkdir()
        os.chmod(dp, 0o755)

    workers = 16
    with ProcessPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_stage_worker, [(str(cdir), files, w, workers) for w in range(workers)]))

    if marker.exists():
        marker.unlink()
    cl.run_on_host(
        "A366",
        [str(cl.BIN_DIR / "smallfiles"), "verify-base", "--root", str(cdir), "--output", str(marker), "--files", str(files)],
    )
    print(f"  [STAGED & VERIFIED] {cdir}", flush=True)
    return cdir


# ==============================================================================
# Distributed Host Batch Dispatch Helpers
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
        local_tmp = cl.RUN_ROOT / "mounts" / "tmp" / f"{run_tag}-{hname}-specs.json"
        local_tmp.parent.mkdir(parents=True, exist_ok=True)
        local_tmp.write_text(json.dumps(by_host[hname], indent=2) + "\n", encoding="utf-8")
        cl.sync_file_to_host(hname, mounts_path, remote_mounts)
        cl.sync_file_to_host(hname, local_tmp, remote_specs)
        local_tmp.unlink(missing_ok=True)

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
        return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["mounts"]

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


def batch_c3_loader(tasks_by_host: dict[str, list[dict]], run_tag: str) -> list[dict]:
    hosts = sorted(tasks_by_host.keys())
    remote_paths: dict[str, str] = {}
    for hname in hosts:
        rp = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-c3-tasks.json"
        lt = cl.RUN_ROOT / "mounts" / "tmp" / f"{run_tag}-{hname}-c3-tasks.json"
        lt.parent.mkdir(parents=True, exist_ok=True)
        lt.write_text(json.dumps(tasks_by_host[hname], indent=2) + "\n", encoding="utf-8")
        cl.sync_file_to_host(hname, lt, rp)
        lt.unlink(missing_ok=True)
        remote_paths[hname] = rp

    def _run_host(hname: str) -> list[dict]:
        rp = remote_paths[hname]
        p = cl.run_on_host(
            hname,
            ["/usr/bin/python3", "/srv/ax-cluster/direct/scripts/host_batch_runner.py", "c3-batch", "--tasks", rp],
            timeout=7200.0,
        )
        cl.run_on_host(hname, ["rm", "-f", rp], check=False, log_cmd=False)
        return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["results"]

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        per_host = list(pool.map(_run_host, hosts))
    return [r for sub in per_host for r in sub]


# ==============================================================================
# Registry of Prepared C3 Datasets (10k & 100k r1..r3 kept for load/fork/reuse)
# ==============================================================================
def registry_path() -> Path:
    p = cl.RUN_ROOT / "C3" / "prepared-datasets.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_registry() -> dict:
    p = registry_path()
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"datasets": {}}


def save_registry_entry(key: str, val: dict):
    doc = load_registry()
    doc["datasets"][key] = val
    doc["updated_at"] = cl.utc_iso()
    registry_path().write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


# ==============================================================================
# 1. C3 Prepare (10k, 100k, 1M)
# ==============================================================================
def run_c3_prepare(cond: dict, system: str, rep: int, seed: int, work_dir: Path) -> dict:
    cond_id = cond["id"]
    params = cond["parameters"]
    files = int(params["files"])
    workers = int(params.get("workers", 8))
    tag = size_tag(files)
    local_src = ensure_local_corpus(files)

    short_sys = {"brewfs-packed": "pk", "juicefs": "jfs", "juicefs-overlay": "jov"}[system]
    run_tag = f"c3-prep-{tag}-{short_sys}-r{rep}"
    run_id_mark = f"C3/{cond_id}/{system}/r{rep}"

    bkt = f"{cl.RUN_PREFIX}-c3-{short_sys}-{tag}-r{rep}"
    ep0 = cl.RUSTFS_ENDPOINTS[(rep - 1) % 4]
    ep_ver = cl.RUSTFS_ENDPOINTS[rep % 4]

    # Clean up any prior state for this (system, tag, rep)
    if system == "brewfs-packed":
        cl.delete_buckets_with_prefix(bkt)
        cl.ensure_bucket(bkt, ep0)
        ns = f"{cl.RUN_NAMESPACE}_c3_pk_{tag}_r{rep}"
        cl.redis_delete_pattern_db0(f"*{ns}*")
    elif system == "juicefs":
        cl.delete_bucket_and_objects(bkt, ep0)
        cl.ensure_bucket(bkt, ep0)
        db = jfs_db_for(files, rep)
        cl.redis_flush_db(db)
    elif system == "juicefs-overlay":
        cl.delete_bucket_and_objects(bkt, ep0)
        cl.ensure_bucket(bkt, ep0)
        vol = bkt
        cl.redis_delete_pattern_db0(f"{{jfs-fork:{vol}}}:*")

    snap0 = cl.collector_mark_all(run_id_mark, "prepare_start")
    t0 = time.monotonic()

    stage_breakdown: dict = {}
    stop_records: list[dict] = []
    manifest_key = ""
    pk_base_ws = ""

    if system == "brewfs-packed":
        ns = f"{cl.RUN_NAMESPACE}_c3_pk_{tag}_r{rep}"
        build_out_dir = cl.RUN_ROOT / "mounts" / "tmp" / f"{run_tag}-pack-stage"
        if build_out_dir.exists():
            shutil.rmtree(build_out_dir)
        build_out_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = work_dir / "packed-manifest-key.txt"
        manifest_file.unlink(missing_ok=True)

        env_cmd = [
            "env",
            f"TOKIO_WORKER_THREADS={workers}",
            f"RAYON_NUM_THREADS={workers}",
            str(cl.BIN_DIR / "packed_snapshot_fixture"),
            "--output-dir", str(build_out_dir),
            "--source-directory", str(local_src),
            "--source-consistency", "best-effort-detected",
            "--source-hardlink-policy", "reject-external",
            "--bucket", bkt,
            "--endpoint", ep0,
            "--region", "us-east-1",
            "--force-path-style", "true",
            "--s3-rustfs-ec-block-size-hint", "auto",
            "--manifest-output", str(manifest_file),
        ]
        t_b0 = time.monotonic()
        cl.run_on_host("A366", env_cmd, with_secrets=True, timeout=7200.0)
        t_b1 = time.monotonic()
        manifest_key = manifest_file.read_text(encoding="utf-8").strip()
        local_stage_bytes = sum(f.stat().st_size for f in build_out_dir.rglob("*") if f.is_file())
        shutil.rmtree(build_out_dir, ignore_errors=True)

        data_cfg = work_dir / "packed-data-config.json"
        data_cfg.write_text(
            json.dumps(
                {
                    "data": {
                        "backend": "s3",
                        "s3": {
                            "bucket": bkt,
                            "endpoint": ep0,
                            "region": "us-east-1",
                            "force_path_style": True,
                            "disable_payload_checksum": True,
                        },
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        t_reg0 = time.monotonic()
        reg_rec = cl.extract_json_from_cli(
            cl.run_on_host(
                "A366",
                [
                    str(cl.BIN_DIR / "brewfs"),
                    "workspace",
                    "--meta-backend", "redis",
                    "--meta-url", REDIS_URL,
                    "--workspace-namespace", ns,
                    "init-packed-volume",
                    "--manifest-key", manifest_key,
                    "--data-config", str(data_cfg),
                ],
                with_secrets=True,
            ).stdout
        )
        t_reg1 = time.monotonic()
        pk_base_ws = reg_rec["workspace_id"]
        app_done_s = t_b1 - t0
        remote_ready_s = t_reg1 - t0
        stage_breakdown = {
            "build_and_upload_seconds": round(t_b1 - t_b0, 6),
            "register_volume_seconds": round(t_reg1 - t_reg0, 6),
            "local_staging_bytes": local_stage_bytes,
            "manifest_key": manifest_key,
            "workspace_id": pk_base_ws,
        }

    elif system == "juicefs":
        db = jfs_db_for(files, rep)
        t_fmt0 = time.monotonic()
        cl.run_on_host(
            "A366",
            [
                str(cl.BIN_DIR / "juicefs"), "--no-agent", "format",
                "--storage=s3", f"--bucket={ep0}/{bkt}",
                "--block-size=4096", "--compress=none", "--trash-days=0",
                f"redis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/{db}", bkt,
            ],
            with_secrets=True,
        )
        t_fmt1 = time.monotonic()
        mp = work_dir / "mounts.json"
        m_id = f"{run_tag}-init"
        v_id = f"{run_tag}-ver"
        cl.generate_mounts_json(
            [
                make_req(m_id, "initial", "juicefs", "idcai-60001", 1, "root", "none", f"{cond_id}-init", str(cl.BIN_DIR / "juicefs"), windows=["initial"]),
                make_req(v_id, "verify", "juicefs", "idcai-60525", 1, "corpus", "root", f"{cond_id}-ver", str(cl.BIN_DIR / "juicefs"), cache_state="cross-host-cold", windows=["verify"]),
            ],
            mp,
        )
        started = cl.start_mounts(mp, {m_id: {"redis_db": db, "s3_bucket_url": f"{ep0}/{bkt}"}}, work_dir)
        copy_json = work_dir / "smallfiles-copy.json"
        copy_json.unlink(missing_ok=True)
        try:
            target_dir = Path(started[0]["mount_directory"]) / "corpus"
            t_cp0 = time.monotonic()
            cl.run_on_host(
                "A366",
                [
                    "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", started[0]["cgroup"], "--",
                    str(cl.BIN_DIR / "smallfiles-copy"), str(local_src), str(target_dir), str(copy_json), str(files), str(workers),
                ],
                timeout=7200.0,
            )
            t_cp1 = time.monotonic()
            up = cl.wait_uploads(started)[0]
            t_up1 = time.monotonic()
        finally:
            stop_records = cl.stop_mounts(started, work_dir, remove_cache=True)
        app_done_s = t_cp1 - t0
        remote_ready_s = t_up1 - t0
        stage_breakdown = {
            "format_seconds": round(t_fmt1 - t_fmt0, 6),
            "copy_wall_seconds": round(t_cp1 - t_cp0, 6),
            "upload_drain_seconds": round(up["upload_drain_seconds"], 6),
            "smallfiles_copy": json.loads(copy_json.read_text(encoding="utf-8")),
        }

    elif system == "juicefs-overlay":
        vol = bkt
        base_url = f"forkredis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/0?volume={vol}&branch=main"
        t_fmt0 = time.monotonic()
        cl.run_on_host(
            "A366",
            [
                str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "format",
                "--storage=s3", f"--bucket={ep0}/{bkt}",
                "--block-size=4096", "--compress=none", "--trash-days=0",
                base_url, vol,
            ],
            with_secrets=True,
        )
        t_fmt1 = time.monotonic()
        mp = work_dir / "mounts.json"
        m_id = f"{run_tag}-init"
        v_id = f"{run_tag}-ver"
        cl.generate_mounts_json(
            [
                make_req(m_id, "initial", "juicefs-overlay", "idcai-60001", 1, "main", "none", f"{cond_id}-init", str(cl.BIN_DIR / "juicefs-overlay"), windows=["initial"]),
                make_req(v_id, "verify", "juicefs-overlay", "idcai-60525", 1, "main", "none", f"{cond_id}-ver", str(cl.BIN_DIR / "juicefs-overlay"), cache_state="cross-host-cold", windows=["verify"]),
            ],
            mp,
        )
        started = cl.start_mounts(mp, {m_id: {"volume": vol, "branch": "main", "s3_bucket_url": f"{ep0}/{bkt}"}}, work_dir)
        copy_json = work_dir / "smallfiles-copy.json"
        copy_json.unlink(missing_ok=True)
        try:
            target_dir = Path(started[0]["mount_directory"]) / "corpus"
            t_cp0 = time.monotonic()
            cl.run_on_host(
                "A366",
                [
                    "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", started[0]["cgroup"], "--",
                    str(cl.BIN_DIR / "smallfiles-copy"), str(local_src), str(target_dir), str(copy_json), str(files), str(workers),
                ],
                timeout=7200.0,
            )
            t_cp1 = time.monotonic()
            up = cl.wait_uploads(started)[0]
        finally:
            stop_records = cl.stop_mounts(started, work_dir, remove_cache=True)
        t_sl0 = time.monotonic()
        cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--seal", base_url], with_secrets=True)
        t_sl1 = time.monotonic()
        app_done_s = t_cp1 - t0
        remote_ready_s = t_sl1 - t0
        stage_breakdown = {
            "format_seconds": round(t_fmt1 - t_fmt0, 6),
            "copy_wall_seconds": round(t_cp1 - t_cp0, 6),
            "upload_drain_seconds": round(up["upload_drain_seconds"], 6),
            "seal_seconds": round(t_sl1 - t_sl0, 6),
            "smallfiles_copy": json.loads(copy_json.read_text(encoding="utf-8")),
        }

    snap1 = cl.collector_mark_all(run_id_mark, "prepare_done")
    objs = list_all_objects_with_sizes(bkt)
    s3_stored_bytes = sum(objs.values())
    s3_object_count = len(objs)

    # Separate Cross-Host Cold-Remount Verification on A355
    t_ver0 = time.monotonic()
    if system == "brewfs-packed":
        mp_v = work_dir / "mounts.json"
        v_id = f"{run_tag}-ver"
        cl.generate_mounts_json(
            [make_req(v_id, "verify", "brewfs-packed", "idcai-60525", 1, pk_base_ws, "none", f"{cond_id}-ver", str(cl.BIN_DIR / "brewfs"), cache_state="cross-host-cold", windows=["verify"])],
            mp_v,
        )
        vm = cl.start_mounts(
            mp_v,
            {v_id: {"workspace_uuid": pk_base_ws, "workspace_namespace": f"{cl.RUN_NAMESPACE}_c3_pk_{tag}_r{rep}", "s3_bucket": bkt, "s3_endpoint": ep_ver}},
            work_dir,
            register_collector=False,
        )
    elif system == "juicefs":
        vm = cl.start_mounts(
            work_dir / "mounts.json",
            {f"{run_tag}-ver": {"redis_db": jfs_db_for(files, rep), "subdir": "/corpus", "s3_bucket_url": f"{ep_ver}/{bkt}"}},
            work_dir,
            register_collector=False,
        )
    elif system == "juicefs-overlay":
        vm = cl.start_mounts(
            work_dir / "mounts.json",
            {f"{run_tag}-ver": {"volume": bkt, "branch": "main", "subdir": "/corpus", "s3_bucket_url": f"{ep_ver}/{bkt}"}},
            work_dir,
            register_collector=False,
        )

    remote_ver_out = f"{cl.RUN_ROOT}/hosts/A355/{run_tag}-verify.json"
    cl.run_on_host("A355", ["rm", "-f", remote_ver_out], log_cmd=False)
    try:
        vp = cl.run_on_host(
            "A355",
            [str(cl.BIN_DIR / "smallfiles-verify"), vm[0]["mount_directory"], "base", remote_ver_out, str(files), "8", "1"],
            timeout=3600.0,
        )
        ver_doc = json.loads(vp.stdout.decode("utf-8"))
    finally:
        cl.stop_mounts(vm, work_dir, clear_collector=False, remove_cache=True)
        cl.run_on_host("A355", ["rm", "-f", remote_ver_out], check=False, log_cmd=False)
    verify_s = round(time.monotonic() - t_ver0, 6)

    # Save 10k and 100k prepared volumes in registry; drop 1M after verification to conserve cluster storage
    if files in {10_000, 100_000}:
        entry = {
            "files": files,
            "tag": tag,
            "system": system,
            "repetition": rep,
            "bucket": bkt,
            "prepare_seconds": round(remote_ready_s, 6),
            "s3_stored_bytes": s3_stored_bytes,
            "s3_objects": s3_object_count,
        }
        if system == "brewfs-packed":
            entry.update({"manifest_key": manifest_key, "namespace": f"{cl.RUN_NAMESPACE}_c3_pk_{tag}_r{rep}", "base_ws": pk_base_ws})
        elif system == "juicefs":
            entry.update({"redis_db": jfs_db_for(files, rep), "keep_keys": sorted(objs.keys())})
        elif system == "juicefs-overlay":
            entry.update({"volume": bkt, "base_url": f"forkredis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/0?volume={bkt}&branch=main", "keep_keys": sorted(objs.keys())})
        save_registry_entry(f"{tag}/{system}/r{rep}", entry)
    else:
        # 1M cleanup
        if system == "brewfs-packed":
            cl.delete_buckets_with_prefix(bkt)
            cl.redis_delete_pattern_db0(f"*axd1_c3_pk_1m_r{rep}*")
        elif system == "juicefs":
            cl.delete_bucket_and_objects(bkt, ep0)
            cl.redis_flush_db(4)
        elif system == "juicefs-overlay":
            cl.delete_bucket_and_objects(bkt, ep0)
            cl.redis_delete_pattern_db0(f"{{jfs-fork:{bkt}}}:*")

    mount_totals = sum_mount_metrics(stop_records)
    raw_delta = cl.summarize_phase_delta(snap0, snap1)
    logical_bytes = files * 4096
    phase_delta = extract_s3_and_amp(raw_delta, 0, logical_bytes, mount_totals)
    if phase_delta["s3_put_bytes"] == 0:
        phase_delta["s3_put_bytes"] = s3_stored_bytes
        phase_delta["upload_amplification_s3"] = round(s3_stored_bytes / logical_bytes, 6)

    return {
        "schema": "ax-c3-run-result-v1",
        "group": "C3",
        "condition": cond_id,
        "family": "prepare",
        "kind": cond["kind"],
        "system": system,
        "repetition": rep,
        "seed": seed,
        "parameters": params,
        "recorded_at": cl.utc_iso(),
        "timing": {
            "app_done_seconds": round(app_done_s, 6),
            "remote_ready_no_verify_seconds": round(remote_ready_s, 6),
            "verify_seconds": verify_s,
            "remote_ready_with_verify_seconds": round(remote_ready_s + verify_s, 6),
        },
        "metrics": {
            "files": files,
            "logical_bytes": logical_bytes,
            "workers": workers,
            "files_per_second": round(files / max(0.001, remote_ready_s), 3),
            "s3_objects_created": s3_object_count,
            "s3_stored_bytes": s3_stored_bytes,
            "s3_put_bytes": phase_delta["s3_put_bytes"],
            "s3_get_bytes": phase_delta["s3_get_bytes"],
            "backend_write_bytes": phase_delta["backend_physical_write_bytes"],
            "metadata_requests": phase_delta["redis_commands_delta"],
            "peak_space_bytes": s3_stored_bytes + int(stage_breakdown.get("local_staging_bytes", 0)),
        },
        "stage_breakdown": stage_breakdown,
        "phase_delta": phase_delta,
        "verification": ver_doc,
    }


# ==============================================================================
# 2. C3 Load & Packed Cache Ablations (1, 32, 128 readers x 3 epochs on 100k)
# ==============================================================================
def execute_single_load_wave(
    cond_id: str,
    system: str,
    rep: int,
    wave_idx: int,
    placements: list[dict],
    base_seed: int,
    ds_entry: dict,
    overrides: dict,
    work_dir: Path,
    *,
    save_mounts_json: bool = True,
) -> dict:
    short_sys = {"brewfs-packed": "pk", "juicefs": "jfs", "juicefs-overlay": "jov"}[system]
    attempt_tag=work_dir.name if work_dir.name.startswith('attempt-') else 'attempt-01'
    run_tag = f"{cond_id}-{short_sys}-r{rep}-{attempt_tag}-w{wave_idx}"
    run_id_mark = f"C3/{cond_id}/{system}/r{rep}/w{wave_idx}"
    bin_path = str(cl.BIN_DIR / ("brewfs" if system.startswith("brewfs") else system))

    bkt = ds_entry["bucket"]
    pk_temp_namespaces: list[str] = []
    pk_temp_ws: list[str] = []

    if system == "brewfs-packed":
        data_cfg = work_dir / f"{run_tag}-pk-data.json"
        data_cfg.write_text(
            json.dumps(
                {
                    "data": {
                        "backend": "s3",
                        "s3": {
                            "bucket": bkt,
                            "endpoint": cl.RUSTFS_ENDPOINTS[0],
                            "region": "us-east-1",
                            "force_path_style": True,
                            "disable_payload_checksum": True,
                        },
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )

        def _init_pk_reader(idx: int) -> tuple[str, str]:
            ns = f"{cl.RUN_NAMESPACE}_c3_ldr_{cond_id.replace('-', '_')}_{rep}_w{wave_idx}_{idx}"
            rec = cl.extract_json_from_cli(
                cl.run_on_host(
                    "A366",
                    [
                        str(cl.BIN_DIR / "brewfs"),
                        "workspace",
                        "--meta-backend", "redis",
                        "--meta-url", REDIS_URL,
                        "--workspace-namespace", ns,
                        "init-packed-volume",
                        "--manifest-key", ds_entry["manifest_key"],
                        "--data-config", str(data_cfg),
                    ],
                    with_secrets=True,
                    log_cmd=False,
                ).stdout
            )
            return ns, rec["workspace_id"]

        with ThreadPoolExecutor(max_workers=min(16, len(placements))) as pool:
            inited = list(pool.map(_init_pk_reader, range(len(placements))))
        data_cfg.unlink(missing_ok=True)
        pk_temp_namespaces = [x[0] for x in inited]
        pk_temp_ws = [x[1] for x in inited]

    reqs: list[dict] = []
    specs: dict[str, dict] = {}
    for idx, pl in enumerate(placements):
        mid = f"{run_tag}-m{idx + 1:03d}"
        hname = pl["host"]
        hssh = pl["ssh"]
        slot = int(pl["slot"])
        ep = cl.endpoint_for_slot(slot, HOST_IDX[hname])
        if system == "brewfs-packed":
            ws_id = pk_temp_ws[idx]
            ns = pk_temp_namespaces[idx]
            reqs.append(make_req(mid, "measure", system, hssh, slot, ws_id, "none", f"{cond_id}-load", bin_path, overrides=overrides))
            specs[mid] = {"workspace_uuid": ws_id, "workspace_namespace": ns, "s3_bucket": bkt, "s3_endpoint": ep}
        elif system == "juicefs":
            reqs.append(make_req(mid, "measure", system, hssh, slot, f"ro-{idx + 1}", "corpus", f"{cond_id}-load", bin_path, overrides=overrides))
            specs[mid] = {"redis_db": ds_entry["redis_db"], "subdir": "/corpus", "s3_bucket_url": f"{ep}/{bkt}"}
        elif system == "juicefs-overlay":
            reqs.append(make_req(mid, "measure", system, hssh, slot, "main", "none", f"{cond_id}-load", bin_path, overrides=overrides))
            specs[mid] = {"volume": ds_entry["volume"], "branch": "main", "subdir": "/corpus", "s3_bucket_url": f"{ep}/{bkt}"}

    mp_path = work_dir / (f"mounts-w{wave_idx}.json" if not save_mounts_json else "mounts.json")
    cl.generate_mounts_json(reqs, mp_path)

    t_m0 = time.monotonic()
    started = batch_start_mounts(mp_path, specs, run_tag, register_collector=True)
    mount_wall_s = time.monotonic() - t_m0
    m_by_id = {m["id"]: m for m in started}

    stop_records: list[dict] = []
    loader_results: list[dict] = []
    snap0: dict = {}
    snap1: dict = {}
    barrier_utc_ns = time.time_ns() + 3_000_000_000
    t_load_mono0 = time.monotonic()

    try:
        tasks_by_host: dict[str, list[dict]] = defaultdict(list)
        for idx, pl in enumerate(placements):
            mid = f"{run_tag}-m{idx + 1:03d}"
            m_info = m_by_id[mid]
            hname = pl["host"]
            r_seed = base_seed + wave_idx * 10007 + (idx + 1) * 1009
            out_p = f"{cl.RUN_ROOT}/hosts/{hname}/c3-tasks/{run_tag}-r{idx + 1:03d}.json"
            tasks_by_host[hname].append(
                {
                    "reader_id": f"r{idx + 1:03d}",
                    "host": hname,
                    "slot": int(pl["slot"]),
                    "engine": system,
                    "cgroup": m_info["cgroup"],
                    "metrics_port": m_info["metrics_port"],
                    "out_path": out_p,
                    "wait_upload": False,
                    "loader_argv": [
                        str(cl.BIN_DIR / "c3-loader"),
                        "load",
                        "--root", m_info["mount_directory"],
                        "--output", out_p,
                        "--files", "100000",
                        "--epochs", "3",
                        "--threads", "2",
                        "--batch", "64",
                        "--seed", str(r_seed),
                        "--verify-bytes", "1",
                        "--start-unix-ns", str(barrier_utc_ns),
                    ],
                }
            )
        snap0 = cl.collector_mark_all(run_id_mark, "load_start")
        t_load_mono0 = time.monotonic()
        loader_results = batch_c3_loader(tasks_by_host, run_tag)
        t_load_mono1 = time.monotonic()
        snap1 = cl.collector_mark_all(run_id_mark, "load_done")
    finally:
        stop_records = batch_stop_mounts(started, run_tag, clear_collector=True, remove_cache=True)
        if not save_mounts_json:
            mp_path.unlink(missing_ok=True)
        if system == "brewfs-packed":
            for ws_id in pk_temp_ws:
                cl.delete_bucket_and_objects(f"{bkt}-{ws_id}")
            for ns in pk_temp_namespaces:
                cl.redis_delete_pattern_db0(f"*{ns}*")

    # Subtract scheduled barrier wait (~3.0s) from coordinator wall if applicable; per-reader `loader.wall_seconds` is exact post-barrier!
    max_reader_wall_s = max(float(r["loader"]["wall_seconds"]) for r in loader_results)
    mean_reader_wall_s = statistics.mean(float(r["loader"]["wall_seconds"]) for r in loader_results)
    fb_secs = [float(r["loader"]["first_batch_64_seconds"]) for r in loader_results]
    ep0_secs = [float(r["loader"]["epoch_results"][0]["wall_seconds"]) for r in loader_results]
    ep1_secs = [float(r["loader"]["epoch_results"][1]["wall_seconds"]) for r in loader_results]
    ep2_secs = [float(r["loader"]["epoch_results"][2]["wall_seconds"]) for r in loader_results]

    merged_all = cl.merge_log2_histograms([r["loader"]["histograms"]["all"] for r in loader_results])
    merged_ep0 = cl.merge_log2_histograms([r["loader"]["epoch_results"][0]["latency"] for r in loader_results])
    merged_ep1 = cl.merge_log2_histograms([r["loader"]["epoch_results"][1]["latency"] for r in loader_results])
    merged_ep2 = cl.merge_log2_histograms([r["loader"]["epoch_results"][2]["latency"] for r in loader_results])

    total_read_bytes = sum(int(r["loader"]["actual_read_bytes"]) for r in loader_results)
    mount_totals = sum_mount_metrics(stop_records)
    cgroup_cpu_s = sum(int(((sr.get("final_cgroup") or {}).get("cpu_stat") or {}).get("usage_usec", 0)) for sr in stop_records) / 1e6
    cgroup_mem_peak = sum(int((sr.get("final_cgroup") or {}).get("memory_peak", 0)) for sr in stop_records)
    exit_tail_max_s = max((float(sr.get("exit_seconds", 0.0)) for sr in stop_records), default=0.0)

    raw_delta = cl.summarize_phase_delta(snap0, snap1)
    phase_delta = extract_s3_and_amp(raw_delta, total_read_bytes, 0, mount_totals)
    s3_get_reqs = int(
        (
            mount_totals.get("brewfs_s3_get_ops_total", 0.0)
            + mount_totals.get("brewfs_packed_runtime_backend_full_gets_total", 0.0)
            + mount_totals.get("brewfs_packed_runtime_backend_range_gets_total", 0.0)
        )
        or mount_totals.get("juicefs_object_request_count_GET", 0.0)
        or (phase_delta.get("rustfs_s3_requests_by_method") or {}).get("GET", 0)
    )
    # Metadata requests: Redis commands + Packed metadata/index S3 fetches
    pk_meta_reqs = int(mount_totals.get("brewfs_packed_metadata_backend_requests_total", 0.0))
    metadata_requests = int(phase_delta["redis_commands_delta"]) + pk_meta_reqs

    return {
        "wave": wave_idx,
        "mount_wall_seconds": round(mount_wall_s, 6),
        "cohort_load_seconds": round(max_reader_wall_s, 6),
        "wave_total_seconds": round(mount_wall_s + max_reader_wall_s + exit_tail_max_s, 6),
        "reader_wall_mean_seconds": round(mean_reader_wall_s, 6),
        "exit_tail_max_seconds": round(exit_tail_max_s, 6),
        "first_batch_64_mean_seconds": round(statistics.mean(fb_secs), 6),
        "first_batch_64_p50_seconds": percentile_nearest_rank(fb_secs, 0.50),
        "first_batch_64_p95_seconds": percentile_nearest_rank(fb_secs, 0.95),
        "epoch_0_mean_seconds": round(statistics.mean(ep0_secs), 6),
        "epoch_1_mean_seconds": round(statistics.mean(ep1_secs), 6),
        "epoch_2_mean_seconds": round(statistics.mean(ep2_secs), 6),
        "epoch_0_max_seconds": round(max(ep0_secs), 6),
        "epoch_1_max_seconds": round(max(ep1_secs), 6),
        "epoch_2_max_seconds": round(max(ep2_secs), 6),
        "actual_read_bytes": total_read_bytes,
        "s3_get_bytes": phase_delta["s3_get_bytes"],
        "s3_get_requests": s3_get_reqs,
        "backend_read_bytes": phase_delta["backend_physical_read_bytes"],
        "metadata_requests": metadata_requests,
        "redis_commands_delta": phase_delta["redis_commands_delta"],
        "decode_cpu_seconds": round(cgroup_cpu_s, 6),
        "client_peak_memory_bytes_sum": cgroup_mem_peak,
        "latency_us": merged_all,
        "epoch_0_latency_us": merged_ep0,
        "epoch_1_latency_us": merged_ep1,
        "epoch_2_latency_us": merged_ep2,
        "phase_delta": phase_delta,
        "mount_metrics_totals": mount_totals,
        "readers": loader_results,
    }


def run_c3_load_or_ablation(cond: dict, system: str, rep: int, seed: int, cohort_by_key: dict, work_dir: Path) -> dict:
    cond_id = cond["id"]
    params = cond["parameters"]
    agents = int(params["agents"])
    reg = load_registry()["datasets"]
    ds_entry = reg[f"100k/{system}/r{rep}"]

    if agents == 1:
        placements = [{"host": "A366", "ssh": "idcai-60001", "slot": 1}]
    else:
        placements = cohort_by_key[(agents, rep)]["shared_tasks"]

    overrides: dict = {}
    if params.get("packed_cache", "on") != "on":
        overrides["packed_cache"] = params["packed_cache"]
    if int(params.get("retained_payload_mib", 8)) != 8:
        overrides["retained_payload_mib"] = int(params["retained_payload_mib"])
    if int(params.get("metadata_cache_mib", 128)) != 128:
        overrides["metadata_cache_mib"] = int(params["metadata_cache_mib"])

    w = execute_single_load_wave(cond_id, system, rep, 1, placements, seed, ds_entry, overrides, work_dir, save_mounts_json=True)
    readers_raw = w.pop("readers")
    (work_dir / "readers.json").write_text(json.dumps(readers_raw, indent=2) + "\n", encoding="utf-8")

    return {
        "schema": "ax-c3-run-result-v1",
        "group": "C3",
        "condition": cond_id,
        "family": cond["family"],
        "kind": cond["kind"],
        "system": system,
        "repetition": rep,
        "seed": seed,
        "parameters": params,
        "recorded_at": cl.utc_iso(),
        "timing": {
            "mount_wall_seconds": w["mount_wall_seconds"],
            "app_done_seconds": w["cohort_load_seconds"],
            "exit_tail_max_seconds": w["exit_tail_max_seconds"],
            "remote_ready_no_verify_seconds": w["wave_total_seconds"],
            "verify_seconds": 0.0,
            "remote_ready_with_verify_seconds": w["wave_total_seconds"],
        },
        "metrics": {
            "agents": agents,
            "files": 100_000,
            "epochs": 3,
            "first_batch_64_seconds": w["first_batch_64_mean_seconds"],
            "first_batch_64_p50_seconds": w["first_batch_64_p50_seconds"],
            "first_batch_64_p95_seconds": w["first_batch_64_p95_seconds"],
            "epoch_0_mean_seconds": w["epoch_0_mean_seconds"],
            "epoch_1_mean_seconds": w["epoch_1_mean_seconds"],
            "epoch_2_mean_seconds": w["epoch_2_mean_seconds"],
            "epoch_0_max_seconds": w["epoch_0_max_seconds"],
            "epoch_1_max_seconds": w["epoch_1_max_seconds"],
            "epoch_2_max_seconds": w["epoch_2_max_seconds"],
            "reader_wall_mean_seconds": w["reader_wall_mean_seconds"],
            "actual_read_bytes": w["actual_read_bytes"],
            "s3_get_bytes": w["s3_get_bytes"],
            "s3_get_requests": w["s3_get_requests"],
            "backend_read_bytes": w["backend_read_bytes"],
            "metadata_requests": w["metadata_requests"],
            "redis_commands_delta": w["redis_commands_delta"],
            "decode_cpu_seconds": w["decode_cpu_seconds"],
            "client_peak_memory_bytes_sum": w["client_peak_memory_bytes_sum"],
            "latency_us": w["latency_us"],
            "epoch_0_latency_us": w["epoch_0_latency_us"],
            "epoch_1_latency_us": w["epoch_1_latency_us"],
            "epoch_2_latency_us": w["epoch_2_latency_us"],
        },
        "phase_delta": w["phase_delta"],
        "mount_metrics_totals": w["mount_metrics_totals"],
    }


# ==============================================================================
# 3. C3 Fork (10k & 100k, 16 children, 1% dirty + parent & cross-host verify)
# ==============================================================================
def run_c3_fork(cond: dict, system: str, rep: int, seed: int, cohort_by_key: dict, work_dir: Path) -> dict:
    cond_id = cond["id"]
    params = cond["parameters"]
    files = int(params["files"])
    children = int(params.get("children", 16))
    dirty_count = max(1, int(round(files * float(params.get("dirty_fraction", 0.01)))))
    tag = size_tag(files)
    reg = load_registry()["datasets"]
    ds_entry = reg[f"{tag}/{system}/r{rep}"]

    short_sys = {"brewfs-packed": "pk", "juicefs": "jfs", "juicefs-overlay": "jov"}[system]
    attempt_tag=work_dir.name if work_dir.name.startswith('attempt-') else 'attempt-01'
    run_tag = f"{cond_id}-{short_sys}-r{rep}-{attempt_tag}"
    run_id_mark = f"C3/{cond_id}/{system}/r{rep}"
    bin_path = str(cl.BIN_DIR / ("brewfs" if system.startswith("brewfs") else system))
    bkt = ds_entry["bucket"]

    # First 16 slots from the 32-agent cohort distribute across all 6 hosts cleanly
    placements = cohort_by_key[(32, rep)]["shared_tasks"][:children]

    snap0 = cl.collector_mark_all(run_id_mark, "fork_start")
    t_fk0 = time.monotonic()

    child_info: list[dict] = []
    if system == "brewfs-packed":
        data_cfg = work_dir / "pk-fork-data.json"
        data_cfg.write_text(
            json.dumps(
                {
                    "data": {
                        "backend": "s3",
                        "s3": {
                            "bucket": bkt,
                            "endpoint": cl.RUSTFS_ENDPOINTS[0],
                            "region": "us-east-1",
                            "force_path_style": True,
                            "disable_payload_checksum": True,
                        },
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )

        def _fork_pk(idx: int) -> dict:
            ns = f"{cl.RUN_NAMESPACE}_c3_fk_{tag}_{rep}_c{idx + 1:02d}"
            cl.redis_delete_pattern_db0(f"*{ns}*")
            rec = cl.extract_json_from_cli(
                cl.run_on_host(
                    "A366",
                    [
                        str(cl.BIN_DIR / "brewfs"),
                        "workspace",
                        "--meta-backend", "redis",
                        "--meta-url", REDIS_URL,
                        "--workspace-namespace", ns,
                        "init-packed-volume",
                        "--manifest-key", ds_entry["manifest_key"],
                        "--data-config", str(data_cfg),
                    ],
                    with_secrets=True,
                ).stdout
            )
            return {"idx": idx + 1, "ws": rec["workspace_id"], "ns": ns}

        with ThreadPoolExecutor(max_workers=16) as pool:
            child_info = list(pool.map(_fork_pk, range(children)))
        data_cfg.unlink(missing_ok=True)

    elif system == "juicefs":
        mp_ctl = work_dir / "jfs-ctl-mounts.json"
        ctl_id = f"{run_tag}-ctl"
        cl.generate_mounts_json(
            [make_req(ctl_id, "initial", "juicefs", "idcai-60001", 50, "root", "none", f"{run_tag}-ctl", str(cl.BIN_DIR / "juicefs"))],
            mp_ctl,
        )
        cmnt = cl.start_mounts(mp_ctl, {ctl_id: {"redis_db": ds_entry["redis_db"], "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt}"}}, work_dir, register_collector=False)
        try:
            croot = Path(cmnt[0]["mount_directory"])
            fk_root = croot / "forks" / run_tag
            fk_root.mkdir(parents=True, exist_ok=True)

            def _clone_jfs(idx: int) -> dict:
                sub = f"/forks/{run_tag}/c{idx + 1:02d}"
                cl.run_on_host(
                    "A366",
                    [str(cl.BIN_DIR / "juicefs"), "--no-agent", "clone", "-p", str(croot / "corpus"), str(croot / sub.lstrip("/"))],
                    with_secrets=True,
                )
                return {"idx": idx + 1, "ws": f"c{idx + 1:02d}", "subdir": sub}

            with ThreadPoolExecutor(max_workers=16) as pool:
                child_info = list(pool.map(_clone_jfs, range(children)))
        finally:
            cl.stop_mounts(cmnt, work_dir, clear_collector=False, remove_cache=True)
            mp_ctl.unlink(missing_ok=True)

    elif system == "juicefs-overlay":
        def _clone_jov(idx: int) -> dict:
            br = f"{run_tag}-c{idx + 1:02d}".replace("_", "-")
            cl.run_on_host(
                "A366",
                [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--lazy", ds_entry["base_url"], br],
                with_secrets=True,
            )
            return {"idx": idx + 1, "ws": br, "branch": br}

        with ThreadPoolExecutor(max_workers=16) as pool:
            child_info = list(pool.map(_clone_jov, range(children)))

    fork_seconds = round(time.monotonic() - t_fk0, 6)

    # Build mounts.json for 16 fork-child mounts + 1 parent mount (peak_mounts=17) + cross-host verify mounts
    reqs: list[dict] = []
    m_specs: dict[str, dict] = {}
    v_specs: dict[str, dict] = {}

    # Parent mount on A366 slot 45 (coexists during fork-overlap window, peak_mounts=17)
    parent_mid = f"{run_tag}-parent"
    parent_vid = f"{run_tag}-parent-ver"
    if system == "brewfs-packed":
        reqs.append(make_req(parent_mid, "measure", system, "idcai-60001", 45, ds_entry["base_ws"], "none", f"{cond_id}-parent", bin_path, windows=["fork-overlap"]))
        reqs.append(make_req(parent_vid, "verify", system, "idcai-60525", 25, ds_entry["base_ws"], "none", f"{cond_id}-parent-ver", bin_path, cache_state="cross-host-cold", windows=["verify"]))
        m_specs[parent_mid] = {"workspace_uuid": ds_entry["base_ws"], "workspace_namespace": ds_entry["namespace"], "s3_bucket": bkt, "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]}
        v_specs[parent_vid] = {"workspace_uuid": ds_entry["base_ws"], "workspace_namespace": ds_entry["namespace"], "s3_bucket": bkt, "s3_endpoint": cl.RUSTFS_ENDPOINTS[1]}
    elif system == "juicefs":
        reqs.append(make_req(parent_mid, "measure", system, "idcai-60001", 45, "corpus-parent", "none", f"{cond_id}-parent", bin_path, windows=["fork-overlap"]))
        reqs.append(make_req(parent_vid, "verify", system, "idcai-60525", 25, "corpus-parent", "none", f"{cond_id}-parent-ver", bin_path, cache_state="cross-host-cold", windows=["verify"]))
        m_specs[parent_mid] = {"redis_db": ds_entry["redis_db"], "subdir": "/corpus", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt}"}
        v_specs[parent_vid] = {"redis_db": ds_entry["redis_db"], "subdir": "/corpus", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[1]}/{bkt}"}
    elif system == "juicefs-overlay":
        reqs.append(make_req(parent_mid, "measure", system, "idcai-60001", 45, "main", "none", f"{cond_id}-parent", bin_path, windows=["fork-overlap"]))
        reqs.append(make_req(parent_vid, "verify", system, "idcai-60525", 25, "main", "none", f"{cond_id}-parent-ver", bin_path, cache_state="cross-host-cold", windows=["verify"]))
        m_specs[parent_mid] = {"volume": bkt, "branch": "main", "subdir": "/corpus", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt}"}
        v_specs[parent_vid] = {"volume": bkt, "branch": "main", "subdir": "/corpus", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[1]}/{bkt}"}

    for idx, pl in enumerate(placements):
        ci = child_info[idx]
        c_num = ci["idx"]
        src_h = pl["host"]
        src_ssh = pl["ssh"]
        src_slot = int(pl["slot"])
        v_h = VERIFY_HOST_MAP[src_h]
        v_ssh = cl.HOST_BY_NAME[v_h]["ssh"]
        v_slot = src_slot
        mid = f"{run_tag}-c{c_num:02d}"
        vid = f"{run_tag}-c{c_num:02d}-ver"

        ep_m = cl.endpoint_for_slot(src_slot, HOST_IDX[src_h])
        ep_v = cl.endpoint_for_slot(v_slot, HOST_IDX[v_h])
        parent_ws_label = ds_entry.get("base_ws", "corpus")

        reqs.append(make_req(mid, "fork-child", system, src_ssh, src_slot, ci["ws"], parent_ws_label, f"{cond_id}-child", bin_path, windows=["fork-overlap"]))
        reqs.append(make_req(vid, "verify", system, v_ssh, v_slot, ci["ws"], parent_ws_label, f"{cond_id}-child-ver", bin_path, cache_state="cross-host-cold", windows=["verify"]))

        if system == "brewfs-packed":
            m_specs[mid] = {"workspace_uuid": ci["ws"], "workspace_namespace": ci["ns"], "s3_bucket": bkt, "s3_endpoint": ep_m}
            v_specs[vid] = {"workspace_uuid": ci["ws"], "workspace_namespace": ci["ns"], "s3_bucket": bkt, "s3_endpoint": ep_v}
        elif system == "juicefs":
            m_specs[mid] = {"redis_db": ds_entry["redis_db"], "subdir": ci["subdir"], "s3_bucket_url": f"{ep_m}/{bkt}"}
            v_specs[vid] = {"redis_db": ds_entry["redis_db"], "subdir": ci["subdir"], "s3_bucket_url": f"{ep_v}/{bkt}"}
        elif system == "juicefs-overlay":
            m_specs[mid] = {"volume": bkt, "branch": ci["branch"], "subdir": "/corpus", "s3_bucket_url": f"{ep_m}/{bkt}"}
            v_specs[vid] = {"volume": bkt, "branch": ci["branch"], "subdir": "/corpus", "s3_bucket_url": f"{ep_v}/{bkt}"}

    mp_path = work_dir / "mounts.json"
    cl.generate_mounts_json(reqs, mp_path)

    t_mnt0 = time.monotonic()
    started = batch_start_mounts(mp_path, m_specs, f"{run_tag}-m", register_collector=True)
    mount_wall_s = round(time.monotonic() - t_mnt0, 6)
    m_by_id = {m["id"]: m for m in started}

    stop_records: list[dict] = []
    child_results: list[dict] = []
    barrier_utc_ns = time.time_ns() + 3_000_000_000

    try:
        tasks_by_host: dict[str, list[dict]] = defaultdict(list)
        for idx, pl in enumerate(placements):
            c_num = idx + 1
            mid = f"{run_tag}-c{c_num:02d}"
            m_info = m_by_id[mid]
            hname = pl["host"]
            out_p = f"{cl.RUN_ROOT}/hosts/{hname}/c3-tasks/{mid}.json"
            st_p = f"{cl.RUN_ROOT}/hosts/{hname}/c3-tasks/{mid}.state"
            tasks_by_host[hname].append(
                {
                    "reader_id": f"c{c_num:02d}",
                    "host": hname,
                    "slot": int(pl["slot"]),
                    "engine": system,
                    "cgroup": m_info["cgroup"],
                    "metrics_port": m_info["metrics_port"],
                    "out_path": out_p,
                    "state_path": st_p,
                    "wait_upload": True,
                    "loader_argv": [
                        str(cl.BIN_DIR / "c3-loader"),
                        "fork-child",
                        "--root", m_info["mount_directory"],
                        "--output", out_p,
                        "--state-out", st_p,
                        "--files", str(files),
                        "--dirty-count", str(dirty_count),
                        "--workspace", str(c_num),
                        "--seed", str(seed),
                        "--start-unix-ns", str(barrier_utc_ns),
                    ],
                }
            )
        child_results = batch_c3_loader(tasks_by_host, run_tag)
        snap1 = cl.collector_mark_all(run_id_mark, "fork_done")
    finally:
        stop_records = batch_stop_mounts(started, f"{run_tag}-m", clear_collector=True, remove_cache=True)

    # Measure space delta in S3 across child upper buckets / shared bucket
    if system == "brewfs-packed":
        space_delta_bytes = 0
        for ci in child_info:
            cb_objs = list_all_objects_with_sizes(f"{bkt}-{ci['ws']}")
            space_delta_bytes += sum(cb_objs.values())
    else:
        cur_objs = list_all_objects_with_sizes(bkt)
        keep_set = set(ds_entry["keep_keys"])
        space_delta_bytes = sum(sz for k, sz in cur_objs.items() if k not in keep_set)

    # Cross-Host Verification of ALL 16 children + untouched parent!
    t_ver0 = time.monotonic()
    ver_mounts = batch_start_mounts(mp_path, v_specs, f"{run_tag}-v", register_collector=False)
    v_by_id = {m["id"]: m for m in ver_mounts}
    state_by_cid = {r["reader_id"]: r["state_text"] for r in child_results}

    try:
        def _verify_child(idx: int) -> dict:
            pl = placements[idx]
            c_num = idx + 1
            v_h = VERIFY_HOST_MAP[pl["host"]]
            vid = f"{run_tag}-c{c_num:02d}-ver"
            vm = v_by_id[vid]
            remote_state = f"{cl.RUN_ROOT}/hosts/{v_h}/c3-tasks/{vid}.state"
            remote_out = f"{cl.RUN_ROOT}/hosts/{v_h}/c3-tasks/{vid}-ver.json"
            local_st = work_dir / f"c{c_num:02d}.state"
            local_st.write_text(state_by_cid[f"c{c_num:02d}"], encoding="utf-8")
            cl.sync_file_to_host(v_h, local_st, remote_state)
            cl.run_on_host(v_h, ["rm", "-f", remote_out], log_cmd=False)
            vp = cl.run_on_host(
                v_h,
                [str(cl.BIN_DIR / "smallfiles-verify"), vm["mount_directory"], remote_state, remote_out, str(files), "4", str(c_num)],
                timeout=3600.0,
            )
            cl.run_on_host(v_h, ["rm", "-f", remote_state, remote_out], check=False, log_cmd=False)
            return json.loads(vp.stdout.decode("utf-8"))

        with ThreadPoolExecutor(max_workers=16) as pool:
            ver_children = list(pool.map(_verify_child, range(children)))

        # Also verify untouched parent on A355!
        p_vm = v_by_id[parent_vid]
        p_out = f"{cl.RUN_ROOT}/hosts/A355/{parent_vid}.json"
        cl.run_on_host("A355", ["rm", "-f", p_out], log_cmd=False)
        p_vp = cl.run_on_host("A355", [str(cl.BIN_DIR / "smallfiles-verify"), p_vm["mount_directory"], "base", p_out, str(files), "8", "1"], timeout=3600.0)
        cl.run_on_host("A355", ["rm", "-f", p_out], check=False, log_cmd=False)
        ver_parent = json.loads(p_vp.stdout.decode("utf-8"))
    finally:
        batch_stop_mounts(ver_mounts, f"{run_tag}-v", clear_collector=False, remove_cache=True)
    verify_s = round(time.monotonic() - t_ver0, 6)

    # Clean up the 16 child workspaces/objects so parent dataset stays clean
    if system == "brewfs-packed":
        for ci in child_info:
            cl.delete_bucket_and_objects(f"{bkt}-{ci['ws']}")
            cl.redis_delete_pattern_db0(f"*{ci['ns']}*")
    elif system == "juicefs":
        mp_cl = work_dir / "jfs-clean-mounts.json"
        cl_id = f"{run_tag}-clean"
        cl.generate_mounts_json([make_req(cl_id, "initial", "juicefs", "idcai-60001", 50, "root", "none", f"{run_tag}-clean", str(cl.BIN_DIR / "juicefs"))], mp_cl)
        cm = cl.start_mounts(mp_cl, {cl_id: {"redis_db": ds_entry["redis_db"], "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt}"}}, work_dir, register_collector=False)
        try:
            cl.run_on_host("A366", ["rm", "-rf", str(Path(cm[0]["mount_directory"]) / "forks" / run_tag)], log_cmd=False)
        finally:
            cl.stop_mounts(cm, work_dir, clear_collector=False, remove_cache=True)
            mp_cl.unlink(missing_ok=True)
        delete_extra_keys(bkt, set(ds_entry["keep_keys"]))
    elif system == "juicefs-overlay":
        delete_extra_keys(bkt, set(ds_entry["keep_keys"]))

    for r in child_results:
        r.pop("state_text", None)
    (work_dir / "children.json").write_text(json.dumps(child_results, indent=2) + "\n", encoding="utf-8")

    fr_secs = [float(r["loader"]["first_read_seconds"]) for r in child_results]
    fb_secs = [float(r["loader"]["first_batch_64_seconds"]) for r in child_results]
    pw_secs = [float(r["loader"]["private_write_seconds"]) for r in child_results]
    cw_secs = [float(r["loader"]["wall_seconds"]) for r in child_results]
    dr_secs = [float(r["upload_drain_seconds"]) for r in child_results]
    merged_all = cl.merge_log2_histograms([r["loader"]["histograms"]["all"] for r in child_results])

    total_read = sum(int(r["loader"]["actual_read_bytes"]) for r in child_results)
    total_write = sum(int(r["loader"]["actual_write_bytes"]) for r in child_results)
    mount_totals = sum_mount_metrics(stop_records)
    phase_delta = extract_s3_and_amp(cl.summarize_phase_delta(snap0, snap1), total_read, total_write, mount_totals)

    app_done_s = fork_seconds + mount_wall_s + max(cw_secs)
    rr_s = app_done_s + max(dr_secs)

    return {
        "schema": "ax-c3-run-result-v1",
        "group": "C3",
        "condition": cond_id,
        "family": "fork",
        "kind": cond["kind"],
        "system": system,
        "repetition": rep,
        "seed": seed,
        "parameters": params,
        "recorded_at": cl.utc_iso(),
        "timing": {
            "parent_prepare_seconds": ds_entry["prepare_seconds"],
            "fork_seconds": fork_seconds,
            "mount_wall_seconds": mount_wall_s,
            "first_read_mean_seconds": round(statistics.mean(fr_secs), 6),
            "private_write_mean_seconds": round(statistics.mean(pw_secs), 6),
            "upload_drain_max_seconds": round(max(dr_secs), 6),
            "app_done_seconds": round(app_done_s, 6),
            "remote_ready_no_verify_seconds": round(rr_s, 6),
            "verify_seconds": verify_s,
            "remote_ready_with_verify_seconds": round(rr_s + verify_s, 6),
        },
        "metrics": {
            "files": files,
            "children": children,
            "dirty_files_per_child": dirty_count,
            "first_batch_64_mean_seconds": round(statistics.mean(fb_secs), 6),
            "space_delta_bytes": space_delta_bytes,
            "actual_read_bytes": total_read,
            "actual_write_bytes": total_write,
            "s3_get_bytes": phase_delta["s3_get_bytes"],
            "s3_put_bytes": phase_delta["s3_put_bytes"],
            "metadata_requests": phase_delta["redis_commands_delta"],
            "latency_us": merged_all,
        },
        "phase_delta": phase_delta,
        "verification": {
            "passed": all(v.get("passed") for v in ver_children) and ver_parent.get("passed", False),
            "children_verified": len(ver_children),
            "parent_untouched_verified": ver_parent.get("passed", False),
        },
    }


# ==============================================================================
# 4. C3 Reuse (100k, 32 readers x 16 consecutive waves; prefixes 1, 4, 16)
# ==============================================================================
def run_c3_reuse(cond: dict, system: str, rep: int, seed: int, cohort_by_key: dict, work_dir: Path) -> dict:
    cond_id = cond["id"]
    params = cond["parameters"]
    waves = int(params.get("waves", 16))
    prefixes = [int(x) for x in params.get("report_prefixes", [1, 4, 16])]
    reg = load_registry()["datasets"]
    ds_entry = reg[f"100k/{system}/r{rep}"]
    placements = cohort_by_key[(32, rep)]["shared_tasks"]

    # Support checkpointing individual completed waves inside work_dir/waves_checkpoint.json!
    ckpt_file = work_dir / "waves_checkpoint.json"
    wave_summaries: list[dict] = []
    if ckpt_file.is_file():
        wave_summaries = json.loads(ckpt_file.read_text(encoding="utf-8"))

    for w_idx in range(len(wave_summaries) + 1, waves + 1):
        w_res = execute_single_load_wave(
            cond_id,
            system,
            rep,
            w_idx,
            placements,
            seed,
            ds_entry,
            {},
            work_dir,
            save_mounts_json=(w_idx == 1),
        )
        (work_dir/f'wave-{w_idx:02d}-readers.json').write_text(json.dumps(w_res.pop('readers',[]),indent=2)+'\n')
        wave_summaries.append(w_res)
        ckpt_file.write_text(json.dumps(wave_summaries, indent=2) + "\n", encoding="utf-8")
        print(
            f"    [{cond_id} {system} r{rep}] wave {w_idx}/{waves}: wave_s={w_res['wave_total_seconds']:.2f}s "
            f"s3_get={w_res['s3_get_bytes'] / (1024**2):.1f}MiB meta_reqs={w_res['metadata_requests']}",
            flush=True,
        )

    prep_s = float(ds_entry["prepare_seconds"])
    prefix_reports: dict[str, dict] = {}
    for p in prefixes:
        sub = wave_summaries[:p]
        load_sum_s = sum(float(x["wave_total_seconds"]) for x in sub)
        prefix_reports[str(p)] = {
            "waves": p,
            "prepare_seconds": prep_s,
            "cumulative_waves_seconds": round(load_sum_s, 6),
            "cumulative_with_prepare_seconds": round(prep_s + load_sum_s, 6),
            "cumulative_s3_get_bytes": sum(int(x["s3_get_bytes"]) for x in sub),
            "cumulative_metadata_requests": sum(int(x["metadata_requests"]) for x in sub),
        }

    merged_all = cl.merge_log2_histograms([x["latency_us"] for x in wave_summaries])
    total_waves_s = sum(float(x["wave_total_seconds"]) for x in wave_summaries)

    return {
        "schema": "ax-c3-run-result-v1",
        "group": "C3",
        "condition": cond_id,
        "family": "reuse",
        "kind": cond["kind"],
        "system": system,
        "repetition": rep,
        "seed": seed,
        "parameters": params,
        "recorded_at": cl.utc_iso(),
        "timing": {
            "prepare_seconds": prep_s,
            "app_done_seconds": round(sum(float(x["cohort_load_seconds"]) for x in wave_summaries), 6),
            "remote_ready_no_verify_seconds": round(total_waves_s, 6),
            "cumulative_with_prepare_1_seconds": prefix_reports["1"]["cumulative_with_prepare_seconds"],
            "cumulative_with_prepare_4_seconds": prefix_reports["4"]["cumulative_with_prepare_seconds"],
            "cumulative_with_prepare_16_seconds": prefix_reports["16"]["cumulative_with_prepare_seconds"],
            "verify_seconds": 0.0,
            "remote_ready_with_verify_seconds": round(total_waves_s, 6),
        },
        "metrics": {
            "agents": 32,
            "files": 100_000,
            "waves": waves,
            "prepare_seconds": prep_s,
            "cumulative_prefix_1_seconds": prefix_reports["1"]["cumulative_with_prepare_seconds"],
            "cumulative_prefix_4_seconds": prefix_reports["4"]["cumulative_with_prepare_seconds"],
            "cumulative_prefix_16_seconds": prefix_reports["16"]["cumulative_with_prepare_seconds"],
            "s3_get_bytes": sum(int(x["s3_get_bytes"]) for x in wave_summaries),
            "metadata_requests": sum(int(x["metadata_requests"]) for x in wave_summaries),
            "actual_read_bytes": sum(int(x["actual_read_bytes"]) for x in wave_summaries),
            "latency_us": merged_all,
        },
        "prefix_reports": prefix_reports,
        "waves": wave_summaries,
    }


def cleanup_all_c3_datasets():
    print("=== Cleaning up all C3 prepared datasets and local corpora ===", flush=True)
    cl.delete_buckets_with_prefix(f"{cl.RUN_PREFIX}-c3-")
    cl.redis_delete_pattern_db0("*axd1_c3_*")
    cl.redis_delete_pattern_db0("{jfs-fork:axd1-c3-*}:*")
    for db in range(4, 11):
        cl.redis_flush_db(db)
    shutil.rmtree(CORPUS_ROOT, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="all", help="Specific C3 condition ID or 'all'")
    ap.add_argument("--cleanup-after", action="store_true", help="Clean up C3 S3/Redis datasets after all 105 C3 runs complete")
    args = ap.parse_args()

    c3_plan = json.loads((cl.RUN_ROOT / "plan/C3.json").read_text(encoding="utf-8"))
    cond_by_id = {c["id"]: c for c in c3_plan["conditions"]}
    cohort_by_key = {(int(c["agents"]), int(c["repetition"])): c for c in c3_plan["cohorts"]}

    with cl.MeasurementLock():
        # Record pending-feature conditions cleanly
        for job in c3_plan["jobs"]:
            cid = job["condition"]
            if args.condition != "all" and cid != args.condition:
                continue
            cond = cond_by_id[cid]
            reqs = cond["parameters"].get("requires", [])
            if reqs:
                sys_name = job["system"]
                rep = int(job["repetition"])
                wdir, _, done = prepare_run_dir(cid, sys_name, rep)
                if not done:
                    pf_doc = {
                        "schema": "ax-c3-run-result-v1",
                        "group": "C3",
                        "condition": cid,
                        "family": cond["family"],
                        "kind": cond["kind"],
                        "system": sys_name,
                        "repetition": rep,
                        "status": "pending-feature",
                        "requires": reqs,
                        "parameters": cond["parameters"],
                        "reason": f"Required adapter(s) {reqs} are not implemented in current BrewFS/JuiceFS binaries (per docs/SETUP.md & docs/ABLATIONS.md).",
                        "recorded_at": cl.utc_iso(),
                    }
                    finalize_run_result(cid, sys_name, rep, wdir, pf_doc, state="pending-feature")
                    print(f"  [PENDING-FEATURE] {cid} {sys_name} r{rep} (requires={reqs})", flush=True)

        # Execute live jobs in plan order (prepare -> load -> fork -> reuse -> ablations)
        for job in c3_plan["jobs"]:
            cid = job["condition"]
            if args.condition != "all" and cid != args.condition:
                continue
            cond = cond_by_id[cid]
            if cond["parameters"].get("requires"):
                continue
            sys_name = job["system"]
            rep = int(job["repetition"])
            seed = int(job["seed"])
            wdir, attempt, done = prepare_run_dir(cid, sys_name, rep)
            if done:
                print(f"  [SKIP passed] {cid} {sys_name} r{rep}", flush=True)
                continue

            cl.update_status("C3", cid, sys_name, rep, "running", {"attempt": attempt})
            fam = cond["family"]
            print(f">>> Running {cid} ({fam}) | {sys_name} | r{rep} ...", flush=True)
            if fam == "prepare":
                res = run_c3_prepare(cond, sys_name, rep, seed, wdir)
            elif fam in {"load", "whole-pack-cache", "retained-payload", "metadata-cache"}:
                res = run_c3_load_or_ablation(cond, sys_name, rep, seed, cohort_by_key, wdir)
            elif fam == "fork":
                res = run_c3_fork(cond, sys_name, rep, seed, cohort_by_key, wdir)
            elif fam == "reuse":
                res = run_c3_reuse(cond, sys_name, rep, seed, cohort_by_key, wdir)
            else:
                raise ValueError(f"Unhandled C3 family {fam}")

            finalize_run_result(cid, sys_name, rep, wdir, res, state="passed")
            print(
                f"  [PASS] {cid} {sys_name} r{rep}: app_done={res['timing']['app_done_seconds']:.2f}s "
                f"rr={res['timing']['remote_ready_no_verify_seconds']:.2f}s",
                flush=True,
            )

        if args.cleanup_after and args.condition == "all":
            status_doc = json.loads((cl.RUN_ROOT / "status.json").read_text(encoding="utf-8"))
            c3_conds = status_doc["groups"]["C3"]["conditions"].values()
            if all(c["state"] in {"passed", "pending-feature"} for c in c3_conds):
                cleanup_all_c3_datasets()

    print("=== C3 RUNNER FINISHED ===", flush=True)


if __name__ == "__main__":
    main()
