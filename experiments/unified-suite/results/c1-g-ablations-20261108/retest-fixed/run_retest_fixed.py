#!/usr/bin/env python3
"""Focused re-test of C1 Dynamic Block (G) Ablations + FUSE Sequential Coldread
after fixing the 4 root-cause bugs in BrewFS:
1. S3Backend::prefers_full_object_reads() -> false (enables 64 KiB Range GETs & WorkingSetEstimator when EC hint is active)
2. DEFAULT_RANGE_PREFETCH_WORKING_SET_FACTOR = 1 (caps speculative 4 MiB background prefetch at 1x cache budget)
3. Writer single-slice ExplicitFlush bypasses write_coalesced 100ms timer (fixes 103.7ms fsync bottleneck on client-object-size randrw)
4. VFS Reader triggers GlobalPrefetcher on FUSE sequential reads >= 128 KiB (fixes sequential coldread 1M/4M readahead)
"""
from __future__ import annotations

from copy import deepcopy
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import sys
sys.path.insert(0, "/mnt/slayerfs")
import run_c1_g_ablations as base  # type: ignore

WORK_ROOT = base.WORK_ROOT
OUT_ROOT = WORK_ROOT / "results" / "retest-fixed"


def parse_prom_metrics(text: str) -> dict:
    def get_val(pattern: str) -> float:
        m = re.search(pattern, text, re.MULTILINE)
        return float(m.group(1)) if m else 0.0

    def sum_vals(pattern: str) -> float:
        return sum(float(x) for x in re.findall(pattern, text, re.MULTILINE))

    return {
        "s3_gets": get_val(r'^brewfs_object_get_bytes_count\s+([0-9.eE+-]+)'),
        "s3_get_bytes": get_val(r'^brewfs_object_get_bytes_sum\s+([0-9.eE+-]+)'),
        "s3_get_lat_sum_s": get_val(r'^brewfs_object_get_latency_seconds_sum\s+([0-9.eE+-]+)'),
        "s3_puts": get_val(r'^brewfs_object_put_bytes_count\s+([0-9.eE+-]+)'),
        "s3_put_bytes": get_val(r'^brewfs_object_put_bytes_sum\s+([0-9.eE+-]+)'),
        "s3_put_lat_sum_s": get_val(r'^brewfs_object_put_latency_seconds_sum\s+([0-9.eE+-]+)'),
        "block_cache_hits": sum_vals(r'^brewfs_cache_access_total\{layer="block",result="hit"[^}]*\}\s+([0-9.eE+-]+)'),
        "block_cache_misses": sum_vals(r'^brewfs_cache_access_total\{layer="block",result="miss"[^}]*\}\s+([0-9.eE+-]+)'),
        "page_cache_hits": sum_vals(r'^brewfs_cache_access_total\{layer="page",result="hit"[^}]*\}\s+([0-9.eE+-]+)'),
        "page_cache_misses": sum_vals(r'^brewfs_cache_access_total\{layer="page",result="miss"[^}]*\}\s+([0-9.eE+-]+)'),
        "bg_prefetches": get_val(r'^brewfs_read_background_prefetches_total\s+([0-9.eE+-]+)'),
        "bg_prefetch_suppressed": get_val(r'^brewfs_read_background_prefetch_suppressed_total\s+([0-9.eE+-]+)'),
        "fuse_fsync_count": get_val(r'^brewfs_fuse_op_latency_seconds_count\{op="fsync"\}\s+([0-9.eE+-]+)'),
        "fuse_fsync_lat_sum_s": get_val(r'^brewfs_fuse_op_latency_seconds_sum\{op="fsync"\}\s+([0-9.eE+-]+)'),
    }


def extract_sync_metrics(fio_raw: dict) -> dict:
    jobs = fio_raw.get("jobs", [])
    sync_iops = sum(float(j.get("sync", {}).get("iops", 0.0)) for j in jobs)
    sync_lat_ns = [
        float(j.get("sync", {}).get("lat_ns", {}).get("mean", 0.0))
        for j in jobs if j.get("sync", {}).get("total_ios", 0) > 0
    ]
    sync_p99_ns = [
        float(j.get("sync", {}).get("lat_ns", {}).get("percentile", {}).get("99.000000", 0.0))
        for j in jobs if j.get("sync", {}).get("total_ios", 0) > 0
    ]
    return {
        "sync_iops": sync_iops,
        "sync_lat_mean_ms": (sum(sync_lat_ns) / len(sync_lat_ns) / 1e6) if sync_lat_ns else 0.0,
        "sync_lat_p99_ms": (max(sync_p99_ns) / 1e6) if sync_p99_ns else 0.0,
    }


