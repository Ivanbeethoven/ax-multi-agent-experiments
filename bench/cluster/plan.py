"""Validate the design and export paired placements without contacting servers."""

from __future__ import annotations

import argparse
import itertools
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GROUPS = {"C1": "c1-io", "C2": "c2-agents", "C3": "c3-ml", "C4": "c4-fork"}


def validate(design: dict) -> None:
    hosts = design["hosts"]
    if len(hosts) != 6 or len({h["ssh"] for h in hosts}) != 6:
        raise ValueError("Six distinct SSH hosts are required")
    if design["execution"]["automatic_launch"]:
        raise ValueError("This exporter does not start remote tasks")
    if len(design["seeds"]) != design["repetitions"]:
        raise ValueError("Each repetition needs one seed")
    for host in hosts:
        if not 0 < host["active"] <= host["slots"]:
            raise ValueError(f"Invalid slot allocation: {host['name']}")
        memory = host["slots"] * design["workspace_memory_gib"]
        memory += sum(host[k] for k in (
            "rustfs_memory_gib", "redis_memory_gib",
            "coordinator_memory_gib", "os_memory_gib"))
        if memory > host["memory_gib_floor"]:
            raise ValueError(f"Memory exceeded: {host['name']}")
        if not 0 < host["client_cpu_quota"] < host["logical_cpus"]:
            raise ValueError(f"Invalid CPU quota: {host['name']}")
    capacity = sum(h["active"] for h in hosts)
    spare = sum(h["slots"] - h["active"] for h in hosts)
    if capacity != 192 or design["maximum_active_agents"] != capacity:
        raise ValueError("The main design requires 192 active workspaces")
    if spare < max(design["fork"]["children"]):
        raise ValueError("Not enough simultaneous child mounts")
    if any(n < len(hosts) or n > capacity or n % 8 for n in design["active_agents"]):
        raise ValueError("Agent counts must use all hosts and complete eight-case groups")
    layout = design["layout"]
    if layout["block_kib"] != [64, 256, 1024, 4096]:
        raise ValueError("Unsupported block sizes")
    if layout.get("read_quantum_source") != "persisted-object-layout" or not layout.get("record_actual_block_and_quantum"):
        raise ValueError("Record actual B/q from persisted object layout")
    if sum(h["rustfs_memory_gib"] > 0 for h in hosts) != 4:
        raise ValueError("Four RustFS nodes are required")
    if len(set(design["trace_cases"])) != 8:
        raise ValueError("The cohort requires eight distinct cases")


def placement(design: dict, count: int) -> list[dict]:
    """Spread an initial workspace to every host, then fill proportionally."""
    hosts = design["hosts"]
    if count < 1 or count > sum(h["active"] for h in hosts):
        raise ValueError("Unsupported cohort size")
    if count < len(hosts):
        return [{"host": h["name"], "ssh": h["ssh"], "slot": 1, "metrics_port": design["metrics_port_base"] + 1} for h in hosts[:count]]
    assigned = [1] * len(hosts)
    for _ in range(count - len(hosts)):
        eligible = [i for i, h in enumerate(hosts) if assigned[i] < h["active"]]
        chosen = min(eligible, key=lambda i: (assigned[i] / hosts[i]["active"], i))
        assigned[chosen] += 1
    result = []
    for slot in range(1, max(assigned) + 1):
        for index, host in enumerate(hosts):
            if slot <= assigned[index]:
                result.append({"host": host["name"], "ssh": host["ssh"],
                               "slot": slot,
                               "metrics_port": design["metrics_port_base"] + slot})
    return result


def cohort(design: dict, count: int, repetition: int) -> dict:
    seed = design["seeds"][repetition - 1]
    rng = random.Random(seed)
    cases = list(design["trace_cases"])
    rng.shuffle(cases)
    tasks = []
    for index, site in enumerate(placement(design, count)):
        tasks.append({
            "task_id": f"a{index + 1:03d}", "case": cases[index % len(cases)],
            "start_delay_ms": rng.randrange(design["pacing"]["start_window_seconds"] * 1000 + 1),
            "pacing_seed": seed + 100003 * (index + 1), **site,
        })
    shift = (repetition - 1) % len(design["systems"])
    return {"agents": count, "repetition": repetition, "seed": seed,
            "system_order": design["systems"][shift:] + design["systems"][:shift],
            "shared_tasks": tasks,
            "boundary_schedules": "required-before-execution"}


def experiments(design: dict, group: str | None = None, include_optional: bool = False) -> list[dict]:
    result = []
    for group_id, directory in GROUPS.items():
        if group and group != group_id:
            continue
        spec = json.loads((ROOT / "experiments" / directory / "experiment.json").read_text(encoding="utf-8"))
        for family in spec["cases"]:
            if family["kind"] == "optional" and not include_optional:
                continue
            axes = family["axes"]
            for index, values in enumerate(itertools.product(*axes.values()), 1):
                parameters = {**spec.get("defaults", {}), **family.get("fixed", {})}
                parameters.update(zip(axes, values))
                systems = design["systems"] if family["systems"] == "primary" else family["systems"]
                result.append({"id": f"{group_id}-{family['name']}-{index:02d}",
                               "group": group_id, "family": family["name"], "kind": family["kind"],
                               "system_parameters": family.get("system_parameters", {}),
                               "systems": systems, "parameters": parameters,
                               "repetitions": design["repetitions"], "status": "planned"})
    return result


def build_plan(design: dict, group: str | None = None, include_optional: bool = False) -> dict:
    validate(design)
    conditions = experiments(design, group, include_optional)
    for condition in conditions:
        params = condition["parameters"]
        peak = params.get("agents", 1) + params.get("children", 0) + params.get("base_mounts", 0)
        if peak > sum(h["slots"] for h in design["hosts"]):
            raise ValueError(f"Mount capacity exceeded: {condition['id']}")
        condition["peak_mounts"] = peak
    jobs = []
    for condition in conditions:
        systems = condition["systems"]
        for repetition in range(1, design["repetitions"] + 1):
            shift = (repetition - 1) % len(systems)
            for system in systems[shift:] + systems[:shift]:
                jobs.append({"id": f"{condition['id']}-{system}-r{repetition}",
                             "condition": condition["id"], "group": condition["group"],
                             "system": system, "repetition": repetition,
                             "seed": design["seeds"][repetition - 1],
                             "parameters": {**condition["parameters"],
                                            **condition["system_parameters"].get(system, {})},
                             "status": "planned"})
    counts = {"conditions": len(conditions), "system_runs": len(jobs),
              "active_agents": sum(h["active"] for h in design["hosts"]),
              "main_agent_limit": max(design["active_agents"]),
              "mount_slots": sum(h["slots"] for h in design["hosts"])}
    return {"schema": "ax-cluster-plan-v2", "status": "design-review", "counts": counts,
            "conditions": conditions, "jobs": jobs,
            "cohorts": [cohort(design, n, r) for n in design["active_agents"]
                        for r in range(1, design["repetitions"] + 1)]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, default=ROOT / "configs" / "cluster.json")
    parser.add_argument("--group", choices=GROUPS)
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--out", type=Path)
    options = parser.parse_args()
    design = json.loads(options.design.read_text(encoding="utf-8"))
    plan = build_plan(design, options.group, options.include_optional)
    if options.out:
        options.out.parent.mkdir(parents=True, exist_ok=True)
        with options.out.open("x", encoding="utf-8") as stream:
            json.dump(plan, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps(plan["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
