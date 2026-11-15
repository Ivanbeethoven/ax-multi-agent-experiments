"""fio workload execution and content checks."""
#!/usr/bin/env python3

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, "/srv/ax-cluster/direct/scripts")
import cluster_lib as cl
from c1_concurrent import ConcurrentPair

SYSTEM_TAGS = {"brewfs-native-overlay": "brew", "juicefs": "jfs", "juicefs-overlay": "jov"}
import rustfs_layout_tools as rlt

OPENER = build_opener(ProxyHandler({}))
REDIS_URL = f"redis://:{cl.SECRETS['redis_password']}@{cl.REDIS_HOST}:{cl.REDIS_PORT}/0"
SEEDS_BY_REP = {1: 2026111401, 2: 2026111402, 3: 2026111403}
WORKING_SET_BYTES = 32 * 1024**3
BYTES_PER_JOB = 4 * 1024**3
JOBS = 8

def fio_json_to_metrics(fio_doc: dict) -> dict:
    """Extracts exact ops, bytes, bandwidth, IOPS, and log2-us-v1 histogram across all fio jobs."""
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
            bins = clat.get("bins") or {}
            for ns_str, cnt in bins.items():
                ns = int(ns_str)
                cnt = int(cnt)
                us = ns // 1000
                b = 0 if us <= 0 else min(31, us.bit_length())
                buckets[b] += cnt

    # With group_reporting=1, fio reports exact combined percentiles in jobs[0]
    j0 = (fio_doc.get("jobs") or [{}])[0]
    merged_hist = cl.merge_log2_histograms([{"buckets": buckets, "max_us": max_us, "min_us": min_us or 0.0, "count": sum(buckets)}])

    # Also capture exact fio group_reporting percentiles for read and write
    fio_pct = {}
    for ddir in ("read", "write"):
        p_map = ((j0.get(ddir) or {}).get("clat_ns") or {}).get("percentile") or {}
        if p_map:
            fio_pct[ddir] = {
                "p50_us": round(float(p_map.get("50.000000", 0.0)) / 1000.0, 3),
                "p95_us": round(float(p_map.get("95.000000", 0.0)) / 1000.0, 3),
                "p99_us": round(float(p_map.get("99.000000", 0.0)) / 1000.0, 3),
            }
    # If pure read or pure write, use fio's high-resolution FIO group percentile for p50/p95/p99 while keeping 32-bucket array!
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
    }


def scrape_mount_prom(port: int, mount_dir: str = "", engine: str = "", host_name: str = "A366") -> dict[str, float]:
    raw = ""
    if host_name == "A366":
        try:
            with OPENER.open(Request(f"http://127.0.0.1:{port}/metrics"), timeout=2.0) as resp:
                raw = resp.read(8 * 1024 * 1024).decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        if mount_dir and engine.startswith("brewfs"):
            try:
                raw += "\n" + Path(mount_dir, ".stats").read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    else:
        py = (
            "import pathlib, urllib.request\n"
            f"raw = ''\n"
            f"try:\n"
            f"  raw = urllib.request.urlopen('http://127.0.0.1:{port}/metrics', timeout=2.0).read(8388608).decode('utf-8', 'replace')\n"
            f"except Exception: pass\n"
            f"if {bool(mount_dir and engine.startswith('brewfs'))!r}:\n"
            f"  try: raw += '\\n' + pathlib.Path({mount_dir!r}, '.stats').read_text('utf-8', 'replace')\n"
            f"  except OSError: pass\n"
            "print(raw)\n"
        )
        p = cl.run_on_host(host_name, ["/usr/bin/python3", "-c", py], check=False, log_cmd=False)
        raw = p.stdout.decode("utf-8", errors="replace")
    out: dict[str, float] = {}
    for ln in raw.splitlines():
        if not ln or ln.startswith("#"):
            continue
        toks = ln.split()
        if len(toks) >= 2:
            try:
                out[toks[0]] = float(toks[1])
            except ValueError:
                pass
    return out


def diff_prom(p0: dict[str, float], p1: dict[str, float]) -> dict[str, float]:
    out = {}
    for k, v1 in p1.items():
        if k.startswith(("brewfs_", "juicefs_", "forkredis_")):
            v0 = p0.get(k, 0.0)
            if v1 != v0:
                out[k] = round(v1 - v0, 6)
    return out


def list_all_keys(bucket: str) -> set[str]:
    s3 = cl.make_s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    keys: set[str] = set()
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def make_req(mid: str, phase: str, engine: str, host_ssh: str, slot: int, ws: str, parent_ws: str, purpose: str, binary: str, cache_state: str = "cold", overrides: dict | None = None):
    r = {
        "id": mid,
        "resource_profile": "fio",
        "phase": phase,
        "engine": engine,
        "host": host_ssh,
        "slot": slot,
        "workspace": ws,
        "parent_workspace": parent_ws,
        "purpose": purpose,
        "binary": binary,
        "cache_state": cache_state,
        "windows": [phase],
        "root": str(cl.RUN_ROOT),
    }
    if overrides:
        r["overrides"] = overrides
    return r