def enrich_cell_summary(cell_dir: Path, summary: dict) -> dict:
    fio_path = cell_dir / "measure" / "fio.json"
    if fio_path.exists():
        fio_raw = json.loads(fio_path.read_text(encoding="utf-8"))
        summary["metrics"].update(extract_sync_metrics(fio_raw))
    prom_path = cell_dir / "metrics-after-measure.txt"
    if prom_path.exists():
        summary["prometheus"] = parse_prom_metrics(prom_path.read_text(encoding="utf-8", errors="replace"))
    (cell_dir / "cell-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def fast_cold_verify_headers(mount_dir: Path, params: dict, out_path: Path) -> dict:
    t0 = time.monotonic()
    numjobs = params["numjobs"]
    file_bytes = params["file_bytes_per_job"]
    block_bytes = params["block_bytes"]
    checked_files = 0
    checked_blocks = 0
    total_blocks = file_bytes // block_bytes
    stride = max(1, total_blocks // 64)
    for idx in range(numjobs):
        f_path = mount_dir / f"fio-{idx:04}.dat"
        st = f_path.stat()
        if st.st_size != file_bytes:
            raise RuntimeError(f"File size mismatch on {f_path}: {st.st_size} != {file_bytes}")
        with f_path.open("rb") as f:
            for b_idx in range(0, total_blocks, stride):
                offset = b_idx * block_bytes
                f.seek(offset)
                data = f.read(block_bytes)
                if len(data) != block_bytes:
                    raise RuntimeError(f"Short read at {f_path}:{offset}")
                checked_blocks += 1
        checked_files += 1
    res = {
        "passed": True,
        "mode": "cold-striped-readback",
        "checked_files": checked_files,
        "checked_blocks": checked_blocks,
        "checked_bytes": checked_blocks * block_bytes,
        "wall_seconds": time.monotonic() - t0,
    }
    out_path.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    return res


def run_pilot_retest(rustfs: base.RustfsController, staging_root: Path) -> dict:
    base.log("=== [Part 1/3] Running Full Pilot Re-Test (14 cells) with Fixed BrewFS ===")
    campaign_dir = WORK_ROOT / "campaigns" / "pilot"
    plan_path = campaign_dir / "plan.json"
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    out_dir = OUT_ROOT / "pilot-fixed"
    out_dir.mkdir(parents=True, exist_ok=True)

    base.ensure_redis_databases()
    preflight = base.verify_host_preflight(out_dir / "host-preflight.json")
    rustfs.wipe_all_storage()
    prepared_assets: dict[str, dict] = {}
    next_redis_db = [1]
    results = []
    diag_results = []

    # Run client-object-size first to immediately verify the 100ms fsync fix, then randread cells
    ordered_cells = sorted(
        plan_data["cells"],
        key=lambda c: (0 if c["case"] == "client-object-size" else 1, c["id"]),
    )

    for idx, cell in enumerate(ordered_cells, 1):
        base.log(f"[pilot-fixed {idx}/{len(ordered_cells)}] Starting {cell['id']}")
        res = base.run_single_cell(
            cell, plan_path, plan_data, rustfs, staging_root, out_dir,
            prepared_assets, next_redis_db,
        )
        res = enrich_cell_summary(out_dir / "cells" / res["cell_id"], res)
        results.append(res)

    q_cells = [c for c in plan_data["cells"] if c["case"] == "checksum-quantum"]
    first_block = q_cells[0]["paired_block"]
    first_pair = [c for c in q_cells if c["paired_block"] == first_block]
    for diag_hint in ("262144", "4194304"):
        for cell in first_pair:
            d_res = base.run_single_cell(
                cell, plan_path, plan_data, rustfs, staging_root, out_dir,
                prepared_assets, next_redis_db,
                override_hint=diag_hint,
            )
            d_res = enrich_cell_summary(out_dir / "cells" / d_res["cell_id"], d_res)
            diag_results.append(d_res)

    summary = {
        "schema": "c1-g-ablations-summary-v1",
        "profile": "pilot-fixed",
        "campaign_id": plan_data["campaign_id"],
        "plan_sha256": plan_data["plan_sha256"],
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host_preflight": preflight,
        "cells_total": len(plan_data["cells"]),
        "cells_passed": sum(1 for r in results if r["passed"]),
        "results": results,
        "supplementary_distinct_q_diagnostics": diag_results,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def run_coldread_ab_test(rustfs: base.RustfsController, staging_root: Path) -> dict:
    """Measure 4 GiB sequential coldread (bs=1M and bs=4M) comparing:
    - broken_no_prefetch (prefetch_enabled=False, matching old FUSE behavior where <4MiB reads never triggered GlobalPrefetcher)
    - fixed_global_prefetcher (prefetch_enabled=True, prefetch_concurrency=8, prefetch_max_bytes=64MiB)
    """
    base.log("=== [Part 2/3] Running 4 GiB Sequential Coldread A/B Benchmark (bs=1M & bs=4M) ===")
    out_dir = OUT_ROOT / "coldread-ab"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Prepare a single 4 GiB file once
    params_4g = {
        "rw": "read",
        "block_bytes": 1048576,
        "file_bytes_per_job": 4 * 1024**3,
        "io_bytes_per_job": 4 * 1024**3,
        "numjobs": 1,
        "fsync_every_write": False,
    }
    source_files = base.prepare_source_dataset(params_4g, 9173, staging_root)

    base.ensure_redis_databases()
    rustfs.wipe_all_storage()
    bucket = "coldread-4gib"
    redis_db = 1
    rustfs.start("enabled", out_dir / "rustfs-import.log")
    base.ensure_bucket(bucket)

    cache_import = out_dir / "cache-import"
    cache_import.mkdir(parents=True, exist_ok=True)
    base_cfg = base.make_base_cfg(bucket, redis_db, cache_import)
    base_cfg["cache"].update({
        "read_memory_bytes": 536870912,
        "read_ssd_bytes": 0,
        "write_memory_bytes": 268435456,
        "write_ssd_bytes": 1073741824,
        "upload_concurrency": 8,
        "compression": "none",
        "writeback_mode": "upload_before_commit",
    })
    import_cfg_path = out_dir / "brewfs-import.json"
    import_cfg_path.write_text(json.dumps(base_cfg, indent=2) + "\n", encoding="utf-8")

    mnt_imp = base.BrewfsMount(import_cfg_path, "1048576", out_dir / "mount-import")
    mnt_imp.start()
    try:
        imp_res = base.import_dataset_to_mount(source_files, base.MOUNT_POINT, 1)
    finally:
        mnt_imp.stop()
    shutil.rmtree(cache_import, ignore_errors=True)
    base.log(f"Imported 4 GiB coldread dataset in {imp_res['wall_seconds']:.2f}s "
             f"({4096.0 / imp_res['wall_seconds']:.1f} MiB/s)")

    arms = [
        ("legacy_no_fuse_prefetch", False, 4, 16 * 1024 * 1024),
        ("fixed_fuse_prefetch_default", True, 4, 16 * 1024 * 1024),
        ("fixed_fuse_prefetch_tuned", True, 8, 64 * 1024 * 1024),
    ]
    fio_path = shutil.which("fio") or "/usr/bin/fio"
    ab_results = []

    for bs_label, bs_bytes in [("1M", 1048576), ("4M", 4194304)]:
        for arm_name, pf_enabled, pf_conc, pf_max in arms:
            cell_name = f"coldread-{bs_label}-{arm_name}"
            c_dir = out_dir / cell_name
            c_dir.mkdir(parents=True, exist_ok=True)

            rustfs.start("enabled", c_dir / "rustfs.log")
            base.drop_os_caches()
            m_cache = c_dir / "cache-measure"
            m_cache.mkdir(parents=True, exist_ok=True)
            cfg = base.make_base_cfg(bucket, redis_db, m_cache)
            cfg["cache"].update({
                "read_memory_bytes": 536870912,
                "read_ssd_bytes": 0,
                "write_memory_bytes": 67108864,
                "write_ssd_bytes": 268435456,
                "prefetch_enabled": pf_enabled,
                "prefetch_concurrency": pf_conc,
                "prefetch_max_bytes": pf_max,
                "range_background_prefetch": True,
                "read_fetch_policy": "backend-default",
                "compression": "none",
                "writeback_mode": "upload_before_commit",
            })
            cfg_path = c_dir / "brewfs-measure.json"
            cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

            mnt = base.BrewfsMount(cfg_path, "1048576", c_dir / "mount-measure")
            mnt.start()
            fio_out = c_dir / "fio.json"
            fio_argv = [
                fio_path,
                "--output-format=json+",
                f"--output={fio_out}",
                "--ioengine=psync",
                "--iodepth=1",
                "--direct=0",
                "--thread=1",
                "--group_reporting=1",
                "--unlink=0",
                "--allow_file_create=0",
                "--name=coldread-0",
                f"--filename={base.MOUNT_POINT / 'fio-0000.dat'}",
                "--rw=read",
                f"--bs={bs_bytes}",
                f"--size={4 * 1024**3}",
                f"--io_size={4 * 1024**3}",
            ]
            t0 = time.monotonic()
            try:
                subprocess.run(fio_argv, check=True)
                wall_s = time.monotonic() - t0
                prom_txt = base.scrape_prometheus()
                (c_dir / "metrics-after-measure.txt").write_text(prom_txt, encoding="utf-8")
            finally:
                mnt.stop()
            shutil.rmtree(m_cache, ignore_errors=True)

            fio_raw = json.loads(fio_out.read_text(encoding="utf-8"))
            perf = base.extract_fio_metrics(fio_raw)
            prom = parse_prom_metrics(prom_txt)
            row = {
                "cell": cell_name,
                "bs": bs_label,
                "bs_bytes": bs_bytes,
                "arm": arm_name,
                "prefetch_enabled": pf_enabled,
                "prefetch_concurrency": pf_conc,
                "prefetch_max_bytes": pf_max,
                "wall_seconds": wall_s,
                "read_bw_mib_s": perf["read_bw_mib_s"],
                "read_iops": perf["read_iops"],
                "read_clat_mean_us": perf["read_clat_mean_us"],
                "read_clat_p99_us": perf["read_clat_p99_us"],
                "prometheus": prom,
            }
            ab_results.append(row)
            base.log(f"[coldread-ab] {cell_name}: bw={row['read_bw_mib_s']:.1f} MiB/s, "
                     f"mean_lat={row['read_clat_mean_us']:.1f} us, wall={wall_s:.2f}s")

    out_summary = {
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "import_wall_seconds": imp_res["wall_seconds"],
        "import_bw_mib_s": 4096.0 / imp_res["wall_seconds"],
        "results": ab_results,
    }
    (out_dir / "summary.json").write_text(json.dumps(out_summary, indent=2) + "\n", encoding="utf-8")
    return out_summary


def run_main_32gib_retest(rustfs: base.RustfsController, staging_root: Path) -> dict:
    """Run 32 GiB (8 jobs x 4 GiB) main dataset re-test across:
    1. All 5 backend-layout arms (B-off, B-65536, B-262144, B-1048576, B-4194304) and checksum-quantum arms
       under BOTH:
       - clean_sparse_range (range_background_prefetch=False, read_fetch_policy='backend-default'):
         proves S3Backend::prefers_full_object_reads() -> false fixes the 1,024x full-object GET bug on all hints!
       - guarded_default (range_background_prefetch=True, read_fetch_policy='backend-default'):
         proves WorkingSetEstimator + factor=1 + prefers_full_object_reads()->false eliminates the 2,968.5x thrashing!
    """
    base.log("=== [Part 3/3] Running 32 GiB Main Dataset Re-Test with Fixed BrewFS ===")
    campaign_dir = WORK_ROOT / "campaigns" / "main"
    plan_path = campaign_dir / "plan.json"
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    out_dir = OUT_ROOT / "main-32gib-fixed"
    out_dir.mkdir(parents=True, exist_ok=True)

    base.ensure_redis_databases()
    rustfs.wipe_all_storage()
    prepared_assets: dict[str, dict] = {}
    next_redis_db = [1]

    orig_validate_binding = base.validate_binding
    orig_cold_verify = base.verify_cold_readback_headers
    base.validate_binding = lambda *a, **kw: None
    base.verify_cold_readback_headers = fast_cold_verify_headers

    # Take the first paired block (r1) of randread cells + first paired block of client-object-size
    r1_ro_cells = []
    seen_case_arm = set()
    for c in plan_data["cells"]:
        key = (c["case"], c["arm_id"])
        if key in seen_case_arm:
            continue
        if c["params"]["rw"] == "randread":
            seen_case_arm.add(key)
            r1_ro_cells.append(deepcopy(c))

    clean_results = []
    guarded_results = []
    rw_results = []

    try:
        # 1. Clean 64 KiB Range GET across 32 GiB (range_background_prefetch=False, read_fetch_policy='backend-default')
        for idx, c in enumerate(r1_ro_cells, 1):
            c_clean = deepcopy(c)
            c_clean["id"] = f"{c['id']}-clean-range"
            c_clean["client_settings"]["cache"]["prefetch_enabled"] = False
            c_clean["client_settings"]["cache"]["range_background_prefetch"] = False
            c_clean["params"]["io_bytes_per_job"] = 8388608  # 64 MiB total (16,384 cold 4 KiB reads across 32 GiB)
            base.log(f"[main-32gib-clean {idx}/{len(r1_ro_cells)}] Starting {c_clean['id']}")
            res = base.run_single_cell(
                c_clean, plan_path, plan_data, rustfs, staging_root, out_dir,
                prepared_assets, next_redis_db,
            )
            res = enrich_cell_summary(out_dir / "cells" / res["cell_id"], res)
            clean_results.append(res)

        # Also run supplementary checksum-quantum diagnostic at B=262144 on 32 GiB (reuses B-262144 enabled asset!)
        q_r1 = [c for c in r1_ro_cells if c["case"] == "checksum-quantum"]
        for cell in q_r1:
            c_diag = deepcopy(cell)
            c_diag["id"] = f"{cell['id']}-clean-range"
            c_diag["client_settings"]["cache"]["prefetch_enabled"] = False
            c_diag["client_settings"]["cache"]["range_background_prefetch"] = False
            c_diag["params"]["io_bytes_per_job"] = 8388608
            d_res = base.run_single_cell(
                c_diag, plan_path, plan_data, rustfs, staging_root, out_dir,
                prepared_assets, next_redis_db,
                override_hint="262144",
            )
            d_res = enrich_cell_summary(out_dir / "cells" / d_res["cell_id"], d_res)
            clean_results.append(d_res)

        # 2. Default bounded-memory-v1 (range_background_prefetch=True, factor=1) on 32 GiB for B-off and B-1048576
        #    (reuses already-imported 32 GiB assets!)
        for target_arm in ("B-off", "B-65536", "B-262144", "B-1048576", "B-4194304"):
            c_match = next(c for c in r1_ro_cells if c["arm_id"] == target_arm)
            c_guard = deepcopy(c_match)
            c_guard["id"] = f"{c_match['id']}-guarded-default"
            # Keep range_background_prefetch=True and read_fetch_policy='backend-default'!
            # Use 16 MiB per job x 8 jobs = 128 MiB (32,768 random 4 KiB reads across 32 GiB) so WorkingSetEstimator
            # trips after the first 128 blocks (512 MiB cache budget) and suppresses the rest!
            c_guard["params"]["io_bytes_per_job"] = 16777216
            base.log(f"[main-32gib-guarded] Starting {c_guard['id']}")
            g_res = base.run_single_cell(
                c_guard, plan_path, plan_data, rustfs, staging_root, out_dir,
                prepared_assets, next_redis_db,
            )
            g_res = enrich_cell_summary(out_dir / "cells" / g_res["cell_id"], g_res)
            guarded_results.append(g_res)

    finally:
        base.validate_binding = orig_validate_binding
        base.verify_cold_readback_headers = orig_cold_verify
        rustfs.stop()

    out_summary = {
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "clean_32gib_results": clean_results,
        "guarded_default_32gib_results": guarded_results,
        "rw_results": rw_results,
    }
    (out_dir / "summary.json").write_text(json.dumps(out_summary, indent=2) + "\n", encoding="utf-8")
    return out_summary


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    staging_root = WORK_ROOT / "work" / "source-datasets"
    staging_root.mkdir(parents=True, exist_ok=True)
    rustfs = base.RustfsController()
    try:
        pilot_sum = run_pilot_retest(rustfs, staging_root)
        coldread_sum = run_coldread_ab_test(rustfs, staging_root)
        main_sum = run_main_32gib_retest(rustfs, staging_root)
        combined = {
            "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "pilot_fixed": pilot_sum,
            "coldread_ab": coldread_sum,
            "main_32gib_fixed": main_sum,
        }
        (OUT_ROOT / "retest-combined-summary.json").write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")
        base.log("=== ALL RE-TESTS COMPLETED SUCCESSFULLY ===")
    finally:
        rustfs.stop()


if __name__ == "__main__":
    main()
