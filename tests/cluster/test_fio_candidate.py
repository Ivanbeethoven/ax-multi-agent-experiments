from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "bench/cluster/runtime/run_fio_candidate.py"
MIB = 1024**2
GIB = 1024**3


def graftfs_mount(identifier: str, purpose: str) -> dict:
    return {
        "id": identifier,
        "purpose": purpose,
        "engine": "graftfs-native",
        "binary": "/original/graftfs",
        "environment": {"TOKIO_WORKER_THREADS": "4"},
        "cache": {
            "write_memory_bytes": 256 * MIB,
            "memory_budget_bytes": 256 * MIB,
            "read_memory_bytes": 512 * MIB,
            "read_fetch_policy": "sparse",
        },
        "capacity_reservation_bytes": 3 * GIB,
        "prefetch": {"data_window_bytes": 4 * MIB, "data_workers": 4},
        "download_workers": 4,
    }


def juicefs_mount(identifier: str, purpose: str) -> dict:
    return {
        "id": identifier,
        "purpose": purpose,
        "engine": "juicefs",
        "binary": "/original/juicefs",
        "options": {"buffer-size": "768M", "max-readahead": "16M"},
        "capacity_reservation_bytes": 768 * MIB,
        "prefetch": {"data_window_bytes": 4 * MIB, "data_workers": 4},
        "download_workers": 4,
    }


class FioCandidateMainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        (self.repo_root / "source-record.json").write_text("{}\n", encoding="utf-8")

        self.mount_template = {
            "mounts": [
                graftfs_mount("input-graftfs", "input"),
                graftfs_mount("measure-graftfs", "measure"),
                juicefs_mount("input-juicefs", "input"),
            ],
            "host_windows": [],
        }
        self.requests = [
            {"id": "input-graftfs", "purpose": "input", "engine": "graftfs-native"},
            {"id": "measure-graftfs", "purpose": "measure", "engine": "graftfs-native"},
            {"id": "input-juicefs", "purpose": "input", "engine": "juicefs"},
        ]
        self.generated_mounts = []
        self.original_documents = []
        self.run_io_jobs = []

        plan = {
            "jobs": [
                {
                    "id": "one-job",
                    "group": "01-io",
                    "output": "01-io/one-job",
                    "parameters": {"system": "graftfs-native"},
                }
            ]
        }
        (self.run_root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

        self.cluster_lib = types.ModuleType("cluster_lib")
        self.cluster_lib.RUN_ROOT = self.run_root
        self.cluster_lib.REPO_ROOT = self.repo_root
        self.cluster_lib.ALL_HOST_NAMES = []

        def generate_mounts_json(requests, path):
            self.assertEqual({request["id"] for request in requests},
                             {record["id"] for record in self.mount_template["mounts"]})
            document = copy.deepcopy(self.mount_template)
            self.original_documents.append(copy.deepcopy(document))
            return document

        self.cluster_lib.generate_mounts_json = generate_mounts_json

        self.run_suite = types.ModuleType("run_suite")

        def save(path, value):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

        def run_io(job, output):
            self.run_io_jobs.append(copy.deepcopy(job))
            generated = self.cluster_lib.generate_mounts_json(
                copy.deepcopy(self.requests), Path(output) / "mounts.json"
            )
            self.generated_mounts.append(copy.deepcopy(generated))
            return {
                "timing": {"app_done_seconds": 1.0},
                "application": {"throughput_mib_s": 2.0},
            }

        self.run_suite.save = save
        self.run_suite.run_io = run_io

    def run_main(self, *extra_args):
        output = self.root / "candidate-output"
        argv = [
            "run_fio_candidate.py",
            "--workers", "8",
            "--binary", str(self.root / "candidate-graftfs"),
            "--job", "one-job",
            "--out", str(output),
            *extra_args,
        ]
        with patch.dict(sys.modules, {
            "cluster_lib": self.cluster_lib,
            "run_suite": self.run_suite,
        }):
            spec = importlib.util.spec_from_file_location("fio_candidate_under_test", RUNNER_PATH)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with patch.object(sys, "argv", argv):
                module.main()
        self.assertEqual(len(self.run_io_jobs), 1)
        self.assertEqual(len(self.generated_mounts), 1)
        self.assertEqual(len(self.original_documents), 1)
        self.assertEqual(self.original_documents[0], self.mount_template)
        self.assertEqual(self.mount_template["mounts"][0]["cache"]["write_memory_bytes"], 256 * MIB)
        return self.generated_mounts[0]

    def test_input_write_memory_and_whole_object_policy_reach_graftfs_mounts(self):
        original_input_reservation = self.mount_template["mounts"][0]["capacity_reservation_bytes"]
        original_measure = copy.deepcopy(self.mount_template["mounts"][1])
        original_juicefs = copy.deepcopy(self.mount_template["mounts"][2])

        generated = self.run_main(
            "--input-write-memory-mib", "1024",
            "--read-fetch-policy", "whole-object",
        )
        mounts = {record["id"]: record for record in generated["mounts"]}
        input_mount = mounts["input-graftfs"]
        measure_mount = mounts["measure-graftfs"]
        juicefs = mounts["input-juicefs"]

        self.assertEqual(input_mount["cache"]["write_memory_bytes"], 1024 * MIB)
        self.assertEqual(input_mount["cache"]["memory_budget_bytes"], 1024 * MIB)
        self.assertEqual(
            input_mount["capacity_reservation_bytes"],
            original_input_reservation + (1024 - 256) * MIB,
        )
        self.assertEqual(input_mount["cache"]["read_fetch_policy"], "whole-object")

        self.assertEqual(measure_mount["cache"]["write_memory_bytes"],
                         original_measure["cache"]["write_memory_bytes"])
        self.assertEqual(measure_mount["cache"]["memory_budget_bytes"],
                         original_measure["cache"]["memory_budget_bytes"])
        self.assertEqual(measure_mount["capacity_reservation_bytes"],
                         original_measure["capacity_reservation_bytes"])
        self.assertEqual(measure_mount["cache"]["read_fetch_policy"], "whole-object")

        self.assertEqual(juicefs, original_juicefs)

    def test_new_options_leave_existing_mount_configuration_unchanged_by_default(self):
        expected = copy.deepcopy(self.mount_template)

        generated = self.run_main()
        mounts = {record["id"]: record for record in generated["mounts"]}
        for record in expected["mounts"]:
            actual = mounts[record["id"]]
            self.assertEqual(actual["capacity_reservation_bytes"],
                             record["capacity_reservation_bytes"])
            if record["engine"].startswith("graftfs"):
                self.assertEqual(actual["cache"], record["cache"])
            else:
                self.assertEqual(actual["options"], record["options"])
                self.assertNotIn("cache", actual)

    def test_explicit_plan_and_source_record_are_used_without_changing_current_plan(self):
        current_plan = (self.run_root / "plan.json").read_text()
        alternate = json.loads(current_plan)
        alternate["jobs"][0]["parameters"]["candidate_plan"] = True
        plan_path = self.root / "candidate-plan.json"
        plan_path.write_text(json.dumps(alternate), encoding="utf-8")
        source = {"binary": "/candidate/graftfs", "branch": "codex/read-buffer"}
        source_path = self.root / "candidate-source.json"
        source_path.write_text(json.dumps(source), encoding="utf-8")

        self.run_main("--plan", str(plan_path), "--source-record", str(source_path))

        self.assertTrue(self.run_io_jobs[0]["parameters"]["candidate_plan"])
        output = self.root / "candidate-output"
        self.assertEqual(json.loads((output / "plan.json").read_text()), alternate)
        actual_source = output / "one-job/attempt-01/source-record.json"
        self.assertEqual(json.loads(actual_source.read_text()), source)
        self.assertEqual((self.run_root / "plan.json").read_text(), current_plan)

    def test_workload_policy_selects_sequential_read_fetching(self):
        path = self.run_root / "plan.json"
        plan = json.loads(path.read_text())
        plan["jobs"][0]["parameters"].update(pattern="sequential", operation="read")
        path.write_text(json.dumps(plan), encoding="utf-8")
        generated = self.run_main("--read-fetch-policy", "workload")
        for record in generated["mounts"]:
            if record["engine"].startswith("graftfs"):
                self.assertEqual(record["cache"]["read_fetch_policy"], "whole-object")

    def test_workload_policy_keeps_write_fetching_configuration(self):
        path = self.run_root / "plan.json"
        plan = json.loads(path.read_text())
        plan["jobs"][0]["parameters"].update(pattern="sequential", operation="write")
        path.write_text(json.dumps(plan), encoding="utf-8")
        generated = self.run_main("--read-fetch-policy", "workload")
        for record in generated["mounts"]:
            if record["engine"].startswith("graftfs"):
                self.assertEqual(record["cache"]["read_fetch_policy"], "sparse")


if __name__ == "__main__":
    unittest.main()
