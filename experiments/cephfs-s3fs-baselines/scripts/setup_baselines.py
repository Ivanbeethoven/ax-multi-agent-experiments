#!/usr/bin/env python3
"""Prepare all 6 cluster hosts on A366 for CephFS and s3fs baseline runs."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import time

SCRIPT_DIR = Path(__file__).resolve().parent
for cand in (SCRIPT_DIR, SCRIPT_DIR.parent / "runtime"):
    if (cand / "baseline_cluster_lib.py").is_file():
        sys.path.insert(0, str(cand))
        break

import baseline_cluster_lib as cl
import baseline_context as ctx
from baseline_suite import SUPPORTED_SYSTEMS, build_plan


COLLECTOR_UNIT_TEMPLATE = """[Unit]
Description=AX Baseline Telemetry Collector ({host})
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {scripts_dir}/baseline_collector.py --host {host} --socket {sock} --out-dir {out_dir}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
"""


def _install_s3fs_passwd_all() -> None:
    sec = cl.load_s3_secrets()
    if not sec["s3_access_key"] or not sec["s3_secret_key"]:
        raise RuntimeError(f"Missing S3 credentials in {cl.RUNTIME_CFG['rustfs_env_path']}")
    passwd_content = f"{sec['s3_access_key']}:{sec['s3_secret_key']}\n"
    passwd_path = cl.RUNTIME_CFG["s3fs_passwd_file"]

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
        tf.write(passwd_content)
        tmp_local = Path(tf.name)
    os.chmod(tmp_local, 0o600)
    try:
        for h in cl.ALL_HOST_NAMES:
            cl.sync_file_to_host(h, tmp_local, passwd_path)
            cl.run_on_host(h, ["chmod", "600", passwd_path], log_cmd=False)
    finally:
        tmp_local.unlink(missing_ok=True)


def _setup_collectors() -> None:
    svc_name = cl.RUNTIME_CFG["collector_service_name"]

    def _one(h: str) -> dict:
        out_dir = f"{cl.RUN_ROOT}/telemetry/{h}"
        unit_txt = COLLECTOR_UNIT_TEMPLATE.format(
            host=h,
            scripts_dir=cl.SCRIPTS_DIR,
            sock=cl.COLLECTOR_SOCK,
            out_dir=out_dir,
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
            tf.write(unit_txt)
            tmp_unit = Path(tf.name)
        try:
            cl.run_on_host(h, ["mkdir", "-p", out_dir, str(cl.COLLECTOR_SOCK.parent)], log_cmd=False)
            cl.sync_file_to_host(h, tmp_unit, f"/etc/systemd/system/{svc_name}")
            cl.run_on_host(h, ["systemctl", "daemon-reload"], log_cmd=False)
            cl.run_on_host(h, ["systemctl", "enable", "--now", svc_name], log_cmd=False)
            cl.run_on_host(h, ["systemctl", "restart", svc_name], log_cmd=False)
        finally:
            tmp_unit.unlink(missing_ok=True)
        last_err = None
        for _ in range(50):
            try:
                return cl.collector_rpc(h, {"action": "ping"})
            except Exception as exc:
                last_err = exc
                time.sleep(0.2)
        raise RuntimeError(f"Collector socket did not become ready on {h}: {last_err}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(_one, cl.ALL_HOST_NAMES))


def _stop_cephfs_if_running() -> bool:
    chk = cl.run_on_host(
        "A366",
        ["systemctl", "is-active", "--quiet", "ceph-mon@A366.service"],
        check=False,
        log_cmd=False,
    )
    if chk.returncode == 0:
        cl.run_on_host("A366", ["/usr/local/bin/cephfs-cluster", "stop"], timeout=300.0, check=False)
        return True
    return False


def _teardown_baselines(stop_cephfs: bool) -> dict:
    svc_name = cl.RUNTIME_CFG["collector_service_name"]
    for h in cl.ALL_HOST_NAMES:
        cl.run_on_host(h, ["systemctl", "stop", svc_name], check=False, log_cmd=False)
    if stop_cephfs:
        _stop_cephfs_if_running()
    return {"ok": True, "teardown": True, "cephfs_stopped": stop_cephfs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", choices=SUPPORTED_SYSTEMS, help="Prepare only cephfs or only s3fs")
    parser.add_argument("--skip-main-guard", action="store_true")
    parser.add_argument("--teardown", action="store_true", help="Stop baseline collectors (and optionally CephFS)")
    parser.add_argument("--stop-cephfs", action="store_true", help="With --teardown, also stop CephFS cluster")
    args = parser.parse_args()

    if args.teardown:
        print(json.dumps(_teardown_baselines(args.stop_cephfs), indent=2))
        return

    if not args.skip_main_guard:
        cl.assert_cluster_idle_for_baselines()

    cl.RUN_ROOT.mkdir(parents=True, exist_ok=True)
    plan = build_plan(ctx.load_baseline_suite())
    plan_path = cl.RUN_ROOT / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

    want_cephfs = args.system in (None, "cephfs")
    want_s3fs = args.system in (None, "s3fs")

    if want_s3fs:
        _install_s3fs_passwd_all()
        cl.ensure_hardlink_copy_shim(cl.ALL_HOST_NAMES)
        for ep in cl.RUSTFS_BACKENDS[cl.RUNTIME_CFG["default_s3fs_backend"]]["endpoints"]:
            cl.make_s3_client(ep).list_buckets()

    if want_cephfs:
        cl.run_on_host("A366", ["/usr/local/bin/cephfs-cluster", "start"], timeout=300.0)
        for h in cl.ALL_HOST_NAMES:
            cl.run_on_host(
                h,
                ["sh", "-c", "mountpoint -q /mnt/cephfs && umount -l /mnt/cephfs || true"],
                check=False,
                log_cmd=False,
            )
    else:
        _stop_cephfs_if_running()

    _setup_collectors()
    print(
        json.dumps(
            {
                "ok": True,
                "run_root": str(cl.RUN_ROOT),
                "total_planned_jobs": plan["total_jobs"],
                "cephfs_prepared": want_cephfs,
                "s3fs_prepared": want_s3fs,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
