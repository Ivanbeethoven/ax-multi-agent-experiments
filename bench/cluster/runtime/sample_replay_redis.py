"""Sample Redis while one replay attempt is active; write metrics on the coordinator."""
import argparse
import json
import time
from pathlib import Path

import cluster_lib as cl


def sample():
    started = time.monotonic()
    info = dict(line.split(":", 1) for line in cl.redis_cli("INFO", "ALL").splitlines()
                if ":" in line and not line.startswith("#"))
    keys = ("uptime_in_seconds", "blocked_clients", "rejected_connections", "evicted_keys",
            "instantaneous_ops_per_sec", "total_commands_processed", "used_cpu_user_main_thread",
            "used_cpu_sys_main_thread", "aof_delayed_fsync", "aof_pending_bio_fsync")
    return {key: float(info[key]) for key in keys if key in info} | {
        "utc_ns": time.time_ns(), "info_elapsed_ms": round((time.monotonic() - started) * 1000, 2)}


def main(job, attempt):
    root = cl.RUN_ROOT
    dest = root / "diagnostics" / f"{job}-{attempt}"
    dest.mkdir(exist_ok=True, parents=True)
    with (dest / "redis-samples.jsonl").open("a", buffering=1) as log:
        while True:
            row = json.loads((root / "status.json").read_text())["jobs"][job]
            if Path(row.get("output", "")).name != attempt or row["state"] != "running":
                return
            try:
                data = sample()
            except (OSError, ValueError, RuntimeError) as exc:
                data = {"utc_ns": time.time_ns(), "sample_error": type(exc).__name__}
            log.write(json.dumps(data) + "\n")
            time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    main(args.job, args.attempt)
