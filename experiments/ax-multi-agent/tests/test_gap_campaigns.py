"""Executable plan contracts for the identified workload gaps; no cloud/model calls."""

import collections
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.generate_campaign import CAMPAIGNS, make_cells, write_campaign


class GapCampaignTests(unittest.TestCase):
    def test_dirty_migration_is_small_paired_and_counts_pause_save(self):
        cells = make_cells("rq4-dirty-migration", repetitions=1)
        self.assertEqual(len(cells), 18)
        self.assertEqual(len(make_cells("rq4-dirty-migration", repetitions=3)), 54)
        self.assertEqual({c["baseline_id"] for c in cells}, {"B0", "B2", "B4"})
        groups = collections.defaultdict(list)
        for cell in cells:
            groups[cell["pair_id"]].append(cell)
            p = cell["parameters"]
            self.assertEqual((p["workspaces"], p["neighbors"], p["concurrency"]), (16, 12, 4))
            self.assertEqual(p["selected"], [0, 4, 8, 12])
            self.assertEqual(p["timing_origin"], "pause_requested")
            self.assertTrue(p["save_in_pause_interval"])
            self.assertFalse(p["pre_pause_checkpoint_allowed"])
            self.assertTrue(p["requires_dirty_checkpoint_adapter"])
            self.assertTrue(p["requires_no_checkpoint_write_hook"])
            self.assertEqual(p["dirty_bytes_semantics"], "logical_bytes_written_since_last_confirmed_checkpoint")
            self.assertIn("post_pause_upload_bytes", p["required_receipt_fields"])
            self.assertEqual(p["expected_restored_round"], 11 if p["dirty_modified_bytes"] else 10)
            self.assertEqual(p["dirty_session_bytes"], 65536 if p["dirty_modified_bytes"] else 0)
            self.assertEqual(cell["required_operations"], ["run_scenario"])
            self.assertFalse(p["live_model_calls_allowed"])
        self.assertEqual({c["parameters"]["dirty_modified_bytes"] for c in cells}, {0, 2**20, 128 * 2**20})
        self.assertEqual(len(groups), 3)
        for group in groups.values():
            self.assertEqual(len(group), 6)
            self.assertEqual({c["variant"] for c in group}, {"migration", "no_migration"})
            self.assertTrue(all(c["parameters"] == group[0]["parameters"] for c in group))

    def test_sharing_boundary_retains_low_reuse_and_fresh_reference(self):
        cells = make_cells("rq1-sharing-boundary", repetitions=3)
        self.assertEqual(len(cells), 24)
        self.assertEqual({c["parameters"]["base_version_count"] for c in cells}, {1, 64})
        self.assertEqual({c["baseline_id"] for c in cells}, {"B0", "B1", "B2", "B3"})
        for cell in cells:
            p = cell["parameters"]
            self.assertEqual((p["workspaces"], p["client_nodes"]), (256, 4))
            self.assertEqual(p["base_version_assignment"], "workspace_id % base_version_count + 1")
            self.assertEqual(p["cache_state"], "cold")
            self.assertTrue(p["same_paths_sizes_permissions_and_operations"])
            self.assertEqual(p["reference_policy"], "fresh_paired_reference_not_reused_from_capacity_campaign")
            self.assertFalse(p["live_model_calls_allowed"])
        capacity_pairs = {c["pair_id"] for c in make_cells("rq1-capacity")}
        self.assertFalse(capacity_pairs & {c["pair_id"] for c in cells})

    def test_fault_qualification_defaults_to_one_and_has_fixed_oracles(self):
        cells = make_cells("rq4-fault-qualification")
        self.assertEqual(len(cells), 19)
        self.assertEqual({c["replicate"] for c in cells}, {1})
        self.assertEqual(len(make_cells("rq4-fault-qualification", repetitions=3)), 57)
        counts = collections.Counter(c["parameters"]["fault_case"] for c in cells)
        self.assertEqual(counts, {"before_ack": 5, "after_ack": 5, "stale_writer": 5, "half_publish": 4})
        for cell in cells:
            p = cell["parameters"]
            self.assertTrue(p["requires_fault_adapter"])
            self.assertFalse(p["performance_claims_allowed"])
            self.assertFalse(p["live_model_calls_allowed"])
            self.assertEqual(cell["status"], "planned")
            self.assertTrue(p["trigger_event"])
            self.assertGreaterEqual(len(p["required_hooks"]), 5)
            self.assertTrue(p["oracle"])
            if p["fault_case"] == "before_ack":
                self.assertTrue(p["requires_frozen_confirmed_checkpoint"])
                self.assertEqual(p["oracle"]["missing_history_support"], "unsupported")
                self.assertEqual(p["oracle"]["recover_checkpoint"], "P0")
            elif p["fault_case"] == "after_ack":
                self.assertFalse(p["oracle"]["confirmed_state_loss_allowed"])
                self.assertEqual(p["oracle"]["recover_checkpoint"], "P1")
            elif p["fault_case"] == "stale_writer":
                self.assertTrue(p["fault_must_not_preblock_data_path"])
                self.assertFalse(p["oracle"]["stale_current_commit_allowed"])
                self.assertFalse(p["oracle"]["sentinel_in_current_target_allowed"])
            else:
                self.assertEqual((p["halt_after_saved_shards"], p["source_shard_count"]), (32, 64))
                self.assertFalse(p["oracle"]["partial_V2_binding_allowed"])
                self.assertNotEqual(cell["baseline_id"], "B4")
                self.assertEqual((cell["experiment_id"], cell["workload_id"]), ("T3", "W3"))

    def test_distributed_cells_freeze_cache_and_replay_state_boundaries(self):
        live_input = Path(__file__).resolve().parents[1] / "inputs/live-tasks.json"
        self.assertEqual(hashlib.sha256(live_input.read_bytes()).hexdigest(),
                         "3d5dbb4dec95606d1ac907cf87859d3db2fb3711193252bae0afc80e1f1543cb")
        for campaign in CAMPAIGNS:
            for cell in make_cells(campaign, repetitions=1):
                p = cell["parameters"]
                if p["execution_mode"] == "distributed":
                    self.assertIn(p["cache_state"], ("cold", "warm"))
                    self.assertEqual(p["cache_protocol_id"],
                                     f"client-{p['cache_state']}-backend-observed-v1")
                if p.get("workload_kind") == "tool_replay":
                    self.assertEqual(p["state_execution"], "excluded")
                    self.assertIsNone(p["trace_manifest_sha256"])
                    self.assertIsNone(p["trace_manifest"])
                    self.assertFalse(p["live_model_calls_allowed"])
                    self.assertEqual(p["tasks"], 16)
        warm = [c for c in make_cells("ablations", repetitions=1)
                if c["parameters"].get("cache_state") == "warm"]
        self.assertEqual(len(warm), 4)

    def test_a4_scattered_is_a_paired_factor_not_a_replaced_negative(self):
        cells = make_cells("ablations", repetitions=3)
        self.assertEqual(len(cells), 102)
        a4 = [c for c in cells if c["parameters"]["ablation_id"] == "A4"]
        self.assertEqual(len(a4), 36)
        self.assertEqual(collections.Counter(c["parameters"]["read_pattern"] for c in a4),
                         {"shared": 12, "scattered": 12, "sequential": 12})
        self.assertTrue(all(c["parameters"]["consumers"] == 16 for c in a4))

    def test_campaign_index_hashes_exact_bytes_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="brewfs-gap-plan-") as temporary:
            directory = Path(temporary) / "new-plan"
            index = write_campaign("rq4-fault-qualification", directory)
            self.assertEqual(index["cells"], 19)
            self.assertEqual(index["repetitions"], 1)
            self.assertFalse(index["execution_enabled"])
            self.assertEqual(set(index["cell_sha256"]), set(index["order"]))
            self.assertEqual(index, json.loads((directory / "index.json").read_bytes()))
            for cell_id in index["order"]:
                raw = (directory / f"{cell_id}.json").read_bytes()
                self.assertTrue(raw.endswith(b"\n"))
                self.assertNotIn(b"\r\n", raw)
                self.assertEqual(index["cell_sha256"][cell_id], hashlib.sha256(raw).hexdigest())
            chosen = directory / f"{index['order'][0]}.json"
            original = chosen.read_bytes()
            chosen.write_bytes(original + b" ")
            self.assertNotEqual(index["cell_sha256"][index["order"][0]], hashlib.sha256(chosen.read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                write_campaign("rq4-fault-qualification", directory)
            self.assertEqual(chosen.read_bytes(), original + b" ")


if __name__ == "__main__":
    unittest.main()
