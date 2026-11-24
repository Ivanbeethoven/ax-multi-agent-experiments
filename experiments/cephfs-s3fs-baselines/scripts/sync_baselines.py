#!/usr/bin/env python3
"""Package and upload CephFS/s3fs baseline configs and scripts to /srv/ax-cluster/baselines on all 6 hosts."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import subprocess
import tarfile

SCRIPT_DIR = Path(__file__).resolve().parent
BASELINE_PKG_DIR = SCRIPT_DIR.parent
REPO_ROOT = BASELINE_PKG_DIR.parents[1]


def build_bundle_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        def _add_bytes(arcname: str, payload: bytes, mode: int = 0o644) -> None:
            ti = tarfile.TarInfo(name=arcname)
            ti.size = len(payload)
            ti.mode = mode
            tf.addfile(ti, io.BytesIO(payload))

        for cfg in sorted((BASELINE_PKG_DIR / "configs").glob("*.json")):
            _add_bytes(f"configs/{cfg.name}", cfg.read_bytes(), 0o644)

        for name in ("cluster.json", "rustfs-backends.json", "trace-inputs.json"):
            src = REPO_ROOT / "configs" / name
            _add_bytes(f"repo-configs/{name}", src.read_bytes(), 0o644)

        for py in sorted((BASELINE_PKG_DIR / "runtime").glob("*.py")):
            _add_bytes(f"scripts/{py.name}", py.read_bytes(), 0o755)

        for csrc in sorted((BASELINE_PKG_DIR / "runtime").glob("*.c")):
            _add_bytes(f"scripts/{csrc.name}", csrc.read_bytes(), 0o644)

        _add_bytes("scripts/setup_baselines.py", (SCRIPT_DIR / "setup_baselines.py").read_bytes(), 0o755)

        # Include bench.cluster.c4_workload so `python3 -m bench.cluster.c4_workload` works directly
        _add_bytes("bench/__init__.py", b"", 0o644)
        _add_bytes("bench/cluster/__init__.py", b"", 0o644)
        _add_bytes(
            "bench/cluster/c4_workload.py",
            (REPO_ROOT / "bench" / "cluster" / "c4_workload.py").read_bytes(),
            0o644,
        )
    return buf.getvalue()


def sync_all_hosts(hosts: list[dict], remote_root: str) -> list[dict]:
    payload = build_bundle_bytes()

    def _sync_one(h: dict) -> dict:
        alias = h["ssh"]
        p = subprocess.run(
            ["ssh", alias, f"mkdir -p {remote_root} && tar -xf - -C {remote_root}"],
            input=payload,
            capture_output=True,
            timeout=120,
            check=True,
        )
        return {"host": h["name"], "ssh": alias, "bytes": len(payload), "returncode": p.returncode}

    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(_sync_one, hosts))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="Optional single host name (e.g. node-coord) or SSH alias")
    args = parser.parse_args()

    cluster_cfg = json.loads((REPO_ROOT / "configs" / "cluster.json").read_text(encoding="utf-8"))
    rt_cfg = json.loads((BASELINE_PKG_DIR / "configs" / "baseline-runtime.json").read_text(encoding="utf-8"))
    hosts = cluster_cfg["hosts"]
    if args.host:
        hosts = [h for h in hosts if args.host in (h["name"], h["ssh"])]
        if not hosts:
            raise ValueError(f"Unknown host: {args.host}")

    results = sync_all_hosts(hosts, rt_cfg["remote_baseline_root"])
    print(json.dumps({"ok": True, "remote_root": rt_cfg["remote_baseline_root"], "synced": results}, indent=2))


if __name__ == "__main__":
    main()
