import ast
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
PROGRESS = ROOT / "infra" / "idcai" / "campaign_progress.py"
CLUSTER_LIB = ROOT / "bench" / "cluster" / "runtime" / "cluster_lib.py"


def load_function(path, name, namespace=None):
    source = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    scope = {} if namespace is None else namespace
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, str(path), "exec"), scope)
    return scope[name]


def group_record(completed_conditions, total_conditions,
                 completed_system_runs, total_system_runs,
                 pending_feature_conditions):
    return {
        "completed_conditions": completed_conditions,
        "total_conditions": total_conditions,
        "completed_system_runs": completed_system_runs,
        "total_system_runs": total_system_runs,
        "pending_feature_conditions": pending_feature_conditions,
        "conditions": {},
    }


class CampaignProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summarize = staticmethod(load_function(PROGRESS, "summarize"))

    def test_c1_through_c4_counts_are_preserved(self):
        groups = {
            "C1": group_record(2, 5, 1, 3, 4),
            "C2": group_record(3, 7, 2, 5, 6),
            "C3": group_record(5, 11, 3, 7, 8),
            "C4": group_record(7, 13, 4, 9, 10),
        }

        result = self.summarize({"groups": groups}, {})

        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["groups"], {
            "C1": {
                "completed_conditions": 2,
                "total_conditions": 5,
                "completed_system_runs": 1,
                "total_system_runs": 3,
                "pending_feature_conditions": 4,
            },
            "C2": {
                "completed_conditions": 3,
                "total_conditions": 7,
                "completed_system_runs": 2,
                "total_system_runs": 5,
                "pending_feature_conditions": 6,
            },
            "C3": {
                "completed_conditions": 5,
                "total_conditions": 11,
                "completed_system_runs": 3,
                "total_system_runs": 7,
                "pending_feature_conditions": 8,
            },
            "C4": {
                "completed_conditions": 7,
                "total_conditions": 13,
                "completed_system_runs": 4,
                "total_system_runs": 9,
                "pending_feature_conditions": 10,
            },
        })

    def test_failed_service_needs_attention(self):
        status = {"groups": {"C1": group_record(1, 2, 1, 2, 0)}}
        services = {"c1.service": {"ActiveState": "failed"}}

        result = self.summarize(status, services)

        self.assertEqual(result["state"], "needs-attention")

    def test_pending_feature_conditions_prevent_completion(self):
        groups = {
            name: group_record(4, 4, 2, 2, 1 if name == "C3" else 0)
            for name in ("C1", "C2", "C3", "C4")
        }
        services = {"campaign.service": {"ActiveState": "inactive"}}

        result = self.summarize({"groups": groups}, services)

        self.assertEqual(result["state"], "incomplete")

    def test_all_completed_groups_are_complete(self):
        groups = {
            name: group_record(4, 4, 2, 2, 0)
            for name in ("C1", "C2", "C3", "C4")
        }
        services = {"campaign.service": {"ActiveState": "inactive"}}

        result = self.summarize({"groups": groups}, services)

        self.assertEqual(result["state"], "complete")

    def test_awaiting_confirmation_overrides_service_activity(self):
        status = {
            "plan_review": "awaiting-confirmation",
            "groups": {"C1": group_record(0, 1, 0, 3, 0)},
        }
        services = {"ax-direct-campaign.service": {"ActiveState": "active"}}

        result = self.summarize(status, services)

        self.assertEqual(result["state"], "awaiting-confirmation")
        self.assertEqual(result["groups"]["C1"]["total_system_runs"], 3)
        self.assertEqual(result["services"], services)


class FakePath:
    def __init__(self, *parts):
        self.parts = tuple(str(part) for part in parts)

    def __truediv__(self, other):
        return FakePath(*self.parts, other)

    def mkdir(self, **kwargs):
        pass

    def __str__(self):
        return "/".join(self.parts)


class SshOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = {
            "Path": FakePath,
            "SSH_KEY": FakePath("/private", "cluster_ed25519"),
            "_SSH_HOST_LOCKS": {"test-host": threading.Lock()},
            "_SSH_OPEN_LIMIT": threading.BoundedSemaphore(1),
            "subprocess": subprocess,
            "time": time,
        }
        cls.ssh_options = staticmethod(load_function(CLUSTER_LIB, "ssh_options", namespace))

    def test_failed_master_open_is_attempted_exactly_three_times(self):
        check = subprocess.CompletedProcess(args=["ssh", "-O", "check"], returncode=255)
        failures = [subprocess.CalledProcessError(255, ["ssh"]) for _ in range(3)]
        host = {"name": "test-host", "ip": "192.0.2.10"}

        with patch("subprocess.run", side_effect=[check, *failures]) as run, \
                patch("time.sleep"), self.assertRaises(subprocess.CalledProcessError):
            self.ssh_options(host)

        master_opens = [
            call.args[0] for call in run.call_args_list
            if "-MNf" in call.args[0]
        ]
        self.assertEqual(len(master_opens), 3)

    def test_existing_master_is_reused_without_opening_another(self):
        check = subprocess.CompletedProcess(args=["ssh", "-O", "check"], returncode=0)
        host = {"name": "test-host", "ip": "192.0.2.10"}

        with patch("subprocess.run", return_value=check) as run:
            options = self.ssh_options(host)

        self.assertEqual(run.call_count, 1)
        self.assertNotIn("-MNf", run.call_args.args[0])
        self.assertIn("-o", options)
        self.assertIn("ControlMaster=auto", options)


if __name__ == "__main__":
    unittest.main()
