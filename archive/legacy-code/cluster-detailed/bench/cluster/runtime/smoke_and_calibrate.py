#!/usr/bin/env python3
"""Milestone 1 verification on A366:
1. 1,000-file functional smoke test across ALL 4 engine modes:
   - brewfs-native-overlay (init -> populate 1k -> seal -> fork -> mutate -> upload_zero -> cross-host verify on A355)
   - brewfs-packed (smallfiles 1k -> packed_snapshot_fixture -> init-packed-volume -> audit_mount -> load -> fork + fork-child -> cross-host verify on A355)
   - juicefs (format DB 14 -> populate 1k -> clone -p -> mount --subdir -> mutate -> upload_zero -> cross-host verify on A355)
   - juicefs-overlay (format forkredis -> populate 1k -> clone --seal -> clone --lazy -> mount child -> mutate -> upload_zero -> cross-host verify on A355)
2. Single-task full-fuse-replay smoke test across all 3 C2 engines on office-excel-01 (with reviewed tool boundaries + short pacing + cross-host verify-tree on A355).
3. 3-pair 8-mount collector ON/OFF overhead calibration under measurement.lock, verifying median overhead <= 5%, writing hosts/collector-overhead-8mounts.json.
4. Save each completed measurement and register temporary trace resources for later cleanup.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import statistics
import sys
import time

sys.path.insert(0, "/srv/ax-cluster/direct/scripts")
import cluster_lib as cl

SMOKE_DIR = Path("/srv/ax-cluster/runs/direct/run-001/mounts/smoke")
SMOKE_DIR.mkdir(parents=True, exist_ok=True)
REDIS_URL = f"redis://:{cl.SECRETS['redis_password']}@{cl.REDIS_HOST}:{cl.REDIS_PORT}/0"


def make_req(mid, phase, engine, host_ssh, slot, ws, parent_ws, purpose, binary, cache_state="cold", windows=None):
    return {
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


def revision_from_record(rec: dict) -> str:
    rev = rec.get("base_revision") or rec.get("fork_base")
    rh = rev["root_hash"]
    if isinstance(rh, list):
        rh = "".join(f"{b:02x}" for b in rh)
    return f"{rev['layer_id']}:{rev['sealed_version']}:{rh}"


def test_1000_files_all_engines(engines=None) -> dict:
    print("=== [1/3] 1,000-file smoke test across all 4 engine modes ===", flush=True)
    engines = set(engines or ['brewfs-native-overlay','brewfs-packed','juicefs','juicefs-overlay'])
    results_file=cl.RUN_ROOT/'mounts/file-checks.json'
    results=json.loads(results_file.read_text()) if results_file.exists() else {}
    local_src = SMOKE_DIR / 'local-1k-src'
    local_out = SMOKE_DIR / 'local-1k-out'

    if 'brewfs-native-overlay' in engines:
        # 1A. brewfs-native-overlay
        bkt_nat = "axd1-smoke-nat"
        ns_nat = "axd1_smoke_nat"
        cl.delete_bucket_and_objects(bkt_nat)
        cl.ensure_bucket(bkt_nat)
        cl.redis_delete_pattern_db0(f"*{ns_nat}*")

        init_out = cl.extract_json_from_cli(
            cl.run_on_host(
                "A366",
                [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns_nat, "init-shared-volume"],
                with_secrets=True,
            ).stdout
        )
        base_ws = init_out["workspace_id"]
        mp_cfg = SMOKE_DIR / "nat-mounts.json"
        cl.generate_mounts_json(
            [
                make_req("smoke-nat-init", "initial", "brewfs-native-overlay", "idcai-60001", 1, base_ws, "none", "smoke-init", str(cl.BIN_DIR / "brewfs")),
            ],
            mp_cfg,
        )
        m_init = cl.start_mounts(
            mp_cfg,
            {"smoke-nat-init": {"workspace_uuid": base_ws, "workspace_namespace": ns_nat, "s3_bucket": bkt_nat, "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]}},
            SMOKE_DIR,
        )
        try:
            cl.run_on_host("A366", [str(cl.BIN_DIR / "c1-namespace"), "prepare", "--root", m_init[0]["mount_directory"], "--files", "1000", "--workers", "4"])
            up_init = cl.wait_uploads(m_init)[0]
        finally:
            cl.stop_mounts(m_init, SMOKE_DIR, remove_cache=True)

        seal_out = cl.extract_json_from_cli(
            cl.run_on_host(
                "A366",
                [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns_nat, "seal-native-base", "--workspace", base_ws],
                with_secrets=True,
            ).stdout
        )
        rev_nat = seal_out["revision"]
        fork_out = cl.extract_json_from_cli(
            cl.run_on_host(
                "A366",
                [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns_nat, "fork-shared-bucket", "--revision", rev_nat, "--count", "2", "--mode", "shared"],
                with_secrets=True,
            ).stdout
        )
        child_ws, clean_sibling_ws = fork_out["workspaces"]

        mp_cfg2 = SMOKE_DIR / "nat-child-mounts.json"
        cl.generate_mounts_json(
            [
                make_req("smoke-nat-child", "measure", "brewfs-native-overlay", "idcai-60001", 1, child_ws, base_ws, "smoke-measure", str(cl.BIN_DIR / "brewfs")),
                make_req("smoke-nat-verify", "verify", "brewfs-native-overlay", "idcai-60525", 1, child_ws, base_ws, "smoke-verify", str(cl.BIN_DIR / "brewfs"), cache_state="cross-host-cold", windows=["verify"]),
                make_req("smoke-nat-sib", "verify", "brewfs-native-overlay", "idcai-60525", 2, clean_sibling_ws, base_ws, "smoke-sib", str(cl.BIN_DIR / "brewfs"), cache_state="cross-host-cold", windows=["verify"]),
            ],
            mp_cfg2,
        )
        m_c = cl.start_mounts(
            mp_cfg2,
            {"smoke-nat-child": {"workspace_uuid": child_ws, "workspace_namespace": ns_nat, "s3_bucket": bkt_nat, "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]}},
            SMOKE_DIR,
        )
        try:
            for seg in ("readdir", "getattr", "read-4k", "overwrite-1pct", "repeat-overwrite-100x10", "rename-1pct", "delete-1pct", "create-1pct"):
                cl.run_on_host(
                    "A366",
                    [
                        "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", m_c[0]["cgroup"], "--",
                        str(cl.BIN_DIR / "c1-namespace"), seg, "--root", m_c[0]["mount_directory"], "--files", "1000",
                    ],
                )
            up_c = cl.wait_uploads(m_c)[0]
        finally:
            cl.stop_mounts(m_c, SMOKE_DIR, remove_cache=True)

        # Cross-host cold remount verify on A355 (both mutated child AND untouched sibling!)
        m_v = cl.start_mounts(
            mp_cfg2,
            {
                "smoke-nat-verify": {"workspace_uuid": child_ws, "workspace_namespace": ns_nat, "s3_bucket": bkt_nat, "s3_endpoint": cl.RUSTFS_ENDPOINTS[1]},
                "smoke-nat-sib": {"workspace_uuid": clean_sibling_ws, "workspace_namespace": ns_nat, "s3_bucket": bkt_nat, "s3_endpoint": cl.RUSTFS_ENDPOINTS[1]},
            },
            SMOKE_DIR,
        )
        try:
            by_id = {x["id"]: x for x in m_v}
            v_child = json.loads(cl.run_on_host("A355", [str(cl.BIN_DIR / "c1-namespace"), "verify-final", "--root", by_id["smoke-nat-verify"]["mount_directory"], "--files", "1000"]).stdout.decode())
            v_sib = json.loads(cl.run_on_host("A355", [str(cl.BIN_DIR / "c1-namespace"), "verify-initial", "--root", by_id["smoke-nat-sib"]["mount_directory"], "--files", "1000"]).stdout.decode())
        finally:
            cl.stop_mounts(m_v, SMOKE_DIR, remove_cache=True)
        results["brewfs-native-overlay"] = {"passed": True, "upload_drain_s": up_c["upload_drain_seconds"], "verify_child": v_child, "verify_sibling_isolation": v_sib}
        print("  [PASS] brewfs-native-overlay 1,000-file + cross-host verify + sibling isolation", flush=True)

        (cl.RUN_ROOT / "mounts/file-checks.json").write_text(json.dumps(results, indent=2) + "\n")

    if 'brewfs-packed' in engines:
        # 1B. brewfs-packed
        bkt_pk = "axd1-smk-pk"
        ns_pk_load = "axd1_smoke_pk_load"
        ns_pk_child = "axd1_smoke_pk_child"
        cl.delete_buckets_with_prefix(bkt_pk)
        cl.ensure_bucket(bkt_pk)
        cl.redis_delete_pattern_db0("*axd1_smoke_pk*")

        local_src = SMOKE_DIR / "local-1k-src"
        local_out = SMOKE_DIR / "local-1k-out"
        for d in (local_src, local_out):
            if d.exists():
                shutil.rmtree(d)
        SMOKE_DIR.mkdir(parents=True, exist_ok=True)
        # Fast parallel 1,000-file staging verified with smallfiles verify-base
        cl.run_on_host(
            "A366",
            [
                "python3",
                "-c",
                (
                    "import os, struct; from pathlib import Path\n"
                    f"root = Path({str(local_src)!r}); small = root / 'small'\n"
                    "small.mkdir(parents=True); os.chmod(root, 0o755); os.chmod(small, 0o755)\n"
                    "for d in range(1000): p = small / f'd{d:04d}'; p.mkdir(); os.chmod(p, 0o755)\n"
                    "M = 0xffffffffffffffff\n"
                    "for i in range(1000):\n"
                    "    s = i; buf = bytearray(4096)\n"
                    "    for off in range(0, 4096, 8):\n"
                    "        s = (s + 0x9e3779b97f4a7c15) & M\n"
                    "        z = ((s ^ (s >> 30)) * 0xbf58476d1ce4e5b9) & M\n"
                    "        z = ((z ^ (z >> 27)) * 0x94d049bb133111eb) & M\n"
                    "        z = z ^ (z >> 31)\n"
                    "        struct.pack_into('<Q', buf, off, z)\n"
                    "    fp = small / f'd{i % 1024:04d}' / f'f{i:07d}'\n"
                    "    fp.write_bytes(buf); os.chmod(fp, 0o644)\n"
                ),
            ],
        )
        cl.run_on_host("A366", [str(cl.BIN_DIR / "smallfiles"), "verify-base", "--root", str(local_src), "--output", str(local_out), "--files", "1000"])

        manifest_file = SMOKE_DIR / "smoke-packed-manifest.txt"
        if manifest_file.exists():
            manifest_file.unlink()
        cl.run_on_host(
            "A366",
            [
                str(cl.BIN_DIR / "packed_snapshot_fixture"),
                "--source-directory", str(local_src),
                "--source-consistency", "best-effort-detected",
                "--source-hardlink-policy", "reject-external",
                "--bucket", bkt_pk,
                "--endpoint", cl.RUSTFS_ENDPOINTS[0],
                "--region", "us-east-1",
                "--force-path-style", "true",
                "--s3-rustfs-ec-block-size-hint", "auto",
                "--manifest-output", str(manifest_file),
            ],
            with_secrets=True,
        )
        manifest_key = manifest_file.read_text(encoding="utf-8").strip()
        data_cfg_path = SMOKE_DIR / "pk-data-cfg.json"
        data_cfg_path.write_text(
            json.dumps(
                {
                    "data": {
                        "backend": "s3",
                        "s3": {
                            "bucket": bkt_pk,
                            "endpoint": cl.RUSTFS_ENDPOINTS[0],
                            "region": "us-east-1",
                            "force_path_style": True,
                            "disable_payload_checksum": True,
                        },
                    }
                }
            )
            + "\n"
        )
        pk_load_rec = cl.extract_json_from_cli(
            cl.run_on_host(
                "A366",
                [
                    str(cl.BIN_DIR / "brewfs"),
                    "workspace",
                    "--meta-backend", "redis",
                    "--meta-url", REDIS_URL,
                    "--workspace-namespace", ns_pk_load,
                    "init-packed-volume",
                    "--manifest-key", manifest_key,
                    "--data-config", str(data_cfg_path),
                ],
                with_secrets=True,
            ).stdout
        )
        pk_base_ws = pk_load_rec["workspace_id"]

        pk_child_rec = cl.extract_json_from_cli(
            cl.run_on_host(
                "A366",
                [
                    str(cl.BIN_DIR / "brewfs"),
                    "workspace",
                    "--meta-backend", "redis",
                    "--meta-url", REDIS_URL,
                    "--workspace-namespace", ns_pk_child,
                    "init-packed-volume",
                    "--manifest-key", manifest_key,
                    "--data-config", str(data_cfg_path),
                ],
                with_secrets=True,
            ).stdout
        )
        pk_child_ws = pk_child_rec["workspace_id"]

        mp_pk = SMOKE_DIR / "pk-mounts.json"
        cl.generate_mounts_json(
            [
                make_req("smoke-pk-load", "measure", "brewfs-packed", "idcai-60001", 1, pk_base_ws, "none", "smoke-pk-load", str(cl.BIN_DIR / "brewfs")),
                make_req("smoke-pk-child", "fork-child", "brewfs-packed", "idcai-60001", 2, pk_child_ws, pk_base_ws, "smoke-pk-fork", str(cl.BIN_DIR / "brewfs")),
                make_req("smoke-pk-verify", "verify", "brewfs-packed", "idcai-60525", 1, pk_child_ws, pk_base_ws, "smoke-pk-verify", str(cl.BIN_DIR / "brewfs"), cache_state="cross-host-cold", windows=["verify"]),
                make_req("smoke-pk-sib", "verify", "brewfs-packed", "idcai-60525", 2, pk_base_ws, "none", "smoke-pk-sib", str(cl.BIN_DIR / "brewfs"), cache_state="cross-host-cold", windows=["verify"]),
            ],
            mp_pk,
        )
        m_pk = cl.start_mounts(
            mp_pk,
            {
                "smoke-pk-load": {"workspace_uuid": pk_base_ws, "workspace_namespace": ns_pk_load, "s3_bucket": bkt_pk, "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]},
                "smoke-pk-child": {"workspace_uuid": pk_child_ws, "workspace_namespace": ns_pk_child, "s3_bucket": bkt_pk, "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]},
            },
            SMOKE_DIR,
        )
        pk_load_out = SMOKE_DIR / "pk-load.json"
        pk_fc_out = SMOKE_DIR / "pk-fc.json"
        pk_state = SMOKE_DIR / "pk-child.state"
        for f in (pk_load_out, pk_fc_out, pk_state):
            if f.exists():
                f.unlink()
        try:
            by_id = {x["id"]: x for x in m_pk}
            cl.run_on_host("A366", [str(cl.BIN_DIR / "c3-loader"), "load", "--root", by_id["smoke-pk-load"]["mount_directory"], "--output", str(pk_load_out), "--files", "1000", "--epochs", "2", "--threads", "2", "--batch", "64", "--seed", "2026111401", "--verify-bytes", "1"])
            cl.run_on_host("A366", [str(cl.BIN_DIR / "c3-loader"), "fork-child", "--root", by_id["smoke-pk-child"]["mount_directory"], "--output", str(pk_fc_out), "--state-out", str(pk_state), "--files", "1000", "--dirty-count", "10", "--workspace", "1", "--seed", "2026111401"])
            cl.wait_uploads([by_id["smoke-pk-child"]])
        finally:
            cl.stop_mounts(m_pk, SMOKE_DIR, remove_cache=True)

        cl.sync_file_to_host("A355", pk_state, "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-pk.state")
        m_pkv = cl.start_mounts(
            mp_pk,
            {
                "smoke-pk-verify": {"workspace_uuid": pk_child_ws, "workspace_namespace": ns_pk_child, "s3_bucket": bkt_pk, "s3_endpoint": cl.RUSTFS_ENDPOINTS[1]},
                "smoke-pk-sib": {"workspace_uuid": pk_base_ws, "workspace_namespace": ns_pk_load, "s3_bucket": bkt_pk, "s3_endpoint": cl.RUSTFS_ENDPOINTS[1]},
            },
            SMOKE_DIR,
        )
        try:
            by_v = {x["id"]: x for x in m_pkv}
            cl.run_on_host("A355", ["rm", "-f", "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-pk-verify.json", "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-pk-sib.json"], log_cmd=False)
            vp = cl.run_on_host(
                "A355",
                [
                    str(cl.BIN_DIR / "smallfiles-verify"),
                    by_v["smoke-pk-verify"]["mount_directory"],
                    "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-pk.state",
                    "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-pk-verify.json",
                    "1000",
                    "4",
                    "1",
                ],
            )
            vs = cl.run_on_host(
                "A355",
                [
                    str(cl.BIN_DIR / "smallfiles-verify"),
                    by_v["smoke-pk-sib"]["mount_directory"],
                    "base",
                    "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-pk-sib.json",
                    "1000",
                    "4",
                    "1",
                ],
            )
            v_pk = json.loads(vp.stdout.decode())
            v_pk_sib = json.loads(vs.stdout.decode())
        finally:
            cl.stop_mounts(m_pkv, SMOKE_DIR, remove_cache=True)
            cl.run_on_host("A355", ["rm", "-f", "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-pk.state", "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-pk-verify.json", "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-pk-sib.json"], log_cmd=False)
        results["brewfs-packed"] = {"passed": True, "manifest_key": manifest_key, "verify_child": v_pk, "verify_sibling_isolation": v_pk_sib}
        print("  [PASS] brewfs-packed 1,000-file + audit_mount native gauges + fork-child cross-host verify", flush=True)

        (cl.RUN_ROOT / "mounts/file-checks.json").write_text(json.dumps(results, indent=2) + "\n")

    if 'juicefs' in engines:
        # 1C. juicefs (official 1.4.2) on temporary DB 14
        bkt_jfs = "axd1-smoke-jfs"
        cl.delete_bucket_and_objects(bkt_jfs)
        cl.ensure_bucket(bkt_jfs)
        cl.redis_flush_db(14)
        cl.run_on_host(
            "A366",
            [
                str(cl.BIN_DIR / "juicefs"), "--no-agent", "format",
                "--storage=s3", f"--bucket={cl.RUSTFS_ENDPOINTS[0]}/{bkt_jfs}",
                "--block-size=4096", "--compress=none",
                f"redis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/14", "axd1-smoke-jfs",
            ],
            with_secrets=True,
        )
        mp_jfs = SMOKE_DIR / "jfs-mounts.json"
        cl.generate_mounts_json(
            [
                make_req("smoke-jfs-root", "initial", "juicefs", "idcai-60001", 1, "root", "none", "smoke-jfs-init", str(cl.BIN_DIR / "juicefs")),
                make_req("smoke-jfs-child", "measure", "juicefs", "idcai-60001", 2, "ws1", "base", "smoke-jfs-child", str(cl.BIN_DIR / "juicefs"), windows=["measure"]),
                make_req("smoke-jfs-verify", "verify", "juicefs", "idcai-60525", 1, "ws1", "base", "smoke-jfs-verify", str(cl.BIN_DIR / "juicefs"), cache_state="cross-host-cold", windows=["verify"]),
            ],
            mp_jfs,
        )
        m_jr = cl.start_mounts(mp_jfs, {"smoke-jfs-root": {"redis_db": 14, "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt_jfs}"}}, SMOKE_DIR)
        try:
            rdir = Path(m_jr[0]["mount_directory"])
            (rdir / "bases" / "b1").mkdir(parents=True, exist_ok=True)
            (rdir / "workspaces").mkdir(parents=True, exist_ok=True)
            cl.run_on_host("A366", [str(cl.BIN_DIR / "c1-namespace"), "prepare", "--root", str(rdir / "bases" / "b1"), "--files", "1000", "--workers", "4"])
            cl.wait_uploads(m_jr)
            cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs"), "--no-agent", "clone", "-p", str(rdir / "bases" / "b1"), str(rdir / "workspaces" / "ws1")], with_secrets=True)
        finally:
            cl.stop_mounts(m_jr, SMOKE_DIR, remove_cache=True)

        m_jc = cl.start_mounts(mp_jfs, {"smoke-jfs-child": {"redis_db": 14, "subdir": "/workspaces/ws1", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt_jfs}"}}, SMOKE_DIR)
        try:
            for seg in ("overwrite-1pct", "repeat-overwrite-100x10", "rename-1pct", "delete-1pct", "create-1pct"):
                cl.run_on_host("A366", [str(cl.BIN_DIR / "c1-namespace"), seg, "--root", m_jc[0]["mount_directory"], "--files", "1000"])
            cl.wait_uploads(m_jc)
        finally:
            cl.stop_mounts(m_jc, SMOKE_DIR, remove_cache=True)

        m_jv = cl.start_mounts(mp_jfs, {"smoke-jfs-verify": {"redis_db": 14, "subdir": "/workspaces/ws1", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[1]}/{bkt_jfs}"}}, SMOKE_DIR)
        try:
            v_jfs = json.loads(cl.run_on_host("A355", [str(cl.BIN_DIR / "c1-namespace"), "verify-final", "--root", m_jv[0]["mount_directory"], "--files", "1000"]).stdout.decode())
        finally:
            cl.stop_mounts(m_jv, SMOKE_DIR, remove_cache=True)
        results["juicefs"] = {"passed": True, "verify_child": v_jfs}
        print("  [PASS] juicefs 1,000-file + clone -p + --subdir + cross-host verify", flush=True)

        (cl.RUN_ROOT / "mounts/file-checks.json").write_text(json.dumps(results, indent=2) + "\n")

    if 'juicefs-overlay' in engines:
        # 1D. juicefs-overlay (forkredis)
        bkt_jov = "axd1-smoke-jov"
        vol_jov = "axd1-smoke-jov"
        cl.delete_bucket_and_objects(bkt_jov)
        cl.ensure_bucket(bkt_jov)
        cl.redis_delete_pattern_db0(f"{{jfs-fork:{vol_jov}}}:*")
        base_url_jov = f"forkredis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/0?volume={vol_jov}&branch=main"
        cl.run_on_host(
            "A366",
            [
                str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "format",
                "--storage=s3", f"--bucket={cl.RUSTFS_ENDPOINTS[0]}/{bkt_jov}",
                "--block-size=4096", "--compress=none",
                base_url_jov, vol_jov,
            ],
            with_secrets=True,
        )
        mp_jov = SMOKE_DIR / "jov-mounts.json"
        cl.generate_mounts_json(
            [
                make_req("smoke-jov-init", "initial", "juicefs-overlay", "idcai-60001", 1, "main", "none", "smoke-jov-init", str(cl.BIN_DIR / "juicefs-overlay")),
                make_req("smoke-jov-child", "measure", "juicefs-overlay", "idcai-60001", 2, "child1", "main", "smoke-jov-child", str(cl.BIN_DIR / "juicefs-overlay"), windows=["measure"]),
                make_req("smoke-jov-verify", "verify", "juicefs-overlay", "idcai-60525", 1, "child1", "main", "smoke-jov-verify", str(cl.BIN_DIR / "juicefs-overlay"), cache_state="cross-host-cold", windows=["verify"]),
            ],
            mp_jov,
        )
        m_oi = cl.start_mounts(mp_jov, {"smoke-jov-init": {"volume": vol_jov, "branch": "main", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt_jov}"}}, SMOKE_DIR)
        try:
            cl.run_on_host("A366", [str(cl.BIN_DIR / "c1-namespace"), "prepare", "--root", m_oi[0]["mount_directory"], "--files", "1000", "--workers", "4"])
            cl.wait_uploads(m_oi)
        finally:
            cl.stop_mounts(m_oi, SMOKE_DIR, remove_cache=True)

        cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--seal", base_url_jov], with_secrets=True)
        cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--lazy", base_url_jov, "child1"], with_secrets=True)

        m_oc = cl.start_mounts(mp_jov, {"smoke-jov-child": {"volume": vol_jov, "branch": "child1", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt_jov}"}}, SMOKE_DIR)
        try:
            for seg in ("overwrite-1pct", "repeat-overwrite-100x10", "rename-1pct", "delete-1pct", "create-1pct"):
                cl.run_on_host("A366", [str(cl.BIN_DIR / "c1-namespace"), seg, "--root", m_oc[0]["mount_directory"], "--files", "1000"])
            cl.wait_uploads(m_oc)
        finally:
            cl.stop_mounts(m_oc, SMOKE_DIR, remove_cache=True)

        m_ov = cl.start_mounts(mp_jov, {"smoke-jov-verify": {"volume": vol_jov, "branch": "child1", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[1]}/{bkt_jov}"}}, SMOKE_DIR)
        try:
            v_jov = json.loads(cl.run_on_host("A355", [str(cl.BIN_DIR / "c1-namespace"), "verify-final", "--root", m_ov[0]["mount_directory"], "--files", "1000"]).stdout.decode())
        finally:
            cl.stop_mounts(m_ov, SMOKE_DIR, remove_cache=True)
        results["juicefs-overlay"] = {"passed": True, "verify_child": v_jov}
        print("  [PASS] juicefs-overlay 1,000-file + clone --seal/--lazy + cross-host verify", flush=True)

        (cl.RUN_ROOT / "mounts/file-checks.json").write_text(json.dumps(results, indent=2) + "\n")

    # Cleanup 1k smoke buckets/keys
    for b in ("axd1-smoke-nat", "axd1-smoke-jfs", "axd1-smoke-jov"):
        cl.delete_bucket_and_objects(b)
    cl.delete_buckets_with_prefix("axd1-smk-pk")
    cl.redis_delete_pattern_db0("*axd1_smoke_*")
    cl.redis_delete_pattern_db0("{jfs-fork:axd1-smoke-*}:*")
    cl.redis_flush_db(14)
    shutil.rmtree(local_src, ignore_errors=True)
    shutil.rmtree(local_out, ignore_errors=True)
    return results


def copy_initial_tree(case: str, dst_dir: str):
    src = f"/srv/ax-cluster/input/traces/{case}/initial/."
    cl.run_on_host("A366", ["cp", "-a", src, dst_dir.rstrip("/") + "/"])


def test_single_task_trace_and_8mount_calibration() -> tuple[dict, dict]:
    print("=== [2/3] Single-task full-fuse-replay smoke test across all 3 C2 systems (office-excel-01) ===", flush=True)
    case = "office-excel-01"
    trace_dir = Path(f"/srv/ax-cluster/input/traces/{case}")
    events_p = trace_dir / "capture/events.jsonl"
    blobs_p = trace_dir / "capture/blobs"
    final_tree_p = trace_dir / "final-tree.json"
    replayed_p = trace_dir / "replayed"
    # Pick the exact reviewed short pacing plan generated for office-excel-01 (agents=8, r1, a001)
    pacing_p = Path("/srv/ax-cluster/input/pacing/agents_8_r1_short/a001.json")

    single_results = {}

    # Prepare BrewFS Native Overlay base for office-excel-01 (also reused for 8-mount calibration!)
    bkt_tr = "axd1-smoke-tr-brew"
    ns_tr = "axd1_smoke_tr_brew"
    cl.delete_bucket_and_objects(bkt_tr)
    cl.ensure_bucket(bkt_tr)
    cl.redis_delete_pattern_db0(f"*{ns_tr}*")

    init_rec = cl.extract_json_from_cli(
        cl.run_on_host("A366", [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns_tr, "init-shared-volume"], with_secrets=True).stdout
    )
    brew_base_ws = init_rec["workspace_id"]
    mp_tr_b = SMOKE_DIR / "tr-brew-init.json"
    cl.generate_mounts_json(
        [make_req("smoke-trb-init", "initial", "brewfs-native-overlay", "idcai-60001", 1, brew_base_ws, "none", "tr-init", str(cl.BIN_DIR / "brewfs"))],
        mp_tr_b,
    )
    mb_i = cl.start_mounts(mp_tr_b, {"smoke-trb-init": {"workspace_uuid": brew_base_ws, "workspace_namespace": ns_tr, "s3_bucket": bkt_tr, "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]}}, SMOKE_DIR)
    try:
        copy_initial_tree(case, mb_i[0]["mount_directory"])
        cl.wait_uploads(mb_i)
    finally:
        cl.stop_mounts(mb_i, SMOKE_DIR, remove_cache=True)
    brew_rev = cl.extract_json_from_cli(
        cl.run_on_host("A366", [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns_tr, "seal-native-base", "--workspace", brew_base_ws], with_secrets=True).stdout
    )["revision"]

    # 2A: BrewFS single-task replay + cross-host verify
    brew_c1 = cl.extract_json_from_cli(
        cl.run_on_host("A366", [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns_tr, "fork-shared-bucket", "--revision", brew_rev, "--count", "1"], with_secrets=True).stdout
    )["workspaces"][0]
    mp_tr_b2 = SMOKE_DIR / "tr-brew-run.json"
    cl.generate_mounts_json(
        [
            make_req("smoke-trb-run", "measure", "brewfs-native-overlay", "idcai-60001", 1, brew_c1, brew_base_ws, "tr-run", str(cl.BIN_DIR / "brewfs")),
            make_req("smoke-trb-ver", "verify", "brewfs-native-overlay", "idcai-60525", 1, brew_c1, brew_base_ws, "tr-ver", str(cl.BIN_DIR / "brewfs"), cache_state="cross-host-cold", windows=["verify"]),
        ],
        mp_tr_b2,
    )
    mb_r = cl.start_mounts(mp_tr_b2, {"smoke-trb-run": {"workspace_uuid": brew_c1, "workspace_namespace": ns_tr, "s3_bucket": bkt_tr, "s3_endpoint": cl.RUSTFS_ENDPOINTS[0]}}, SMOKE_DIR)
    rep_out_b = SMOKE_DIR / "replay-brew.json"
    if rep_out_b.exists():
        rep_out_b.unlink()
    try:
        cl.run_on_host(
            "A366",
            [
                "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", mb_r[0]["cgroup"], "--",
                str(cl.BIN_DIR / "full-fuse-replay"), str(events_p), str(blobs_p), mb_r[0]["mount_directory"], str(rep_out_b),
                "--performance", "--simulate-llm", str(pacing_p),
            ],
        )
        cl.wait_uploads(mb_r)
    finally:
        cl.stop_mounts(mb_r, SMOKE_DIR, remove_cache=True)

    mb_v = cl.start_mounts(mp_tr_b2, {"smoke-trb-ver": {"workspace_uuid": brew_c1, "workspace_namespace": ns_tr, "s3_bucket": bkt_tr, "s3_endpoint": cl.RUSTFS_ENDPOINTS[1]}}, SMOKE_DIR)
    try:
        ver_b = json.loads(
            cl.run_on_host(
                "A355",
                [
                    str(cl.BIN_DIR / "trace-verify-and-index"), "verify-tree",
                    "--mount-dir", mb_v[0]["mount_directory"],
                    "--final-tree", str(final_tree_p),
                    "--replayed-dir", str(replayed_p),
                    "--out", "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-trb-ver.json",
                ],
            ).stdout.decode()
        )
    finally:
        cl.stop_mounts(mb_v, SMOKE_DIR, remove_cache=True)
    rep_doc_b = json.loads(rep_out_b.read_text())
    single_results["brewfs-native-overlay"] = {"replay": {"operations": rep_doc_b["operations"], "execution_seconds": rep_doc_b["execution_seconds"], "think_time": rep_doc_b["think_time"]}, "verify": ver_b}
    print(f"  [PASS] brewfs-native-overlay single-task replay ({rep_doc_b['operations']} ops, sleep={rep_doc_b['think_time']['actual_sleep_seconds']:.2f}s) + cross-host verify", flush=True)

    # 2B: Official JuiceFS single-task replay + cross-host verify
    bkt_tr_j = "axd1-smoke-tr-jfs"
    cl.delete_bucket_and_objects(bkt_tr_j)
    cl.ensure_bucket(bkt_tr_j)
    cl.redis_flush_db(14)
    cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs"), "--no-agent", "format", "--storage=s3", f"--bucket={cl.RUSTFS_ENDPOINTS[0]}/{bkt_tr_j}", "--block-size=4096", "--compress=none", f"redis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/14", bkt_tr_j], with_secrets=True)
    mp_tr_j = SMOKE_DIR / "tr-jfs.json"
    cl.generate_mounts_json(
        [
            make_req("smoke-trj-init", "initial", "juicefs", "idcai-60001", 1, "root", "none", "trj-init", str(cl.BIN_DIR / "juicefs")),
            make_req("smoke-trj-run", "measure", "juicefs", "idcai-60001", 2, "ws1", "base", "trj-run", str(cl.BIN_DIR / "juicefs"), windows=["measure"]),
            make_req("smoke-trj-ver", "verify", "juicefs", "idcai-60525", 1, "ws1", "base", "trj-ver", str(cl.BIN_DIR / "juicefs"), cache_state="cross-host-cold", windows=["verify"]),
        ],
        mp_tr_j,
    )
    mj_i = cl.start_mounts(mp_tr_j, {"smoke-trj-init": {"redis_db": 14, "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt_tr_j}"}}, SMOKE_DIR)
    try:
        rd = Path(mj_i[0]["mount_directory"])
        (rd / "bases" / case).mkdir(parents=True, exist_ok=True)
        (rd / "workspaces").mkdir(parents=True, exist_ok=True)
        copy_initial_tree(case, str(rd / "bases" / case))
        cl.wait_uploads(mj_i)
        cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs"), "--no-agent", "clone", "-p", str(rd / "bases" / case), str(rd / "workspaces" / "ws1")], with_secrets=True)
    finally:
        cl.stop_mounts(mj_i, SMOKE_DIR, remove_cache=True)

    mj_r = cl.start_mounts(mp_tr_j, {"smoke-trj-run": {"redis_db": 14, "subdir": "/workspaces/ws1", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt_tr_j}"}}, SMOKE_DIR)
    rep_out_j = SMOKE_DIR / "replay-jfs.json"
    if rep_out_j.exists():
        rep_out_j.unlink()
    try:
        cl.run_on_host(
            "A366",
            [
                "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", mj_r[0]["cgroup"], "--",
                str(cl.BIN_DIR / "full-fuse-replay"), str(events_p), str(blobs_p), mj_r[0]["mount_directory"], str(rep_out_j),
                "--performance", "--simulate-llm", str(pacing_p),
            ],
        )
        cl.wait_uploads(mj_r)
    finally:
        cl.stop_mounts(mj_r, SMOKE_DIR, remove_cache=True)

    mj_v = cl.start_mounts(mp_tr_j, {"smoke-trj-ver": {"redis_db": 14, "subdir": "/workspaces/ws1", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[1]}/{bkt_tr_j}"}}, SMOKE_DIR)
    try:
        ver_j = json.loads(
            cl.run_on_host(
                "A355",
                [
                    str(cl.BIN_DIR / "trace-verify-and-index"), "verify-tree",
                    "--mount-dir", mj_v[0]["mount_directory"],
                    "--final-tree", str(final_tree_p),
                    "--replayed-dir", str(replayed_p),
                    "--out", "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-trj-ver.json",
                ],
            ).stdout.decode()
        )
    finally:
        cl.stop_mounts(mj_v, SMOKE_DIR, remove_cache=True)
    rep_doc_j = json.loads(rep_out_j.read_text())
    single_results["juicefs"] = {"replay": {"operations": rep_doc_j["operations"], "execution_seconds": rep_doc_j["execution_seconds"]}, "verify": ver_j}
    print(f"  [PASS] juicefs single-task replay ({rep_doc_j['operations']} ops) + cross-host verify", flush=True)

    # 2C: JuiceFS Overlay single-task replay + cross-host verify
    bkt_tr_o = "axd1-smoke-tr-jov"
    vol_tr_o = "axd1-smoke-tr-jov"
    cl.delete_bucket_and_objects(bkt_tr_o)
    cl.ensure_bucket(bkt_tr_o)
    cl.redis_delete_pattern_db0(f"{{jfs-fork:{vol_tr_o}}}:*")
    url_o = f"forkredis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/0?volume={vol_tr_o}&branch=main"
    cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "format", "--storage=s3", f"--bucket={cl.RUSTFS_ENDPOINTS[0]}/{bkt_tr_o}", "--block-size=4096", "--compress=none", url_o, vol_tr_o], with_secrets=True)
    mp_tr_o = SMOKE_DIR / "tr-jov.json"
    cl.generate_mounts_json(
        [
            make_req("smoke-tro-init", "initial", "juicefs-overlay", "idcai-60001", 1, "main", "none", "tro-init", str(cl.BIN_DIR / "juicefs-overlay")),
            make_req("smoke-tro-run", "measure", "juicefs-overlay", "idcai-60001", 2, "child1", "main", "tro-run", str(cl.BIN_DIR / "juicefs-overlay"), windows=["measure"]),
            make_req("smoke-tro-ver", "verify", "juicefs-overlay", "idcai-60525", 1, "child1", "main", "tro-ver", str(cl.BIN_DIR / "juicefs-overlay"), cache_state="cross-host-cold", windows=["verify"]),
        ],
        mp_tr_o,
    )
    mo_i = cl.start_mounts(mp_tr_o, {"smoke-tro-init": {"volume": vol_tr_o, "branch": "main", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt_tr_o}"}}, SMOKE_DIR)
    try:
        copy_initial_tree(case, mo_i[0]["mount_directory"])
        cl.wait_uploads(mo_i)
    finally:
        cl.stop_mounts(mo_i, SMOKE_DIR, remove_cache=True)
    cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--seal", url_o], with_secrets=True)
    cl.run_on_host("A366", [str(cl.BIN_DIR / "juicefs-overlay"), "--no-agent", "clone", "--lazy", url_o, "child1"], with_secrets=True)

    mo_r = cl.start_mounts(mp_tr_o, {"smoke-tro-run": {"volume": vol_tr_o, "branch": "child1", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[0]}/{bkt_tr_o}"}}, SMOKE_DIR)
    rep_out_o = SMOKE_DIR / "replay-jov.json"
    if rep_out_o.exists():
        rep_out_o.unlink()
    try:
        cl.run_on_host(
            "A366",
            [
                "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", mo_r[0]["cgroup"], "--",
                str(cl.BIN_DIR / "full-fuse-replay"), str(events_p), str(blobs_p), mo_r[0]["mount_directory"], str(rep_out_o),
                "--performance", "--simulate-llm", str(pacing_p),
            ],
        )
        cl.wait_uploads(mo_r)
    finally:
        cl.stop_mounts(mo_r, SMOKE_DIR, remove_cache=True)

    mo_v = cl.start_mounts(mp_tr_o, {"smoke-tro-ver": {"volume": vol_tr_o, "branch": "child1", "s3_bucket_url": f"{cl.RUSTFS_ENDPOINTS[1]}/{bkt_tr_o}"}}, SMOKE_DIR)
    try:
        ver_o = json.loads(
            cl.run_on_host(
                "A355",
                [
                    str(cl.BIN_DIR / "trace-verify-and-index"), "verify-tree",
                    "--mount-dir", mo_v[0]["mount_directory"],
                    "--final-tree", str(final_tree_p),
                    "--replayed-dir", str(replayed_p),
                    "--out", "/srv/ax-cluster/runs/direct/run-001/hosts/A355/smoke-tro-ver.json",
                ],
            ).stdout.decode()
        )
    finally:
        cl.stop_mounts(mo_v, SMOKE_DIR, remove_cache=True)
    rep_doc_o = json.loads(rep_out_o.read_text())
    single_results["juicefs-overlay"] = {"replay": {"operations": rep_doc_o["operations"], "execution_seconds": rep_doc_o["execution_seconds"]}, "verify": ver_o}
    print(f"  [PASS] juicefs-overlay single-task replay ({rep_doc_o['operations']} ops) + cross-host verify", flush=True)
    (SMOKE_DIR / 'trace-checks.json').write_text(json.dumps(single_results, indent=2) + '\n')

    # === [3/3] 8-mount collector ON/OFF overhead calibration (3 pairs alternating order) ===
    print("=== [3/3] 8-mount collector ON/OFF overhead calibration (3 pairs under measurement.lock) ===", flush=True)
    # Place 8 mounts across the cluster matching C2-scale-01 host distribution (A366:2, A355:1, A672:1, A601:2, A635:1, A368:1)
    placement_8 = [
        ("A366", "idcai-60001", 1),
        ("A366", "idcai-60001", 2),
        ("A355", "idcai-60525", 1),
        ("A672", "idcai-60103", 1),
        ("A601", "idcai-60439", 1),
        ("A601", "idcai-60439", 2),
        ("A635", "idcai-60533", 1),
        ("A368", "idcai-60555", 1),
    ]

    def run_8mount_arm(pair_idx: int, collector_on: bool) -> dict:
        fork_8 = cl.extract_json_from_cli(
            cl.run_on_host(
                "A366",
                [str(cl.BIN_DIR / "ax-brewfs-admin"), "--meta-url", REDIS_URL, "--workspace-namespace", ns_tr, "fork-shared-bucket", "--revision", brew_rev, "--count", "8"],
                with_secrets=True,
            ).stdout
        )["workspaces"]
        tag = f"cal-p{pair_idx}-{'on' if collector_on else 'off'}"
        reqs = []
        specs = {}
        for idx, (hname, hssh, slot) in enumerate(placement_8):
            mid = f"{tag}-m{idx}"
            reqs.append(make_req(mid, "measure", "brewfs-native-overlay", hssh, slot, fork_8[idx], brew_base_ws, "cal-8", str(cl.BIN_DIR / "brewfs")))
            specs[mid] = {
                "workspace_uuid": fork_8[idx],
                "workspace_namespace": ns_tr,
                "s3_bucket": bkt_tr,
                "s3_endpoint": cl.endpoint_for_slot(slot, idx),
            }
        mp_cal = SMOKE_DIR / f"{tag}-mounts.json"
        cl.generate_mounts_json(reqs, mp_cal)

        if not collector_on:
            cl.collector_pause_all(True)
        else:
            cl.collector_pause_all(False)

        started = cl.start_mounts(mp_cal, specs, SMOKE_DIR, register_collector=collector_on)
        try:
            def _replay_one(m: dict) -> dict:
                hname = m["host_name"]
                out_f = f"/srv/ax-cluster/runs/direct/run-001/hosts/{hname}/{m['id']}-replay.json"
                cl.run_on_host(hname, ["rm", "-f", out_f], log_cmd=False)
                cl.run_on_host(
                    hname,
                    [
                        "/usr/bin/python3", "/srv/ax-cluster/direct/scripts/mount_driver.py", "exec-in-cgroup", "--cgroup", m["cgroup"], "--",
                        str(cl.BIN_DIR / "full-fuse-replay"), str(events_p), str(blobs_p), m["mount_directory"], out_f,
                        "--performance", "--simulate-llm", str(pacing_p),
                    ],
                )
                raw = cl.run_on_host(hname, ["cat", out_f], log_cmd=False).stdout
                return json.loads(raw.decode("utf-8"))

            t0 = time.monotonic()
            with ThreadPoolExecutor(max_workers=8) as pool:
                rep_docs = list(pool.map(_replay_one, started))
            cl.wait_uploads(started)
            makespan_s = time.monotonic() - t0
        finally:
            cl.stop_mounts(started, SMOKE_DIR, clear_collector=collector_on, remove_cache=True)
            if not collector_on:
                cl.collector_pause_all(False)

        merged_hist = cl.merge_log2_histograms([d["latency_us"]["all"] for d in rep_docs])
        active_mean = statistics.mean(d["active_replay_seconds"] for d in rep_docs)
        fs_op_mean = statistics.mean(d["fs_operation_seconds"] for d in rep_docs)
        return {
            "collector_on": collector_on,
            "makespan_seconds": round(makespan_s, 6),
            "mean_active_replay_seconds": round(active_mean, 6),
            "mean_fs_operation_seconds": round(fs_op_mean, 6),
            "merged_op_p50_us": merged_hist["p50_us"],
            "merged_op_p95_us": merged_hist["p95_us"],
            "merged_op_p99_us": merged_hist["p99_us"],
            "total_operations": merged_hist["count"],
            "task_results": rep_docs,
        }

    pairs = []
    with cl.MeasurementLock():
        for p_idx in range(1, 4):
            # Alternate order per pair: odd -> ON then OFF, even -> OFF then ON
            order = [True, False] if p_idx % 2 == 1 else [False, True]
            pair_arms = {}
            for is_on in order:
                res = run_8mount_arm(p_idx, is_on)
                pair_arms["on" if is_on else "off"] = res
                arm_name = f"cal-p{p_idx}-{'on' if is_on else 'off'}-result.json"
                (SMOKE_DIR / arm_name).write_text(json.dumps(res, indent=2) + '\n')
                print(f"  Pair {p_idx} collector={'ON' if is_on else 'OFF'}: makespan={res['makespan_seconds']:.3f}s active={res['mean_active_replay_seconds']:.3f}s p99={res['merged_op_p99_us']:.0f}us", flush=True)
            rel_makespan = (pair_arms["on"]["makespan_seconds"] - pair_arms["off"]["makespan_seconds"]) / pair_arms["off"]["makespan_seconds"]
            rel_active = (pair_arms["on"]["mean_active_replay_seconds"] - pair_arms["off"]["mean_active_replay_seconds"]) / pair_arms["off"]["mean_active_replay_seconds"]
            rel_p99 = (pair_arms["on"]["merged_op_p99_us"] - pair_arms["off"]["merged_op_p99_us"]) / max(1.0, pair_arms["off"]["merged_op_p99_us"])
            pairs.append(
                {
                    "pair": p_idx,
                    "order": ["on", "off"] if p_idx % 2 == 1 else ["off", "on"],
                    "on": pair_arms["on"],
                    "off": pair_arms["off"],
                    "relative_makespan_diff": round(rel_makespan, 6),
                    "relative_active_replay_diff": round(rel_active, 6),
                    "relative_p99_diff": round(rel_p99, 6),
                }
            )

    med_makespan_overhead = statistics.median(p["relative_makespan_diff"] for p in pairs)
    med_p99_overhead = statistics.median(p["relative_p99_diff"] for p in pairs)
    cal_report = {
        "schema": "ax-collector-overhead-calibration-v1",
        "mounts": 8,
        "pairs": pairs,
        "median_relative_makespan_diff": round(med_makespan_overhead, 6),
        "median_relative_p99_diff": round(med_p99_overhead, 6),
        "threshold": 0.05,
        "passed": abs(med_makespan_overhead) <= 0.05 and med_p99_overhead <= 0.05,
        "recorded_at": cl.utc_iso(),
    }

    (cl.RUN_ROOT / 'hosts/collector-overhead-8mounts.json').write_text(json.dumps(cal_report, indent=2) + '\n')
    cleanup = {'state': 'pending', 'buckets': [bkt_tr, bkt_tr_j, bkt_tr_o],
               'redis_db0_patterns': ['*axd1_smoke_tr_brew*', '{jfs-fork:axd1-smoke-tr-jov}:*'],
               'redis_databases': [14], 'recorded_at': cl.utc_iso()}
    (SMOKE_DIR / 'trace-cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
    print('Trace results saved; temporary objects are registered in mounts/smoke/trace-cleanup.json', flush=True)

    return single_results, cal_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=['all','files','trace'], default='all')
    parser.add_argument('--engines', nargs='+', choices=['brewfs-native-overlay','brewfs-packed','juicefs','juicefs-overlay'])
    args = parser.parse_args()
    res_1k = test_1000_files_all_engines(args.engines) if args.stage in ('all','files') else {}
    if args.stage == 'files':
        print(json.dumps(res_1k, indent=2))
        return
    res_tr, cal_report = test_single_task_trace_and_8mount_calibration()

    # Flush all 6 host collectors so pre-workload raw segments exist and are verified readable!
    flush_status = {}
    for h in cl.ALL_HOST_NAMES:
        flush_status[h] = cl.collector_rpc(h, {"action": "flush"})

    smoke_summary = {
        "schema": "ax-smoke-verification-v1",
        "recorded_at": cl.utc_iso(),
        "thousand_file_smoke": res_1k,
        "single_task_trace_smoke": res_tr,
        "collector_flush": flush_status,
    }
    smoke_out = cl.RUN_ROOT / "mounts/smoke-verification.json"
    smoke_out.write_text(json.dumps(smoke_summary, indent=2) + "\n", encoding="utf-8")

    cal_out = cl.RUN_ROOT / "hosts/collector-overhead-8mounts.json"
    cal_out.write_text(json.dumps(cal_report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(cal_report, indent=2), flush=True)
    if not cal_report['passed']:
        raise RuntimeError('Collector overhead exceeds the planned threshold')
    print('Filesystem checks and eight-mount collector calibration passed',flush=True)


if __name__ == "__main__":
    main()