def prepare_run_dir(cond_id: str, system: str, rep: int) -> tuple[Path, int, bool]:
    """Returns (work_dir, attempt_num, already_passed). Preserves failed prior attempts in attempt-01."""
    with cl._STATUS_LOCK:
        status_doc = json.loads((cl.RUN_ROOT / "status.json").read_text(encoding="utf-8"))
    prev_state = (
        status_doc["groups"]["C1"]["conditions"]
        .get(cond_id, {})
        .get("runs", {})
        .get(f"{system}/r{rep}", {})
        .get("state", "planned")
    )
    base_dir = cl.RUN_ROOT / "C1" / cond_id / system / f"r{rep}"
    if prev_state == "passed" and (base_dir / "result.json").is_file():
        return base_dir, 1, True

    if base_dir.exists() and any(base_dir.iterdir()):
        # Preserve previous partial/failed files into attempt-XX
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


def finalize_run_result(cond_id: str, system: str, rep: int, work_dir: Path, result_obj: dict):
    out_file = work_dir / "result.json"
    out_file.write_text(json.dumps(result_obj, indent=2) + "\n", encoding="utf-8")
    base_dir = cl.RUN_ROOT / "C1" / cond_id / system / f"r{rep}"
    if work_dir != base_dir:
        (base_dir / "result.json").write_text(json.dumps(result_obj, indent=2) + "\n", encoding="utf-8")
    cl.update_status(
        "C1",
        cond_id,
        system,
        rep,
        "passed",
        {
            "app_done_seconds": result_obj["timing"]["app_done_seconds"],
            "remote_ready_seconds": result_obj["timing"]["remote_ready_no_verify_seconds"],
            "result_path": str((base_dir / "result.json").relative_to(cl.RUN_ROOT)),
            "execution_mode": result_obj.get('execution_mode', 'exclusive'),
            "shared_backend_group": result_obj.get('shared_backend_group'),
        },
    )


def enrich_amplification(delta: dict, read_bytes: int, write_bytes: int, mount_prom_delta: dict | None = None) -> dict:
    d = dict(delta)
    # Extract S3 GET / PUT payload bytes from client or server counters
    mp = mount_prom_delta or {}
    s3_get_b = 0.0
    s3_put_b = 0.0
    jfs_get = 0.0
    jfs_put = 0.0
    brew_get = 0.0
    brew_put = 0.0
    for k, v in mp.items():
        if k.startswith("juicefs_object_request_data_bytes") and 'method="GET"' in k:
            jfs_get += v
        elif k.startswith("juicefs_object_request_data_bytes") and 'method="PUT"' in k:
            jfs_put += v
        elif k in ("brewfs_s3_get_bytes_total", "brewfs_packed_runtime_backend_received_bytes_total") and "{" not in k:
            brew_get += v
        elif k == "brewfs_s3_put_bytes_total" and "{" not in k:
            brew_put += v
    s3_get_b = jfs_get or brew_get or float(d.get("rustfs_s3_get_bytes", 0))
    s3_put_b = jfs_put or brew_put or float(d.get("rustfs_s3_put_bytes", 0))
    d["s3_get_bytes"] = int(round(s3_get_b))
    d["s3_put_bytes"] = int(round(s3_put_b))
    d["client_reported_s3_get_bytes"] = int(round(jfs_get or brew_get)) if mp else None
    d["client_reported_s3_put_bytes"] = int(round(jfs_put or brew_put)) if mp else None
    d["s3_byte_source"] = 'client' if (jfs_get or brew_get or jfs_put or brew_put) else 'rustfs-s3'
    d["read_amplification_physical"] = round(d["backend_physical_read_bytes"] / read_bytes, 6) if read_bytes > 0 else None
    d["read_amplification_s3"] = round(s3_get_b / read_bytes, 6) if read_bytes > 0 else None
    d["write_amplification_physical"] = round(d["backend_physical_write_bytes"] / write_bytes, 6) if write_bytes > 0 else None
    d["write_amplification_s3"] = round(s3_put_b / write_bytes, 6) if write_bytes > 0 else None
    return d


# ==============================================================================
# Reusable Parent Workspace Managers (32 GiB I/O)
# ==============================================================================

