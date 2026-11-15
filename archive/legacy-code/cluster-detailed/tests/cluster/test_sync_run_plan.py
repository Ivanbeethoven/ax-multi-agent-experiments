import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bench.cluster import plan
from bench.cluster.runtime import sync_run_plan


ROOT = Path(__file__).resolve().parents[2]
SYNC_SCRIPT = ROOT / "bench" / "cluster" / "runtime" / "sync_run_plan.py"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def raw_file_contents(root):
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class SyncRunPlanTests(unittest.TestCase):
    def test_reconcile_drops_removed_work_preserves_passes_and_interrupts_running(self):
        condition = {
            "id": "C1-fio-01",
            "systems": ["brewfs-native-overlay", "juicefs"],
            "repetitions": 2,
        }
        original = {
            "phase": "running",
            "groups": {
                "C1": {
                    "conditions": {
                        "C1-layout-01": {
                            "state": "passed",
                            "runs": {"rustfs-range/r1": {"state": "passed"}},
                        },
                        "C1-fio-01": {
                            "state": "incomplete",
                            "runs": {
                                "brewfs-native-overlay/r1": {"state": "passed", "note": "keep"},
                                "brewfs-native-overlay/r2": {"state": "running", "note": "interrupt"},
                                "juicefs/r1": {"state": "stopped", "note": "retain"},
                                "juicefs/r2": {"state": "planned"},
                                "obsolete-system/r1": {"state": "passed"},
                            },
                        },
                    },
                    "completed_conditions": 1,
                    "total_conditions": 2,
                    "completed_system_runs": 2,
                    "total_system_runs": 5,
                    "pending_feature_conditions": 0,
                }
            },
        }
        original_copy = json.loads(json.dumps(original))
        plans = {"C1": {"conditions": [condition], "counts": {"system_runs": 4}}}

        updated = sync_run_plan.reconcile(original, plans)

        self.assertEqual(original, original_copy)
        group = updated["groups"]["C1"]
        self.assertEqual(set(group["conditions"]), {"C1-fio-01"})
        condition_state = group["conditions"]["C1-fio-01"]
        self.assertEqual(condition_state["runs"]["brewfs-native-overlay/r1"],
                         {"state": "passed", "note": "keep"})
        self.assertEqual(condition_state["runs"]["brewfs-native-overlay/r2"]["state"], "interrupted")
        self.assertEqual(condition_state["runs"]["juicefs/r1"]["state"], "stopped")
        self.assertNotIn("obsolete-system/r1", condition_state["runs"])
        self.assertEqual(condition_state["state"], "incomplete")
        self.assertEqual(group["total_conditions"], 1)
        self.assertEqual(group["total_system_runs"], 4)
        self.assertEqual(group["completed_conditions"], 0)
        self.assertEqual(group["completed_system_runs"], 1)
        self.assertEqual(updated["totals"], {
            "conditions": 1,
            "system_runs": 4,
            "completed_conditions": 0,
            "completed_runs": 1,
        })
        self.assertEqual(updated["phase"], "paused")

    def test_cli_dry_run_and_update_leave_raw_files_unchanged(self):
        campaign = load_json(ROOT / "configs" / "campaign-direct.json")
        design = load_json(ROOT / "configs" / "cluster.json")
        plans = {group: plan.build_plan(design, group) for group in campaign["groups"]}
        first_condition = plans["C1"]["conditions"][0]
        current_id = first_condition["id"]
        expected_runs = [
            f"{system}/r{repetition}"
            for system in first_condition["systems"]
            for repetition in range(1, first_condition["repetitions"] + 1)
        ]
        current_runs = {
            key: {"state": state, "prior_note": state}
            for key, state in zip(expected_runs, ("passed", "running", "stopped"))
        }
        current_runs["rustfs-range/r1"] = {"state": "passed", "prior_note": "removed"}
        current_runs["obsolete-system/r1"] = {"state": "passed", "prior_note": "removed"}

        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary) / "run"
            plan_dir = run_root / "plan"
            raw_dir = run_root / "raw-results"
            plan_dir.mkdir(parents=True)
            raw_dir.mkdir()
            old_plan_bytes = {}
            for group in campaign["groups"]:
                old_plan_bytes[group] = f"prior plan {group}\n".encode("utf-8")
                (plan_dir / f"{group}.json").write_bytes(old_plan_bytes[group])
            raw_file = raw_dir / "C1" / "raw.jsonl"
            raw_file.parent.mkdir()
            raw_file.write_bytes(b'{"preserve":"raw result"}\n')
            raw_before = raw_file_contents(raw_dir)

            groups = {}
            for group in campaign["groups"]:
                conditions = {
                    f"{group}-removed-01": {
                        "state": "passed",
                        "runs": {"removed-system/r1": {"state": "passed"}},
                    }
                }
                if group == "C1":
                    conditions[current_id] = {"state": "incomplete", "runs": current_runs}
                groups[group] = {
                    "conditions": conditions,
                    "completed_conditions": 1,
                    "total_conditions": len(conditions),
                    "completed_system_runs": 1,
                    "total_system_runs": 1,
                    "pending_feature_conditions": 0,
                }
            status_path = run_root / "status.json"
            status_path.write_text(json.dumps({"phase": "running", "groups": groups}), encoding="utf-8")
            original_status = status_path.read_bytes()

            command = [
                sys.executable,
                str(SYNC_SCRIPT),
                "--repo-root",
                str(ROOT),
                "--run-root",
                str(run_root),
            ]
            dry_run = subprocess.run(command + ["--check-only"], capture_output=True, text=True, check=False)
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertEqual(json.loads(dry_run.stdout), {
                "conditions": 114,
                "system_runs": 648,
                "completed_conditions": 0,
                "completed_runs": 1,
            })
            self.assertEqual(status_path.read_bytes(), original_status)
            self.assertEqual({group: (plan_dir / f"{group}.json").read_bytes()
                              for group in old_plan_bytes}, old_plan_bytes)
            self.assertEqual(raw_file_contents(raw_dir), raw_before)
            self.assertFalse((run_root / "plan-history").exists())

            updated_run = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(updated_run.returncode, 0, updated_run.stderr)
            self.assertEqual(json.loads(updated_run.stdout)["conditions"], 114)
            self.assertEqual(json.loads(updated_run.stdout)["system_runs"], 648)
            self.assertEqual(raw_file_contents(raw_dir), raw_before)

            saved = load_json(status_path)
            self.assertEqual(saved["phase"], "paused")
            self.assertEqual(saved["totals"]["conditions"], 114)
            self.assertEqual(saved["totals"]["system_runs"], 648)
            c1_conditions = saved["groups"]["C1"]["conditions"]
            self.assertNotIn("C1-layout-01", c1_conditions)
            self.assertNotIn("C1-removed-01", c1_conditions)
            current = c1_conditions[current_id]
            self.assertEqual(current["runs"][expected_runs[0]]["state"], "passed")
            self.assertEqual(current["runs"][expected_runs[1]]["state"], "interrupted")
            self.assertEqual(current["runs"][expected_runs[2]]["state"], "stopped")
            self.assertNotIn("rustfs-range/r1", current["runs"])

            history = run_root / "plan-history" / "revision-001"
            self.assertEqual((history / "status.json").read_bytes(), original_status)
            self.assertEqual({group: (history / f"{group}.json").read_bytes()
                              for group in old_plan_bytes}, old_plan_bytes)


if __name__ == "__main__":
    unittest.main()
