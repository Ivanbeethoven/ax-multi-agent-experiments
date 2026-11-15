#!/usr/bin/env python3
"""Derive summary from immutable raw JSON and check comparability controls."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def metric(text, name, label=None):
    if not isinstance(text, str):
        return None
    values = []
    for line in text.splitlines():
        if not re.match(re.escape(name) + r"(?:\{|\s)", line):
            continue
        if label and label not in line:
            continue
        values.append(float(line.rsplit(None, 1)[-1]))
    return sum(values) if values else None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", type=Path, required=True)
    p.add_argument("--trace", type=Path, required=True)
    p.add_argument("--diagnostic-results", type=Path, help="separate clean timing=op pair, if used")
    a = p.parse_args()
    def load(name):
        diagnostic = a.diagnostic_results and ("-op." in name or "-op-" in name)
        root = a.diagnostic_results if diagnostic else a.results
        return json.loads((root / name).read_text(encoding="utf-8-sig"))
    rows = []
    for cell in ("brewfs-off", "jfs-off", "brewfs-op", "jfs-op"):
        m = load(cell + ".measurement.json")
        if "error" in m:
            rows.append({"cell": cell, "error": m["error"]})
            continue
        raw = load(cell + ".result.json")
        before = load(cell + "-before-backend.json")
        after = load(cell + "-after-backend.json")
        key = "brewfs_s3_put_bytes_total" if cell.startswith("brewfs") else "juicefs_object_request_data_bytes"
        src = "brewfs_stats" if cell.startswith("brewfs") else "jfs_metrics"
        label = None if cell.startswith("brewfs") else 'method="PUT"'
        b, e = metric(before.get(src), key, label), metric(after.get(src), key, label)
        daemon = m["daemon_processes"]
        daemon_cpu = {}
        for name, stats in daemon["before"].items():
            end = daemon["after"].get(name, {})
            if "user_ticks" in stats and "user_ticks" in end:
                ticks = end["user_ticks"] + end["system_ticks"] - stats["user_ticks"] - stats["system_ticks"]
                seconds = ticks / daemon["clock_ticks_per_second"]
                daemon_cpu[name] = {"cpu_seconds": seconds,
                    "cpu_percent_one_core": seconds / m["rusage"]["process_wall_seconds"] * 100,
                    "sampled_peak_rss_kib": daemon["sampled_peak_rss_kib"][name]}
        pending = {key: metric(after.get("brewfs_stats"), key) for key in (
            "brewfs_writeback_dirty_bytes", "brewfs_writeback_live_dirty_bytes",
            "brewfs_writeback_recent_pending_upload_bytes", "brewfs_writeback_remote_upload_inflight_bytes")}
        jfs_metrics = {}
        for key in ("juicefs_process_cpu_seconds_total", "juicefs_process_resident_memory_bytes", "juicefs_process_start_time_seconds"):
            v1, v2 = metric(before.get("jfs_metrics"), key), metric(after.get("jfs_metrics"), key)
            jfs_metrics[key] = {"before": v1, "after": v2,
                                "delta": v2 - v1 if v1 is not None and v2 is not None else None}
        rows.append({"cell": cell, "raw_results_directory": str(a.diagnostic_results if a.diagnostic_results and cell.endswith("op") else a.results),
            "timing": raw["timing"], "workers": raw["workers"], "pace": raw["pace"],
            "valid": raw["valid"], "initial_tree_hash_verified": raw["initial_tree_hash_verified"],
            "initial_tree_sha256": raw["declared_initial_tree_sha256"], "trace_sha256": raw["trace_sha256"],
            "attempted": raw["totals"]["attempted"], "successful": raw["totals"]["successful"],
            "expected_errors": raw["totals"]["expected_errors"], "failures": raw["totals"]["mismatches"],
            "io_errors": raw["totals"]["io_errors"], "execution_seconds": raw["execution_seconds"],
            "logical_operations_per_second": raw["logical_operations_per_second"],
            "read_bytes": raw["totals"]["actual_read_bytes"], "write_bytes": raw["totals"]["actual_write_bytes"],
            "replay_process": m["rusage"], "daemon_cpu_and_rss": daemon_cpu,
            "juicefs_process_metrics": jfs_metrics,
            "diagnostic_latency": m["diagnostic_latency"],
            "backend_put_counter": {"metric": key, "label": label, "before": b, "after": e,
                "delta_bytes": (e - b) if b is not None and e is not None else None,
                "scope": "client metric snapshot before replay to five seconds after process exit; fixture excluded; includes backend representation/metadata PUT payloads, not wire bytes"},
            "brewfs_after_pending_byte_gauges": pending,
            "post_tree": json.loads(m["post_tree"]["stdout"]).get("initial_tree_sha256") if m["post_tree"].get("returncode") == 0 else m["post_tree"]})
    complete = [x for x in rows if "error" not in x]
    trace_hash = hashlib.sha256(a.trace.read_bytes()).hexdigest()
    checks = {
        "four_completed_cells": len(complete) == 4,
        "same_trace": len({x["trace_sha256"] for x in complete}) == 1 and all(x["trace_sha256"] == trace_hash for x in complete),
        "same_initial_tree": len({x["initial_tree_sha256"] for x in complete}) == 1,
        "same_final_tree": len({x["post_tree"] for x in complete if isinstance(x["post_tree"], str)}) == 1 and all(isinstance(x["post_tree"], str) for x in complete),
        "all_valid": all(x["valid"] and x["failures"] == 0 and x["io_errors"] == 0 for x in complete),
        "initial_hashes_verified": all(x["initial_tree_hash_verified"] for x in complete),
        "same_operation_counts": len({x["attempted"] for x in complete}) == 1,
        "same_read_bytes": len({x["read_bytes"] for x in complete}) == 1,
        "same_write_bytes": len({x["write_bytes"] for x in complete}) == 1,
        "required_workers_and_pace": all(x["workers"] == 4 and x["pace"] == "asap" for x in complete),
        "primary_timing_off": all(x["timing"] == "off" and x["diagnostic_latency"] is None for x in complete if x["cell"].endswith("off")),
        "daemon_pids_alive_at_end": all(all("error" not in v for v in load(x["cell"] + ".measurement.json")["daemon_processes"]["after"].values()) for x in complete),
        "juicefs_client_start_time_stable": all(x["juicefs_process_metrics"]["juicefs_process_start_time_seconds"]["before"] is not None and x["juicefs_process_metrics"]["juicefs_process_start_time_seconds"]["delta"] == 0 for x in complete),
        "trace_below_50MB": a.trace.stat().st_size <= 50_000_000,
    }
    output = {"checks": checks, "trace_bytes": a.trace.stat().st_size, "trace_sha256": trace_hash, "rows": rows}
    (a.results / "summary.json").write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
