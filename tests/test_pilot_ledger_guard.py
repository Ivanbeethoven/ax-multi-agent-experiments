from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from bench import suite_plan, suite_state
from bench.experiment_io import load
from bench.pilot_ledger_guard import multi_ledger_guard
from bench.runtime.pilot_pools import cell_resources, resource_guard


REPO = Path(__file__).resolve().parents[1]
PILOT = REPO / "experiments/unified-suite/pilot-aliyun"
EVIDENCE = PILOT / "evidence/preparation/parallel-control-01"
spec = spec_from_file_location("single_pool_fixture", PILOT / "scripts/draft-c4-single-pool.py")
draft = module_from_spec(spec)
spec.loader.exec_module(draft)


@unittest.skipUnless(sys.platform.startswith("linux"), "coordinator flock requires Linux")
class TestPilotLedgerGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = load(EVIDENCE / "plan.json")
        cls.new = draft.draft(EVIDENCE / "plan.json", EVIDENCE / "inventory.json", "code-12")["plan.json"]
        cls.cell = cls.new["cells"][0]
        cls.b_cell = next(cell for cell in cls.old["cells"] if cell["pool"] == "pool-b")

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="pilot ledgers #")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.canonical, self.current = self.root / "original", self.root / "current"
        suite_state.initialize(self.canonical / "state.sqlite", self.old)
        suite_state.initialize(self.current / "state.sqlite", self.new)
        (self.canonical / "runner-locks").mkdir()

    def guard(self, **kwargs):
        return multi_ledger_guard(self.canonical, self.current, self.old, self.new, self.cell["id"], **kwargs)

    @staticmethod
    def reserve(path, plan, cell, mode, *, include_cell=True):
        # Build only SQLite reservation state; no fake qualification or performance receipts.
        with sqlite3.connect(path / "state.sqlite") as db:
            if include_cell:
                db.execute("UPDATE cells SET status='claimed' WHERE id=?", (cell["id"],))
            db.execute("UPDATE blocks SET status='claimed' WHERE id=?", (cell["paired_block"],))
            if mode == "cell":
                db.execute("INSERT INTO cell_leases VALUES (?,?,?,?)", (cell["id"], cell["pool"], "owner", "token"))
                db.executemany("INSERT INTO cell_resources VALUES (?,?)",
                               [(r, cell["id"]) for r in cell_resources(plan, cell)])
            else:
                db.execute("UPDATE blocks SET owner='owner',token='token' WHERE id=?", (cell["paired_block"],))
                block = next(b for b in plan["blocks"] if b["id"] == cell["paired_block"])
                db.executemany("INSERT INTO resources VALUES (?,?)",
                               [(r, block["id"]) for r in block["resource_ids"]])

    def test_shared_os_lock_held_for_scope_and_released_on_exit(self):
        with self.guard() as scanned:
            self.assertEqual(set(scanned), {self.canonical / "state.sqlite", self.current / "state.sqlite"})
            with self.assertRaisesRegex(ValueError, "owns physical resource"):
                with resource_guard(self.canonical, self.b_cell, self.old):
                    self.fail("old pool acquired shared B client while C4 held it")
        with resource_guard(self.canonical, self.b_cell, self.old):
            pass

    def test_original_cell_lease_conflicts_across_pool_names(self):
        self.reserve(self.canonical, self.old, self.b_cell, "cell")
        b_ids = set(cell_resources(self.old, self.b_cell))
        overlap = sorted(b_ids.intersection(cell_resources(self.new, self.cell)))
        self.assertEqual(len(overlap), 2)  # B client's disk and instance.
        for ownership in (None, ("owner", "token")):
            with self.subTest(ownership=ownership), self.assertRaises(ValueError) as caught:
                with self.guard(ownership=ownership):
                    self.fail("old campaign owner was mistaken for current owner")
            self.assertIn(f"physical resources reserved by cell {self.b_cell['id']}", str(caught.exception))
            self.assertTrue(all(resource in str(caught.exception) for resource in overlap))
            self.assertFalse(any(resource in str(caught.exception) for resource in b_ids.difference(overlap)))

    def test_current_paired_owner_must_match_active_cell_and_token(self):
        self.reserve(self.current, self.new, self.cell, "block")
        for kwargs in ({}, {"ownership": ("owner", "other")}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "reserved by block"):
                with self.guard(**kwargs):
                    self.fail("unowned paired reservation admitted")
        with self.guard(ownership=("owner", "token")):
            pass
        with sqlite3.connect(self.current / "state.sqlite") as db:
            db.execute("UPDATE cells SET status='complete' WHERE id=?", (self.cell["id"],))
        with self.assertRaisesRegex(ValueError, "reserved by block"):
            with self.guard(ownership=("owner", "token")):
                self.fail("between-arm reservation was bypassed")
        other = self.new["cells"][1]
        with self.assertRaisesRegex(ValueError, "reserved by block"):
            with multi_ledger_guard(self.canonical, self.current, self.old, self.new, other["id"],
                                    ownership=("owner", "token")):
                self.fail("owner started an unclaimed paired arm")

    def test_another_paired_ledger_is_matched_by_physical_ids(self):
        other = self.root / "other"
        cfg = deepcopy(self.new["config"])
        cfg["campaign_id"] = "other-actual-ali-pilot"
        cfg["clusters"][0]["id"] = "pool-other"
        cfg["clusters"][0]["resource_ids"] = ["host:i-testactual1", "disk:d-testactual1"]
        nonoverlap = suite_plan.build(cfg)
        suite_state.initialize(other / "state.sqlite", nonoverlap)
        self.reserve(other, nonoverlap, nonoverlap["cells"][0], "block", include_cell=False)
        with self.guard() as scanned:
            self.assertIn(other / "state.sqlite", scanned)
        # Independently claim a ledger which physically overlaps only on a disk.
        shared_disk = next(r for r in cell_resources(self.new, self.cell) if r.startswith("disk:"))
        cfg["clusters"][0]["resource_ids"][1] = shared_disk
        overlap = suite_plan.build(cfg)
        overlapping = self.root / "overlapping"
        suite_state.initialize(overlapping / "state.sqlite", overlap)
        self.reserve(overlapping, overlap, overlap["cells"][0], "block", include_cell=False)
        with self.assertRaisesRegex(ValueError, f"reserved by block .*{shared_disk}"):
            with self.guard():
                self.fail("disk-only overlap was missed")

    def test_partial_footprint_and_wrong_expected_plan_fail_closed(self):
        self.reserve(self.canonical, self.old, self.b_cell, "cell")
        with sqlite3.connect(self.canonical / "state.sqlite") as db:
            db.execute("DELETE FROM cell_resources WHERE id=?", (cell_resources(self.old, self.b_cell)[0],))
        with self.assertRaisesRegex(ValueError, "footprint differs"):
            with self.guard():
                self.fail("damaged ledger treated as free")
        cfg = deepcopy(self.new["config"])
        cfg["campaign_id"] = "different-expected-campaign"
        with self.assertRaisesRegex(ValueError, "ledger plan does not match expected campaign"):
            with multi_ledger_guard(self.canonical, self.current, self.old,
                                    suite_plan.build(cfg), self.cell["id"]):
                self.fail("wrong plan accepted")