class ParentPool:
    def __init__(self, nodes_meta: dict[str, dict], policy="on"):
        self.policy = policy
        self.nodes_meta = nodes_meta
        self.parents: dict[str, dict] = {}
        self.state_dir = cl.RUN_ROOT / "mounts" / ("io-parents-" + policy)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def ensure_io32g_parent(
        self,
        system: str,
        brewfs_hint: str = "auto",
        *,
        prep_host: str = "A366",
        prep_slot: int = 1,
        prep_ep: str | None = None,
    ) -> dict:
        key = f"{system}:{brewfs_hint}" if system == "brewfs-native-overlay" else system
        with self._lock:
            if key in self.parents:
                return self.parents[key]

        meta_file = self.state_dir / f"io32g-{key.replace(':', '-')}.json"
        keys_file = meta_file.with_name(meta_file.stem + "-keys.json")
        if meta_file.is_file():
            saved = json.loads(meta_file.read_text(encoding="utf-8"))
            if saved.get("input_placement") == "child-writable-layer":
                saved["parent_keys"] = set(json.loads(keys_file.read_text())) if keys_file.is_file() else set()
                with self._lock:
                    self.parents[key] = saved
                return saved
            existing = list_all_keys(saved["bucket"])
            if saved.get("input_placement") == "child-writable-layer" or len(existing) >= 100:
                if keys_file.is_file():
                    pkeys = set(json.loads(keys_file.read_text(encoding="utf-8")))
                else:
                    pkeys = existing
                saved["parent_keys"] = pkeys
                with self._lock:
                    self.parents[key] = saved
                return saved

        # Zero-cost reuse: if brewfs-native-overlay:auto already persisted actual_B == 1048576, alias 1048576 directly!
        if system == "brewfs-native-overlay" and brewfs_hint == "1048576":
            auto_meta = self.state_dir / "io32g-brewfs-native-overlay-auto.json"
            auto_keys = self.state_dir / "io32g-brewfs-native-overlay-auto-keys.json"
            if auto_meta.is_file():
                auto_saved = json.loads(auto_meta.read_text(encoding="utf-8"))
                act_b = (auto_saved.get("layout", {}).get("summary") or {}).get("actual_B")
                existing = list_all_keys(auto_saved["bucket"])
                if act_b == 1048576 and len(existing) >= 100:
                    if auto_keys.is_file():
                        pkeys = set(json.loads(auto_keys.read_text(encoding="utf-8")))
                    else:
                        pkeys = existing
                    aliased = dict(auto_saved)
                    aliased["hint"] = "1048576"
                    aliased["reused_from"] = "brewfs-native-overlay:auto"
                    meta_file.write_text(json.dumps(aliased, indent=2) + "\n", encoding="utf-8")
                    keys_file.write_text(json.dumps(sorted(pkeys)) + "\n", encoding="utf-8")
                    (cl.RUN_ROOT / "mounts" / f"c1-parent-layout-{key.replace(':', '-')}.json").write_text(
                        json.dumps(aliased["layout"], indent=2) + "\n", encoding="utf-8"
                    )
                    aliased["parent_keys"] = pkeys
                    with self._lock:
                        self.parents[key] = aliased
                    print(
                        f"  [REUSED] {key} -> {aliased['bucket']} ({len(pkeys)} objects, actual_B={act_b}, zero re-upload)",
                        flush=True,
                    )
                    return aliased

        ep = prep_ep or cl.RUSTFS_ENDPOINTS[0]
        prep_ssh = cl.HOST_BY_NAME[prep_host]["ssh"]
        print(f"=== Preparing reusable 32 GiB C1 parent for {key} on {prep_host} (slot {prep_slot}, {ep}) ===", flush=True)
        t0 = time.monotonic()
        if system == "brewfs-native-overlay":
            tag = brewfs_hint if brewfs_hint == "auto" else f"b{int(brewfs_hint)//1024}"
            bucket = f"{cl.RUN_PREFIX}-c1-{self.policy}-brew-{tag}"
            ns = f"{cl.RUN_NAMESPACE}_c1_{self.policy}_brew_{tag}"
            cl.delete_bucket_and_objects(bucket, endpoint=ep)
            cl.ensure_bucket(bucket, endpoint=ep)
            cl.redis_delete_pattern_db0(f"*{ns}*")

            init_rec = cl.extract_json_from_cli(
                cl.run_on_host("A366", [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns, "init-shared-volume"], with_secrets=True).stdout
            )
            base_ws = init_rec["workspace_id"]
            rev = init_rec["fork_base"]
            if not rev:
                raise RuntimeError("New workspace did not return an empty fork base")
            layout = {"summary": {}}
            pkeys = set()
            info = {
                "system": system,
                "hint": brewfs_hint,
                "bucket": bucket,
                "namespace": ns,
                "base_ws": base_ws,
                "revision": rev,
                "input_placement": "child-writable-layer",
                "prepare_seconds": round(time.monotonic() - t0, 6),
                "object_count": len(pkeys),
                "layout": layout,
            }

        elif system == "juicefs":
            bucket = f"{cl.RUN_PREFIX}-c1-{self.policy}-jfs"
            redis_db = 1
            cl.delete_bucket_and_objects(bucket, endpoint=ep)
            cl.ensure_bucket(bucket, endpoint=ep)
            cl.redis_flush_db(redis_db)
            cl.run_on_host(
                "A366",
                [
                    str(cl.BIN_DIR / "juicefs"), "--no-agent", "format",
                    "--storage=s3", f"--bucket={ep}/{bucket}",
                    "--block-size=4096", "--compress=none", "--trash-days=0",
                    f"redis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/{redis_db}", bucket,
                ],
                with_secrets=True,
            )
            mp_cfg = self.state_dir / "prep-jfs-mounts.json"
            mid = "c1-prep-jfs"
            cl.generate_mounts_json([make_req(mid, "initial", "juicefs", prep_ssh, prep_slot, "root", "none", "c1-prep-32g", str(cl.BIN_DIR / "juicefs"))], mp_cfg)
            m = cl.start_mounts(mp_cfg, {mid: {"redis_db": redis_db, "s3_bucket_url": f"{ep}/{bucket}"}}, self.state_dir, register_collector=False)
            try:
                rdir = Path(m[0]["mount_directory"])
                cl.run_on_host(prep_host, ["mkdir", "-p", str(rdir / "bases" / "io32g"), str(rdir / "workspaces")])
                cl.run_on_host(prep_host, [str(cl.BIN_DIR / "c1-io-helper"), "init-files", "--root", str(rdir / "bases" / "io32g"), "--jobs", "8", "--bytes-per-job", str(BYTES_PER_JOB)], timeout=7200)
                cl.wait_uploads(m, timeout=3600)
            finally:
                cl.stop_mounts(m, self.state_dir, clear_collector=False, remove_cache=True)

            layout = rlt.inspect_bucket_layout(self.nodes_meta, bucket)
            pkeys = list_all_keys(bucket)
            info = {
                "system": system,
                "hint": "product-default",
                "bucket": bucket,
                "redis_db": redis_db,
                "base_subdir": "/bases/io32g",
                "prepare_seconds": round(time.monotonic() - t0, 6),
                "object_count": len(pkeys),
                "layout": layout,
            }

        elif system == "juicefs-overlay":
            bucket = f"{cl.RUN_PREFIX}-c1-{self.policy}-jov"
            vol = f"{cl.RUN_PREFIX}-c1-{self.policy}-jov"
            cl.delete_bucket_and_objects(bucket, endpoint=ep)
            cl.ensure_bucket(bucket, endpoint=ep)
            cl.redis_delete_pattern_db0(f"{{jfs-fork:{vol}}}:*")
            main_url = f"forkredis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/0?volume={vol}&branch=main"
            cl.run_on_host(
                "A366",
                [
                    str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "format",
                    "--storage=s3", f"--bucket={ep}/{bucket}",
                    "--block-size=4096", "--compress=none", "--trash-days=0",
                    main_url, vol,
                ],
                with_secrets=True,
            )
            mp_cfg = self.state_dir / "prep-jov-mounts.json"
            mid = "c1-prep-jov"
            cl.generate_mounts_json([make_req(mid, "initial", "juicefs-overlay", prep_ssh, prep_slot, "main", "none", "c1-prep-32g", str(cl.BIN_DIR / "juicefs-overlay"))], mp_cfg)
            m = cl.start_mounts(mp_cfg, {mid: {"volume": vol, "branch": "main", "s3_bucket_url": f"{ep}/{bucket}"}}, self.state_dir, register_collector=False)
            try:
                cl.run_on_host(prep_host, [str(cl.BIN_DIR / "c1-io-helper"), "init-files", "--root", m[0]["mount_directory"], "--jobs", "8", "--bytes-per-job", str(BYTES_PER_JOB)], timeout=7200)
                cl.wait_uploads(m, timeout=3600)
            finally:
                cl.stop_mounts(m, self.state_dir, clear_collector=False, remove_cache=True)

            cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--seal", main_url], with_secrets=True)
            layout = rlt.inspect_bucket_layout(self.nodes_meta, bucket)
            pkeys = list_all_keys(bucket)
            info = {
                "system": system,
                "hint": "product-default",
                "bucket": bucket,
                "volume": vol,
                "main_url": main_url,
                "prepare_seconds": round(time.monotonic() - t0, 6),
                "object_count": len(pkeys),
                "layout": layout,
            }
        else:
            raise ValueError(f"Unsupported system {system}")

        save_copy = dict(info)
        meta_file.write_text(json.dumps(save_copy, indent=2) + "\n", encoding="utf-8")
        keys_file.write_text(json.dumps(sorted(pkeys)) + "\n", encoding="utf-8")
        (cl.RUN_ROOT / "mounts" / f"c1-parent-layout-{key.replace(':', '-')}.json").write_text(
            json.dumps(info["layout"], indent=2) + "\n", encoding="utf-8"
        )
        info["parent_keys"] = pkeys
        with self._lock:
            self.parents[key] = info
        print(f"  [READY] {key}: {len(pkeys)} objects in {info['prepare_seconds']:.1f}s (actual_B={info['layout']['summary'].get('actual_B')}, actual_q={info['layout']['summary'].get('actual_q')})", flush=True)
        return info

    def drop_io32g_parent(self, system: str, brewfs_hint: str):
        key = f"{system}:{brewfs_hint}" if system == "brewfs-native-overlay" else system
        meta_file = self.state_dir / f"io32g-{key.replace(':', '-')}.json"
        keys_file = meta_file.with_name(meta_file.stem + "-keys.json")
        with self._lock:
            info = self.parents.pop(key, None)
            if info is None and meta_file.is_file():
                info = json.loads(meta_file.read_text(encoding="utf-8"))
            still_shared = bool(info) and any(v.get("bucket") == info.get("bucket") for v in self.parents.values())
        archive=self.state_dir/'retired'
        archive.mkdir(exist_ok=True)
        for source in (meta_file,keys_file):
            if source.exists():
                index=1
                target=archive/f'{source.stem}-{index:02d}.json'
                while target.exists():
                    index+=1;target=archive/f'{source.stem}-{index:02d}.json'
                source.replace(target)
        if info and not still_shared:
            cl.delete_bucket_and_objects(info["bucket"])
            if "namespace" in info:
                cl.redis_delete_pattern_db0(f"*{info['namespace']}*")

    def fork_child(
        self,
        parent: dict,
        tag: str,
        work_dir: Path,
        *,
        measure_ep: str | None = None,
        verify_ep: str | None = None,
        adm_slot: int = 50,
    ) -> tuple[str, dict, dict, float]:
        """Creates a fresh child workspace from `parent` and returns (child_id, measure_spec, verify_spec, fork_seconds)."""
        sys_name = parent["system"]
        m_ep = measure_ep or cl.RUSTFS_ENDPOINTS[0]
        v_ep = verify_ep or cl.RUSTFS_ENDPOINTS[1]
        t0 = time.monotonic()
        if sys_name == "brewfs-native-overlay":
            fork_rec = cl.extract_json_from_cli(
                cl.run_on_host(
                    "A366",
                    [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", parent["namespace"], "fork-shared-bucket", "--revision", parent["revision"], "--count", "1"],
                    with_secrets=True,
                ).stdout
            )
            child_ws = fork_rec["workspaces"][0]
            fork_s = time.monotonic() - t0
            m_spec = {
                "workspace_uuid": child_ws,
                "workspace_namespace": parent["namespace"],
                "s3_bucket": parent["bucket"],
                "s3_endpoint": m_ep,
                "rustfs_ec_block_size_hint": parent["hint"],
            }
            v_spec = {
                "workspace_uuid": child_ws,
                "workspace_namespace": parent["namespace"],
                "s3_bucket": parent["bucket"],
                "s3_endpoint": v_ep,
                "rustfs_ec_block_size_hint": parent["hint"],
            }
            prep_id = f"{tag}-input"
            prep_path = work_dir / "input-mounts.json"
            cl.generate_mounts_json([make_req(prep_id, "initial", sys_name, "idcai-60001", adm_slot,
                                              child_ws, "empty", "input", str(cl.BIN_DIR / "brewfs"))], prep_path)
            mounted = cl.start_mounts(prep_path, {prep_id: m_spec}, work_dir, register_collector=False)
            before = time.monotonic()
            try:
                cl.run_on_host("A366", [str(cl.BIN_DIR / "c1-io-helper"), "init-files",
                    "--root", mounted[0]["mount_directory"], "--jobs", str(JOBS),
                    "--bytes-per-job", str(BYTES_PER_JOB)], timeout=7200)
                cl.wait_uploads(mounted)
            finally:
                cl.stop_mounts(mounted, work_dir, clear_collector=False, remove_cache=True)
            parent["layout"] = rlt.inspect_bucket_layout(self.nodes_meta, parent["bucket"])
            (work_dir / "input.json").write_text(json.dumps({"placement":"child-writable-layer",
                "workspace":child_ws,"bytes":JOBS * BYTES_PER_JOB,"seconds":time.monotonic()-before,
                "layout":parent["layout"]}, indent=2)+"\n", encoding="utf-8")
            return child_ws, m_spec, v_spec, fork_s

        if sys_name == "juicefs":
            adm_mp = work_dir / "jfs-clone-mounts.json"
            adm_id = f"{tag}-adm"
            cl.generate_mounts_json([make_req(adm_id, "initial", "juicefs", "idcai-60001", adm_slot, "root", "none", "jfs-clone", str(cl.BIN_DIR / "juicefs"))], adm_mp)
            m_adm = cl.start_mounts(adm_mp, {adm_id: {"redis_db": parent["redis_db"], "s3_bucket_url": f"{m_ep}/{parent['bucket']}"}}, work_dir, register_collector=False)
            try:
                rdir = Path(m_adm[0]["mount_directory"])
                dst_rel = f"workspaces/{tag}"
                if (rdir / dst_rel).exists():
                    shutil.rmtree(rdir / dst_rel)
                t_c0 = time.monotonic()
                cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs"), "--no-agent", "clone", "-p", str(rdir / parent["base_subdir"].lstrip("/")), str(rdir / dst_rel)], with_secrets=True)
                fork_s = time.monotonic() - t_c0
            finally:
                cl.stop_mounts(m_adm, work_dir, clear_collector=False, remove_cache=True)
            m_spec = {"redis_db": parent["redis_db"], "subdir": f"/workspaces/{tag}", "s3_bucket_url": f"{m_ep}/{parent['bucket']}"}
            v_spec = {"redis_db": parent["redis_db"], "subdir": f"/workspaces/{tag}", "s3_bucket_url": f"{v_ep}/{parent['bucket']}"}
            return tag, m_spec, v_spec, fork_s

        if sys_name == "juicefs-overlay":
            t_c0 = time.monotonic()
            cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--lazy", parent["main_url"], tag], with_secrets=True)
            fork_s = time.monotonic() - t_c0
            m_spec = {"volume": parent["volume"], "branch": tag, "s3_bucket_url": f"{m_ep}/{parent['bucket']}"}
            v_spec = {"volume": parent["volume"], "branch": tag, "s3_bucket_url": f"{v_ep}/{parent['bucket']}"}
            return tag, m_spec, v_spec, fork_s

        raise ValueError(sys_name)

# ==============================================================================
# FIO Workload Runner (fio, block-random/seq, reference-random/seq)
# ==============================================================================

def build_fio_jobfile(cond: dict, mount_dir: str, seed: int) -> tuple[str, bool, str, int]:
    """Returns (jobfile_text, is_pure_read, verify_mode, seq_written_per_job)."""
    fam = cond["family"]
    params = cond["parameters"]

    lines = [
        "[global]",
        "ioengine=psync",
        "iodepth=1",
        "direct=1",
        "do_verify=0",
        f"size={BYTES_PER_JOB}",
        "group_reporting=1",
        "randrepeat=1",
        "norandommap=0",
    ]

    is_pure_read = False
    verify_mode = "read"
    seq_written_per_job = 0

    if fam == "fio":
        prof = params["profile"]
        if prof == "seqread-1m":
            lines += ["rw=read", "bs=1024k", f"io_size={8589934592 // JOBS}"]
            is_pure_read = True
        elif prof == "seqwrite-1m":
            per_job = 8589934592 // JOBS
            lines += ["rw=write", "bs=1024k", f"io_size={per_job}", "end_fsync=1", "buffer_pattern=0x5a3c96e1"]
            verify_mode = "seqwrite"
            seq_written_per_job = per_job
        elif prof == "randread-4k":
            lines += ["rw=randread", "bs=4k", f"number_ios={65536 // JOBS}"]
            is_pure_read = True
        elif prof == "randwrite-sync-4k":
            lines += ["rw=randwrite", "bs=4k", f"number_ios={4096 // JOBS}", "fsync=1", "end_fsync=1", "buffer_pattern=0x5a3c96e1"]
            verify_mode = "randwrite"
        elif prof == "randrw-70r-64k":
            lines += ["rw=randrw", "rwmixread=70", "bs=64k", f"number_ios={16384 // JOBS}", "fsync=128", "end_fsync=1", "buffer_pattern=0x5a3c96e1"]
            verify_mode = "randwrite"
        else:
            raise ValueError(f"Unknown fio profile {prof}")

    elif fam in {"block-random", "reference-random"}:
        op = params["operation"]
        rk = int(params["request_kib"])
        per_job = int(params.get("io_bytes", 268435456)) // JOBS  # 32 MiB/job = 256 MiB total
        if op == "read":
            lines += ["rw=randread", f"bs={rk}k", f"io_size={per_job}"]
            is_pure_read = True
        else:
            fsync_n = max(1, (8 * 1024) // rk)  # 8 MiB/job = 64 MiB combined across 8 jobs
            lines += ["rw=randwrite", f"bs={rk}k", f"io_size={per_job}", f"fsync={fsync_n}", "end_fsync=1", "buffer_pattern=0x5a3c96e1"]
            verify_mode = "randwrite"

    elif fam in {"block-sequential", "reference-sequential"}:
        op = params["operation"]
        per_job = int(params.get("io_bytes", 8589934592)) // JOBS  # 1 GiB/job = 8 GiB total
        if op == "read":
            lines += ["rw=read", "bs=1024k", f"io_size={per_job}"]
            is_pure_read = True
        else:
            lines += ["rw=write", "bs=1024k", f"io_size={per_job}", "fsync=8", "end_fsync=1", "buffer_pattern=0x5a3c96e1"]
            verify_mode = "seqwrite"
            seq_written_per_job = per_job
    else:
        raise ValueError(f"Unexpected family {fam}")

    for j in range(JOBS):
        lines += [
            f"[job{j}]",
            f"filename={mount_dir}/job-{j}.dat",
            f"randseed={seed + j * 1009}",
        ]
    return "\n".join(lines) + "\n", is_pure_read, verify_mode, seq_written_per_job


def execute_fio_family_run(
    cond: dict,
    system: str,
    rep: int,
    pool: ParentPool,
    brewfs_hint: str = "auto",
    *,
    lane: int = 0,
    measure_ep: str | None = None,
    verify_ep: str | None = None,
    hold_lock: bool = True,
    parallel_mode: bool = False,
    pair: ConcurrentPair | None = None,
    output: Path | None = None,
    seed_override: int | None = None,
):
    cond_id = cond["id"]
    work_dir, attempt, already_done = (output, int(output.name.removeprefix("attempt-")), False) if output else prepare_run_dir(cond_id, system, rep)
    if already_done:
        return

    if lane == 0:
        measure_host, measure_ssh, m_slot = "A366", "idcai-60001", 1
        verify_host, verify_ssh, v_slot = "A355", "idcai-60525", 21
        adm_slot = 50
        m_ep = measure_ep or cl.RUSTFS_ENDPOINTS[0]
        v_ep = verify_ep or cl.RUSTFS_ENDPOINTS[1]
    else:
        measure_host, measure_ssh, m_slot = "A355", "idcai-60525", 2
        verify_host, verify_ssh, v_slot = "A366", "idcai-60001", 22
        adm_slot = 49
        m_ep = measure_ep or cl.RUSTFS_ENDPOINTS[2]
        v_ep = verify_ep or cl.RUSTFS_ENDPOINTS[3]

    seed = seed_override or SEEDS_BY_REP[rep]
    parent = pool.ensure_io32g_parent(system, brewfs_hint)
    overrides = {}
    if cond["parameters"].get("prefetch") == "off":
        overrides["prefetch"] = "off"

    tag = f"{cond_id.lower()}-{SYSTEM_TAGS[system]}-r{rep}-a{attempt}".replace("_", "-")
    if output is None:
        cl.update_status("C1", cond_id, system, rep, "running", {"attempt": attempt, "lane": lane, "measure_host": measure_host,
                         "execution_mode": 'shared-backend-concurrent' if pair else 'exclusive',
                         "shared_backend_group": str(pair.directory/'result.json') if pair else None})
    started_at = cl.utc_iso()

    lock_ctx = cl.MeasurementLock() if hold_lock else contextlib.nullcontext()
    with lock_ctx:
        child_ws, m_spec, v_spec, fork_s = pool.fork_child(parent, tag, work_dir, measure_ep=m_ep, verify_ep=v_ep, adm_slot=adm_slot)
        mp_path = work_dir / "mounts.json"
        m_id = f"{tag}-m"
        v_id = f"{tag}-v"
        bin_path = str(cl.BIN_DIR / ("brewfs" if system.startswith("brewfs") else system))
        cl.generate_mounts_json(
            [
                make_req(m_id, "measure", system, measure_ssh, m_slot, child_ws, "io32g", cond_id, bin_path, overrides=overrides),
                make_req(v_id, "verify", system, verify_ssh, v_slot, child_ws, "io32g", f"{cond_id}-verify", bin_path, cache_state="cross-host-cold", overrides=overrides),
            ],
            mp_path,
        )

        job_txt_check, is_pure_read, verify_mode, seq_written = build_fio_jobfile(cond, "/tmp", seed)
        if is_pure_read:
            rlt.evict_bucket_page_cache(pool.nodes_meta, [parent["bucket"]])

        m_started = cl.start_mounts(mp_path, {m_id: m_spec}, work_dir, register_collector=False)
        m_info = m_started[0]
        cl.collector_set_mounts(
            {
                measure_host: [
                    {
                        "id": m_info["id"],
                        "slot": m_info["slot"],
                        "engine": m_info["engine"],
                        "pid": m_info["pid"],
                        "cgroup": m_info["cgroup"],
                        "metrics_port": m_info["metrics_port"],
                        "mount_directory": m_info["mount_directory"],
                    }
                ]
            },
            hosts=[measure_host],
        )
        job_txt, _, _, _ = build_fio_jobfile(cond, m_info["mount_directory"], seed)
        job_file = work_dir / "workload.fio"
        job_file.write_text(job_txt, encoding="utf-8")
        if measure_host != "A366":
            cl.sync_file_to_host(measure_host, job_file, str(job_file))

        hot_seconds = None
        hot_metrics = None
        hot_delta = None
        upload_drain_s = 0.0
        stop_info = {}

        try:
            if is_pure_read:
                # Pass 1: Cold
                fio_cold_out = work_dir / "fio-cold.json"
                prom_c0 = scrape_mount_prom(m_info["metrics_port"], m_info["mount_directory"], system, host_name=measure_host)
                snap_c0 = pair.mark('cold_start') if pair else cl.collector_mark_all(f"{cond_id}/{system}/r{rep}", "cold_start")
                t_c0 = time.monotonic()
                cl.run_on_host(
                    measure_host,
                    [
                        "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", m_info["cgroup"], "--",
                        "fio", "--output-format=json+", f"--output={fio_cold_out}", str(job_file),
                    ],
                    timeout=7200,
                    log_path=str(fio_cold_out),
                )
                app_done_s = time.monotonic() - t_c0
                snap_c1 = pair.mark('cold_done') if pair else cl.collector_mark_all(f"{cond_id}/{system}/r{rep}", "cold_done")
                prom_c1 = scrape_mount_prom(m_info["metrics_port"], m_info["mount_directory"], system, host_name=measure_host)

                # Pass 2: Same-mount Hot
                fio_hot_out = work_dir / "fio-hot.json"
                prom_h0 = prom_c1
                snap_h0 = pair.mark('hot_start') if pair else cl.collector_mark_all(f"{cond_id}/{system}/r{rep}", "hot_start")
                t_h0 = time.monotonic()
                cl.run_on_host(
                    measure_host,
                    [
                        "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", m_info["cgroup"], "--",
                        "fio", "--output-format=json+", f"--output={fio_hot_out}", str(job_file),
                    ],
                    timeout=7200,
                    log_path=str(fio_hot_out),
                )
                hot_seconds = time.monotonic() - t_h0
                snap_h1 = pair.mark('hot_done') if pair else cl.collector_mark_all(f"{cond_id}/{system}/r{rep}", "hot_done")
                prom_h1 = scrape_mount_prom(m_info["metrics_port"], m_info["mount_directory"], system, host_name=measure_host)

                if measure_host != "A366":
                    fio_cold_out.write_bytes(cl.run_on_host(measure_host, ["cat", str(fio_cold_out)], log_cmd=False).stdout)
                    fio_hot_out.write_bytes(cl.run_on_host(measure_host, ["cat", str(fio_hot_out)], log_cmd=False).stdout)

                app_metrics = fio_json_to_metrics(json.loads(fio_cold_out.read_text(encoding="utf-8")))
                hot_metrics = fio_json_to_metrics(json.loads(fio_hot_out.read_text(encoding="utf-8")))
                mount_prom_delta = diff_prom(prom_c0, prom_c1)
                hot_prom_delta = diff_prom(prom_h0, prom_h1)
                backend_delta = None if pair else enrich_amplification(cl.summarize_phase_delta(snap_c0, snap_c1), app_metrics["read_bytes"], app_metrics["write_bytes"], mount_prom_delta)
                hot_delta = None if pair else enrich_amplification(cl.summarize_phase_delta(snap_h0, snap_h1), hot_metrics["read_bytes"], hot_metrics["write_bytes"], hot_prom_delta)
            else:
                # Write / Mixed workload
                fio_out = work_dir / "fio.json"
                prom_0 = scrape_mount_prom(m_info["metrics_port"], m_info["mount_directory"], system, host_name=measure_host)
                snap_0 = pair.mark('write_start') if pair else cl.collector_mark_all(f"{cond_id}/{system}/r{rep}", "write_start")
                t_w0 = time.monotonic()
                cl.run_on_host(
                    measure_host,
                    [
                        "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", m_info["cgroup"], "--",
                        "fio", "--output-format=json+", f"--output={fio_out}", str(job_file),
                    ],
                    timeout=7200,
                    log_path=str(fio_out),
                )
                app_done_s = time.monotonic() - t_w0
                pair.mark('app_done') if pair else cl.collector_mark_all(f"{cond_id}/{system}/r{rep}", "app_done")
                up_res = cl.wait_uploads(m_started, timeout=3600)[0]
                upload_drain_s = float(up_res["upload_drain_seconds"])
                snap_1 = pair.mark('upload_done') if pair else cl.collector_mark_all(f"{cond_id}/{system}/r{rep}", "upload_done")
                prom_1 = scrape_mount_prom(m_info["metrics_port"], m_info["mount_directory"], system, host_name=measure_host)

                if measure_host != "A366":
                    fio_out.write_bytes(cl.run_on_host(measure_host, ["cat", str(fio_out)], log_cmd=False).stdout)

                app_metrics = fio_json_to_metrics(json.loads(fio_out.read_text(encoding="utf-8")))
                mount_prom_delta = diff_prom(prom_0, prom_1)
                backend_delta = None if pair else enrich_amplification(cl.summarize_phase_delta(snap_0, snap_1), app_metrics["read_bytes"], app_metrics["write_bytes"], mount_prom_delta)
        except BaseException:
            if pair:
                pair.abort()
            raise
        finally:
            if pair:
                try:
                    pair.wait('measurement-finished')
                except threading.BrokenBarrierError:
                    pass
            cl.collector_set_mounts({measure_host: []}, hosts=[measure_host])
            stop_list = cl.stop_mounts(m_started, work_dir, clear_collector=False, remove_cache=True)
            stop_info = stop_list[0] if stop_list else {}

        # Cross-host cold remount verification on verify_host
        t_v0 = time.monotonic()
        v_started = cl.start_mounts(mp_path, {v_id: v_spec}, work_dir, register_collector=False)
        try:
            v_cmd = [
                str(cl.BIN_DIR / "c1-io-helper"), "verify-files",
                "--root", v_started[0]["mount_directory"],
                "--jobs", "8",
                "--bytes-per-job", str(BYTES_PER_JOB),
                "--mode", verify_mode,
                "--seed", str(seed),
            ]
            if verify_mode == "seqwrite":
                v_cmd += ["--seq-written-per-job", str(seq_written)]
            vp = cl.run_on_host(verify_host, v_cmd, timeout=1800)
            verify_doc = json.loads(vp.stdout.decode("utf-8").strip().splitlines()[-1])
            (work_dir / "verify.json").write_text(json.dumps(verify_doc, indent=2) + "\n", encoding="utf-8")
        finally:
            cl.stop_mounts(v_started, work_dir, clear_collector=False, remove_cache=True)
        verify_s = time.monotonic() - t_v0

        # Keep object data until the experiment run is archived.
        measurement = {
            "phase": "verified", "measurement_completed_at": cl.utc_iso(),
            "app_done_seconds": app_done_s, "repeat_read_seconds": hot_seconds,
            "application": app_metrics, "repeat_read": hot_metrics,
            "backend_delta": backend_delta, "repeat_backend_delta": hot_delta,
            "object_layout": parent["layout"], "verification": verify_doc,
        }
        checkpoint = work_dir / "measurement.json.tmp"
        checkpoint.write_text(json.dumps(measurement, indent=2) + "\n")
        checkpoint.replace(work_dir / "measurement.json")

    exit_s = float(stop_info.get("exit_seconds", 0.0))
    rr_no_ver = app_done_s + upload_drain_s
    tot_bytes = app_metrics["read_bytes"] + app_metrics["write_bytes"]
    lay_sum = parent["layout"].get("summary") or {}

    res = {
        "schema": "ax-run-result-v1",
        "group": "C1",
        "condition_id": cond_id,
        "family": cond["family"],
        "kind": cond["kind"],
        "system": system,
        "repeat": rep,
        "attempt": attempt,
        "seed": seed,
        "parameters": cond["parameters"],
        "state": "passed",
        "execution_mode": "shared-backend-concurrent" if pair else "exclusive",
        "shared_backend_group": str(pair.directory / 'result.json') if pair else None,
        "started_at": started_at,
        "ended_at": cl.utc_iso(),
        "object_layout": {
            "requested_B": brewfs_hint if system == "brewfs-native-overlay" else "product-default",
            "actual_B": lay_sum.get("actual_B"),
            "actual_q": lay_sum.get("actual_q"),
            "raw_quantum_meta": lay_sum.get("raw_quantum_meta"),
            "ec_m": lay_sum.get("ec_m", 2),
            "ec_n": lay_sum.get("ec_n", 2),
            "logical_size": lay_sum.get("logical_size"),
        },
        "timing": {
            "prepare_or_fork_seconds": round(fork_s, 6),
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
            "hot_throughput_mib_s": round(((hot_metrics["read_bytes"] + hot_metrics["write_bytes"]) / 1048576.0) / max(1e-9, hot_seconds), 6) if (hot_metrics and hot_seconds) else None,
            "hot_iops": round(hot_metrics["operations"] / max(1e-9, hot_seconds), 3) if (hot_metrics and hot_seconds) else None,
            "hot_latency_us": hot_metrics["latency_us"] if hot_metrics else None,
        },
        "backend_delta": backend_delta,
        "hot_backend_delta": hot_delta,
        "mount_metrics_delta": mount_prom_delta,
        "client_mount": {
            "execution_mode": "shared-backend-concurrent" if pair else "exclusive",
            "measure_host": measure_host,
            "verify_host": verify_host,
            "s3_endpoint": m_ep,
            "peak_memory_bytes": (stop_info.get("final_cgroup") or {}).get("memory_peak"),
            "pending_at_exit": stop_info.get("pending_at_exit"),
        },
        "verification": verify_doc,
        "object_retention": "until-run-archive",
    }
    if output is None:
        finalize_run_result(cond_id, system, rep, work_dir, res)
    else:
        (work_dir / "result.json").write_text(json.dumps(res, indent=2) + "\n")
    print(
        f"  [DONE] {cond_id} {system} r{rep} ({measure_host}): app={app_done_s:.2f}s "
        f"({res['application']['throughput_mib_s']:.1f} MiB/s, p99={app_metrics['latency_us']['p99_us']:.0f}us) "
        f"drain={upload_drain_s:.2f}s verify=PASS",
        flush=True,
    )
