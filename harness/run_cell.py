"""Preflight or explicitly execute one campaign cell, retaining failure records.

Usage: python -m harness.run_cell --cell-file cell.json --adapter adapter.json
       --output /new/run-directory --preflight  (or --execute)
Run from the repository root, or invoke this file by absolute path.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import traceback

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.adapter import Adapter
from harness.qualification import CACHE_PROTOCOLS, replay_problems, scenario_capability_problems


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _missing(value, prefix):
    if value is None or value == "" or value == {} or value == []:
        return [prefix]
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in _missing(child, prefix + "." + key)]
    if isinstance(value, list):
        return [item for i, child in enumerate(value) for item in _missing(child, prefix + "." + str(i))]
    return []


def validate_cell(cell):
    if not isinstance(cell, dict) or cell.get("schema_version") != 1:
        raise ValueError("cell schema_version must be 1")
    for key in ("cell_id", "experiment_id", "rq", "design_ids", "workload_id", "baseline_id",
                "variant", "pair_id", "replicate", "profile", "parameters", "required_operations", "status"):
        if key not in cell:
            raise ValueError("cell missing field: " + key)
    if cell["experiment_id"] not in {"T1", "T2", "T3", "T4", "T5", "T6"}:
        raise ValueError("unknown experiment_id")
    if cell["baseline_id"] not in {"B0", "B1", "B2", "B3", "B4", "LOCAL_REFERENCE"}:
        raise ValueError("unknown baseline_id")
    if cell["profile"] not in {"smoke", "full"}:
        raise ValueError("profile must be smoke or full")
    if not isinstance(cell["parameters"], dict):
        raise ValueError("parameters must be an object")
    if not isinstance(cell["required_operations"], list) or not all(isinstance(op, str) and op for op in cell["required_operations"]):
        raise ValueError("required_operations must be a string array")
    if cell["baseline_id"] == "LOCAL_REFERENCE" and cell["profile"] != "smoke":
        raise ValueError("LOCAL_REFERENCE is restricted to smoke checks")
    if cell["status"] != "planned":
        raise ValueError("only planned cells can be run")


def preflight(cell, adapter):
    problems = replay_problems(cell)
    if cell["parameters"].get("execution_mode") == "distributed":
        protocol = cell["parameters"].get("cache_protocol_id")
        if protocol not in CACHE_PROTOCOLS:
            problems.append("cache:unknown_protocol")
        elif cell["parameters"].get("cache_state", CACHE_PROTOCOLS[protocol]) != CACHE_PROTOCOLS[protocol]:
            problems.append("cache:planned_state_mismatch")
    if adapter.baseline_id != cell["baseline_id"]:
        problems.append("cell_adapter_baseline_mismatch")
    if adapter.config.get("state") != "configured":
        problems.append("adapter_not_configured")
    required = sorted(set(cell["required_operations"]) | {"discover"})
    for op in required:
        if not adapter.supports(op):
            problems.append("operation_not_configured:" + op)
    if adapter.scope == "real":
        runtime = adapter.config.get("runtime", {})
        for key in ("versions", "resources", "configuration"):
            problems += ["missing:" + p for p in _missing(runtime.get(key), "runtime." + key)]
    discovery = None
    if not problems:
        discovery = adapter.call("discover", {"cell_id": cell["cell_id"], "required_operations": required,
                                               "variant": cell["variant"], "parameters": cell["parameters"]})
        if discovery["status"] != "ok":
            problems.append("discover_failed:" + discovery["status"])
        else:
            data = discovery.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("operations"), list):
                problems.append("discover_missing_operations")
            else:
                problems += scenario_capability_problems(cell, data)
                for op in required:
                    if op not in data["operations"]:
                        problems.append("operation_not_observed:" + op)
                if adapter.scope == "real":
                    runtime = data.get("runtime", {})
                    for key in ("versions", "resources", "configuration"):
                        problems += ["missing_observed:" + p for p in _missing(runtime.get(key), "runtime." + key)]
                    for key, expected in adapter.config["runtime"]["versions"].items():
                        if runtime.get("versions", {}).get(key) != expected:
                            problems.append("version_mismatch:" + key)
    return {"status": "ok" if not problems else "rejected", "problems": problems,
            "required_operations": required, "discovery": discovery,
            "scope": adapter.scope, "adapter_qualified": not problems, "performance_claims_allowed": False}


def run(cell_file, adapter_file, output, *, execute=False):
    output = Path(output).resolve()
    # Refuse reuse rather than deleting or mixing partial results.
    output.mkdir(parents=True, exist_ok=False)
    (output / "logs").mkdir()
    (output / "workspaces").mkdir()
    result = {"schema_version": 1, "status": "error", "started_at": datetime.now(timezone.utc).isoformat(),
              "mode": "execute" if execute else "preflight", "cell_id": None,
              "scope": None, "preflight": None, "measurements": None,
              "performance_claims_allowed": False, "error": None}
    try:
        cell_bytes = Path(cell_file).read_bytes()
        cell = json.loads(cell_bytes)
        (output / "cell.json").write_bytes(cell_bytes)
        result["cell_id"] = cell.get("cell_id") if isinstance(cell, dict) else None
        result["cell_sha256"] = hashlib.sha256(cell_bytes).hexdigest()
        validate_cell(cell)
        result["baseline_id"] = cell["baseline_id"]
        config_bytes = Path(adapter_file).read_bytes()
        config = json.loads(config_bytes)
        result["adapter_sha256"] = hashlib.sha256(config_bytes).hexdigest()
        # Do not copy operator configuration: it may contain secrets. Hashing and
        # the discover response record the deployment without printing credentials.
        adapter = Adapter(adapter_file, log_dir=output / "logs",
                          workspace_root=config.get("workspace_root") or output / "workspaces",
                          artifact_root=output)
        result["scope"] = adapter.scope
        check = preflight(cell, adapter)
        result["preflight"] = check
        write_json(output / "preflight.json", check)
        if check["status"] != "ok":
            result["status"] = "rejected"
            result["error"] = "preflight_failed"
        elif not execute:
            result["status"] = "preflight_ok"
        else:
            from harness.scenarios import execute_cell
            scenario_result = execute_cell(cell, adapter, output)
            if not isinstance(scenario_result, dict) or scenario_result.get("status") not in {"ok", "error", "unsupported", "rejected"}:
                raise ValueError("scenario must return a result object with explicit status")
            write_json(output / "scenario.json", scenario_result)
            result["status"] = scenario_result["status"]
            result["scenario"] = scenario_result
            result["measurements"] = scenario_result.get("measurements")
            result["performance_claims_allowed"] = (adapter.scope == "real" and
                scenario_result["status"] == "ok" and scenario_result.get("measurement_valid") is True)
            if scenario_result["status"] != "ok":
                result["error"] = "scenario_" + scenario_result["status"]
    except Exception as exc:
        # Preserve diagnosis locally, never echo command/config/payload text.
        (output / "logs" / "runner-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        result["status"] = "error"
        result["error"] = type(exc).__name__
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell-file", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args.cell_file, args.adapter, args.output, execute=args.execute)
    except FileExistsError:
        print(json.dumps({"status": "rejected", "error": "output_already_exists"}))
        return 2
    except OSError:
        print(json.dumps({"status": "error", "error": "cannot_create_output"}))
        return 2
    print(json.dumps({"status": result["status"], "result_file": str(args.output.resolve() / "result.json"),
                      "performance_claims_allowed": result["performance_claims_allowed"]}))
    return 0 if result["status"] in {"ok", "preflight_ok"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
