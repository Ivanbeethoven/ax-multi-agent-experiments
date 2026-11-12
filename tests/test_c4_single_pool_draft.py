from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bench import suite_plan
from bench.experiment_io import digest, load
from bench.runtime.pilot_pools import cell_resources, resource_guard


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "experiments/unified-suite/pilot-aliyun/scripts/draft-c4-single-pool.py"
spec = spec_from_file_location("draft_c4_single_pool", SCRIPT)
module = module_from_spec(spec)
spec.loader.exec_module(module)
EVIDENCE = REPO / module.route.EVIDENCE


class TestC4SinglePoolDraft(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = load(EVIDENCE / "plan.json")
        cls.old_inventory = load(EVIDENCE / "inventory.json")
        cls.documents = module.draft(EVIDENCE / "plan.json", EVIDENCE / "inventory.json", "code-12")

    def test_new_paired_plan_allocates_two_distinct_executors(self):
        plan = suite_plan.check_plan(self.documents["plan.json"])
        preview = self.documents["draft.json"]
        config, = plan["config"]["clusters"]
        self.assertNotEqual(plan["plan_sha256"], self.old["plan_sha256"])
        self.assertEqual((config["agent_slots"], config["supported_suites"]), (2, ["C4"]))
        self.assertEqual(set(config["observer_ids"]), {n["id"] for n in preview["ssh_probe_preview"]["nodes"]})
        self.assertEqual({(c["suite"], c["case"], c["pool"]) for c in plan["cells"]},
                         {("C4", "handoff-1", module.POOL)})
        self.assertEqual({c["arm_id"] for c in plan["cells"]},
                         {"juicefs-stay", "juicefs-move", "native-workspace-stay", "native-workspace-move"})
        self.assertEqual(len(plan["blocks"]), 1)
        self.assertEqual(len(plan["assets"]), 2)
        self.assertFalse(plan["provenance_bound"])
        self.assertFalse(preview["reviewed"])
        self.assertFalse(preview["ssh_probe_preview"]["contacted"])
        nodes = {n["id"].removeprefix(module.POOL + "-"): n for n in preview["ssh_probe_preview"]["nodes"]}
        a, b = self.old_inventory["pools"]
        self.assertEqual(nodes["client"]["environment_id"], a["roles"]["client"]["instance_id"])
        self.assertEqual(nodes["target"]["environment_id"], b["roles"]["client"]["instance_id"])
        self.assertEqual(nodes["target"]["repo"], "/srv/ax-target/code-12")
        self.assertEqual({k: nodes[k]["slots"][0]["cgroup"] for k in ("client", "target")},
                         {"client": "/sys/fs/cgroup/ax-slot-1", "target": "/sys/fs/cgroup/ax-slot-1"})
        self.assertEqual(preview["endpoints"], {"metadata": "redis://10.20.2.162:6379",
                                                 "objects": "http://10.20.2.163:9000"})

    @unittest.skipUnless(sys.platform.startswith("linux"), "coordinator flock is Linux-specific")
    def test_new_lock_blocks_old_a_and_b_under_canonical_namespace(self):
        new = self.documents["plan.json"]
        cell = new["cells"][0]
        guard = self.documents["draft.json"]["guard"]
        self.assertEqual(len(guard["new_lock_files"]), 12)
        self.assertEqual(len(cell_resources(new, cell)), 11)
        self.assertEqual({k: len(v) for k, v in guard["old_physical_overlap"].items()}, {"pool-a": 9, "pool-b": 2})
        with tempfile.TemporaryDirectory() as folder, resource_guard(folder, cell, new):
            for pool_id, cell_id in guard["old_reference_cell_ids"].items():
                original = next(c for c in self.old["cells"] if c["id"] == cell_id)
                with self.subTest(pool=pool_id), self.assertRaisesRegex(ValueError, "owns physical resource"):
                    with resource_guard(folder, original, self.old):
                        self.fail("an old guard bypassed the new guard under the same directory")

    def test_cli_output_inventory_pin_matches_disk_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "new"
            cmd = [sys.executable, "-B", str(SCRIPT), "--code-directory", "code-12", "--out", str(out)]
            subprocess.run(cmd, check=True, capture_output=True)
            self.assertEqual({p.name for p in out.iterdir()}, {"config.json", "inventory.json", "plan.json", "draft.json"})
            config = load(out / "config.json")
            plan = suite_plan.check_plan(load(out / "plan.json"))
            self.assertEqual(config, plan["config"])
            self.assertEqual(config["clusters"][0]["inventory_sha256"], digest(out / "inventory.json"))
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
