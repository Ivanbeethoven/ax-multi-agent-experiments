from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench.experiment_io import load, write_new
from bench.runtime.prepare import anchor
from bench.suite_catalog import template
from bench.suite_plan import build


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "experiments/unified-suite/pilot-aliyun/scripts/close-c4-handoff-target.py"
spec = spec_from_file_location("close_c4_handoff_target", SCRIPT)
module = module_from_spec(spec)
spec.loader.exec_module(module)


class TestCloseC4HandoffTarget(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.point = self.root / "mount"
        self.run_dir = self.root / "supervisor"
        self.out = self.root / "closed"
        self.asset_dir = self.root / "asset"
        for path in (self.point, self.run_dir, self.asset_dir):
            path.mkdir()
        self.boot = "c05e3b63-e0e1-4eed-bcd1-bcf91e6e1500"
        self.boot_path = self.root / "boot_id"
        self.boot_path.write_text(self.boot + "\n", encoding="utf-8")
        self.proc = {"pid": 313, "start_ticks": 4000, "boot_id": self.boot, "state": "R"}
        self.supervisor = {**self.proc, "pid": 314}
        config = template()
        config["placement"] = {"mode": "paired-pool"}
        config["clusters"] = config["clusters"][:1]
        config["cases"] = [c for c in config["cases"] if c["name"] == "handoff-1"]
        plan = build(config)
        self.cell = next(c for c in plan["cells"] if c["arm_id"] == "juicefs-move")
        plan_pin = self.pin(self.root / "plan.json", plan)
        native = {"metadata_url": "redis://127.0.0.1:6379/250"}
        self.anchor = anchor("juicefs", native)
        self.mount = {"mount_id": 72, "point": str(self.point), "filesystem": "fuse.juicefs"}
        mount_pin = self.pin(self.root / "mount.json", {
            "schema": "experiment-mount-v1", "reviewed": True, "anchor": self.anchor,
            "mount_point": str(self.point), "filesystem": "juicefs", "metrics_address": "127.0.0.1:9567",
            "suite_binding": {"plan": plan_pin, "cell_id": self.cell["id"]},
        })
        self.ready = {"passed": True, "mount": self.mount, "process": self.proc, "anchor": self.anchor}
        tool = self.root / "pilot-node.py"
        tool.write_text("test fixture; execution is mocked\n", encoding="utf-8")
        self.request = {
            "schema": module.SCHEMA, "reviewed": True, "mount_run": str(self.run_dir),
            "cell_id": self.cell["id"], "mount_spec": mount_pin,
            "ready": self.pin(self.run_dir / "ready.json", {**self.ready, "spec": mount_pin}),
            "process": self.pin(self.run_dir / "process.json", {
                "spec": mount_pin, "process": self.proc, "supervisor": self.supervisor,
            }),
            "asset": self.pin(self.asset_dir / "asset.json", {
                "product": "juicefs", "plan": plan_pin, "mount_point": str(self.point), "config": native,
            }),
            "pilot_node": module.ref(tool),
        }
        self.actions = []
        self.running = True
        self.sample_gap = 1_050_000_000
        self.bad_gauge = False
        self.dirty_stop = False

    @staticmethod
    def pin(path, value):
        write_new(path, value)
        return module.ref(path)

    def collect(self, tool, asset, cell, out):
        self.actions.append("debt")
        self.assertEqual(asset, self.asset_dir)
        self.assertEqual(cell, self.cell["id"])
        out.mkdir()
        gauges = {k: 0.0 for k in module.GAUGES["juicefs"]}
        samples = []
        for i in range(2):
            native = dict(gauges)
            if self.bad_gauge and i == 1:
                native["juicefs_staging_blocks"] = 2.0
            (out / f"native-{i}.prom").write_text(
                "".join(f"{k} {v}\n" for k, v in native.items()), encoding="utf-8")
            samples.append(self.pin(out / f"sample-{i}.json", {
                "passed": True, "cell_id": cell, "native_gauges": gauges,
                "at": ("2026-11-12T10:00:00+00:00" if i == 0 else "2026-11-12T10:00:01.05+00:00"),
                "monotonic_ns": 100 + i * self.sample_gap,
                "pending_upload_bytes": 0, "pending_upload_operations": 0,
            }))
        self.pin(out / "result.json", {"passed": True, "cell_id": cell, "samples": samples})

    def control(self, run, action):
        self.actions.append(action)
        self.assertEqual(run, self.run_dir)
        if action == "status":
            return {**self.ready, "process": {**self.proc, "state": "S"}}
        self.running = False
        stopping = {"process": self.proc, "anchor": self.anchor, "original_mount": self.mount}
        self.pin(run / "stopping.json", stopping)
        stopped = {
            "passed": True, "exit_code": 0, "mount_absent": not self.dirty_stop, "anchor": self.anchor,
            "original_mount": self.mount, "active_workers": [],
        }
        self.pin(run / "stopped.json", stopped)
        return stopped

    def close(self):
        request = self.pin(self.root / "request.json", self.request)
        with patch.object(module, "BOOT_ID", self.boot_path), \
                patch.object(module, "collect_debt", self.collect), \
                patch.object(module.node, "control", self.control), \
                patch.object(module.common, "alive", lambda _: self.running), \
                patch.object(module.common, "mounts", lambda _: [self.mount] if self.running else []), \
                patch.object(module.time, "monotonic_ns", side_effect=[0, 3_000_000_000]):
            return module.close_target(request, self.out)

    def test_stops_after_distinct_native_samples_and_emits_frozen_closure(self):
        result = self.close()
        self.assertEqual(self.actions, ["status", "debt", "status", "stop"])
        self.assertEqual(result, load(self.out / "result.json"))
        self.assertEqual(result["ready"], self.request["ready"])
        self.assertEqual(result["process"], self.request["process"])
        self.assertEqual(len(result["debt"]["samples"]), 2)
        self.assertEqual(result["stopped"], module.ref(self.run_dir / "stopped.json"))
        proof = load(self.out / "suite-closure.json")
        self.assertTrue(proof["all_exited"] and proof["all_unmounted"])
        self.assertEqual([row["pid"] for row in proof["processes"]], [313, 314])
        self.assertEqual(proof["pending_upload_operations"], 0)
        with self.assertRaisesRegex(ValueError, "never retry"):
            module.close_target(module.ref(self.root / "request.json"), self.root / "another")

    def test_changed_original_process_receipt_never_touches_mount(self):
        self.request["process"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "original receipt pin differs"):
            self.close()
        self.assertEqual(self.actions, [])

    def test_nonzero_native_gauge_cannot_be_hidden_by_summary(self):
        self.bad_gauge = True
        with self.assertRaisesRegex(ValueError, "native zero-debt"):
            self.close()
        self.assertEqual(self.actions, ["status", "debt"])

    def test_repeated_sample_even_with_different_wall_times_does_not_stop(self):
        self.sample_gap = 0
        with self.assertRaisesRegex(ValueError, "distinct ordered"):
            self.close()
        self.assertEqual(self.actions, ["status", "debt"])

    def test_normal_stop_receipt_must_prove_mount_absence(self):
        self.dirty_stop = True
        with self.assertRaisesRegex(ValueError, "clean target stop"):
            self.close()
        self.assertEqual(self.actions.count("stop"), 1)
        self.assertFalse((self.out / "result.json").exists())
