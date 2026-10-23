"""Mechanical input/receipt checks, never permission to claim performance.

Hashes and internally consistent replay receipts establish what was supplied;
they do not independently establish that a cloud operation actually happened.
"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re

PACKAGE = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IMAGE = re.compile(r"(?:[^\s]+@)?sha256:[0-9a-f]{64}\Z")
CACHE_PROTOCOLS = {f"client-{state}-backend-observed-v1": state for state in ("cold", "warm")}
DIRTY_HOOKS = {"write_without_checkpoint", "pause_selected", "save_after_pause",
               "confirm_checkpoint", "resume_selected"}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha256(value):
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def artifact(reference, root):
    """References are relative to a fixed root, never an implicit path allowlist."""
    if not isinstance(reference, dict) or not sha256(reference.get("sha256")):
        raise ValueError("invalid artifact reference")
    name = reference.get("path")
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("artifact path must be relative")
    root = Path(root).resolve()
    path = (root / name).resolve(strict=True)
    if path == root or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("artifact escapes its root")
    if digest(path) != reference["sha256"]:
        raise ValueError("artifact hash mismatch")
    return path


def jsonl(path):
    records = []
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            value = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
            if not isinstance(value, dict):
                raise ValueError("JSONL record must be an object")
            records.append(value)
    if not records:
        raise ValueError("empty JSONL")
    return records


def check_trace(task, root):
    trace_path = artifact(task.get("trace"), root)
    observed_path = artifact(task.get("qualification_events"), root)
    trace, observed = jsonl(trace_path), jsonl(observed_path)
    header, receipt = trace[0], observed[0]
    for record in (header, receipt):
        for key in ("instance_id", "base_commit", "image_digest", "state_execution"):
            if record.get(key) != task[key]:
                raise ValueError("trace identity mismatch")
        if record.get("schema_version") != 1:
            raise ValueError("trace schema mismatch")
    if (header.get("format") != "ax-tool-trace" or header.get("workload_kind") != "tool_replay"
            or header.get("live_model_calls_allowed") is not False
            or header.get("divergence_policy") != "stop_task_no_model_fallback"):
        raise ValueError("not a bounded tool trace")
    if (receipt.get("format") != "ax-tool-replay-qualification"
            or receipt.get("trace_sha256") != task["trace"]["sha256"]
            or type(receipt.get("model_calls")) is not int or receipt["model_calls"] != 0
            or receipt.get("model_fallback_used") is not False):
        raise ValueError("qualification provenance mismatch")
    steps, events = trace[1:], observed[1:]
    if not steps or len(steps) != len(events):
        raise ValueError("qualification step coverage mismatch")
    seen = set()
    for step, event in zip(steps, events):
        sid = step.get("step_id")
        if not isinstance(sid, str) or not sid or sid in seen or event.get("step_id") != sid:
            raise ValueError("invalid or reordered tool step")
        dependencies = step.get("depends_on")
        if not isinstance(dependencies, list) or not all(isinstance(x, str) and x in seen for x in dependencies):
            raise ValueError("invalid tool dependencies")
        argv = step.get("argv")
        if step.get("op") != "tool" or not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise ValueError("missing actual tool command")
        cwd = step.get("cwd")
        if (not isinstance(cwd, str) or ".." in PurePosixPath(cwd).parts
                or not PurePosixPath(cwd).is_relative_to("/workspace")):
            raise ValueError("missing fixed workspace cwd")
        expected = step.get("expected")
        if not isinstance(expected, dict) or type(expected.get("exit_code")) is not int:
            raise ValueError("missing tool outcome oracle")
        for field in ("stdout_sha256", "stderr_sha256", "files_sha256"):
            if not sha256(expected.get(field)):
                raise ValueError("invalid tool oracle hash")
        if (event.get("status") != "matched" or type(event.get("exit_code")) is not int
                or any(event.get(k) != v for k, v in expected.items())):
            raise ValueError("qualification outcome diverged")
        seen.add(sid)


def replay_problems(cell):
    p = cell["parameters"]
    if cell.get("experiment_id") == "T4" and p.get("workload_kind") not in {"tool_replay", "live_agent"}:
        return ["replay:workload_kind_missing_or_unknown"]
    if (p.get("workload_kind") != "tool_replay" and not p.get("requires_qualified_trace_manifest")
            and p.get("ablation_id") != "A6"):
        return []
    problems = []
    if p.get("workload_kind") != "tool_replay" or p.get("live_model_calls_allowed") is not False:
        problems.append("replay:no_model_policy_required")
    if p.get("divergence_policy") != "stop_task_no_model_fallback":
        problems.append("replay:stop_on_divergence_required")
    name = p.get("trace_manifest")
    if not isinstance(name, str) or not name:
        return problems + ["replay:trace_manifest_missing"]
    if not sha256(p.get("trace_manifest_sha256")):
        return problems + ["replay:trace_manifest_sha256_missing"]
    try:
        path = Path(name)
        if not path.is_absolute():
            path = PACKAGE / path
        path = path.resolve(strict=True)
        if digest(path) != p["trace_manifest_sha256"]:
            raise ValueError("manifest hash mismatch")
        manifest = read_json(path)
        frozen_path = PACKAGE / "inputs" / "live-tasks.json"
        frozen = read_json(frozen_path)
        expected = {t["instance_id"]: t for t in frozen["tasks"]}
        if (manifest.get("schema_version") != 1 or manifest.get("workload_kind") != "tool_replay"
                or manifest.get("frozen_tasks_sha256") != digest(frozen_path)
                or type(p.get("tasks")) is not int or p["tasks"] != len(expected) or len(expected) != 16):
            raise ValueError("manifest does not bind the 16 frozen tasks")
        mode = manifest.get("state_execution")
        if mode not in {"excluded", "recorded_state_io", "native_runtime"} or mode != p.get("state_execution", "excluded"):
            raise ValueError("state execution mismatch")
        tasks = manifest.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != len(expected):
            raise ValueError("missing tasks")
        ids = [t.get("instance_id") for t in tasks if isinstance(t, dict)]
        if len(ids) != len(set(ids)) or set(ids) != set(expected):
            raise ValueError("task identity coverage mismatch")
        for task in tasks:
            source = expected[task["instance_id"]]
            if task.get("base_commit") != source["base_commit"] or task.get("prompt_sha256") != source["prompt_sha256"]:
                raise ValueError("frozen task input mismatch")
            if not isinstance(task.get("image_digest"), str) or IMAGE.fullmatch(task["image_digest"]) is None:
                raise ValueError("image is not pinned by digest")
            if task.get("state_execution") != mode:
                raise ValueError("task state execution mismatch")
            check_trace(task, path.parent)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # Bounded errors, never include trace content or operator paths.
        problems.append("replay:manifest_or_artifact_invalid:" + type(exc).__name__)
    return problems


def cache_problems(cell, observations, artifact_root):
    p = cell["parameters"]
    protocol = p.get("cache_protocol_id")
    if protocol not in CACHE_PROTOCOLS:
        return ["cache:unknown_protocol"]
    expected = CACHE_PROTOCOLS[protocol]
    if p.get("cache_state", expected) != expected:
        return ["cache:planned_state_mismatch"]
    if not isinstance(observations, dict):
        return ["cache:observations_must_be_object"]
    errors = []
    if observations.get("protocol_id") != protocol or observations.get("client_state") != expected:
        errors.append("cache:observed_state_mismatch")
    if observations.get("backend_state") not in {"cold", "warm", "unknown", "managed_uncontrolled", "mixed"}:
        errors.append("cache:backend_state_missing")
    if type(observations.get("reset_after_validation")) is not bool:
        errors.append("cache:reset_order_missing")
    if expected == "cold":
        if observations.get("reset_after_validation") is not True:
            errors.append("cache:reset_must_follow_validation")
        actions = observations.get("actions", {})
        for name in ("page", "dentry", "process", "filesystem"):
            try:
                action = actions.get(name, {})
                if not isinstance(action.get("method"), str) or not action["method"]:
                    raise ValueError("missing cache action")
                saved = read_json(artifact(action.get("receipt"), artifact_root))
                if (saved.get("cache") != name or saved.get("method") != action["method"]
                        or saved.get("completed") is not True or saved.get("after_validation") is not True):
                    raise ValueError("cache receipt mismatch")
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                errors.append("cache:invalid_" + name + "_receipt")
    return errors


def scenario_capability_problems(cell, discovery):
    """Discover must name the requested operation; ordinary migration is insufficient."""
    p = cell["parameters"]
    dirty = (p.get("requires_dirty_checkpoint_adapter") or p.get("requires_no_checkpoint_write_hook")
             or "dirty_modified_bytes" in p)
    fault = p.get("requires_fault_adapter") or "fault_case" in p
    if not dirty and not fault:
        return []
    capabilities = discovery.get("scenario_capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("schema_version") != 1:
        return ["scenario_capabilities:missing_or_invalid"]
    problems = []

    def check_hooks(section, required, prefix):
        hooks = section.get("hooks")
        if not isinstance(hooks, list) or not all(isinstance(x, str) and x for x in hooks):
            problems.append(prefix + ":hooks_missing")
            return
        for hook in sorted(required):
            if hook not in hooks:
                problems.append(prefix + ":hook_missing:" + hook)

    if dirty:
        section = capabilities.get("dirty_checkpoint")
        if not isinstance(section, dict):
            problems.append("dirty_checkpoint:unsupported")
        else:
            for key in ("supported", "write_without_checkpoint", "save_after_pause"):
                if section.get(key) is not True:
                    problems.append("dirty_checkpoint:requires_" + key)
            required = p.get("required_hooks", [])
            if not isinstance(required, list) or not all(isinstance(x, str) and x for x in required):
                problems.append("dirty_checkpoint:invalid_required_hooks")
            else:
                check_hooks(section, DIRTY_HOOKS | set(required), "dirty_checkpoint")
    if fault:
        case = p.get("fault_case")
        required = p.get("required_hooks")
        if not isinstance(case, str) or not case:
            problems.append("fault:case_missing")
        elif not isinstance(required, list) or not required or not all(isinstance(x, str) and x for x in required):
            problems.append("fault:required_hooks_missing")
        else:
            faults = capabilities.get("faults")
            section = faults.get(case) if isinstance(faults, dict) else None
            if not isinstance(section, dict) or section.get("supported") is not True:
                problems.append("fault:unsupported_case:" + case)
            else:
                check_hooks(section, set(required), "fault:" + case)
    return problems


def mechanism_problems(cell, receipt):
    p, variant = cell["parameters"], cell["variant"]
    ablation = p.get("ablation_id")
    if not ablation:
        return []
    if not isinstance(receipt, dict):
        return ["receipt:must_be_object"]
    allowed = {
        "A1": {"shared_packed", "shared_plain", "private_packed", "private_plain"},
        "A2": {"private_delta", "full_materialization"},
        "A3": {"packed", "equivalent_plain"},
        "A4": {"fixed_layout_range", "selected_layout_range", "fixed_layout_whole_get", "selected_layout_whole_get"},
        "A5": {"reopen_remote_state", "copy_then_resume"},
        "A6": {"full", "plain_metadata", "full_materialization", "fixed_layout"},
    }
    if variant not in allowed.get(ablation, set()):
        return ["receipt:unknown_ablation_variant"]
    errors = []
    if receipt.get("variant") != variant:
        errors.append("variant_receipt_mismatch")
    for field in p["required_receipt_fields"]:
        if field not in receipt or receipt[field] is None:
            errors.append("receipt:" + field)
    expected = {}
    if ablation == "A1":
        choices = {"shared_packed": (True, True), "shared_plain": (True, False),
                   "private_packed": (False, True), "private_plain": (False, False)}
        if variant in choices:
            expected = dict(zip(("shared_base", "packed_metadata"), choices[variant]))
    elif ablation == "A2":
        expected = {"save_mode": variant, "remote_ack": True}
    elif ablation == "A3":
        expected = {"metadata_representation": variant, "cache_mode": p["cache_state"]}
    elif ablation == "A4":
        expected = {"layout_policy": "selected" if variant.startswith("selected_") else "fixed",
                    "range_mode": "whole_get" if variant.endswith("whole_get") else "range"}
        if not sha256(receipt.get("object_digest")):
            errors.append("receipt:invalid_object_digest")
        for name in ("B", "q", "ec_data", "ec_parity"):
            value = receipt.get(name)
            if type(value) is not int or value < (0 if name == "ec_parity" else 1):
                errors.append("receipt:invalid_" + name)
    elif ablation == "A5":
        expected = {"transfer_mode": variant}
        ownership = receipt.get("ownership_protocol")
        if (not isinstance(ownership, dict) or not isinstance(ownership.get("protocol_id"), str)
                or not ownership.get("protocol_id") or ownership.get("single_writer") is not True
                or ownership.get("stale_publication_rejected") is not True):
            errors.append("receipt:ownership_protocol_not_fenced")
    elif ablation == "A6":
        expected = {"metadata_representation": "equivalent_plain" if variant == "plain_metadata" else "packed",
                    "save_mode": "full_materialization" if variant == "full_materialization" else "private_delta",
                    "layout_policy": "fixed" if variant == "fixed_layout" else "selected"}
    for field, value in expected.items():
        observed = receipt.get(field)
        if type(observed) is not type(value) or observed != value:
            errors.append("receipt:wrong_" + field)
    if not expected:
        errors.append("receipt:unknown_ablation_variant")
    return errors


def finite_time(value, *, positive):
    return (type(value) in {float, int} and math.isfinite(value)
            and (value > 0 if positive else value >= 0))
