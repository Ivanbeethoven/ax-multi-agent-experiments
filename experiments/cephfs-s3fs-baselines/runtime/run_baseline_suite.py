#!/usr/bin/env python3
"""Run the CephFS and s3fs baseline experiment suite across groups 01..04."""

import argparse
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_cluster_lib as cl
import baseline_context as ctx
from baseline_groups import run_fork, run_io, run_packed, run_replay, save_json
from baseline_suite import EXPECTED_GROUPS, SUPPORTED_SYSTEMS, build_plan, select_tiny_jobs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=EXPECTED_GROUPS)
    parser.add_argument("--system", choices=SUPPORTED_SYSTEMS)
    parser.add_argument("--job")
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--check", action="store_true", help="Run small-data qualification on all 28 conditions")
    mode_grp.add_argument("--tiny", action="store_true", help="Run ultra-small-scale verification across groups 01..04")
    parser.add_argument("--coexist-safe", action="store_true", help="Skip measurement.lock and drop_caches for safe coexistence")
    parser.add_argument("--skip-main-guard", action="store_true", help="Skip active run-002 status check")
    args = parser.parse_args()

    if args.coexist_safe:
        cl.set_coexist_safe(True)

    if not args.skip_main_guard:
        cl.assert_cluster_idle_for_baselines()

    plan_path = cl.RUN_ROOT / "plan.json"
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    else:
        plan = build_plan(ctx.load_baseline_suite())
        save_json(plan_path, plan)

    scale = "tiny" if args.tiny else ("check" if args.check else "formal")
    status_name = {"tiny": "tiny-status.json", "check": "check-status.json", "formal": "status.json"}[scale]
    status_path = cl.RUN_ROOT / status_name
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {"scale": scale, "jobs": {}}

    runners = {
        "01-io": run_io,
        "02-fork": run_fork,
        "03-packed": run_packed,
        "04-agent-replay": run_replay,
    }

    candidate_jobs = select_tiny_jobs(plan["jobs"]) if (args.tiny and not args.job) else plan["jobs"]

    for job in candidate_jobs:
        if args.group and job["group"] != args.group:
            continue
        if args.system and job["system"] != args.system:
            continue
        if args.job and job["id"] != args.job:
            continue
        if status["jobs"].get(job["id"], {}).get("state") == "passed":
            continue

        if scale == "tiny":
            output = cl.RUN_ROOT / "tiny" / job["output"]
        elif scale == "check":
            output = cl.RUN_ROOT / "checks" / job["output"]
        else:
            output = cl.RUN_ROOT / job["output"]
        output.mkdir(parents=True, exist_ok=True)
        attempts = sorted(output.glob("attempt-*"))
        attempt_dir = output / f"attempt-{len(attempts) + 1:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        save_json(attempt_dir / "job.json", job)

        status["scale"] = scale
        status["current"] = job["id"]
        status["jobs"][job["id"]] = {
            "state": "running",
            "scale": scale,
            "system": job["system"],
            "group": job["group"],
            "started_at": cl.utc_iso(),
            "output": str(attempt_dir),
        }
        save_json(status_path, status)
        print(f"START [{scale}] {job['id']} ({job['system']}) {job['parameters']}", flush=True)

        try:
            result = runners[job["group"]](job, attempt_dir, scale=scale)
            result.update(
                job_id=job["id"],
                group=job["group"],
                system=job["system"],
                parameters=job["parameters"],
                state="passed",
                scale=scale,
                coexist_safe=bool(args.coexist_safe),
                validation_only=(scale != "formal"),
            )
            save_json(attempt_dir / "result.json", result)
            save_json(output / "result.json", result)
            status["jobs"][job["id"]].update(state="passed", ended_at=cl.utc_iso())
            print(f"DONE {job['id']}", flush=True)
        except Exception as exc:
            detail = {"state": "failed", "ended_at": cl.utc_iso(), "error": str(exc), "traceback": traceback.format_exc()}
            save_json(attempt_dir / "failure.json", detail)
            status["jobs"][job["id"]].update(detail)
            save_json(status_path, status)
            raise

        status["completed_runs"] = sum(item.get("state") == "passed" for item in status["jobs"].values())
        save_json(status_path, status)
        for host in cl.ALL_HOST_NAMES:
            try:
                cl.collector_rpc(host, {"action": "flush"})
            except Exception:
                pass

    status["current"] = None
    save_json(status_path, status)


if __name__ == "__main__":
    main()
