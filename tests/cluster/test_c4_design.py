"""Validate the C4 plan against the available merge interfaces."""

import json
from pathlib import Path
import unittest

from bench.cluster import plan

ROOT = Path(__file__).resolve().parents[2]


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class C4DesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = read("configs/cluster.json")
        cls.plan = plan.build_plan(cls.design, "C4")
        cls.adapters = read("experiments/c4-fork/adapters.json")

    def family(self, name):
        return [c for c in self.plan["conditions"] if c["family"] == name]

    def test_native_and_posix_jobs_use_their_actual_strategies(self):
        for job in self.plan["jobs"]:
            if "merge_adapter" not in job["parameters"]:
                continue
            p = job["parameters"]
            if job["system"] in ("juicefs", "juicefs-overlay"):
                self.assertEqual(p["merge_adapter"], "posix-three-way")
                self.assertEqual(p["merge_discovery"], "full-tree")
                self.assertEqual(p["merge_data"], "materialize")
                self.assertIn("posix-three-way-merge", p["requires"])
                self.assertFalse(any(v.startswith("native-") for v in p["requires"]))
                self.assertEqual(p["parent_version"], "immutable-source-tree")
            elif "merge-posix-brewfs" in job["condition"]:
                self.assertEqual(p["merge_adapter"], "posix-three-way")
            else:
                self.assertEqual(p["merge_adapter"], "native-offline")

    def test_parent_size_sweep_keeps_child_and_target_changes_fixed(self):
        cases = self.family("merge-size")
        self.assertEqual({c["parameters"]["files"] for c in cases}, {10000, 100000})
        for c in cases:
            self.assertEqual(c["parameters"]["changes"], 100)
            self.assertEqual(c["parameters"]["target_changes"], 0)

    def test_target_delta_and_fan_in_cover_accumulating_private_state(self):
        cases = self.family("merge-parent-progress") + self.family("merge-target-delta")
        self.assertEqual({c["parameters"]["target_changes"] for c in cases}, {100, 1000, 10000})
        self.assertEqual({c["parameters"]["changes"] for c in cases}, {100})
        fan_in = self.family("merge-fan-in")
        self.assertEqual({c["parameters"]["children"] for c in fan_in}, {4, 16, 64})
        for c in fan_in:
            self.assertEqual(c["parameters"]["fan_in"], "serial-child-id")
            self.assertEqual(c["parameters"]["child_paths"], "disjoint")

    def test_offline_same_bucket_scope_and_unavailable_switches_are_explicit(self):
        needed = self.adapters["brewfs"]["preconditions"]
        self.assertIn("child-and-target-unmounted", needed)
        self.assertIn("same-object-bucket", needed)
        self.assertIn("base-sealed-depth-1", needed)
        self.assertIn("writable-head-depth-2", needed)
        self.assertEqual(self.adapters["brewfs"]["discovery_switch"], "requires-implementation")
        for name in ("merge-tree-scan", "merge-materialize", "merge-tree-materialize"):
            self.assertIn("native-merge-strategy-switches", self.family(name)[0]["parameters"]["requires"])

    def test_c4_is_separate_and_all_mounts_fit_on_the_assigned_hosts(self):
        campaign = read("configs/campaign-c4.json")
        self.assertEqual(campaign["groups"], ["C4"])
        self.assertEqual(read("configs/campaign.json")["groups"], ["C1", "C2", "C3"])
        self.assertEqual(self.plan["counts"]["conditions"], 30)
        self.assertEqual(self.plan["counts"]["system_runs"], 228)
        self.assertEqual(max(c["peak_mounts"] for c in self.plan["conditions"]), 66)
        counts = {h["name"]: 0 for h in self.design["hosts"]}
        for site in plan.placement(self.design, 64):
            counts[site["host"]] += 1
        counts["A366"] += 2
        self.assertEqual(sum(counts.values()), campaign["maximum_simultaneous_mounts"])
        for host in self.design["hosts"]:
            self.assertLessEqual(counts[host["name"]], host["slots"])

    def test_whole_method_comparison_is_not_a_single_mechanism_ablation(self):
        control = self.family("merge-posix-brewfs")[0]
        self.assertEqual(control["kind"], "comparison")
        self.assertEqual(control["systems"], ["brewfs-native-overlay"])
        self.assertEqual(control["parameters"]["merge_discovery"], "full-tree")
        self.assertEqual(control["parameters"]["merge_data"], "materialize")


if __name__ == "__main__":
    unittest.main()
