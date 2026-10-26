"""Campaign pairing and honest summary checks; all measurements here are synthetic."""

import collections
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.analyze import paired_effects, summarize
from harness.evidence_audit import CHECKS, ARTIFACTS, sha256
from harness.generate_campaign import make_cells


class CampaignTests(unittest.TestCase):
    def test_smoke_count_profiles_and_no_formal_execution_claims(self):
        cells = make_cells("smoke", repetitions=3)
        self.assertEqual(len(cells), 12)
        self.assertEqual({c["experiment_id"] for c in cells}, {"T1", "T2", "T3", "T6"})
        self.assertEqual({c["profile"] for c in cells}, {"smoke"})
        self.assertEqual({c["baseline_id"] for c in cells}, {"LOCAL_REFERENCE"})
        self.assertTrue(all(c["status"] == "planned" for c in cells))
        self.assertEqual(len({c["cell_id"] for c in cells}), len(cells))
        self.assertEqual([c["execution_order"] for c in cells], list(range(1, 13)))

    def test_ablation_pairs_have_identical_input_parameters_and_fixed_replicates(self):
        cells = make_cells("ablations", repetitions=3)
        # 4 A1 + 4 A2 + 8 A3 + 12 A4 + 2 A5 + 4 A6 per repetition.
        self.assertEqual(len(cells), 102)
        expected_variants = {
            "A1": {"shared_packed", "shared_plain", "private_packed", "private_plain"},
            "A2": {"private_delta", "full_materialization"},
            "A3": {"packed", "equivalent_plain"},
            "A4": {"fixed_layout_range", "selected_layout_range", "fixed_layout_whole_get", "selected_layout_whole_get"},
            "A5": {"reopen_remote_state", "copy_then_resume"},
            "A6": {"full", "plain_metadata", "full_materialization", "fixed_layout"},
        }
        paired = collections.defaultdict(list)
        for cell in cells:
            paired[cell["pair_id"]].append(cell)
            self.assertEqual(cell["profile"], "full")
            self.assertEqual(cell["baseline_id"], "B0")
            self.assertEqual(cell["status"], "planned")
            self.assertEqual(cell["parameters"]["execution_mode"], "distributed")
        for group in paired.values():
            ablation = group[0]["parameters"]["ablation_id"]
            self.assertEqual({c["variant"] for c in group}, expected_variants[ablation])
            self.assertEqual(len(group), len(expected_variants[ablation]))
            self.assertTrue(all(c["parameters"] == group[0]["parameters"] for c in group))
            self.assertEqual(len({c["replicate"] for c in group}), 1)
        self.assertEqual({c["replicate"] for c in cells}, {1, 2, 3})

    def test_generation_is_reproducible_without_silently_dropping_brewfs(self):
        first = make_cells("rq3-live", repetitions=2, seed=20261023)
        second = make_cells("rq3-live", repetitions=2, seed=20261023)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3 * 4 * 2)
        self.assertEqual({c["baseline_id"] for c in first}, {"B0", "B1", "B2", "B3"})
        self.assertTrue(all(c["parameters"]["tasks"] == 16 for c in first))
        self.assertEqual({c["parameters"]["concurrency"] for c in first}, {1, 4, 16})
        self.assertTrue(all(c["readiness"] == "deferred_brewfs" for c in first if c["baseline_id"] == "B0"))
        changed = make_cells("rq3-live", repetitions=2, seed=20261024)
        self.assertNotEqual([c["cell_id"] for c in first], [c["cell_id"] for c in changed])

    def test_invalid_campaign_or_repetition_rejected(self):
        with self.assertRaises(ValueError):
            make_cells("smoke", repetitions=0)
        with self.assertRaises(ValueError):
            make_cells("no-such-campaign")

    def test_replay_and_a6_preserve_inputs_and_do_not_enable_model_fallback(self):
        replay = make_cells("rq3-replay", repetitions=3)
        self.assertEqual(len(replay), 36)
        self.assertEqual({c["parameters"]["concurrency"] for c in replay}, {1, 4, 16})
        a6 = [c for c in make_cells("ablations", repetitions=3)
              if c["parameters"]["ablation_id"] == "A6"]
        self.assertEqual(sum(c["parameters"]["tasks"] for c in a6), 192)
        for cell in replay + a6:
            p = cell["parameters"]
            self.assertEqual(p["tasks"], 16)
            self.assertEqual(p["workload_kind"], "tool_replay")
            self.assertIs(p["live_model_calls_allowed"], False)
            self.assertTrue(p["requires_qualified_trace_manifest"])
            self.assertIsNone(p["trace_manifest"])
            self.assertEqual(p["divergence_policy"], "stop_task_no_model_fallback")
            self.assertEqual(cell["required_operations"], ["run_scenario"])


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brewfs-analysis-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.serial = 0

    def recorded_result(self, cell_id="test-cell", pair_id="pair-1", *, requested=16,
                        successful=16, elapsed=10.0, status="ok", scope="real",
                        profile="full", performance=True, baseline="B0", variant="default", planned_requested=16, audited=True):
        self.serial += 1
        folder = self.root / f"run-{self.serial}"
        folder.mkdir()
        cell = {"cell_id": cell_id, "pair_id": pair_id, "experiment_id": "T1",
                "baseline_id": baseline, "variant": variant, "profile": profile,
                "parameters": {"workspaces": planned_requested}}
        result = {"status": status, "scope": scope, "cell_id": cell_id, "baseline_id": baseline,
                  "performance_claims_allowed": performance,
                  "measurements": {"requested": requested, "successful": successful,
                                   "elapsed_s": elapsed, "physical_bytes": None,
                                   "backend_requests": None}}
        (folder / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
        result["cell_sha256"] = sha256(folder / "cell.json")
        (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
        if audited:
            self.write_review(folder)
        return folder / "result.json"

    def write_review(self, folder):
        # Synthetic data in a temporary test directory, never a real experiment.
        cell = json.loads((folder / "cell.json").read_text())
        result = json.loads((folder / "result.json").read_text())
        n = cell["parameters"]["workspaces"]
        successful = result["measurements"]["successful"]
        successful = successful if type(successful) is int else 0
        artifacts = {}
        for key in ARTIFACTS:
            file = folder / (key + ".json")
            data = ([{"item_id": i, "status": "ok" if i < successful else "error"} for i in range(n)]
                    if key == "items" else {"synthetic_unit_test_only": True})
            file.write_text(json.dumps(data), encoding="utf-8")
            artifacts[key] = {"path": file.name, "sha256": sha256(file)}
        review = {"schema_version": 1, "status": "accepted", "reviewed_by": "synthetic-unit-test",
                  "cell_sha256": sha256(folder / "cell.json"), "result_sha256": sha256(folder / "result.json"),
                  "checks": {k: True for k in CHECKS}, "artifacts": artifacts}
        (folder / "performance-audit.json").write_text(json.dumps(review), encoding="utf-8")

    def checked_summary(self, paths, extra_cells=()):
        campaign = self.root / ("campaign-" + str(len(list(self.root.glob("campaign-*")))))
        campaign.mkdir()
        order, hashes = [], {}
        for path in list(paths) + list(extra_cells):
            cell_path = Path(path).parent / "cell.json"
            cell = json.loads(cell_path.read_text())
            if cell["cell_id"] in hashes:
                continue
            order.append(cell["cell_id"])
            target = campaign / (cell["cell_id"] + ".json")
            target.write_bytes(cell_path.read_bytes())
            hashes[cell["cell_id"]] = sha256(target)
        index = campaign / "index.json"
        index.write_text(json.dumps({"cells": len(order), "order": order, "cell_sha256": hashes}), encoding="utf-8")
        return summarize(paths, index)

    def test_failure_denominator_and_missing_counters_are_preserved(self):
        path = self.recorded_result(requested=16, successful=12, status="error")
        result = self.checked_summary([path])
        row = result["rows"][0]
        self.assertEqual(row["completion_rate"], 0.75)
        self.assertEqual(row["requested"], 16)
        self.assertFalse(row["all_completed"])
        self.assertIsNone(row["physical_bytes"])
        self.assertIsNone(row["backend_requests"])
        self.assertEqual(result["groups"][0]["failed_or_rejected_cells"], 1)

    def test_absent_measurements_are_not_converted_to_zero(self):
        path = self.recorded_result(requested=None, successful=None, elapsed=None,
                                    status="rejected", performance=False)
        row = self.checked_summary([path])["rows"][0]
        self.assertIsNone(row["completion_rate"])
        self.assertIsNone(row["elapsed_s"])
        self.assertFalse(row["eligible"])
        self.assertFalse(row["all_completed"])

    def test_smoke_or_local_results_are_never_eligible(self):
        a = self.recorded_result("smoke", profile="smoke", performance=True)
        b = self.recorded_result("local", scope="local_reference", performance=True)
        self.assertTrue(all(not r["eligible"] for r in self.checked_summary([a, b])["rows"]))

    def test_duplicate_cell_ids_rejected_instead_of_pooling_retry(self):
        a = self.recorded_result("same-id")
        b = self.recorded_result("same-id")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.checked_summary([a, b])

    def test_invalid_counts_and_elapsed_time_rejected(self):
        for requested, successful in ((0, 0), (16, 17), (16, -1), (16.0, 1), (True, True),
                                      (16, False), (None, 5)):
            with self.subTest(requested=requested, successful=successful):
                path = self.recorded_result(requested=requested, successful=successful)
                with self.assertRaises(ValueError):
                    self.checked_summary([path])
        for elapsed in (-1, float("inf"), float("nan"), "ten", True):
            with self.subTest(elapsed=elapsed):
                path = self.recorded_result(elapsed=elapsed)
                with self.assertRaises(ValueError):
                    self.checked_summary([path])

    def test_paired_effects_report_failure_missing_and_smoke_exclusions(self):
        paths = []
        for pair, before, after, overrides in (
            ("good", 20, 15, {}),
            ("failed", 20, 5, {"successful": 8, "status": "error"}),
            ("smoke", 20, 5, {"profile": "smoke"}),
            ("missing-time", 20, None, {}),
        ):
            paths.append(self.recorded_result(pair + "-ref", pair, elapsed=before, baseline="B2"))
            paths.append(self.recorded_result(pair + "-treatment", pair, elapsed=after, **overrides))
        paths.append(self.recorded_result("missing-pair-ref", "missing-pair", baseline="B2"))
        effects = paired_effects(self.checked_summary(paths)["rows"], "B2:default", "B0:default")
        self.assertEqual(effects["complete_successful_pairs"], 1)
        self.assertEqual(effects["missing_pairs"], 1)
        self.assertEqual(effects["excluded_failure_or_unqualified_pairs"], 3)
        self.assertEqual(effects["mean_time_reduction_fraction"], 0.25)
        self.assertEqual(effects["pairs"][0]["seconds_saved"], 5)

    def test_distinct_cells_cannot_overwrite_same_pair_and_label(self):
        a = self.recorded_result("attempt-a", "same-pair", baseline="B2")
        b = self.recorded_result("attempt-b", "same-pair", baseline="B2")
        c = self.recorded_result("treatment", "same-pair", baseline="B0")
        rows = self.checked_summary([a, b, c])["rows"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            paired_effects(rows, "B2:default", "B0:default")

    def test_unrun_cells_stay_in_the_plan_and_are_not_invented_measurements(self):
        observed = self.recorded_result("finished", baseline="B2")
        unrun = self.recorded_result("not-launched", baseline="B0")
        result = self.checked_summary([observed], extra_cells=[unrun])
        self.assertEqual(result["coverage"], {"planned_cells": 2, "received_cells": 1, "not_run_cells": 1})
        row = next(r for r in result["rows"] if r["cell_id"] == "not-launched")
        self.assertEqual(row["status"], "not_run")
        self.assertEqual(row["planned_requested"], 16)
        self.assertIsNone(row["successful"])
        self.assertFalse(row["eligible"])

    def test_cli_can_report_a_campaign_that_has_not_started(self):
        from harness.generate_campaign import write_campaign
        campaign = self.root / "unstarted-plan"
        write_campaign("rq4-fault-qualification", campaign)
        output = self.root / "unstarted-summary"
        process = subprocess.run([sys.executable, "-m", "harness.analyze", "--campaign",
            str(campaign / "index.json"), "--output", str(output)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        summary = json.loads((output / "summary.json").read_text())
        self.assertEqual(summary["coverage"], {"planned_cells": 19, "received_cells": 0, "not_run_cells": 19})
        self.assertTrue(all(r["status"] == "not_run" and r["planned_requested"] > 0 for r in summary["rows"]))

    def test_boolean_alone_no_longer_grants_performance_eligibility(self):
        path = self.recorded_result(audited=False)
        row = self.checked_summary([path])["rows"][0]
        self.assertFalse(row["eligible"])
        self.assertIn("missing_performance_audit", row["review_problems"])
        reviewed = self.recorded_result("reviewed")
        self.assertFalse(summarize([reviewed])["rows"][0]["eligible"])

    def test_result_identity_population_and_hash_must_match(self):
        for key, bad in (("cell_id", "another-cell"), ("baseline_id", "B3"), ("cell_sha256", "0" * 64)):
            path = self.recorded_result(key)
            result = json.loads(path.read_text())
            result[key] = bad
            path.write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "mismatch"):
                self.checked_summary([path])
        path = self.recorded_result("wrong-population", requested=1, successful=1)
        with self.assertRaisesRegex(ValueError, "population"):
            self.checked_summary([path])

    def test_changed_raw_file_revokes_review(self):
        path = self.recorded_result()
        (path.parent / "events.json").write_text("changed")
        row = self.checked_summary([path])["rows"][0]
        self.assertFalse(row["eligible"])
        self.assertIn("artifact_hash:events", row["review_problems"])

    def test_review_requires_exact_item_ids_not_only_total(self):
        path = self.recorded_result()
        items = path.parent / "items.json"
        records = json.loads(items.read_text())
        records[-1]["item_id"] = records[0]["item_id"]
        items.write_text(json.dumps(records))
        audit = path.parent / "performance-audit.json"
        review = json.loads(audit.read_text())
        review["artifacts"]["items"]["sha256"] = sha256(items)
        audit.write_text(json.dumps(review))
        row = self.checked_summary([path])["rows"][0]
        self.assertFalse(row["eligible"])
        self.assertIn("item_identity_coverage", row["review_problems"])

    def test_different_scales_are_not_pooled_into_one_speedup(self):
        paths = []
        for size, after in ((16, 15), (1024, 10)):
            for baseline, elapsed in (("B2", 20), ("B0", after)):
                path = self.recorded_result(f"{size}-{baseline}", str(size), baseline=baseline, elapsed=elapsed,
                                            planned_requested=size, requested=size, successful=size)
                paths.append(path)
        rows = self.checked_summary(paths)["rows"]
        result = paired_effects(rows, "B2:default", "B0:default")
        self.assertIsNone(result["mean_time_reduction_fraction"])
        self.assertEqual(len(result["by_condition"]), 2)
        rows[1]["condition"] = "wrong-condition"
        with self.assertRaisesRegex(ValueError, "different workload"):
            paired_effects(rows, "B2:default", "B0:default")


if __name__ == "__main__":
    unittest.main()
