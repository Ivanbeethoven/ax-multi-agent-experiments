"""Create explicit settings for each mount and check overlapping resource use."""

import argparse
import copy
import json
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[2]
MIB = 1024 ** 2
PHASES = {"initial", "verify", "measure", "remount", "fork-child"}
STATES = {"cold", "same-process", "disk-reuse", "cross-host-cold"}
POOLS = ("ROOTS", "METADATA_OWNED", "PLANS", "CONTROL", "STORED", "RAW", "DECODE_WORKSPACE", "OUTPUT")


def option_bytes(value):
    match = re.fullmatch(r"(\d+)([KMGT]?)", str(value), re.IGNORECASE)
    if match is None:
        raise ValueError("Unsupported byte size: " + str(value))
    return int(match[1]) * 1024 ** (" KMGT".index(match[2].upper()) if match[2] else 0)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render(request, profiles, design):
    required = {"id", "phase", "engine", "host", "slot", "workspace", "parent_workspace",
                "purpose", "binary", "cache_state", "windows", "root"}
    if required - request.keys():
        raise ValueError("Missing mount settings: " + ", ".join(sorted(required - request.keys())))
    if request["phase"] not in PHASES or request["cache_state"] not in STATES:
        raise ValueError("Unknown mount phase or cache state")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", request["id"]):
        raise ValueError("Invalid mount ID")
    host = next((h for h in design["hosts"] if h["ssh"] == request["host"]), None)
    if host is None or not 1 <= request["slot"] <= host["slots"]:
        raise ValueError("Unknown host or slot outside host capacity")
    root = PurePosixPath(request["root"])
    if not root.is_absolute() or ".." in root.parts or not PurePosixPath(request["binary"]).is_absolute():
        raise ValueError("Explicit absolute server paths are required")
    if not request["windows"] or not all(isinstance(x, str) and x for x in request["windows"]):
        raise ValueError("At least one resource window is required")
    resource_name = request.get("resource_profile", "default")
    resources = profiles if resource_name == "default" else profiles["resource_profiles"][resource_name]
    record = copy.deepcopy(request)
    record["resource_profile"] = resource_name
    record.update(memory_bytes=resources["memory_mib"] * MIB,
                  disk_bytes=resources["disk_mib"] * MIB,
                  mount_directory=str(root / "mounts" / request["id"]),
                  cache_root=str(root / "cache" / request["id"]),
                  metrics_port=design["metrics_port_base"] + request["slot"],
                  sharing="exclusive-per-mount", environment={}, options={}, cache={},
                  metadata_prefetch="no-independent-switch",
                  observed_connection_ceiling=profiles["maximum_network_connections_per_mount"])
    if request["cache_state"] in {"disk-reuse", "same-process"}:
        if not request.get("previous_mount") or not request.get("reuse_cache_root"):
            raise ValueError("Cache reuse requires its previous owner and exact cache root")
        reused = PurePosixPath(request["reuse_cache_root"])
        if not reused.is_relative_to(root / "cache"):
            raise ValueError("Reused cache must belong to this run")
        record["cache_root"] = str(reused)
        record["sharing"] = "same-process" if request["cache_state"] == "same-process" else "sequential-owner"
    changes = request.get("overrides", {})
    allowed = {"prefetch", "packed_cache", "retained_payload_mib", "metadata_cache_mib", "row_cache_mib", "fork_cache_mib", "writes", "truncate_flush_seconds"}
    if changes.keys() - allowed:
        raise ValueError("Unknown mount override")
    if request.get("cache_sharing", "private") != "private":
        raise ValueError("Host sharing requires a shared-read-cache adapter")
    for key in ("prefetch", "packed_cache"):
        if key in changes and changes[key] not in {"on", "off"}:
            raise ValueError("Invalid on/off setting: " + key)
    for key in ("retained_payload_mib", "metadata_cache_mib", "row_cache_mib", "fork_cache_mib"):
        if key in changes and (type(changes[key]) is not int or changes[key] < 0):
            raise ValueError("Cache sizes require nonnegative integers")
    if changes.get("retained_payload_mib", 8) > 64:
        raise ValueError("Retained payload exceeds the supported 64 MiB")
    engine = request["engine"]
    if "truncate_flush_seconds" in changes:
        seconds = changes["truncate_flush_seconds"]
        if engine != "brewfs-native-overlay" or type(seconds) is not int or not 1 <= seconds <= 300:
            raise ValueError("Workspace truncate flush timeout must be 1 through 300 seconds")
    if engine in {"brewfs-native-overlay", "brewfs-packed", "brewfs-flat"}:
        record["volume_format"] = "flat" if engine == "brewfs-flat" else "workspace"
        record["cache"] = copy.deepcopy(profiles["brewfs_cache"])
        record["cache"].update(resources.get("brewfs_cache_overrides", {}))
        record["cache"]["cache_root"] = record["cache_root"]
        if "write_staging_mib" in resources:
            record["cache"]["write_ssd_bytes"] = resources["write_staging_mib"] * MIB
        if engine != "brewfs-packed":
            record["cache"]["read_ssd_bytes"] = (record["disk_bytes"] - record["cache"]["write_ssd_bytes"])
        record["environment"] = copy.deepcopy(profiles["brewfs_environment"])
        environment = record["environment"]
        if "truncate_flush_seconds" in changes:
            environment["BREWFS_TRUNCATE_FLUSH_TIMEOUT_SECS"] = str(changes["truncate_flush_seconds"])
        packed = engine == "brewfs-packed"
        environment["BREWFS_PACKED_CACHE"] = changes.get("packed_cache", "on") if packed else "off"
        for option, name in (("retained_payload_mib", "BREWFS_PACKED_RETAINED_PAYLOAD_BYTES"),
                             ("metadata_cache_mib", "BREWFS_PACKED_METADATA_CACHE_BYTES"),
                             ("row_cache_mib", "BREWFS_WORKSPACE_ROW_CACHE_BYTES")):
            if option in changes:
                environment[name] = str(changes[option] * MIB)
        if changes.get("prefetch") == "off":
            record["cache"].update(prefetch_enabled=False, range_background_prefetch=False)
        if changes.get("writes") == "local-staged":
            record["cache"]["writeback_mode"] = "commit_before_upload"
        record["prefetch"] = {"data_window_bytes":record["cache"]["prefetch_max_bytes"],
                              "data_workers":record["cache"]["prefetch_concurrency"] if record["cache"]["prefetch_enabled"] else 0,
                              "initial_window_bytes":4 * MIB,
                              "packed_metadata_and_data":environment["BREWFS_PACKED_CACHE"],
                              "packed_heads":4 if packed and environment["BREWFS_PACKED_CACHE"] == "on" else 0,
                              "packed_downloads":4 if packed and environment["BREWFS_PACKED_CACHE"] == "on" else 0}
        # The reservation deliberately includes overlapping accounting limits.
        reservation = sum(record["cache"][k] for k in ("read_memory_bytes", "write_memory_bytes", "memory_budget_bytes"))
        if engine != "brewfs-flat":
            reservation += int(environment["BREWFS_WORKSPACE_ROW_CACHE_BYTES"])
        if packed:
            reservation += sum(int(environment[f"BREWFS_PACKED_{pool}_BUDGET_BYTES"]) for pool in POOLS)
            reservation += int(environment["BREWFS_PACKED_METADATA_CACHE_BYTES"]) + int(environment["BREWFS_PACKED_RETAINED_PAYLOAD_BYTES"])
        record["capacity_reservation_bytes"] = reservation
        if engine == "brewfs-flat":
            record["environment"] = {key: value for key, value in environment.items()
                if not key.startswith(("BREWFS_PACKED_", "BREWFS_WORKSPACE_"))}
            record["prefetch"]["packed_metadata_and_data"] = "not-applicable"
    elif engine in {"juicefs", "juicefs-overlay"}:
        record["volume_format"] = "forkredis" if engine == "juicefs-overlay" else "redis"
        record["options"] = copy.deepcopy(profiles["juicefs_options"])
        record["options"].update(resources.get("juicefs_options_overrides", {}))
        record["options"].update({"cache-dir":record["cache_root"], "metrics":f"127.0.0.1:{record['metrics_port']}"})
        staging_mib = resources.get("write_staging_mib", profiles["juicefs_staging_reserve_mib"])
        record["options"]["cache-size"] = str(resources["disk_mib"] - staging_mib) + "M"
        if changes.get("prefetch") == "off":
            record["options"].update({"prefetch":"0", "max-readahead":"0"})
        if changes.get("writes") == "local-staged":
            record["options"]["writeback"] = "true"
        record["fork_cache_bytes"] = changes.get("fork_cache_mib", profiles["juicefs_overlay_cache_bytes"] // MIB) * MIB if engine == "juicefs-overlay" else 0
        record["capacity_reservation_bytes"] = option_bytes(record["options"]["buffer-size"]) + 2 * record["fork_cache_bytes"]
        record["prefetch"] = {"data_window_bytes":option_bytes(record["options"]["max-readahead"]),
                              "data_workers":int(record["options"]["max-downloads"]),
                              "packed_metadata_and_data":"not-applicable", "packed_heads":0,"packed_downloads":0}
    else:
        raise ValueError("No mount adapter for " + engine)
    if record["capacity_reservation_bytes"] + profiles["process_reserve_mib"] * MIB > record["memory_bytes"]:
        raise ValueError("Mount memory budget exceeded")
    record["download_workers"] = record["prefetch"]["data_workers"] + record["prefetch"]["packed_downloads"]
    record["asynchronous_tail"] = "record-remaining-downloads-and-bytes-until-process-exit"
    return record


def validate_windows(records, design):
    totals = {}
    for record in records:
        for window in set(record["windows"]):
            key = (window, record["host"])
            totals.setdefault(key, {"memory":0,"disk":0,"heads":0,"downloads":0,"connections":0,"slots":set(),"roots":set()})
            total = totals[key]
            if record["slot"] in total["slots"] or record["cache_root"] in total["roots"]:
                raise ValueError("Overlapping mounts share a slot or cache directory")
            total["slots"].add(record["slot"])
            total["roots"].add(record["cache_root"])
            total["memory"] += record["memory_bytes"]
            total["disk"] += record["disk_bytes"]
            total["heads"] += record["prefetch"]["packed_heads"]
            total["downloads"] += record["download_workers"]
            total["connections"] += record["observed_connection_ceiling"]
    rows = []
    for (window, alias), total in totals.items():
        host = next(h for h in design["hosts"] if h["ssh"] == alias)
        if total["memory"] > host["slots"] * design["workspace_memory_gib"] * 1024**3:
            raise ValueError("Host memory budget exceeded")
        if total["disk"] > host["slots"] * design["workspace_disk_gib"] * 1024**3:
            raise ValueError("Host disk budget exceeded")
        if total["downloads"] > host["slots"] * 8 or total["heads"] > host["slots"] * 4:
            raise ValueError("Host prefetch concurrency exceeded")
        rows.append({"window":window,"host":alias,**{k:v for k,v in total.items() if k not in {"slots","roots"}},"mounts":len(total["slots"])})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    design, profiles = load(ROOT / "configs/cluster.json"), load(ROOT / "configs/mount-profiles.json")
    records = [render(r, profiles, design) for r in load(args.requests)]
    result = {"mounts":records,"host_windows":validate_windows(records, design)}
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"mounts":len(records),"host_windows":len(result["host_windows"])}))


if __name__ == "__main__":
    main()
