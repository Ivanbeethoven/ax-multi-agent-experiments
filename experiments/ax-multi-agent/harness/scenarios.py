"""Local operation qualification and a strict boundary for distributed execution.

The built-in paths do not claim capacity, cloud migration or Agent performance.
Those require a configured run_scenario command and its raw observations.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time
from .fixtures import create_fixture, verify_fixture, checked_root
from .worker import read_workload, mutate_workspace, verify_state, query_metadata, consume_records
from .qualification import cache_problems, finite_time, mechanism_problems, sha256


def distributed(cell, adapter, output):
    output = Path(output).resolve()
    run_tag = hashlib.sha256(cell["cell_id"].encode()).hexdigest()[:16]
    data_root = adapter.workspace_root / ("cell-" + run_tag) / "distributed"
    artifact_root = output / "distributed-artifacts"
    # Fail rather than mix different attempts. Instrumentation does not live on
    # the measured filesystem; the dedicated artifact key has its own allowlist.
    data_root.mkdir(parents=True, exist_ok=False)
    artifact_root.mkdir(parents=True, exist_ok=False)
    response = adapter.call("run_scenario", {"cell": cell, "output_path": str(data_root),
                            "artifact_output_dir": str(artifact_root)},
                            timeout_s=cell["parameters"].get("runner_timeout_s", 36000))
    if response["status"] != "ok":
        return {"status": response["status"], "measurement_valid": False, "measurements": None, "adapter_response": response}
    data = response.get("data")
    if not isinstance(data, dict):
        return {"status": "rejected", "measurement_valid": False, "measurements": None,
                "missing_evidence": ["data_must_be_object"], "adapter_response": response}
    evidence = data.get("measurement_evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    required = ["resource_manifest_sha256", "input_manifest_sha256", "clock_definition", "cache_observations",
                "durability_qualification", "raw_events_sha256", "correctness_report_sha256"]
    missing = [key for key in required if not evidence.get(key)]
    for key in required:
        if key.endswith("_sha256") and not sha256(evidence.get(key)):
            missing.append("invalid:" + key)
    missing += cache_problems(cell, evidence.get("cache_observations"), artifact_root)
    metrics = data.get("measurements", {})
    if not isinstance(metrics, dict):
        metrics = {}
    if not all(key in metrics for key in ("requested", "successful", "deadline_s", "elapsed_s")):
        missing.append("measurements_required_fields")
    else:
        n, ok = metrics["requested"], metrics["successful"]
        if not (type(n) is int and type(ok) is int and n > 0 and 0 <= ok <= n):
            missing.append("invalid_outcome_denominator")
        params = cell["parameters"]
        if "requests_per_s" in params:
            expected = params["requests_per_s"] * params["measurement_s"]
        elif cell["experiment_id"] == "T5":
            expected = len(params["selected"]) if "selected" in params else params.get("workspaces", 1)
        else:
            expected = params.get("tasks", params.get("consumers", params.get("retained_versions", params.get("workspaces", 1))))
        if n != expected:
            missing.append("planned_population_mismatch")
    if not finite_time(metrics.get("elapsed_s"), positive=False):
        missing.append("invalid_elapsed_s")
    if not finite_time(metrics.get("deadline_s"), positive=True):
        missing.append("invalid_deadline_s")
    missing += mechanism_problems(cell, data.get("mechanism_receipt"))
    # Passing this contract is necessary, not scientific validation of the claims.
    return {"status": "rejected" if missing else "ok", "contract_qualified": not missing, "measurement_valid": False,
            "measurements": metrics, "missing_evidence": missing, "adapter_response": response,
            "data_root": str(data_root), "artifact_root": str(artifact_root),
            "note": "Contract check only. Raw artifact hashes, per-item coverage, clocks, input/resource matching and causal receipts require independent audit. No automatic performance eligibility."}


def execute_cell(cell, adapter, output):
    mode = cell["parameters"].get("execution_mode")
    if mode == "distributed":
        return distributed(cell, adapter, output)
    if mode not in {"lifecycle", "fork", "sharing", "metadata"}:
        return {"status": "unsupported", "measurement_valid": False, "measurements": None}
    # The qualification runner runs on the host where the mounted paths exist.
    # It must not turn a remote path string into an invented remote operation.
    output = Path(output)
    p, profile = cell["parameters"], cell["profile"]
    run_tag = hashlib.sha256(cell["cell_id"].encode()).hexdigest()[:16]
    root = adapter.workspace_root / ("cell-" + run_tag)
    # A cell must never overwrite data left by an earlier attempt.
    root.mkdir(parents=True, exist_ok=False)
    fixture = root / "fixture"
    kind = "F2" if mode == "sharing" else "F1"
    create_fixture(kind, fixture, profile=profile, version=1, manifest_path=output / "fixture-manifest.json")
    events, records, paths = [], [], []
    lock = threading.Lock()
    started = time.perf_counter()
    def event(operation, fn, workspace_id=None):
        begin = time.perf_counter()
        try:
            value = fn()
            good = value.get("status", "ok") == "ok" and value.get("ok", True)
        except Exception as exc:
            value, good = {"status": "error", "error_type": type(exc).__name__}, False
        with lock:
            events.append({"operation": operation, "workspace_id": workspace_id, "started_s": begin - started,
                "duration_s": time.perf_counter() - begin, "ok": bool(good), "details": value})
        if not good:
            raise RuntimeError("operation_failed:" + operation)
        return value
    def call(op, payload):
        payload = dict(payload)
        wid = payload.get("workspace_id")
        if wid is not None:
            prefix = "cell-" + run_tag + "-"
            if not str(wid).startswith(prefix):
                wid = prefix + str(wid)
            payload["workspace_id"] = wid
        if op in {"create", "fork", "open"}:
            payload["output_path"] = str(root / wid)
        if op == "save":
            payload["output_path"] = str(root / (wid + "--checkpoint--" + payload["label"]))
        if op in {"create", "fork", "open", "save"}:
            payload = {**payload, "source_quiescent": True}
        if op == "save":
            payload = {**payload, "session_saved": True, "session_kind": "deterministic_fixture_not_live_agent"}
        return event(op, lambda: adapter.call(op, payload), payload.get("workspace_id"))["data"]
    def checked_workspace(data):
        path = checked_root(Path(data["path"]))
        # Mounts/subvolumes can live outside adapter.workspace_root for B2/B3.
        # The backend checks its configured mount root; this runner requires that
        # the returned path is an accessible directory on this host.
        if not path.is_dir():
            raise ValueError("workspace is not locally accessible")
        with lock:
            paths.append((data.get("workspace_id"), str(path)))
        return path
    try:
        parent = None
        if mode == "fork":
            parent = checked_workspace(call("create", {"workspace_id": "parent", "source_path": str(fixture)}))
            event("parent_mutate", lambda: mutate_workspace(parent, -1, profile=profile), "parent")
            call("save", {"workspace_id": "parent", "path": str(parent), "label": "parent-round-1"})
        n = p.get("workspaces", p.get("consumers", 1))
        producer_version = None
        if mode == "sharing":
            producer = checked_workspace(call("create", {"workspace_id": "producer", "source_path": str(fixture)}))
            producer_version = call("save", {"workspace_id": "producer", "path": str(producer), "label": "dataset-v1"})["version_id"]
        def one(i):
            submitted = time.perf_counter()
            try:
                wid = f"item-{i}"
                if mode == "sharing":
                    path = checked_workspace(call("open", {"workspace_id": wid, "version_id": producer_version}))
                    result = event("consume", lambda: consume_records(path, i, p.get("read_pattern", "shared"), profile=profile,
                        output=output / f"consumer-{i}.jsonl"), i)
                elif mode == "metadata":
                    path = checked_workspace(call("create", {"workspace_id": wid, "source_path": str(fixture)}))
                    result = event("query", lambda: query_metadata(path, profile, p.get("order", "direct")), i)
                else:
                    path = checked_workspace(call("fork", {"workspace_id": wid, "parent_path": str(parent)}) if parent else
                        call("create", {"workspace_id": wid, "source_path": str(fixture)}))
                    if not parent:
                        event("read", lambda: read_workload(path, profile), i)
                    options = {"modified_bytes": p.get("modified_bytes", 1048576), "shape": p.get("shape", "contiguous"),
                               "namespace": p.get("namespace", False), "profile": profile}
                    event("mutate", lambda: mutate_workspace(path, i, **options), i)
                    saved = call("save", {"workspace_id": wid, "path": str(path), "label": "round-1"})
                    opened = checked_workspace(call("open", {"workspace_id": wid + "-restored", "version_id": saved["version_id"]}))
                    result = event("verify_restored", lambda: verify_state(opened, i, parent=bool(parent), **options), i)
                return {"item_id": i, "ok": True, "elapsed_s": time.perf_counter() - submitted, "oracle": result}
            except Exception as exc:
                return {"item_id": i, "ok": False, "elapsed_s": time.perf_counter() - submitted, "error_type": type(exc).__name__}
        with ThreadPoolExecutor(max_workers=p.get("concurrency", min(n, 4))) as pool:
            records = list(pool.map(one, range(n)))
        # Child writes must not change the source or saved parent.
        event("verify_base_unchanged", lambda: verify_fixture(fixture, kind, profile))
        if parent:
            event("verify_parent_unchanged", lambda: verify_state(parent, -1, profile=profile))
    except Exception as exc:
        records.append({"item_id": "setup_or_isolation", "ok": False, "error_type": type(exc).__name__})
    finally:
        for wid, path in paths:
            try:
                call("release", {"workspace_id": wid, "path": path})
            except Exception:
                records.append({"item_id": "release:" + str(wid), "ok": False})
    (output / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    (output / "items.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    requested = p.get("workspaces", p.get("consumers", 1))
    recorded = {r["item_id"] for r in records if isinstance(r["item_id"], int)}
    records.extend({"item_id": i, "ok": False, "reason": "not_started"}
                   for i in range(requested) if i not in recorded)
    (output / "items.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    successful = sum(r["ok"] for r in records if isinstance(r["item_id"], int))
    ok = successful == requested and all(r["ok"] for r in records)
    return {"status": "ok" if ok else "error", "measurement_valid": False,
        "measurements": {"requested": requested, "successful": successful, "elapsed_s": time.perf_counter() - started,
                         "deadline_s": None, "physical_bytes": None, "backend_requests": None},
        "qualification_only": True, "profile": profile, "paper_eligible": False,
        "note": "Single-host operation/oracle qualification. Includes verification and command startup; no cloud, capacity or cold-cache performance claim."}
