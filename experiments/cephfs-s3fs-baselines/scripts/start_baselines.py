#!/usr/bin/env python3
"""Sync, prepare, and launch the CephFS/s3fs baseline suite via systemd-run on A366 (idcai-60001)."""

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
BASELINE_PKG_DIR = SCRIPT_DIR.parent
REPO_ROOT = BASELINE_PKG_DIR.parents[1]
sys.path.insert(0, str(BASELINE_PKG_DIR / "runtime"))

from baseline_suite import EXPECTED_GROUPS, SUPPORTED_SYSTEMS
import sync_baselines


def _ssh_a366(remote_cmd: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "idcai-60001", remote_cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=EXPECTED_GROUPS)
    parser.add_argument("--system", choices=SUPPORTED_SYSTEMS)
    parser.add_argument("--job")
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--check", action="store_true", help="Launch small-data check run across all 28 jobs")
    mode_grp.add_argument("--tiny", action="store_true", help="Launch ultra-small-scale verification across representative jobs")
    parser.add_argument("--coexist-safe", action="store_true", help="Skip measurement.lock and drop_caches during tiny/check verification")
    parser.add_argument("--skip-main-guard", action="store_true", help="Allow starting while main suite is active")
    parser.add_argument("--sync-only", action="store_true", help="Only sync files to the 6 hosts without starting daemons or runs")
    args = parser.parse_args()

    cluster_cfg = json.loads((REPO_ROOT / "configs" / "cluster.json").read_text(encoding="utf-8"))
    rt_cfg = json.loads((BASELINE_PKG_DIR / "configs" / "baseline-runtime.json").read_text(encoding="utf-8"))

    synced = sync_baselines.sync_all_hosts(cluster_cfg["hosts"], rt_cfg["remote_baseline_root"])
    if args.sync_only:
        print(json.dumps({"ok": True, "mode": "sync-only", "synced": synced}, indent=2))
        return

    venv_py = "/srv/ax-idcai-20261113/venv/bin/python3"
    scripts_dir = f"{rt_cfg['remote_baseline_root']}/scripts"
    run_root = f"{rt_cfg['remote_runs_root']}/{rt_cfg['run_id']}"

    setup_cmd = f"{venv_py} {scripts_dir}/setup_baselines.py"
    if args.system:
        setup_cmd += f" --system {shlex.quote(args.system)}"
    if args.skip_main_guard:
        setup_cmd += " --skip-main-guard"
    setup_p = _ssh_a366(setup_cmd, timeout=600)
    if setup_p.returncode != 0:
        raise SystemExit(f"setup_baselines.py failed:\nSTDOUT:\n{setup_p.stdout}\nSTDERR:\n{setup_p.stderr}")

    if args.tiny:
        unit = rt_cfg.get("runner_tiny_unit", f"{rt_cfg['runner_unit']}-tiny")
        log_name = "suite-tiny.log"
    elif args.check:
        unit = rt_cfg["runner_check_unit"]
        log_name = "suite-check.log"
    else:
        unit = rt_cfg["runner_unit"]
        log_name = "suite.log"
    log_path = f"{run_root}/logs/{log_name}"
    run_argv = [
        "systemd-run",
        f"--unit={unit}",
        "--collect",
        f"--property=WorkingDirectory={rt_cfg['remote_baseline_root']}",
        f"--property=Environment=PYTHONPATH={rt_cfg['remote_baseline_root']}:{scripts_dir}",
        "--property=MemoryMax=8G",
        "--property=MemorySwapMax=0",
        "--property=LimitNOFILE=1048576",
        f"--property=StandardOutput=append:{log_path}",
        f"--property=StandardError=append:{log_path}",
        "--",
        venv_py,
        f"{scripts_dir}/run_baseline_suite.py",
    ]
    if args.tiny:
        run_argv.append("--tiny")
    elif args.check:
        run_argv.append("--check")
    if args.coexist_safe:
        run_argv.append("--coexist-safe")
    if args.skip_main_guard:
        run_argv.append("--skip-main-guard")
    if args.group:
        run_argv += ["--group", args.group]
    if args.system:
        run_argv += ["--system", args.system]
    if args.job:
        run_argv += ["--job", args.job]

    quoted_run = " ".join(shlex.quote(x) for x in run_argv)
    launch_cmd = (
        f"mkdir -p {shlex.quote(run_root + '/logs')} && "
        f"if [ \"$(systemctl is-active {shlex.quote(unit)} 2>/dev/null)\" = 'active' ]; then "
        f"echo 'ALREADY_ACTIVE'; exit 2; fi && "
        f"systemctl reset-failed {shlex.quote(unit)} >/dev/null 2>&1 || true && "
        f"{quoted_run}"
    )
    lp = _ssh_a366(launch_cmd, timeout=60)
    if lp.returncode != 0:
        raise SystemExit(f"Failed to launch systemd unit {unit!r}:\n{lp.stdout}\n{lp.stderr}")

    print(
        json.dumps(
            {
                "ok": True,
                "runner_unit": unit,
                "run_root": run_root,
                "log_file": log_path,
                "command": run_argv,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
