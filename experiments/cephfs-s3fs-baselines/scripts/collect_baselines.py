#!/usr/bin/env python3
"""Collect finished CephFS/s3fs baseline run artifacts from A366 back to local results/baselines/."""

import argparse
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

SCRIPT_DIR = Path(__file__).resolve().parent
BASELINE_PKG_DIR = SCRIPT_DIR.parent
REPO_ROOT = BASELINE_PKG_DIR.parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="Override run_id from baseline-runtime.json")
    parser.add_argument("--dest", type=Path, help="Local destination directory")
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--checks-only", action="store_true", help="Download only checks/ and check-status.json")
    mode_grp.add_argument("--tiny-only", action="store_true", help="Download only tiny/ and tiny-status.json")
    args = parser.parse_args()

    rt_cfg = json.loads((BASELINE_PKG_DIR / "configs" / "baseline-runtime.json").read_text(encoding="utf-8"))
    run_id = args.run_id or rt_cfg["run_id"]
    remote_run_dir = f"{rt_cfg['remote_runs_root']}/{run_id}"
    local_dest = args.dest or (REPO_ROOT / "results" / "baselines" / run_id)
    local_dest.mkdir(parents=True, exist_ok=True)

    if args.tiny_only:
        tar_cmd = (
            f"cd {shlex.quote(remote_run_dir)} && "
            "tar --exclude='mnt' --exclude='cache' --exclude='tmp' -cf - "
            "$( [ -f tiny-status.json ] && echo tiny-status.json ) "
            "$( [ -f plan.json ] && echo plan.json ) "
            "$( [ -d tiny ] && echo tiny )"
        )
    elif args.checks_only:
        tar_cmd = (
            f"cd {shlex.quote(remote_run_dir)} && "
            "tar --exclude='mnt' --exclude='cache' --exclude='tmp' -cf - "
            "$( [ -f check-status.json ] && echo check-status.json ) "
            "$( [ -f plan.json ] && echo plan.json ) "
            "$( [ -d checks ] && echo checks )"
        )
    else:
        tar_cmd = (
            f"cd {shlex.quote(remote_run_dir)} && "
            "tar --exclude='mnt' --exclude='cache' --exclude='tmp' -cf - ."
        )

    p = subprocess.run(
        ["ssh", "idcai-60001", tar_cmd],
        capture_output=True,
        timeout=1800,
        check=True,
    )
    with tarfile.open(fileobj=io.BytesIO(p.stdout), mode="r:") as tf:
        tf.extractall(path=local_dest, filter="data")

    print(
        json.dumps(
            {
                "ok": True,
                "remote_run_dir": remote_run_dir,
                "local_dest": str(local_dest),
                "bytes_transferred": len(p.stdout),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
