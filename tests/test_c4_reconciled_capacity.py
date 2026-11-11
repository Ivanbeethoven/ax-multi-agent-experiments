"""Offline checks for the 100k C4 base capacity reconciliation."""
from copy import deepcopy
from pathlib import Path
import runpy
import unittest


SCRIPT = (Path(__file__).resolve().parents[1]
          / "experiments/unified-suite/pilot-aliyun/scripts/reconcile-c4-capacity.py")
RECONCILE = runpy.run_path(str(SCRIPT))
META = "/srv/ax-metadata"
CLIENT = "/srv/ax-client"
OBJECTS = "/srv/ax-rustfs-data-"
META_TOTAL = 60_070_604_800

# Allocation and Redis figures from the original C4 before/after cache.
# Device identifiers and recovery proof are synthetic; no cache or cloud needed.
ALLOCATIONS = {
    "client": [(CLIENT, 100_056_096_768, 26_214_400,
                17_059_889_152, 2_539_984, 17_077_473_280, 2_540_155)],
    "metadata": [(META, META_TOTAL, 15_728_640,
                  1_197_600_768, 154_861, 1_137_926_144, 154_861)],
    "objects": [
        (OBJECTS + "0", META_TOTAL, 15_728_640, 37_213_044_736, 3_161_097, 38_462_980_096, 3_461_088),
        (OBJECTS + "1", META_TOTAL, 15_728_640, 17_019_600_896, 3_017_267, 18_269_425_664, 3_317_256),
        (OBJECTS + "2", META_TOTAL, 15_728_640, 17_019_404_288, 3_017_267, 18_269_212_672, 3_317_256),
        (OBJECTS + "3", META_TOTAL, 15_728_640, 17_019_576_320, 3_017_267, 18_269_384_704, 3_317_256),
    ],
}


def snapshot(after=False):
    nodes = {}
    for role, allocations in ALLOCATIONS.items():
        disks = []
        for i, (path, total_b, total_i, before_b, before_i, after_b, after_i) in enumerate(allocations):
            name = f"{role}-{i}"
            disks.append(dict(path=path, device=100 + len(nodes) * 4 + i, serial=f"disk-{name}",
                              device_bytes=total_b + 4096, total_bytes=total_b, total_inodes=total_i,
                              used_bytes=after_b if after else before_b,
                              used_inodes=after_i if after else before_i))
        nodes[role] = dict(boot_id=f"boot-{role}", disks=disks)
        if role == "objects":
            nodes[role].update(container_id="rustfs", image="rustfs-image", pid=123)
    nodes["metadata"]["redis"] = dict(process_id=9779, maxmemory=10_737_418_240,
                                     used_memory=804_714_720 if after else 716_605_864,
                                     aof_current_size=371_832_356 if after else 431_505_963)
    return dict(cell_id=RECONCILE["CELL"], files=100_000, pool="pool-a",
                inventory=dict(path="/fake/inventory.json", sha256="a" * 64), nodes=nodes)


def original_forecast():
    rows = []

    def add(role, path, quantity, increment, passed=True):
        rows.append(dict(role=role, path=path, quantity=quantity,
                         measured_base_increment=increment, passed=passed))

    add("client", CLIENT, "bytes", 17_584_128)
    add("client", CLIENT, "inodes", 171)
    add("metadata", META, "bytes", 0, False)
    add("metadata", META, "inodes", 0)
    add("metadata", None, "redis-memory-bytes", 88_108_856)
    add("metadata", None, "redis-aof-bytes", 0, False)
    for i, (b, ino) in enumerate(((1_249_935_360, 299_991), (1_249_824_768, 299_989),
                                  (1_249_808_384, 299_989), (1_249_808_384, 299_989))):
        add("objects", OBJECTS + str(i), "bytes", b)
        add("objects", OBJECTS + str(i), "inodes", ino)
    return dict(passed=False, extra_full_copies=7, reserve_fraction=0.2, rows=rows)


