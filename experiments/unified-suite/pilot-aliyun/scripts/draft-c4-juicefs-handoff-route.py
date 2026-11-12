#!/usr/bin/env python3
"""Map the frozen C4 JuiceFS handoff route without claiming plan admission."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from bench.experiment_io import write_new
from bench.runtime import pilot_pools
from bench.suite_catalog import cases
from bench import suite_plan


EVIDENCE = "experiments/unified-suite/pilot-aliyun/evidence/preparation/parallel-control-01"
PINNED_PLAN = "8649d8b77163e9c47de5a5df2a8f8b2a633e305ec4f657c7ba8115a3e02a76e7"
PINNED_INVENTORY = "6eb8bf22b1189daf5ea859b8d77e659d5a56e2fc5fbb23b9b70c4cc5ebf21c1e"
SCHEMA = "c4-juicefs-handoff-route-draft-v1"
CONTROL = "/srv/ax-pilot-control/parallel-control-01"
WORKSPACE = "/srv/ax-pilot-session/workspace"
POOLS = ("pool-a", "pool-b")
ROLE_NAMES = ("client", "metadata", "objects")


def load_pinned(path, expected):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"not the frozen input: {path}")
    return json.loads(raw.decode("utf-8-sig"))


def lock_names(tokens):
    return {
        token: hashlib.sha256(token.encode()).hexdigest() + ".lock"
        for token in sorted(set(tokens))
    }


def map_route(plan_path, inventory_path):
    plan = suite_plan.check_plan(load_pinned(plan_path, PINNED_PLAN))
    inventory = load_pinned(inventory_path, PINNED_INVENTORY)
    config = plan["config"]
    clusters = {cluster["id"]: cluster for cluster in config["clusters"]}
    pools = {pool["pool_id"]: pool for pool in pilot_pools.inventories(inventory)}
    if set(pools) != set(POOLS) or set(clusters) != set(POOLS):
        raise ValueError("the original two pools are required")

    resources, reference_cells, roles, driver_locks = {}, {}, {}, {}
    for pool_id in POOLS:
        pool, cluster = pools[pool_id], clusters[pool_id]
        if set(pool["roles"]) != set(ROLE_NAMES):
            raise ValueError(f"unexpected role set: {pool_id}")
        resources[pool_id] = pilot_pools.resource_ids(pool)
        if (cluster["inventory_sha256"] != PINNED_INVENTORY
                or cluster["resource_ids"] != resources[pool_id]):
            raise ValueError(f"canonical inventory and plan disagree: {pool_id}")
        cells = [cell for cell in plan["cells"] if pilot_pools.pool_of(cell) == pool_id]
        if not cells or any(pilot_pools.cell_resources(plan, cell) != resources[pool_id] for cell in cells):
            raise ValueError(f"inconsistent canonical cell locks: {pool_id}")
        reference_cells[pool_id] = cells[0]["id"]
        driver_locks[pool_id] = lock_names([f"pool:{pool_id}", *resources[pool_id]])
        for role in ROLE_NAMES:
            name = pilot_pools.alias(pool_id, role)
            node = pool["roles"][role]
            roles[name] = {
                "pool": pool_id,
                "instance_id": node["instance_id"],
                "hostname": node["hostname"],
                "private_ip": node["private_ip"],
                "data_disk_ids": node["data_disk_ids"],
                "resources": ["host:" + node["instance_id"], *("disk:" + disk for disk in node["data_disk_ids"])],
            }

    if set(resources["pool-a"]).intersection(resources["pool-b"]) or any(
            len({role[field] for role in roles.values()}) != len(roles)
            for field in ("hostname", "private_ip")):
        raise ValueError("the two pools must have distinct physical resources and identities")

    case, = [case for case in cases("pilot", "systems")
             if (case["suite"], case["name"], case["workload"]) == ("C4", "handoff-1", "handoff")]
    if any((cell["suite"], cell["case"]) == ("C4", "handoff-1") for cell in plan["cells"]):
        raise ValueError("a route-only draft is not applicable to a plan containing the case")
    condition = suite_plan.fingerprint([case, config["seed"], config["metadata_backend"]])[:16]
    source = "pool-a-client"
    backend = {
        "pool": "pool-a",
        "metadata_role": "pool-a-metadata",
        "objects_role": "pool-a-objects",
        "endpoints": pilot_pools.endpoints(inventory, "pool-a"),
        "volume": None,
    }
    route = {}
    for arm_id, target in (("juicefs-stay", source), ("juicefs-move", "pool-b-client")):
        required_roles = list(dict.fromkeys((source, target, backend["metadata_role"], backend["objects_role"])))
        required_pools = sorted({roles[role]["pool"] for role in required_roles})
        arm_resources = sorted({resource for role in required_roles for resource in roles[role]["resources"]})
        slots = suite_plan.slots_for(config, case, case["arms"][arm_id])
        eligible = [cluster["id"] for cluster in clusters.values()
                    if "C4" in cluster["supported_suites"] and cluster["agent_slots"] >= slots]
        if eligible:
            raise ValueError(f"reevaluate frozen placement blocker for {arm_id}: {eligible}")
        route[arm_id] = {
            "source_role": source,
            "target_role": target,
            "backend_pool": backend["pool"],
            "required_runtime_roles": required_roles,
            "runtime_resource_ids": arm_resources,
            "runtime_pool_ids": required_pools,
            "required_slots_in_one_frozen_pool": slots,
            "eligible_frozen_pools": eligible,
        }

    return {
        "schema": SCHEMA,
        "status": "blocked-frozen-placement",
        "reviewed": False,
        "execution_enabled": False,
        "formal_plan": None,
        "pins": {
            "plan": {"repo_path": f"{EVIDENCE}/plan.json", "sha256": PINNED_PLAN,
                     "plan_sha256": plan["plan_sha256"]},
            "inventory": {"repo_path": f"{EVIDENCE}/inventory.json", "sha256": PINNED_INVENTORY},
        },
        "catalog_case": case,
        "catalog_condition_id": condition,
        "planned_routes": route,
        "backend": backend,
        "workspace_path": WORKSPACE,
        "roles": roles,
        "lock_reference": {
            "scope": "existing native move driver model; not a JuiceFS runner or a lease",
            "lock_directory": f"{CONTROL}/runner-locks",
            "acquire_pool_order": list(POOLS),
            "canonical_reference_cell_by_pool": reference_cells,
            "lock_files_by_pool": driver_locks,
        },
        "frozen_placement": {
            "profile": config["client_profile"],
            "placement": config["placement"],
            "pools": {pool_id: {key: clusters[pool_id][key] for key in ("agent_slots", "supported_suites")}
                      for pool_id in POOLS},
            "complete_case_required_slots_in_one_pool": suite_plan.slots_for(config, case),
            "target_pool_field": None,
            "blocker": "The frozen planner selects one eligible pool per cell, with no destination field.",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-plan", type=Path, default=REPO / EVIDENCE / "plan.json")
    parser.add_argument("--inventory", type=Path, default=REPO / EVIDENCE / "inventory.json")
    parser.add_argument("--out", type=Path, help="write a new draft file instead of printing")
    args = parser.parse_args()
    try:
        result = map_route(args.canonical_plan, args.inventory)
        if args.out is None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            write_new(args.out, result)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
