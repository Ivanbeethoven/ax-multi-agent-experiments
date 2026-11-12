#!/usr/bin/env python3
"""Close one original C4 target mount; caller holds canonical resource guards."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from bench.runtime import common, node
from bench.runtime.observe import prometheus
from bench.runtime.prepare import anchor
from bench.suite_mount import cell_of
from bench.suite_support import closure

SCHEMA = "c4-handoff-target-closure-request-v1"
GAUGES = {
    "brewfs": {
        "brewfs_writeback_live_dirty_bytes", "brewfs_writeback_recent_pending_upload_bytes",
        "brewfs_writeback_stage_inflight_bytes", "brewfs_writeback_remote_upload_inflight_bytes",
        "brewfs_writeback_live_slices", "brewfs_writeback_recent_pending_upload_slices",
        "brewfs_writeback_metadata_inflight",
    },
    "juicefs": {
        "juicefs_staging_block_bytes", "juicefs_staging_blocks", "juicefs_staging_writing_blocks",
    },
}
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


def ref(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"original regular receipt required: {path}")
    return {"path": str(path.resolve(strict=True)), "sha256": common.digest(path)}


def original(path, pin):
    if ref(path) != pin:
        raise ValueError(f"original receipt pin differs: {path}")
    return common.load(common.pinned(pin))


def identity(p):
    if (any(type(p.get(k)) is not int or p[k] <= 0 for k in ("pid", "start_ticks"))
            or not uuid.UUID(p["boot_id"]).int):
        raise ValueError("original process identity required")
    return tuple(p[key] for key in ("pid", "boot_id", "start_ticks"))


def status(run, point, ready, process):
    result = node.control(run, "status")
    if (result.get("passed") is not True or result.get("mount") != ready["mount"]
            or result.get("anchor") != ready["anchor"]
            or identity(result["process"]) != identity(process)
            or common.mounts(point) != [ready["mount"]] or not common.alive(process)):
        raise ValueError("original target mount/process is no longer live")
    return result


def collect_debt(tool, asset, cell, out):
    with (out.parent / "debt.stdout").open("xb") as stdout, (out.parent / "debt.stderr").open("xb") as stderr:
        subprocess.run([sys.executable, str(tool), "debt", "--asset", str(asset),
                        "--cell", cell, "--out", str(out)],
                       stdout=stdout, stderr=stderr, check=True, timeout=30)


def observed_debt(out, filesystem, cell, begin, end):
    batch = ref(out / "result.json")
    value = common.load(common.pinned(batch))
    entries, timestamps, evidence = [], [], []
    for index in range(2):
        sample = ref(out / f"sample-{index}.json")
        native = ref(out / f"native-{index}.prom")
        s = common.load(common.pinned(sample))
        raw = common.pinned(native).read_text(encoding="utf-8")
        gauges = {k: v for k, v in prometheus(raw).items() if k.split("{", 1)[0] in GAUGES[filesystem]}
        if ({k.split("{", 1)[0] for k in gauges} != GAUGES[filesystem] or any(v != 0 for v in gauges.values())
                or s.get("native_gauges") != gauges or s.get("passed") is not True or s.get("cell_id") != cell
                or any(type(s.get(k)) is not int or s[k] != 0
                       for k in ("pending_upload_bytes", "pending_upload_operations"))):
            raise ValueError("actual native zero-debt sample required")
        ts = datetime.fromisoformat(s["at"].replace("Z", "+00:00"))
        ns = s["monotonic_ns"]
        if ts.tzinfo is None or type(ns) is not int or not begin <= ns <= end:
            raise ValueError("debt sample is outside the live capture")
        timestamps.append((ts, ns))
        evidence.append({"sample": sample, "native": native})
        entries.append({
            "evidence": sample, "equals": {"passed": True, "cell_id": cell},
            "pending_bytes_field": "pending_upload_bytes",
            "pending_operations_field": "pending_upload_operations", "observed_at": s["at"],
        })
    (a, ns_a), (b, ns_b) = timestamps
    if (value.get("passed") is not True or value.get("cell_id") != cell
            or value.get("samples") != [row["sample"] for row in evidence]
            or ns_b - ns_a < 10**9 or (b - a).total_seconds() < 1):
        raise ValueError("two distinct ordered original native debt samples required")
    return batch, entries, evidence


def close_target(request, out):
    r = common.spec(request, SCHEMA)
    mount_run = Path(r["mount_run"])
    if not mount_run.is_absolute() or not mount_run.is_dir() or mount_run.resolve() != mount_run:
        raise ValueError("exact absolute target supervisor directory required")
    out = Path(out)
    mount = common.spec(r["mount_spec"], "experiment-mount-v1")
    point = Path(mount["mount_point"])
    if (not out.is_absolute() or out.resolve() != out or not point.is_absolute() or point == Path("/")
            or any(a.is_relative_to(b) or b.is_relative_to(a)
                   for a, b in ((out, mount_run), (out, point), (mount_run, point)))):
        raise ValueError("target mount, supervisor and new closure evidence must be disjoint")
    if any((mount_run / name).exists() or (mount_run / name).is_symlink()
           for name in ("stopping.json", "stopped.json", "unexpected-exit.json")):
        raise ValueError("target is already stopping, stopped or interrupted; never retry")

    ready = original(mount_run / "ready.json", r["ready"])
    proc = original(mount_run / "process.json", r["process"])
    process, supervisor = proc["process"], proc["supervisor"]
    boot = BOOT_ID.read_text().strip()
    if (any(row.get("spec") != r["mount_spec"] for row in (ready, proc))
            or ready.get("passed") is not True or ready.get("anchor") != mount["anchor"]
            or ready["mount"].get("point") != str(point) or not ready["mount"]["filesystem"].startswith("fuse")
            or type(ready["mount"].get("mount_id")) is not int or ready["mount"]["mount_id"] <= 0
            or identity(ready["process"]) != identity(process) or identity(process) == identity(supervisor)
            or process["boot_id"] != boot or supervisor["boot_id"] != boot or not common.alive(supervisor)):
        raise ValueError("target startup receipts do not identify this original mount and supervisor")

    asset_path = common.pinned(r["asset"])
    asset = common.load(asset_path)
    filesystem = mount["filesystem"]
    binding = mount["suite_binding"]
    plan = common.load(common.pinned(binding["plan"]))
    common.pinned(asset["plan"])  # the existing native debt CLI resolves this exact target-local file
    cell = cell_of(plan, r["cell_id"])
    if (filesystem not in GAUGES or asset_path.name != "asset.json" or asset["product"] != filesystem
            or cell["workload"] != "handoff" or cell["arm"]["product"] != filesystem
            or binding["cell_id"] != cell["id"] or binding["plan"]["sha256"] != asset["plan"]["sha256"]
            or asset["mount_point"] != str(point) or anchor(filesystem, asset["config"]) != mount["anchor"]
            or (filesystem == "juicefs" and mount.get("metrics_address") != "127.0.0.1:9567")):
        raise ValueError("debt probe is not bound to this C4 target asset and mount")
    tool = common.pinned(r["pilot_node"])
    if tool.name != "pilot-node.py":
        raise ValueError("pinned existing pilot-node.py required")

    status(mount_run, point, ready, process)
    common.private_dir(out)
    common.write_new(out / "intent.json", {
        "request": request, "cell_id": cell["id"], "original_mount": ready["mount"], "automatic_retry": False,
    })
    begin = time.monotonic_ns()
    collect_debt(tool, asset_path.parent, cell["id"], out / "debt")
    end = time.monotonic_ns()
    batch, entries, evidence = observed_debt(out / "debt", filesystem, cell["id"], begin, end)
    status(mount_run, point, ready, process)
    if not common.alive(supervisor) or BOOT_ID.read_text().strip() != boot:
        raise ValueError("original supervisor identity changed before normal stop")

    common.write_new(out / "stop-intent.json", {"action": "normal-stop", "automatic_retry": False})
    response = node.control(mount_run, "stop")
    stopping_pin, stopped_pin = (ref(mount_run / name) for name in ("stopping.json", "stopped.json"))
    stopping = common.load(common.pinned(stopping_pin))
    stopped = common.load(common.pinned(stopped_pin))
    if (response != stopped or stopped.get("passed") is not True or type(stopped.get("exit_code")) is not int
            or stopped["exit_code"] != 0 or stopped.get("mount_absent") is not True
            or stopped.get("active_workers") != [] or identity(stopping["process"]) != identity(process)
            or any(row.get("anchor") != mount["anchor"] or row.get("original_mount") != ready["mount"]
                   for row in (stopping, stopped))):
        raise ValueError("original supervisor did not prove clean target stop")

    deadline = time.monotonic() + 5
    while common.alive(supervisor) and time.monotonic() < deadline:
        time.sleep(.025)
    if BOOT_ID.read_text().strip() != boot:
        raise ValueError("target rebooted during closure")
    closure_request = out / "closure-request.json"
    common.write_new(closure_request, {
        "schema": "suite-closure-request-v1", "cell_id": cell["id"], "processes": [process, supervisor],
        "mount_points": [str(point)], "debt_samples": entries,
    })
    checked = closure(closure_request)
    if not (checked["all_exited"] and checked["all_unmounted"]):
        raise ValueError("original target processes or mount remain")
    common.write_new(out / "suite-closure.json", {**checked, "passed": True})
    result = {
        "schema": "c4-handoff-target-closure-v1", "passed": True, "cell_id": cell["id"],
        "anchor": mount["anchor"], "original_mount": ready["mount"], "all_exited": True, "all_unmounted": True,
        "pending_upload_bytes": 0, "pending_upload_operations": 0, "request": request,
        "ready": r["ready"], "process": r["process"], "mount_spec": r["mount_spec"],
        "debt": {"batch": batch, "samples": evidence}, "stopping": stopping_pin, "stopped": stopped_pin,
        "closure": ref(out / "suite-closure.json"), "automatic_retry": False,
        "scope": "original target mount and supervisor only; caller retains target cohorts and seals the complete cell",
    }
    common.write_new(out / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(json.dumps(close_target({"path": args.request, "sha256": args.sha256}, args.out), ensure_ascii=False))
