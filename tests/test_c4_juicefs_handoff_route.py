import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest

from bench import suite_plan
from bench.experiment_io import load


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "experiments/unified-suite/pilot-aliyun/scripts/draft-c4-juicefs-handoff-route.py"
spec = spec_from_file_location("draft_c4_juicefs_handoff_route", SCRIPT)
module = module_from_spec(spec)
spec.loader.exec_module(module)
EVIDENCE = REPO / module.EVIDENCE


class TestJuiceFSHandoffRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.draft = module.map_route(EVIDENCE / "plan.json", EVIDENCE / "inventory.json")

    def test_stay_move_use_same_storage_but_only_move_needs_second_host(self):
        stay, move = (self.draft["planned_routes"][f"juicefs-{arm}"] for arm in ("stay", "move"))
        self.assertEqual(stay["source_role"], stay["target_role"])
        self.assertEqual(move["source_role"], stay["source_role"])
        self.assertEqual(move["target_role"], "pool-b-client")
        self.assertEqual(set(move["required_runtime_roles"]) - set(stay["required_runtime_roles"]), {"pool-b-client"})
        self.assertEqual(set(stay["required_runtime_roles"]), {"pool-a-client", "pool-a-metadata", "pool-a-objects"})
        self.assertEqual((len(stay["runtime_resource_ids"]), len(move["runtime_resource_ids"])), (9, 11))
        self.assertEqual(stay["backend_pool"], move["backend_pool"])
        self.assertEqual(self.draft["backend"]["endpoints"],
                         {"metadata": "redis://10.20.2.162:6379", "objects": "http://10.20.2.163:9000"})
        self.assertIsNone(self.draft["backend"]["volume"])

    def test_native_move_guard_includes_idle_backend_hosts(self):
        guard = self.draft["lock_reference"]
        actual_plan = load(EVIDENCE / "plan.json")
        self.assertEqual(guard["acquire_pool_order"], ["pool-a", "pool-b"])
        all_tokens = set()
        for pool in guard["acquire_pool_order"]:
            cell = next(cell for cell in actual_plan["cells"] if cell["pool"] == pool)
            self.assertEqual(guard["canonical_reference_cell_by_pool"][pool], cell["id"])
            locks = guard["lock_files_by_pool"][pool]
            self.assertEqual(set(locks), {f"pool:{pool}", *cell["resource_ids"]})
            for token, filename in locks.items():
                self.assertEqual(filename, hashlib.sha256(token.encode()).hexdigest() + ".lock")
            all_tokens.update(locks)
        self.assertEqual(len(all_tokens), 20)
        self.assertTrue({f"host:{self.draft['roles'][role]['instance_id']}"
                         for role in ("pool-b-metadata", "pool-b-objects")}.issubset(all_tokens))

    def test_frozen_planner_refuses_case_without_any_admission(self):
        self.assertIsNone(self.draft["formal_plan"])
        self.assertFalse(self.draft["execution_enabled"])
        self.assertFalse(self.draft["reviewed"])
        case = self.draft["catalog_case"]
        self.assertEqual(set(case["arms"]), {"juicefs-stay", "juicefs-move",
                                             "native-workspace-stay", "native-workspace-move"})
        self.assertEqual(case["params"]["trace"], "archive-io-20261026")
        config = load(EVIDENCE / "plan.json")["config"]
        config["cases"] = [case]
        with self.assertRaisesRegex(ValueError, "assigned pools lack capacity for arm"):
            suite_plan.build(config)
        for cluster in config["clusters"]:
            cluster["supported_suites"].append("C4")
        with self.assertRaisesRegex(ValueError, "assigned pools lack capacity for arm: .*move"):
            suite_plan.build(config)
        config["placement"]["target_pool"] = "pool-b"
        with self.assertRaisesRegex(ValueError, "unknown placement setting"):
            suite_plan.build(config)

    def test_rejects_changed_input_bytes_even_if_json_is_identical(self):
        for name in ("plan.json", "inventory.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / name
                path.write_bytes((EVIDENCE / name).read_bytes() + b" ")
                plan_path = path if name == "plan.json" else EVIDENCE / "plan.json"
                inventory_path = path if name == "inventory.json" else EVIDENCE / "inventory.json"
                with self.assertRaisesRegex(ValueError, "not the frozen input"):
                    module.map_route(plan_path, inventory_path)
