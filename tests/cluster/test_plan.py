"""Unit tests for the local cluster-plan design exporter."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bench.cluster import plan


HERE = Path(__file__).resolve().parent
DESIGN_PATH = HERE.parents[1] / "configs" / "cluster.json"
PLAN_PATH = HERE.parents[1] / "bench" / "cluster" / "plan.py"


def load_design() -> dict:
    return json.loads(DESIGN_PATH.read_text(encoding="utf-8"))


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.design = load_design()

    def test_resource_totals_and_fixed_six_host_allocation(self) -> None:
        expected = {
            "A366": ("idcai-60001", 48, 52),
            "A355": ("idcai-60525", 32, 40),
            "A672": ("idcai-60103", 16, 16),
            "A601": ("idcai-60439", 40, 48),
            "A635": ("idcai-60533", 40, 48),
            "A368": ("idcai-60555", 16, 20),
        }
        actual = {
            host["name"]: (host["ssh"], host["active"], host["slots"])
            for host in self.design["hosts"]
        }
        self.assertEqual(actual, expected)

        built = plan.build_plan(self.design)
        self.assertEqual(built["counts"]["active_agents"], 192)
        self.assertEqual(built["counts"]["mount_slots"], 224)
        self.assertEqual(len(self.design["hosts"]), 6)
        self.assertEqual(sum(h["active"] for h in self.design["hosts"]), 192)
        self.assertEqual(sum(h["slots"] for h in self.design["hosts"]), 224)

    def test_each_cohort_distributes_eight_cases_evenly(self) -> None:
        built = plan.build_plan(self.design)
        case_count = len(self.design["trace_cases"])
        self.assertEqual(case_count, 8)
        for cohort in built["cohorts"]:
            with self.subTest(agents=cohort["agents"], repetition=cohort["repetition"]):
                tasks = cohort["shared_tasks"]
                self.assertEqual(len(tasks), cohort["agents"])
                counts = {case: 0 for case in self.design["trace_cases"]}
                for task in tasks:
                    counts[task["case"]] += 1
                self.assertEqual(set(counts.values()), {cohort["agents"] // case_count})

    def test_paired_task_sequences_are_deterministic_and_seeded_per_repetition(self) -> None:
        first = plan.cohort(self.design, 32, 1)
        repeated = plan.cohort(self.design, 32, 1)
        second_repetition = plan.cohort(self.design, 32, 2)
        self.assertEqual(first["shared_tasks"], repeated["shared_tasks"])
        self.assertEqual(first["system_order"], self.design["systems"])
        shift = (2 - 1) % len(self.design["systems"])
        self.assertEqual(
            second_repetition["system_order"],
            self.design["systems"][shift:] + self.design["systems"][:shift],
        )
        self.assertNotEqual(first["seed"], second_repetition["seed"])
        self.assertNotEqual(first["shared_tasks"], second_repetition["shared_tasks"])

        # Adding another paired system must reuse the same generated task sequence.
        expanded = copy.deepcopy(self.design)
        expanded["systems"].append("additional-test-system")
        expanded_cohort = plan.cohort(expanded, 32, 1)
        self.assertEqual(expanded_cohort["shared_tasks"], first["shared_tasks"])
        self.assertEqual(expanded_cohort["system_order"], expanded["systems"])
        expanded_plan = plan.build_plan(expanded)
        expected_runs = sum(
            len(condition["systems"]) * condition["repetitions"]
            for condition in expanded_plan["conditions"]
        )
        self.assertEqual(expanded_plan["counts"]["system_runs"], expected_runs)

    def test_maximum_cohort_matches_host_allocations_without_port_conflicts(self) -> None:
        tasks = plan.placement(self.design, 192)
        active_by_host = {h["name"]: h["active"] for h in self.design["hosts"]}
        allocation = {name: 0 for name in active_by_host}
        seen_host_slots: set[tuple[str, int]] = set()
        seen_host_ports: set[tuple[str, int]] = set()
        for task in tasks:
            allocation[task["host"]] += 1
            host_slot = (task["ssh"], task["slot"])
            host_port = (task["ssh"], task["metrics_port"])
            self.assertNotIn(host_slot, seen_host_slots)
            self.assertNotIn(host_port, seen_host_ports)
            seen_host_slots.add(host_slot)
            seen_host_ports.add(host_port)
            self.assertEqual(
                task["metrics_port"], self.design["metrics_port_base"] + task["slot"]
            )
            self.assertLessEqual(task["slot"], active_by_host[task["host"]])
        self.assertEqual(allocation, active_by_host)

    def test_validation_rejects_memory_q_and_spare_mount_violations(self) -> None:
        excess_memory = copy.deepcopy(self.design)
        host = excess_memory["hosts"][0]
        required = (
            host["slots"] * excess_memory["workspace_memory_gib"]
            + host["rustfs_memory_gib"]
            + host["redis_memory_gib"]
            + host["coordinator_memory_gib"]
            + host["os_memory_gib"]
        )
        host["memory_gib_floor"] = required - 1
        with self.assertRaisesRegex(ValueError, "Memory exceeded"):
            plan.validate(excess_memory)

        bad_q = copy.deepcopy(self.design)
        bad_q["layout"]["record_actual_block_and_quantum"] = False
        with self.assertRaisesRegex(ValueError, "actual B/q"):
            plan.validate(bad_q)

        insufficient_children = copy.deepcopy(self.design)
        insufficient_children["fork"]["children"] = [33]
        with self.assertRaisesRegex(ValueError, "child mounts"):
            plan.validate(insufficient_children)


    def test_export_refuses_to_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "existing-plan.json"
            original = "preserve this file\n"
            output_path.write_text(original, encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(PLAN_PATH),
                    "--design",
                    str(DESIGN_PATH),
                    "--out",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
