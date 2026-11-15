"""Check experimental controls, workload coverage and the execution scope."""

import itertools
import json
from pathlib import Path
import unittest

from bench.cluster import plan

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class DesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = read("configs/cluster.json")
        cls.built = plan.build_plan(cls.design)
        cls.conditions = {c["id"]: c for c in cls.built["conditions"]}

    def test_every_declared_pair_changes_only_its_named_parameter(self):
        pairs = read("configs/comparisons.json")["pairs"]
        self.assertEqual(len({p["id"] for p in pairs}), len(pairs))
        for pair in pairs:
            with self.subTest(pair=pair["id"]):
                control = self.conditions[pair["control"]]
                variant = self.conditions[pair["variant"]]
                self.assertEqual(control["group"], variant["group"])
                self.assertTrue(pair["systems"])
                self.assertLessEqual(set(pair["systems"]), set(control["systems"]) & set(variant["systems"]))
                left, right = control["parameters"], variant["parameters"]
                changes = [k for k in sorted(left.keys() | right.keys())
                           if k != "requires" and left.get(k) != right.get(k)]
                self.assertEqual(changes, pair["changed_parameters"])
                self.assertEqual(len(changes), 1)

    def test_c1_covers_read_write_sizes_and_matching_juicefs_work(self):
        cases = list(self.conditions.values())
        brew = [c["parameters"] for c in cases if c["family"] == "block-random"]
        expected = set(itertools.product([64, 256, 1024, 4096], ["read", "write"], [4, 64, 256, 1024]))
        actual = {(p["block_kib"], p["operation"], p["request_kib"]) for p in brew}
        self.assertEqual(actual, expected)
        references = [c for c in cases if c["family"] == "reference-random"]
        workload_fields = ("operation", "request_kib", "jobs", "pattern", "io_bytes",
                           "working_set_gib", "direct", "sync_interval_mib", "prefetch", "writes")
        for native in brew:
            matches = [c for c in references if all(c["parameters"][k] == native[k] for k in workload_fields)]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["systems"], ["juicefs", "juicefs-overlay"])

    def test_c3_metadata_factorial_preserves_payload_work(self):
        names = ("C3-load-02", "C3-metadata-cache-01", "C3-metadata-representation-01", "C3-kv-no-metadata-cache-01")
        values = [self.conditions[name]["parameters"] for name in names]
        self.assertEqual({(v["metadata_representation"], v["metadata_cache_mib"]) for v in values},
                         set(itertools.product(["packed", "kv"], [0, 128])))
        fields = ("files", "file_bytes", "agents", "epochs", "packed_cache", "retained_payload_mib", "cache_sharing")
        self.assertEqual(len({tuple(v[k] for k in fields) for v in values}), 1)

    def test_fio_profiles_define_divisible_total_work_without_content_digests(self):
        spec = read("configs/fio-profiles.json")
        self.assertEqual(spec["defaults"]["verify"], "none")
        jobs = spec["defaults"]["jobs"]
        used = {c["parameters"]["profile"] for c in self.conditions.values() if c["family"] == "fio"}
        self.assertEqual(used, set(spec["profiles"]))
        for profile in spec["profiles"].values():
            amount = profile.get("total_bytes", profile.get("total_operations"))
            self.assertEqual(amount % jobs, 0)
            if "total_bytes" in profile:
                self.assertEqual(amount % (jobs * profile["request_kib"] * 1024), 0)

    def test_c4_merge_factorial_and_additional_base_mount(self):
        names = ("C4-merge-size-02", "C4-merge-tree-scan-01", "C4-merge-materialize-01", "C4-merge-tree-materialize-01")
        values = [self.conditions[name]["parameters"] for name in names]
        self.assertEqual({(v["merge_discovery"], v["merge_data"]) for v in values},
                         set(itertools.product(["delta", "full-tree"], ["reference", "materialize"])))
        self.assertEqual({(v["files"], v["changes"], v["children"], v["target_changes"]) for v in values},
                         {(100000, 100, 1, 0)})
        fan_in = {c["parameters"]["children"]: c for c in self.conditions.values()
                  if c["group"] == "C4" and c["family"] == "merge-fan-in"}
        self.assertEqual(fan_in[16]["peak_mounts"], 18)
        self.assertEqual(fan_in[64]["peak_mounts"], 66)

    def test_campaign_excludes_c4_and_optional_capacity(self):
        campaign = read("configs/campaign.json")
        self.assertEqual(campaign["groups"], ["C1", "C2", "C3"])
        self.assertEqual(campaign["deferred_groups"], ["C4"])
        self.assertFalse(campaign["include_optional"])
        self.assertEqual(self.design["execution"]["manual_groups"], campaign["groups"])
        selected = [c for c in self.conditions.values() if c["group"] in campaign["groups"]]
        self.assertEqual(len(selected), 107)
        self.assertEqual(sum(len(c["systems"]) * c["repetitions"] for c in selected), 507)
        self.assertLessEqual(max(c["peak_mounts"] for c in selected), 128)

    def test_shared_cache_keeps_host_resource_and_concurrency_budgets(self):
        sharing = read("configs/cache-sharing.json")
        self.assertEqual(sharing["allocation"], "sum-of-participating-private-read-cache-budgets")
        self.assertEqual(sharing["concurrency"], "same-as-private-control-by-host")
        self.assertEqual(sharing["owner_cpu_memory"], "transferred-from-participating-client-budget")
        self.assertIn("dirty-data", sharing["private"])


if __name__ == "__main__":
    unittest.main()
