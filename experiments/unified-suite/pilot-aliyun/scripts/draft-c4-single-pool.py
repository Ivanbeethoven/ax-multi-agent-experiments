#!/usr/bin/env python3
"""Draft a new four-worker, one-pool C4 handoff campaign without SSH or admission."""
import argparse
from copy import deepcopy
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(REPO), str(REPO / "bench")]

from bench import suite_plan
from bench.experiment_io import write_new
from bench.runtime import pilot_pools
from bench.suite_catalog import cases


def sibling(filename):
    spec = spec_from_file_location(filename.removesuffix(".py").replace("-", "_"), Path(__file__).with_name(filename))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


route = sibling("draft-c4-juicefs-handoff-route.py")
setup_controller = sibling("setup-controller.py")
CAMPAIGN = "pilot-c4-handoff-single-pool-01"
POOL = "pool-c4-handoff"
ROLES = {"client": ("pool-a", "client"), "target": ("pool-b", "client"),
         "metadata": ("pool-a", "metadata"), "objects": ("pool-a", "objects")}


def draft(plan_path, inventory_path, code_directory):
    if not re.fullmatch(r"code-[0-9]{2}", code_directory):
        raise ValueError("explicit code-NN directory required")
    original_plan = suite_plan.check_plan(route.load_pinned(plan_path, route.PINNED_PLAN))
    original_inventory = route.load_pinned(inventory_path, route.PINNED_INVENTORY)
    originals = {p["pool_id"]: p for p in pilot_pools.inventories(original_inventory)}
    for old in original_plan["config"]["clusters"]:
        if (old["inventory_sha256"] != route.PINNED_INVENTORY
                or old["resource_ids"] != pilot_pools.resource_ids(originals[old["id"]])):
            raise ValueError("old plan and inventory disagree")

    combined = {"pool_id": POOL, "roles": {
        new_role: deepcopy(originals[old_pool]["roles"][old_role])
        for new_role, (old_pool, old_role) in ROLES.items()
    }}
    inventory = {"campaign_id": CAMPAIGN, "pools": [combined]}
    inventory_sha = hashlib.sha256((json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).hexdigest()
    resources = pilot_pools.resource_ids(combined)
    fleet = setup_controller.actual_fleet(inventory, code_directory)
    executors = [node for node in fleet["nodes"] if node["role"] == "executor"]
    if (len(executors) != 2 or len({node["environment_id"] for node in executors}) != 2
            or any(len(node["slots"]) != 1 for node in executors)):
        raise ValueError("exactly two actual executors with one slot each required")

    config = deepcopy(original_plan["config"])
    case, = [c for c in cases("pilot", "systems") if c["name"] == "handoff-1" and c["workload"] == "handoff"]
    config.update(
        campaign_id=CAMPAIGN, placement={"mode": "paired-pool"}, cases=[case],
        input_manifest_sha256=None, build_manifest_sha256=None,
        clusters=[{
            "id": POOL, "hardware_class": "c4-a-backend-ab-clients-v1",
            "metadata_backend": config["metadata_backend"], "agent_slots": len(executors),
            "supported_suites": ["C4"], "resource_ids": resources,
            "observer_ids": [node["id"] for node in fleet["nodes"]],
            "inventory_sha256": inventory_sha,
        }],
    )
    plan = suite_plan.build(config)
    old_cells = {pool_id: next(cell for cell in original_plan["cells"] if cell["pool"] == pool_id)
                 for pool_id in route.POOLS}
    preview = {
        "schema": "c4-single-pool-campaign-draft-v1",
        "reviewed": False,
        "execution_enabled": False,
        "plan_sha256": plan["plan_sha256"],
        "inventory_sha256": inventory_sha,
        "canonical_old_plan_sha256": original_plan["plan_sha256"],
        "canonical_old_inventory_sha256": route.PINNED_INVENTORY,
        "original_role_by_new_role": {role: pilot_pools.alias(*old) for role, old in ROLES.items()},
        "endpoints": pilot_pools.endpoints(inventory, POOL),
        "ssh_probe_preview": {
            "argv": ["python3", "experiments/unified-suite/pilot-aliyun/scripts/setup-controller.py",
                     "--inventory", "<reviewed-new-inventory.json>", "--code-directory", code_directory,
                     "--out", "<new-controller-directory>"],
            "nodes": [
                {k: node[k] for k in ("id", "ssh_alias", "private_ip", "environment_id", "repo", "work_root", "slots")}
                for node in fleet["nodes"]
            ],
            "contacted": False,
        },
        "guard": {
            "directory": f"{route.CONTROL}/runner-locks",
            "new_lock_files": route.lock_names([f"pool:{POOL}", *resources]),
            "old_reference_cell_ids": {pool_id: cell["id"] for pool_id, cell in old_cells.items()},
            "old_physical_overlap": {
                pool_id: sorted(set(resources).intersection(pilot_pools.cell_resources(original_plan, cell)))
                for pool_id, cell in old_cells.items()
            },
            "required_sqlite_checks": ["cell_leases/cell_resources", "blocks/resources"],
        },
    }
    return {"inventory.json": inventory, "config.json": config, "plan.json": plan, "draft.json": preview}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-plan", type=Path, default=REPO / route.EVIDENCE / "plan.json")
    parser.add_argument("--inventory", type=Path, default=REPO / route.EVIDENCE / "inventory.json")
    parser.add_argument("--code-directory", required=True, help="expected code-NN on the four hosts; not probed")
    parser.add_argument("--out", type=Path, required=True, help="new local draft directory")
    args = parser.parse_args()
    try:
        documents = draft(args.canonical_plan, args.inventory, args.code_directory)
        args.out.mkdir(exist_ok=False)
        for filename, document in documents.items():
            write_new(args.out / filename, document)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
