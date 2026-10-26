"""Generate paired experiment cells; generation never runs a workload or cloud API."""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random

SEED = 20261023
RQ = {"T1": ["RQ1"], "T2": ["RQ2"], "T3": ["RQ2"], "T4": ["RQ3"],
      "T5": ["RQ4"], "T6": ["RQ1", "RQ2", "RQ3"]}
DESIGNS = {"T1": ["D1", "D3"], "T2": ["D1", "D2"], "T3": ["D1", "D4"],
           "T4": ["D1", "D2", "D3", "D4"], "T5": ["D2", "D5"], "T6": ["D1", "D2", "D3", "D4"]}
WORKLOAD = {"T1": "W1", "T2": "W2", "T3": "W3", "T4": "W4", "T5": "W64", "T6": "W6"}
REQUIRED = {"lifecycle": ["create", "save", "open", "release"],
            "fork": ["create", "save", "fork", "open", "release"],
            "sharing": ["create", "save", "open", "release"],
            "metadata": ["create", "release"], "distributed": ["run_scenario"]}
CAMPAIGNS = ("smoke", "baseline-pilot", "rq1-capacity", "rq1-arrivals", "rq1-sharing-boundary",
             "rq2-lifecycle", "rq2-sharing", "rq3-replay", "rq3-live", "rq4-migration",
             "rq4-dirty-migration", "rq4-fault-qualification", "ablations")


def _repetitions(campaign, repetitions):
    if repetitions is None:
        return 1 if campaign == "rq4-fault-qualification" else 3
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        raise ValueError("repetitions must be positive")
    return repetitions


