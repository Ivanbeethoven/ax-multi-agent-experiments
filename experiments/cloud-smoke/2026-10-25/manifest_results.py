#!/usr/bin/env python3
"""Hash the cloud raw archive and map generated inputs to delivery-relative paths."""
import hashlib
import json
from pathlib import Path
import time

base = Path("/data/cloud-smoke/2026-10-25")
results = base / "results"
manifest = {"created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cloud_results": str(results.resolve()), "files": {}, "automation_scripts": {}}
def add(source, relative):
    manifest["files"][relative] = {"bytes": source.stat().st_size,
                                  "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
for path in sorted(results.rglob("*")):
    if path.is_file() and path.name != "cloud-manifest.json":
        add(path, path.relative_to(results).as_posix())
for name in ("workload.jsonl", "synthesis.json", "trace-statistics.json"):
    add(base / "generated" / name, name)
for name in ("convert_trace.py", "run_smoke.py", "instrument_latency.py", "audit_results.py", "verify_conversion.py", "manifest_results.py", "install-stack.sh", "repair-rustfs-log.sh", "collect-incidents.sh", "install_brewfs_single_node.sh"):
    manifest["automation_scripts"][name] = hashlib.sha256((base / name).read_bytes()).hexdigest()
(results / "cloud-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"files": len(manifest["files"]), "bytes": sum(x["bytes"] for x in manifest["files"].values())}))