class ReconciledCapacity(unittest.TestCase):
    def setUp(self):
        self.before, self.after = snapshot(), snapshot(after=True)
        self.fresh = deepcopy(self.after)
        self.original = original_forecast()
        self.recovery = dict(schema="c4-aof-recovery-proof-v1", passed=True, pool="pool-a",
                             cell_id=RECONCILE["CELL"], retirement_bytes=128 * 1024**2,
                             retirement_inodes=3, retired_aof_logical_bytes=120 * 1024**2)

    def calculate(self, fresh=None):
        rows, headroom = RECONCILE["calculate"](
            self.before, self.after, self.fresh if fresh is None else fresh,
            self.original, self.recovery)
        return {(row["role"], row.get("path"), row["quantity"]): row for row in rows}, headroom

    def test_reclaimed_allocation_and_seven_copies(self):
        rows, headroom = self.calculate()
        self.assertTrue(all(row["passed"] for row in rows.values()))
        disk = rows["metadata", META, "bytes"]
        self.assertTrue(disk["observed_allocation_decrease"])
        self.assertEqual(disk["measured_base_increment"], 1_272_143_872)
        self.assertEqual(disk["extra_full_copies"], 7)
        self.assertEqual(disk["projected_used"], 14_017_810_432)
        inodes = rows["metadata", META, "inodes"]
        self.assertEqual(inodes["measured_base_increment"], 154_864)
        self.assertEqual(inodes["projected_used"], 1_238_925)
        self.assertEqual(inodes["rewrite_headroom_inodes"], 16)
        aof = rows["metadata", None, "redis-aof-bytes"]
        self.assertEqual(aof["measured_base_increment"], 497_661_476)
        self.assertEqual(aof["projected_aof_bytes"], 3_915_136_295)
        self.assertEqual(headroom, 3_915_202_560)
        self.assertEqual(disk["rewrite_headroom_bytes"], headroom)
        self.assertEqual(rows["objects", OBJECTS + "0", "bytes"]["projected_used"], 47_212_527_616)
        self.assertEqual(rows["metadata", None, "redis-memory-bytes"]["projected_used"], 1_421_476_712)
        self.assertEqual(rows["client", CLIENT, "bytes"]["projected_used"], 17_077_473_280)

    def test_twenty_percent_reserve_includes_extra_rewrite(self):
        fresh_disk = self.fresh["nodes"]["metadata"]["disks"][0]
        fresh_disk["used_bytes"] = 35_236_274_176
        rows, headroom = self.calculate()
        row = rows["metadata", META, "bytes"]
        limit = 4 * META_TOTAL // 5
        self.assertEqual(row["projected_used"], limit)
        self.assertTrue(row["passed"])
        fresh_disk["used_bytes"] += 1
        rows, _ = self.calculate()
        row = rows["metadata", META, "bytes"]
        self.assertEqual(row["projected_used"], limit + 1)
        self.assertLess(row["projected_used"] - headroom, limit)
        self.assertFalse(row["passed"])

    def test_rejects_wrong_disk_boot_and_redis_pid(self):
        for name, change, error in (
            ("disk", lambda x: x["nodes"]["objects"]["disks"][0].update(serial="replacement"),
             "Disk identity changed: serial"),
            ("boot", lambda x: x["nodes"]["metadata"].update(boot_id="rebooted"),
             "Service identity changed: metadata"),
            ("redis_pid", lambda x: x["nodes"]["metadata"]["redis"].update(process_id=9780),
             "Redis identity changed"),
        ):
            with self.subTest(name=name):
                fresh = deepcopy(self.fresh)
                change(fresh)
                with self.assertRaisesRegex(ValueError, error):
                    self.calculate(fresh)

    def test_rejects_any_decrease_from_original_increment(self):
        self.original["rows"][0]["measured_base_increment"] += 1
        with self.assertRaisesRegex(ValueError, "Forecast reduced an original increment"):
            self.calculate()


if __name__ == "__main__":
    unittest.main()