def make_cells(campaign, repetitions=None, seed=SEED):
    repetitions = _repetitions(campaign, repetitions)
    blocks = []
    def add(exp, params, systems, variants=("default",), designs=None, mode="distributed"):
        params = dict(params)
        if mode == "distributed":
            cache = params.setdefault("cache_state", "cold")
            if cache not in ("cold", "warm"):
                raise ValueError("distributed cells require a cold or warm client cache protocol")
            params.setdefault("cache_protocol_id", f"client-{cache}-backend-observed-v1")
            if params.get("workload_kind") != "live_agent":
                params.setdefault("live_model_calls_allowed", False)
        if params.get("workload_kind") == "tool_replay":
            params.setdefault("state_execution", "excluded")
            params.setdefault("trace_manifest_sha256", None)
        for rep in range(1, repetitions + 1):
            identity = json.dumps([campaign, exp, params, rep, seed], sort_keys=True)
            pair = hashlib.sha256(identity.encode()).hexdigest()[:16]
            group = []
            for system, variant in itertools.product(systems, variants):
                group.append({"schema_version": 1, "cell_id": f"{campaign}-{pair}-{system}-{variant}",
                    "experiment_id": exp, "rq": RQ[exp], "design_ids": designs or DESIGNS[exp],
                    "workload_id": WORKLOAD[exp], "baseline_id": system, "variant": variant,
                    "pair_id": pair, "replicate": rep, "profile": "smoke" if campaign == "smoke" else "full",
                    "parameters": {**params, "execution_mode": mode, "seed": seed,
                                   "input_pair_seed": int(pair[:8], 16)},
                    "required_operations": REQUIRED[mode], "status": "planned",
                    "readiness": "deferred_brewfs" if system == "B0" else "local_check" if campaign == "smoke" else
                                 "single_client_qualification" if campaign == "baseline-pilot" else "requires_distributed_adapter"})
            blocks.append(group)
    if campaign == "smoke":
        for exp, mode, params in [
            ("T1", "lifecycle", {"workspaces": 2, "concurrency": 2}),
            ("T2", "fork", {"workspaces": 2, "concurrency": 2, "modified_bytes": 16384, "shape": "scattered", "namespace": True}),
            ("T3", "sharing", {"consumers": 2, "read_pattern": "shared"}),
            ("T6", "metadata", {"order": "direct"})]:
            add(exp, params, ["LOCAL_REFERENCE"], mode=mode)
    elif campaign == "baseline-pilot":
        for concurrency in (1, 4):
            add("T1", {"workspaces": 16, "concurrency": concurrency,
                "cache_state": "unqualified", "purpose": "operation_correctness"}, ["B1", "B2"], mode="lifecycle")
    elif campaign == "rq1-capacity":
        for n in (16, 64, 256, 1024):
            add("T1", {"workspaces": n, "admission_cutoff_s": 120, "hold_until_s": 180,
                       "verify_at_s": [120, 180], "client_nodes": 4}, ["B0", "B1", "B2", "B3"])
    elif campaign == "rq1-arrivals":
        for rate in (1, 4, 16, 64):
            for operation in ("create_save_release", "save_existing"):
                add("T1", {"requests_per_s": rate, "operation": operation, "warmup_s": 300,
                    "measurement_s": 900, "drain_s": 600, "deadline_s": 120,
                    "active_slots": 64, "arrival_model": "open_loop"}, ["B0", "B1", "B2", "B3"])
    elif campaign == "rq1-sharing-boundary":
        for versions in (1, 64):
            add("T1", {"workspaces": 256, "client_nodes": 4, "base_version_count": versions,
                "base_version_assignment": "workspace_id % base_version_count + 1",
                "same_paths_sizes_permissions_and_operations": True,
                "admission_cutoff_s": 120, "hold_until_s": 180, "verify_at_s": [120, 180],
                "cache_state": "cold", "cache_budget_total_bytes": 32 * 2**30,
                "required_receipt_fields": ["base_version_count", "content_reuse_fraction",
                    "unique_read_bytes", "cache_bytes", "on_time_usable_workspaces"],
                "reference_policy": "fresh_paired_reference_not_reused_from_capacity_campaign"},
                ["B0", "B1", "B2", "B3"])
    elif campaign == "rq2-lifecycle":
        for size, shape in itertools.product((1, 16, 128, 512), ("contiguous", "scattered")):
            add("T2", {"workspaces": 16, "modified_bytes": size * 2**20, "shape": shape,
                       "namespace": True, "parent": "saved_modified_parent"}, ["B0", "B1", "B2", "B3"])
        for history in (1000, 10000, 100000):
            add("T2", {"active_workspaces": 16, "retained_versions": history,
                       "delete_parity": "even", "verify_restored_versions": 100, "gc_deadline_s": 3600}, ["B0", "B1", "B2", "B3"])
    elif campaign == "rq2-sharing":
        for n, pattern in itertools.product((1, 16, 64), ("shared", "disjoint", "sequential")):
            add("T3", {"consumers": n, "read_pattern": pattern, "versions": [1, 2],
                       "v2_changed_records": 2622, "include_publish_cost": True}, ["B0", "B1", "B2", "B3"])
    elif campaign == "rq3-replay":
        for c in (1, 4, 16):
            add("T4", {"tasks": 16, "concurrency": c, "task_timeout_s": 1800,
                       "batch_deadline_s": 28800, "manifest": "inputs/live-tasks.json",
                       "workload_kind": "tool_replay", "live_model_calls_allowed": False,
                       "trace_manifest": None, "requires_qualified_trace_manifest": True,
                       "divergence_policy": "stop_task_no_model_fallback",
                       "think_time_mode": "recorded_delay_after_predecessor",
                       "requires_image_digests": True}, ["B0", "B1", "B2", "B3"])
    elif campaign == "rq3-live":
        for c in (1, 4, 16):
            add("T4", {"tasks": 16, "concurrency": c, "task_timeout_s": 1800,
                       "batch_deadline_s": 28800, "manifest": "inputs/live-tasks.json",
                       "requires_image_digests": True, "requires_model_id": True,
                       "workload_kind": "live_agent", "budget_status": "optional_expansion_not_current_default"},
                ["B0", "B1", "B2", "B3"])
    elif campaign == "rq4-migration":
        for c in (1, 4, 16):
            add("T5", {"workspaces": 64, "selected": list(range(0, 64, 4)), "neighbors": 48,
                "concurrency": c, "pause_together": True, "first_result_deadline_s": 600,
                "batch_deadline_s": 900, "neighbor_rounds": 120, "neighbor_interval_s": 5,
                "neighbor_deadline_s": 120, "zone_mode": "same_zone"},
                ["B0", "B1", "B2", "B3", "B4"], variants=("no_migration", "migration"))
    elif campaign == "rq4-dirty-migration":
        for dirty_mib in (0, 1, 128):
            add("T5", {"workspaces": 16, "selected": [0, 4, 8, 12], "neighbors": 12,
                "client_nodes": 4, "source_node_formula": "workspace_id // 4",
                "target_node_formula": "(source_node + 1) % 4", "concurrency": 4,
                "initial_confirmed_round": 10, "dirty_modified_bytes": dirty_mib * 2**20,
                "dirty_shape": "contiguous", "dirty_write_round": 11 if dirty_mib else 10,
                "dirty_session_bytes": 65536 if dirty_mib else 0,
                "expected_restored_round": 11 if dirty_mib else 10,
                "dirty_bytes_semantics": "logical_bytes_written_since_last_confirmed_checkpoint",
                "pause_together": True, "timing_origin": "pause_requested",
                "save_in_pause_interval": True, "pre_pause_checkpoint_allowed": False,
                "unconfirmed_write_rule": ("finish_fixed_tool_writes_without_common_checkpoint_then_pause"
                                           if dirty_mib else "no_post_checkpoint_writes_then_pause"),
                "control_rule": "same_pause_and_save_then_continue_on_source_node",
                "required_receipt_fields": ["bytes_written_since_checkpoint", "post_pause_upload_bytes",
                    "post_pause_sync_bytes", "checkpoint_confirmed", "file_session_generation",
                    "target_first_useful_result", "neighbor_results"],
                "first_result_deadline_s": 600, "batch_deadline_s": 900,
                "neighbor_rounds": 120, "neighbor_interval_s": 5, "neighbor_deadline_s": 120,
                "zone_mode": "same_zone", "requires_dirty_checkpoint_adapter": True,
                "requires_no_checkpoint_write_hook": True},
                ["B0", "B2", "B4"], variants=("no_migration", "migration"))
    elif campaign == "rq4-fault-qualification":
        common = {"purpose": "correctness_qualification", "performance_claims_allowed": False,
            "requires_fault_adapter": True, "live_model_calls_allowed": False,
            "workspaces": 1, "initial_confirmed_round": 10, "next_round": 11,
            "modified_bytes": 2**20, "synthetic_session_bytes": 65536,
            "first_result_deadline_s": 600, "batch_deadline_s": 900,
            "failure_scope": "client_node_including_fuse_client_remote_storage_healthy",
            "no_forced_detach_in_normal_migration": True,
            "qualification_repetition_rule": "one_attempt_per_cell_until_explicitly_expanded"}
        fault_specs = {
            "before_ack": {
                "trigger_event": "P1_changes_written_common_ack_blocked",
                "required_hooks": ["freeze_confirmed_P0", "write_P1_block_common_ack",
                    "make_entire_client_unavailable", "restore_exact_P0", "verify_P0_state"],
                "requires_frozen_confirmed_checkpoint": True,
                "oracle": {"recover_checkpoint": "P0", "file_session_tool_generation_must_match": True,
                    "no_unconfirmed_P1_state_exposed": True, "missing_history_support": "unsupported"}},
            "after_ack": {
                "trigger_event": "P1_common_ack_received_before_next_write",
                "required_hooks": ["save_and_ack_P1", "block_next_write", "make_entire_client_unavailable",
                    "restore_on_other_node", "verify_P1_state"],
                "oracle": {"recover_checkpoint": "P1", "confirmed_state_loss_allowed": False,
                    "file_session_tool_generation_must_match": True}},
            "stale_writer": {
                "trigger_event": "target_new_writer_generation_confirmed_source_old_process_alive",
                "required_hooks": ["isolate_source_control_path", "acquire_new_writer_generation",
                    "target_write_and_confirm_P1", "old_identity_write_and_commit_sentinel",
                    "read_current_target_state"],
                "fault_must_not_preblock_data_path": True, "sentinel_bytes": 4096,
                "sentinel_path": "large/l000.bin", "sentinel_offset": 0,
                "sentinel_payload": "SHAKE256('brewfs-ax-v2|stale-writer-sentinel').digest(4096)",
                "oracle": {"local_write_and_current_commit_reported_separately": True,
                    "stale_current_commit_allowed": False, "sentinel_in_current_target_allowed": False,
                    "target_checkpoint": "P1", "neighbor_eviction_effects_reported": True}},
        }
        for fault, spec in fault_specs.items():
            add("T5", {**common, "fault_case": fault, **spec}, ["B0", "B1", "B2", "B3", "B4"])
        add("T3", {"purpose": "correctness_qualification", "performance_claims_allowed": False,
            "requires_fault_adapter": True, "live_model_calls_allowed": False,
            "fault_case": "half_publish", "consumers": 2, "versions": [1, 2],
            "source_shard_count": 64, "halt_after_saved_shards": 32,
            "trigger_event": "32_of_64_V2_application_shards_saved_before_publication",
            "required_hooks": ["publish_and_bind_V1", "save_32_V2_shards", "block_V2_publication",
                "interrupt_producer", "verify_old_V1_and_new_binding"],
            "oracle": {"V1_remains_complete": True, "partial_V2_binding_allowed": False,
                "orphan_objects_and_reclamation_cost_reported": True},
            "failure_scope": "producer_stopped_before_atomic_version_publication",
            "qualification_repetition_rule": "one_attempt_per_cell_until_explicitly_expanded"},
            ["B0", "B1", "B2", "B3"])
    elif campaign == "ablations":
        # Each block pairs runs with identical inputs, budgets and random seeds.
        # These cells deliberately remain deferred until the mechanism is real.
        add("T6", {"ablation_id": "A1", "task": "create_then_lookup", "workspaces": 64,
            "cache_state": "cold", "required_receipt_fields": ["shared_base", "packed_metadata"]},
            ["B0"], ("shared_packed", "shared_plain", "private_packed", "private_plain"), ["D1", "D3"])
        for size in (1, 128):
            add("T2", {"ablation_id": "A2", "workspaces": 16, "modified_bytes": size * 2**20,
                       "required_receipt_fields": ["save_mode", "remote_ack"]},
                ["B0"], ("private_delta", "full_materialization"), ["D2"])
        for order in ("direct", "list-first"):
            for cache in ("cold", "warm"):
                add("T6", {"ablation_id": "A3", "order": order, "cache_state": cache,
                    "positive_lookups": 256, "negative_lookups": 256,
                    "required_receipt_fields": ["metadata_representation", "cache_mode"]},
                    ["B0"], ("packed", "equivalent_plain"), ["D3"])
        for pattern in ("shared", "scattered", "sequential"):
            add("T3", {"ablation_id": "A4", "read_pattern": pattern, "consumers": 16,
                "required_receipt_fields": ["object_digest", "B", "q", "ec_data", "ec_parity", "range_mode"]},
                ["B0"], ("fixed_layout_range", "selected_layout_range", "fixed_layout_whole_get", "selected_layout_whole_get"), ["D4"])
        add("T5", {"ablation_id": "A5", "workspaces": 64, "selected": list(range(0, 64, 4)),
            "first_result_deadline_s": 600, "batch_deadline_s": 900,
            "required_receipt_fields": ["transfer_mode", "ownership_protocol"]},
            ["B0"], ("reopen_remote_state", "copy_then_resume"), ["D2", "D5"])
        add("T4", {"ablation_id": "A6", "tasks": 16, "concurrency": 4,
            "task_timeout_s": 1800, "batch_deadline_s": 28800,
            "workload_kind": "tool_replay", "live_model_calls_allowed": False,
            "trace_manifest": None, "requires_qualified_trace_manifest": True,
            "divergence_policy": "stop_task_no_model_fallback",
            "think_time_mode": "recorded_delay_after_predecessor",
            "required_receipt_fields": ["metadata_representation", "save_mode", "layout_policy"]},
            ["B0"], ("full", "plain_metadata", "full_materialization", "fixed_layout"), ["D2", "D3", "D4"])
        # Ownership enforcement is never disabled for a performance headline.
        # Fault rejection belongs to correctness trials in the protocol.
    else:
        raise ValueError("unknown campaign")
    rng = random.Random(seed)
    rng.shuffle(blocks)
    cells = []
    for block in blocks:
        rng.shuffle(block)
        cells.extend(block)
    for i, cell in enumerate(cells):
        cell["execution_order"] = i + 1
    return cells


