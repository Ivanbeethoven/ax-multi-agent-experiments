"""Validate baseline-suite.json and export a deterministic 28-run execution plan."""

import argparse
import json
from pathlib import Path

EXPECTED_GROUPS = ("01-io", "02-fork", "03-packed", "04-agent-replay")
SUPPORTED_SYSTEMS = ("cephfs", "s3fs")


def validate_suite(suite: dict) -> dict:
    if suite.get("schema") != "ax-baseline-suite-v1":
        raise ValueError(f"Unsupported schema: {suite.get('schema')}")
    if suite.get("repetitions") != 1:
        raise ValueError("Baseline suite repetitions must match main suite (1)")
    seeds = suite.get("seeds") or []
    if len(seeds) < 1:
        raise ValueError("At least one deterministic seed is required")
    limits = suite.get("resource_limits") or {}
    if limits.get("workspace_memory_mib") != 4096 or limits.get("workspace_disk_mib") != 8192:
        raise ValueError("Resource limits must match main suite: 4096 MiB RAM / 8192 MiB disk")

    groups = suite.get("groups") or {}
    if tuple(groups.keys()) != EXPECTED_GROUPS:
        raise ValueError(f"Expected groups {EXPECTED_GROUPS}, got {tuple(groups.keys())}")

    total_runs = 0
    per_system: dict[str, int] = {s: 0 for s in SUPPORTED_SYSTEMS}
    for group_name, group_doc in groups.items():
        configs = group_doc.get("configurations") or []
        declared = int(group_doc.get("runs", -1))
        expected = len(configs) * int(suite["repetitions"])
        if declared != expected:
            raise ValueError(f"Group {group_name} declared {declared} runs, expected {expected}")
        for cfg in configs:
            sys_name = cfg.get("system")
            if sys_name not in SUPPORTED_SYSTEMS:
                raise ValueError(f"Unsupported baseline system {sys_name!r} in {group_name}")
            per_system[sys_name] += int(suite["repetitions"])
        total_runs += expected

    return {
        "valid": True,
        "run_id": suite["run_id"],
        "total_runs": total_runs,
        "per_system_runs": per_system,
        "groups": {g: int(groups[g]["runs"]) for g in EXPECTED_GROUPS},
    }


def select_tiny_jobs(jobs: list[dict]) -> list[dict]:
    """Select an ultra-small representative subset across all 4 groups for both cephfs and s3fs."""
    selected: list[dict] = []
    for job in jobs:
        g = job["group"]
        p = job["parameters"]
        if g == "01-io":
            if (p["pattern"] == "sequential" and p["operation"] == "read") or (
                p["pattern"] == "random" and p["operation"] == "write"
            ):
                selected.append(job)
        elif g == "02-fork":
            if p["mode"] == "fork" and int(p["children"]) in (1, 16):
                selected.append(job)
        elif g == "03-packed":
            selected.append(job)
        elif g == "04-agent-replay":
            if int(p["agents"]) == 8:
                selected.append(job)
    return selected


def build_plan(
    suite: dict,
    *,
    group_filter: str | None = None,
    system_filter: str | None = None,
    tiny: bool = False,
) -> dict:
    summary = validate_suite(suite)
    repetitions = int(suite["repetitions"])
    seeds = [int(x) for x in suite["seeds"]]
    jobs: list[dict] = []

    for group_name in EXPECTED_GROUPS:
        if group_filter and group_name != group_filter:
            continue
        group_doc = suite["groups"][group_name]
        shared = {k: v for k, v in group_doc.items() if k not in ("configurations", "runs")}
        for idx, cfg in enumerate(group_doc["configurations"], start=1):
            if system_filter and cfg["system"] != system_filter:
                continue
            for rep in range(1, repetitions + 1):
                params = dict(shared)
                params.update(cfg)
                job_id = f"{group_name}-{cfg['system']}-{idx:02d}-r{rep}"
                jobs.append(
                    {
                        "id": job_id,
                        "group": group_name,
                        "configuration": idx,
                        "system": cfg["system"],
                        "repeat": rep,
                        "seed": seeds[rep - 1],
                        "output": f"{group_name}/{job_id}",
                        "parameters": params,
                    }
                )

    if tiny:
        jobs = select_tiny_jobs(jobs)

    return {
        "schema": "ax-baseline-plan-v1",
        "run_id": suite["run_id"],
        "validation": summary,
        "filters": {"group": group_filter, "system": system_filter, "tiny": tiny},
        "total_jobs": len(jobs),
        "jobs": jobs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--group", choices=EXPECTED_GROUPS)
    parser.add_argument("--system", choices=SUPPORTED_SYSTEMS)
    parser.add_argument("--tiny", action="store_true", help="Select ultra-small representative jobs across groups 01..04")
    parser.add_argument("--export-plan", type=Path)
    args = parser.parse_args()

    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    plan = build_plan(suite, group_filter=args.group, system_filter=args.system, tiny=args.tiny)
    if args.export_plan:
        args.export_plan.parent.mkdir(parents=True, exist_ok=True)
        args.export_plan.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "total_jobs": plan["total_jobs"], "validation": plan["validation"]}))


if __name__ == "__main__":
    main()
