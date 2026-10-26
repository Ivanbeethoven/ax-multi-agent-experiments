"""Synthetic protocol fixtures only: no recorded Agent, cloud or performance data."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.adapter import Adapter
from harness.generate_campaign import make_cells
from harness.qualification import (DIRTY_HOOKS, PACKAGE, artifact, cache_problems, digest,
                                   mechanism_problems, replay_problems)
from harness.run_cell import preflight, run
from harness.scenarios import distributed


HASH = hashlib.sha256(b"synthetic unit test only").hexdigest()
RUNTIME = {"versions": {"storage": "test"}, "resources": {"cpu": 1}, "configuration": {"cache": "test"}}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path, data):
    path.write_text("".join(json.dumps(r) + "\n" for r in data), encoding="utf-8")


class FakeAdapter:
    scope = "real"

    def __init__(self, root, cell):
        self.workspace_root = root / "workspaces"
        self.workspace_root.mkdir(exist_ok=True)
        self.baseline_id = cell["baseline_id"]
        self.config = {"state": "configured", "runtime": RUNTIME}
        self.calls = []
        self.response = {"status": "ok", "data": {}}

    def supports(self, operation):
        return True

    def call(self, operation, payload, **kwargs):
        self.calls.append((operation, payload))
        if operation == "discover":
            data = {"operations": ["discover", "run_scenario"], "runtime": RUNTIME}
            if hasattr(self, "scenario_capabilities"):
                data["scenario_capabilities"] = self.scenario_capabilities
            return {"status": "ok", "data": data}
        return self.response


class ReplayQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cell = next(c for c in make_cells("rq3-replay", 1) if c["baseline_id"] == "B1")
        self.cell["parameters"].update(cache_protocol_id="client-warm-backend-observed-v1", cache_state="warm")
        self.adapter = FakeAdapter(self.root, self.cell)
        self.manifest_path = self.root / "manifest.json"
        self.manifest = {"schema_version": 1, "workload_kind": "tool_replay", "state_execution": "excluded",
                         "frozen_tasks_sha256": digest(PACKAGE / "inputs" / "live-tasks.json"), "tasks": []}
        frozen = json.loads((PACKAGE / "inputs" / "live-tasks.json").read_text(encoding="utf-8"))
        for i, source in enumerate(frozen["tasks"]):
            task = {k: source[k] for k in ("instance_id", "base_commit", "prompt_sha256")}
            task.update(image_digest="sha256:" + HASH, state_execution="excluded")
            identity = {k: task[k] for k in ("instance_id", "base_commit", "image_digest", "state_execution")}
            header = {"schema_version": 1, "format": "ax-tool-trace", "workload_kind": "tool_replay",
                      "live_model_calls_allowed": False, "divergence_policy": "stop_task_no_model_fallback", **identity}
            expected = {"exit_code": 0, "stdout_sha256": HASH, "stderr_sha256": HASH, "files_sha256": HASH}
            step = {"step_id": "s1", "op": "tool", "argv": ["synthetic-never-executed"],
                    "cwd": "/workspace/repo", "depends_on": [], "expected": expected}
            trace = self.root / f"trace-{i}.jsonl"
            write_jsonl(trace, [header, step])
            task["trace"] = {"path": trace.name, "sha256": digest(trace)}
            receipt = {"schema_version": 1, "format": "ax-tool-replay-qualification", **identity,
                       "trace_sha256": digest(trace), "model_calls": 0, "model_fallback_used": False}
            events = self.root / f"events-{i}.jsonl"
            write_jsonl(events, [receipt, {"step_id": "s1", "status": "matched", **expected}])
            task["qualification_events"] = {"path": events.name, "sha256": digest(events)}
            self.manifest["tasks"].append(task)
        self.save_manifest()

    def save_manifest(self):
        write_json(self.manifest_path, self.manifest)
        self.cell["parameters"].update(trace_manifest=str(self.manifest_path), trace_manifest_sha256=digest(self.manifest_path))

    def alter_artifact(self, key, mutate):
        task = self.manifest["tasks"][0]
        path = self.root / task[key]["path"]
        records = [json.loads(line) for line in path.read_text().splitlines()]
        mutate(records)
        write_jsonl(path, records)
        task[key]["sha256"] = digest(path)
        self.save_manifest()

    def test_consistent_synthetic_files_pass_input_check_only(self):
        result = preflight(self.cell, self.adapter)
        self.assertEqual(result["status"], "ok", result)
        self.assertFalse(result["performance_claims_allowed"])

    def test_null_or_self_declared_qualified_rejected_before_discover(self):
        self.cell["parameters"]["trace_manifest"] = None
        self.cell["parameters"]["qualified"] = True
        result = preflight(self.cell, self.adapter)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.adapter.calls, [])

    def test_a6_null_manifest_is_rejected(self):
        cell = next(c for c in make_cells("ablations", 1) if c["parameters"]["ablation_id"] == "A6")
        self.assertIn("replay:trace_manifest_missing", replay_problems(cell))
        cell["parameters"].pop("requires_qualified_trace_manifest")
        cell["parameters"].pop("workload_kind")
        self.assertTrue(replay_problems(cell), "removing flags must not bypass the A6 check")

    def test_manifest_and_file_hashes_are_recomputed(self):
        self.cell["parameters"]["trace_manifest_sha256"] = "0" * 64
        self.assertTrue(replay_problems(self.cell))
        self.save_manifest()
        (self.root / self.manifest["tasks"][0]["trace"]["path"]).write_text("modified")
        self.assertTrue(replay_problems(self.cell))

    def test_missing_duplicate_and_changed_frozen_inputs_are_rejected(self):
        original = copy.deepcopy(self.manifest)
        mutations = [lambda m: m["tasks"].pop(),
                     lambda m: m["tasks"].__setitem__(1, copy.deepcopy(m["tasks"][0])),
                     lambda m: m["tasks"][0].update(base_commit="0" * 40),
                     lambda m: m["tasks"][0].update(image_digest="image:latest"),
                     lambda m: m.update(frozen_tasks_sha256="0" * 64),
                     lambda m: m.update(state_execution="native_runtime")]
        for mutation in mutations:
            self.manifest = copy.deepcopy(original)
            mutation(self.manifest)
            self.save_manifest()
            self.assertTrue(replay_problems(self.cell))

    def test_filesystem_replay_is_not_tool_replay(self):
        self.alter_artifact("trace", lambda rows: rows[0].update(format="mounted-fs-replay"))
        self.assertTrue(replay_problems(self.cell))

    def test_recorded_outcomes_must_match_actual_qualification_events(self):
        self.alter_artifact("qualification_events", lambda rows: rows[1].update(exit_code=1))
        self.assertTrue(replay_problems(self.cell))

    def test_model_fallback_and_missing_event_are_rejected(self):
        self.alter_artifact("qualification_events", lambda rows: rows[0].update(model_calls=1))
        self.assertTrue(replay_problems(self.cell))
        self.alter_artifact("qualification_events", lambda rows: rows.pop())
        self.assertTrue(replay_problems(self.cell))

    def test_artifact_reference_cannot_escape_root(self):
        with self.assertRaises((OSError, ValueError)):
            artifact({"path": "../escape", "sha256": HASH}, self.root)


class DistributedReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cell = next(c for c in make_cells("rq1-capacity", 1) if c["baseline_id"] == "B1")
        self.cell["parameters"].update(cache_protocol_id="client-warm-backend-observed-v1", cache_state="warm")
        self.adapter = FakeAdapter(self.root, self.cell)
        self.evidence = {key: HASH for key in ("resource_manifest_sha256", "input_manifest_sha256", "raw_events_sha256", "correctness_report_sha256")}
        self.evidence.update(clock_definition={"clock": "coordinator_monotonic"}, durability_qualification={"source": "unit-test-only"},
            cache_observations={"protocol_id": "client-warm-backend-observed-v1", "client_state": "warm",
                                "reset_after_validation": False, "backend_state": "unknown"})
        n = self.cell["parameters"]["workspaces"]
        self.adapter.response = {"status": "ok", "data": {"measurement_evidence": self.evidence,
            "measurements": {"requested": n, "successful": n, "elapsed_s": 1, "deadline_s": 120}}}
        self.serial = 0

    def execute(self):
        self.serial += 1
        cell = copy.deepcopy(self.cell)
        cell["cell_id"] += f"-{self.serial}"
        return distributed(cell, self.adapter, self.root / f"output-{self.serial}")

    def test_each_cell_has_separate_data_and_external_artifacts(self):
        first, second = self.execute(), self.execute()
        self.assertNotEqual(first["data_root"], second["data_root"])
        self.assertNotEqual(first["artifact_root"], second["artifact_root"])
        self.assertFalse(Path(first["artifact_root"]).is_relative_to(self.adapter.workspace_root))
        self.assertTrue(first["contract_qualified"])
        self.assertFalse(first["measurement_valid"])

    def test_reusing_cell_data_directory_is_refused(self):
        distributed(self.cell, self.adapter, self.root / "output-a")
        with self.assertRaises(FileExistsError):
            distributed(self.cell, self.adapter, self.root / "output-b")

    def test_invalid_times_hashes_and_cache_objects_are_rejected(self):
        for field, value in (("elapsed_s", -1), ("deadline_s", 0), ("elapsed_s", float("inf")), ("elapsed_s", True)):
            with self.subTest(field=field, value=value):
                self.adapter.response["data"]["measurements"][field] = value
                self.assertEqual(self.execute()["status"], "rejected")
                self.adapter.response["data"]["measurements"].update(elapsed_s=1, deadline_s=120)
        self.evidence["raw_events_sha256"] = "not-a-hash"
        self.assertEqual(self.execute()["status"], "rejected")
        self.evidence["raw_events_sha256"] = HASH
        self.evidence["cache_observations"] = "cold"
        self.assertEqual(self.execute()["status"], "rejected")

    def test_cold_cache_needs_after_validation_actions_with_matching_files(self):
        self.cell["parameters"].update(cache_protocol_id="client-cold-backend-observed-v1", cache_state="cold")
        obs = {"protocol_id": "client-cold-backend-observed-v1", "client_state": "cold",
               "reset_after_validation": True, "backend_state": "managed_uncontrolled", "actions": {}}
        self.assertTrue(cache_problems(self.cell, obs, self.root))
        for name in ("page", "dentry", "process", "filesystem"):
            path = self.root / (name + ".json")
            write_json(path, {"cache": name, "method": "unit-test-no-action", "completed": True, "after_validation": True})
            obs["actions"][name] = {"method": "unit-test-no-action", "receipt": {"path": path.name, "sha256": digest(path)}}
        self.assertEqual(cache_problems(self.cell, obs, self.root), [])
        (self.root / "page.json").write_text("tampered")
        self.assertIn("cache:invalid_page_receipt", cache_problems(self.cell, obs, self.root))

    def test_ablation_values_must_match_the_named_variant(self):
        cells = make_cells("ablations", 1)
        a1 = next(c for c in cells if c["variant"] == "shared_packed")
        self.assertTrue(mechanism_problems(a1, {"variant": "shared_packed", "shared_base": False, "packed_metadata": False}))
        self.assertEqual(mechanism_problems(a1, {"variant": "shared_packed", "shared_base": True, "packed_metadata": True}), [])
        a2 = next(c for c in cells if c["parameters"]["ablation_id"] == "A2" and c["variant"] == "private_delta")
        self.assertTrue(mechanism_problems(a2, {"variant": "private_delta", "save_mode": "full_materialization", "remote_ack": True}))
        a3 = next(c for c in cells if c["parameters"]["ablation_id"] == "A3")
        receipt = {"variant": a3["variant"], "metadata_representation": a3["variant"], "cache_mode": a3["parameters"]["cache_state"]}
        self.assertEqual(mechanism_problems(a3, receipt), [])
        receipt["cache_mode"] = "opposite"
        self.assertTrue(mechanism_problems(a3, receipt))
        a4 = next(c for c in cells if c["variant"] == "selected_layout_range")
        receipt = {"variant": a4["variant"], "layout_policy": "selected", "range_mode": "range", "object_digest": HASH,
                   "B": 4096, "q": 1, "ec_data": 4, "ec_parity": 2}
        self.assertEqual(mechanism_problems(a4, receipt), [])
        scattered = next(c for c in cells if c["variant"] == "selected_layout_range" and c["parameters"]["read_pattern"] == "scattered")
        self.assertEqual(mechanism_problems(scattered, receipt), [])
        receipt["layout_policy"] = "fixed"
        self.assertTrue(mechanism_problems(a4, receipt))
        a5 = next(c for c in cells if c["variant"] == "reopen_remote_state")
        receipt = {"variant": a5["variant"], "transfer_mode": "reopen_remote_state", "ownership_protocol": {
            "protocol_id": "unit-test", "single_writer": True, "stale_publication_rejected": True}}
        self.assertEqual(mechanism_problems(a5, receipt), [])
        receipt["transfer_mode"] = "copy_then_resume"
        self.assertTrue(mechanism_problems(a5, receipt))
        a6 = next(c for c in cells if c["parameters"]["ablation_id"] == "A6" and c["variant"] == "full")
        self.assertTrue(mechanism_problems(a6, {"variant": "full", "metadata_representation": "equivalent_plain", "save_mode": "private_delta", "layout_policy": "selected"}))

    def test_plain_migration_adapter_cannot_claim_dirty_or_fault_support(self):
        for campaign in ("rq4-dirty-migration", "rq4-fault-qualification"):
            cell = next(c for c in make_cells(campaign, 1) if c["baseline_id"] == "B2")
            adapter = FakeAdapter(self.root, cell)
            result = preflight(cell, adapter)
            self.assertEqual(result["status"], "rejected")
            self.assertIn("scenario_capabilities:missing_or_invalid", result["problems"])

    def test_dirty_checkpoint_checks_both_no_save_write_and_pause_hooks(self):
        cell = next(c for c in make_cells("rq4-dirty-migration", 1) if c["baseline_id"] == "B2")
        adapter = FakeAdapter(self.root, cell)
        section = {"supported": True, "write_without_checkpoint": True, "save_after_pause": True,
                   "hooks": sorted(DIRTY_HOOKS)}
        adapter.scenario_capabilities = {"schema_version": 1, "dirty_checkpoint": section}
        result = preflight(cell, adapter)
        self.assertEqual(result["status"], "ok", result)
        self.assertFalse(result["performance_claims_allowed"])
        section["write_without_checkpoint"] = False
        self.assertEqual(preflight(cell, adapter)["status"], "rejected")
        section["write_without_checkpoint"] = True
        section["hooks"].remove("save_after_pause")
        self.assertEqual(preflight(cell, adapter)["status"], "rejected")

    def test_fault_discovery_must_support_exact_case_and_every_requested_hook(self):
        cells = make_cells("rq4-fault-qualification", 1)
        for case in ("before_ack", "after_ack", "stale_writer", "half_publish"):
            cell = next(c for c in cells if c["baseline_id"] == "B2" and c["parameters"]["fault_case"] == case)
            adapter = FakeAdapter(self.root, cell)
            section = {"supported": True, "hooks": list(cell["parameters"]["required_hooks"])}
            adapter.scenario_capabilities = {"schema_version": 1, "faults": {"wrong_case": section}}
            self.assertEqual(preflight(cell, adapter)["status"], "rejected")
            adapter.scenario_capabilities["faults"] = {case: section}
            result = preflight(cell, adapter)
            self.assertEqual(result["status"], "ok", result)
            self.assertFalse(result["performance_claims_allowed"])
            cell["parameters"].update(cache_protocol_id="client-warm-backend-observed-v1", cache_state="warm")
            n = cell["parameters"].get("consumers", cell["parameters"].get("workspaces", 1))
            adapter.response = {"status": "ok", "data": {"measurement_evidence": self.evidence,
                "measurements": {"requested": n, "successful": n, "elapsed_s": 1, "deadline_s": 120}}}
            trial = distributed(cell, adapter, self.root / case)
            self.assertNotIn("planned_population_mismatch", trial["missing_evidence"])
            self.assertFalse(trial["measurement_valid"])
            section["hooks"].pop()
            self.assertEqual(preflight(cell, adapter)["status"], "rejected")


class ArtifactPathAndCellBytesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_only_runner_provided_artifact_root_is_allowed(self):
        script = self.root / "fake.py"
        script.write_text("print('{\"status\":\"ok\",\"scope\":\"real\",\"evidence\":{\"test\":true}}')")
        config = {"schema_version": 1, "baseline_id": "B1", "execution_scope": "real", "state": "configured",
                  "artifact_root": str(self.root), "operations": {"run_scenario": {"argv": [sys.executable, str(script)]}}}
        workspace = self.root / "workspaces"
        output = self.root / "output"
        adapter = Adapter(config, workspace_root=workspace, log_dir=output / "logs", artifact_root=output)
        self.assertEqual(adapter.call("run_scenario", {"artifact_output_dir": str(output / "artifacts")})["status"], "ok")
        for path in (self.root / "elsewhere", workspace / "artifacts", output / "logs"):
            self.assertEqual(adapter.call("run_scenario", {"artifact_output_dir": str(path)})["status"], "rejected")
        no_runner_root = Adapter(config, workspace_root=workspace, log_dir=output / "logs")
        self.assertEqual(no_runner_root.call("run_scenario", {"artifact_output_dir": str(output / "artifacts")})["status"], "rejected")

    def test_saved_cell_bytes_match_hashed_source(self):
        cell = make_cells("smoke", 1)[0]
        cell_path = self.root / "cell-input.json"
        original = json.dumps(cell, indent=4).replace("\n", "\r\n").encode()
        cell_path.write_bytes(original)
        config = {"schema_version": 1, "baseline_id": "LOCAL_REFERENCE", "execution_scope": "local_reference",
                  "state": "not_configured", "operations": {}}
        config_path = self.root / "adapter.json"
        write_json(config_path, config)
        output = self.root / "run"
        result = run(cell_path, config_path, output)
        self.assertEqual((output / "cell.json").read_bytes(), original)
        self.assertEqual(result["cell_sha256"], digest(output / "cell.json"))
        self.assertFalse(result["performance_claims_allowed"])


if __name__ == "__main__":
    unittest.main()