def write_campaign(campaign, output, repetitions=None, seed=SEED):
    """Write a new plan directory and hash the exact UTF-8/LF cell bytes on disk."""
    repetitions = _repetitions(campaign, repetitions)
    cells = make_cells(campaign, repetitions, seed)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cell_sha256 = {}
    for cell in cells:
        payload = (json.dumps(cell, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (output / (cell["cell_id"] + ".json")).write_bytes(payload)
        cell_sha256[cell["cell_id"]] = hashlib.sha256(payload).hexdigest()
    index = {"schema_version": 1, "campaign": campaign, "status": "planned", "cells": len(cells),
        "execution_enabled": False, "repetitions": repetitions, "seed": seed,
        "order": [c["cell_id"] for c in cells], "cell_sha256": cell_sha256}
    (output / "index.json").write_bytes((json.dumps(index, indent=2) + "\n").encode("utf-8"))
    return index


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--campaign", required=True, choices=CAMPAIGNS)
    p.add_argument("--repetitions", type=int, default=None,
                   help="default 1 for fault qualification, otherwise 3; generation never runs the plan")
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    index = write_campaign(a.campaign, a.output, a.repetitions, a.seed)
    print(json.dumps({"status": "generated_not_executed", "cells": index["cells"], "output": str(a.output)}))


if __name__ == "__main__":
    main()
