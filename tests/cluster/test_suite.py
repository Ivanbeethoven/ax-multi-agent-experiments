import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SUITE_CONFIG = ROOT / "configs" / "experiment-suite.json"
SUITE_MODULE = ROOT / "bench" / "cluster" / "suite.py"


def load_suite_module():
    spec = importlib.util.spec_from_file_location("cluster_suite_test_module", SUITE_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ExperimentSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = json.loads(SUITE_CONFIG.read_text(encoding="utf-8"))
        cls.module = load_suite_module()
        cls.plan = cls.module.build(cls.suite)

    def test_approved_groups_export_56_runs(self):
        expected = {
            "01-io": 20,
            "02-fork": 12,
            "03-packed": 11,
            "04-agent-replay": 13,
        }
        self.assertEqual(self.suite["approval"], "approved")
        self.assertEqual(self.plan["counts"], expected)
        self.assertEqual(self.plan["total_runs"], 56)
        self.assertEqual(len(self.plan["jobs"]), 56)
        self.assertEqual(
            {group: sum(job["group"] == group for job in self.plan["jobs"])
             for group in expected},
            expected,
        )
        self.assertEqual(len({job["id"] for job in self.plan["jobs"]}), 56)

    def test_flat_volume_fio_workloads_append_after_original_io_order(self):
        io_jobs = [job for job in self.plan["jobs"] if job["group"] == "01-io"]
        self.assertEqual(
            [job["id"] for job in io_jobs],
            [f"01-io-{index:02d}-r1" for index in range(1, 21)],
        )
        self.assertEqual(
            [job["configuration"] for job in io_jobs[:16]],
            list(range(1, 17)),
        )
        self.assertEqual(
            [
                (
                    job["parameters"]["system"],
                    job["parameters"].get("dynamic"),
                    job["parameters"]["block_bytes"],
                    job["parameters"]["pattern"],
                    job["parameters"]["operation"],
                )
                for job in io_jobs[:16]
            ],
            [
                ("brewfs-native", "on", "auto", "sequential", "read"),
                ("brewfs-native", "on", "auto", "sequential", "write"),
                ("brewfs-native", "on", "auto", "random", "read"),
                ("brewfs-native", "on", "auto", "random", "write"),
                ("juicefs-redis", "on", "default", "sequential", "read"),
                ("juicefs-redis", "on", "default", "sequential", "write"),
                ("juicefs-redis", "on", "default", "random", "read"),
                ("juicefs-redis", "on", "default", "random", "write"),
                ("brewfs-native", "off", "default", "sequential", "read"),
                ("brewfs-native", "off", "default", "sequential", "write"),
                ("brewfs-native", "off", "default", "random", "read"),
                ("brewfs-native", "off", "default", "random", "write"),
                ("brewfs-native", "on", 65536, "random", "read"),
                ("brewfs-native", "on", 65536, "random", "write"),
                ("brewfs-native", "on", 4194304, "random", "read"),
                ("brewfs-native", "on", 4194304, "random", "write"),
            ],
        )

        flat_configurations = [job["parameters"] for job in io_jobs[16:]]
        self.assertEqual(len(flat_configurations), 4)
        self.assertEqual(
            [
                (configuration["pattern"], configuration["operation"],
                 configuration["request_kib"], configuration["io_bytes"])
                for configuration in flat_configurations
            ],
            [
                ("sequential", "read", 1024, 2147483648),
                ("sequential", "write", 1024, 2147483648),
                ("random", "read", 4, 67108864),
                ("random", "write", 4, 67108864),
            ],
        )
        for configuration in flat_configurations:
            self.assertEqual(configuration["system"], "brewfs-flat")
            self.assertEqual(configuration["dynamic"], "on")
            self.assertEqual(configuration["block_bytes"], "auto")
            self.assertEqual(configuration["initial_data"], "flat-volume")
            self.assertEqual(configuration["jobs"], 8)
            self.assertEqual(configuration["working_set_bytes"], 2147483648)
            self.assertEqual(configuration["fsync_bytes_all_jobs"], 67108864)

    def test_dynamic_fields_are_limited_to_io_packed_and_agent_replay(self):
        groups_with_dynamic = {
            group for group, definition in self.suite["groups"].items()
            if any("dynamic" in configuration for configuration in definition["configurations"])
        }
        self.assertEqual(groups_with_dynamic, {"01-io", "03-packed", "04-agent-replay"})

    def test_fork_group_covers_forks_and_direct_parent_workspaces(self):
        group = self.suite["groups"]["02-fork"]
        configurations = group["configurations"]
        self.assertEqual(group["files"], 100_000)
        self.assertEqual(
            {configuration["mode"] for configuration in configurations},
            {"fork", "direct-parent"},
        )
        systems = {"brewfs-native", "juicefs-redis", "juicefs-overlay"}
        for mode in ("fork", "direct-parent"):
            self.assertEqual(
                {configuration["system"] for configuration in configurations
                 if configuration["mode"] == mode},
                systems,
            )
        self.assertEqual(
            {configuration["children"] for configuration in configurations
             if configuration["mode"] == "fork"},
            {1, 16, 64},
        )

    def test_packed_group_uses_two_epochs_for_100k_files(self):
        group = self.suite["groups"]["03-packed"]
        self.assertEqual(group["files"], 100_000)
        self.assertEqual(group["epochs"], 2)
        self.assertEqual(group["readers"], 32)
        self.assertEqual(
            sum(configuration["phase"] == "prepare" for configuration in group["configurations"]),
            5,
        )
        self.assertEqual(
            sum(configuration["phase"] == "read" for configuration in group["configurations"]),
            6,
        )

    def test_agent_replay_scales_1_8_16_64_with_dynamic_off_only_at_64(self):
        group = self.suite["groups"]["04-agent-replay"]
        configurations = group["configurations"]
        enabled = [configuration for configuration in configurations
                   if configuration.get("dynamic") == "on"]
        disabled = [configuration for configuration in configurations
                    if configuration.get("dynamic") == "off"]
        self.assertEqual(
            {configuration["agents"] for configuration in enabled},
            {1, 8, 16, 64},
        )
        systems = {"brewfs-native", "juicefs-redis", "juicefs-overlay"}
        for agents in (1, 8, 16, 64):
            self.assertEqual(
                {configuration["system"] for configuration in enabled
                 if configuration["agents"] == agents},
                systems,
            )
        self.assertEqual(len(disabled), 1)
        self.assertEqual(disabled[0]["system"], "brewfs-native")
        self.assertEqual(disabled[0]["agents"], 64)

    def test_export_creates_per_job_and_raw_data_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "export"
            self.module.export(self.plan, output)

            self.assertTrue((output / "plan.json").is_file())
            self.assertTrue((output / "status.json").is_file())
            for name in ("hosts", "config", "versions", "logs"):
                self.assertTrue((output / name).is_dir())
            for job in self.plan["jobs"]:
                job_directory = output / job["output"]
                self.assertTrue(job_directory.is_dir())
                self.assertEqual(json.loads((job_directory / "job.json").read_text(encoding="utf-8")), job)
            status = json.loads((output / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["total_runs"], 56)
            self.assertEqual(len(status["jobs"]), 56)
            self.assertTrue(all(value["state"] == "planned" for value in status["jobs"].values()))


if __name__ == "__main__":
    unittest.main()
