"""No cloud calls. Exercise the handoff state machine with simulated API replies."""
import copy
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "alicloud_disk_handoff.py"
SPEC = importlib.util.spec_from_file_location("alicloud_disk_handoff", SCRIPT)
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)


def inventory():
    return {"schema_version": 1, "region_id": "cn-hangzhou", "zone_id": "cn-hangzhou-h",
            "disk_id": "d-test", "source_instance_id": "i-source", "target_instance_id": "i-target",
            "agent_id": "agent1", "owner_agent_ids": ["agent1"], "other_owners": [], "disk_per_agent": True,
            "disk_category": "cloud_essd", "disk_type": "data",
            "expected_state": {"status": "In_use", "instance_id": "i-source"},
            "ownership_tag_key": "ax-agent", "expected_tags": {"ax-agent": "agent1"},
            "expected_xfs_uuid": "12345678-1234-1234-1234-123456789abc",
            "expected_tool_id": "tool-next", "expected_tool_result_sha256": "c" * 64}


def commands():
    return {"schema_version": 1, "aliyun_argv": ["aliyun"], "cloud_timeout_s": 1,
            "poll_interval_s": 0.1, "detach_timeout_s": 0.5, "attach_timeout_s": 0.5, "total_timeout_s": 10,
            "hooks": {name: {"argv": ["hook-" + name], "timeout_s": 1, "reviewed_no_format_no_delete": True} for name in handoff.HOOKS}}


class FakeClock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class FakeCloud:
    def __init__(self, inv, clock):
        self.inv, self.clock = inv, clock
        self.calls = []
        self.disk_status = "In_use"
        self.owner = inv["source_instance_id"]
        self.transition_polls = 0
        self.forever_detaching = False
        self.wrong_attachment = False
        self.wrong_zone = False
        self.disk_overrides = {}
        self.hook_overrides = {}
        self.api_error = None

    def __call__(self, argv, payload, timeout_s, log_prefix):
        self.clock.now += min(0.01, timeout_s)
        self.calls.append(list(argv))
        if payload is not None:
            name = payload["stage"]
            target = name in {"resolve_mount_verify", "resume_tool"}
            e = {"disk_id": self.inv["disk_id"], "agent_id": self.inv["agent_id"],
                 "instance_id": self.inv["target_instance_id"] if target else self.inv["source_instance_id"]}
            if name == "quiesce":
                e.update(new_tools_blocked=True, active_tools=0, other_owners=[])
            elif name == "save_session":
                e.update(session_sha256="a" * 64, content_manifest_sha256="b" * 64,
                         session_on_disk=True, checkpoint_id="checkpoint1")
            elif name == "sync_unmount":
                e.update(synced=True, unmounted=True, active_users=0, forced=False, xfs_uuid=self.inv["expected_xfs_uuid"])
            elif name == "resolve_mount_verify":
                e.update(mounted=True, formatted=False, filesystem="xfs", xfs_uuid=self.inv["expected_xfs_uuid"],
                         content_verified=True, session_verified=True, session_sha256="a" * 64,
                         content_manifest_sha256="b" * 64, checkpoint_id="checkpoint1")
            elif name == "resume_tool":
                e.update(tool_id=self.inv["expected_tool_id"], exit_code=0,
                         result_sha256=self.inv["expected_tool_result_sha256"], correct=True, checkpoint_id="checkpoint1")
            e.update(self.hook_overrides.get(name, {}))
            return {"status": "ok", "evidence": e}
        op = argv[2]
        result = {"RequestId": f"request-{len(self.calls)}"}
        if self.api_error == op:
            return {**result, "Code": "Rejected", "Message": "secret message is not printed"}
        if op == "DescribeDisks":
            if self.disk_status in {"Detaching", "Attaching"}:
                self.transition_polls += 1
                if self.transition_polls >= 2:
                    if self.disk_status == "Detaching" and not self.forever_detaching:
                        self.disk_status, self.owner = "Available", None
                    elif self.disk_status == "Attaching":
                        self.disk_status = "In_use"
                        self.owner = self.inv["source_instance_id"] if self.wrong_attachment else self.inv["target_instance_id"]
            disk = {"DiskId": self.inv["disk_id"], "RegionId": self.inv["region_id"], "ZoneId": self.inv["zone_id"],
                    "Category": "cloud_essd", "Type": "data", "Portable": True, "DiskChargeType": "PostPaid",
                    "MultiAttach": "Disabled", "OperationLocks": {"OperationLock": []},
                    "Tags": {"Tag": [{"TagKey": "ax-agent", "TagValue": "agent1"}]},
                    "Status": self.disk_status, "InstanceId": self.owner or "",
                    "Attachments": {"Attachment": [{"InstanceId": self.owner}] if self.owner else []}}
            disk.update(copy.deepcopy(self.disk_overrides))
            return {**result, "Disks": {"Disk": [disk]}}
        if op == "DescribeInstances":
            return {**result, "Instances": {"Instance": [{"InstanceId": self.inv[key], "ZoneId": "wrong-zone" if self.wrong_zone else self.inv["zone_id"], "Status": "Running"} for key in ("source_instance_id", "target_instance_id")]}}
        if op == "DetachDisk":
            self.disk_status, self.transition_polls = "Detaching", 0
            return result
        if op == "AttachDisk":
            self.disk_status, self.owner, self.transition_polls = "Attaching", self.inv["target_instance_id"], 0
            return result
        raise AssertionError(op)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inv = inventory()
        self.cmd = commands()
        self.clock = FakeClock()
        self.cloud = FakeCloud(self.inv, self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def execute(self):
        instance = handoff.Handoff(self.inv, self.cmd, self.root / "run", transport=self.cloud,
                                   clock=self.clock, sleep=self.clock.sleep)
        return instance.execute(), instance

    def api_names(self):
        return [args[2] for args in self.cloud.calls if args[0] == "aliyun"]

    def test_default_plan_has_no_cloud_calls(self):
        path = self.root / "inventory.json"
        path.write_text(json.dumps(self.inv))
        with patch.object(handoff.SubprocessTransport, "__call__", side_effect=AssertionError("no execution allowed")), redirect_stdout(io.StringIO()) as out:
            status = handoff.main(["--inventory", str(path)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out.getvalue())["cloud_calls_performed"], 0)
        self.assertFalse((self.root / "run").exists())

    def test_complete_handoff_requires_first_correct_tool(self):
        result, instance = self.execute()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["first_correct_tool_observed"])
        self.assertGreater(result["pause_to_first_correct_tool_s"], 0)
        self.assertEqual(self.cloud.calls[-1], ["hook-resume_tool"])
        self.assertEqual(self.api_names().count("DetachDisk"), 1)
        self.assertEqual(self.api_names().count("AttachDisk"), 1)
        self.assertTrue(result["api_request_ids"])
        events = [json.loads(line) for line in (instance.output / "events.jsonl").read_text().splitlines()]
        self.assertTrue(all(e["duration_s"] >= 0 for e in events))
        for args in self.cloud.calls:
            self.assertNotIn("--Force", args)
            self.assertNotIn("DeleteDisk", args)
        for args in self.cloud.calls:
            if args[0] == "aliyun" and args[2] in {"AttachDisk", "DetachDisk"}:
                self.assertEqual(args[args.index("--DeleteWithInstance") + 1], "false")

    def test_wrong_zone_stops_before_quiesce_and_detach(self):
        self.cloud.wrong_zone = True
        result, _ = self.execute()
        self.assertEqual(result["error"], "instance_zone_or_status_mismatch")
        self.assertNotIn("DetachDisk", self.api_names())
        self.assertFalse(any(c[0].startswith("hook-") for c in self.cloud.calls))

    def test_other_agent_ownership_tag_stops_before_mutation(self):
        self.cloud.disk_overrides["Tags"] = {"Tag": [{"TagKey": "ax-agent", "TagValue": "agent2"}]}
        result, _ = self.execute()
        self.assertEqual(result["error"], "disk_ownership_tags_mismatch")
        self.assertEqual(self.api_names(), ["DescribeDisks"])

    def test_multiattach_and_shared_inventory_rejected(self):
        self.cloud.disk_overrides["MultiAttach"] = "Enabled"
        result, _ = self.execute()
        self.assertEqual(result["error"], "multiattach_not_allowed")
        self.inv["other_owners"] = ["agent2"]
        with self.assertRaises(handoff.HandoffError):
            handoff.build_plan(self.inv)

    def test_unmount_failure_stops_before_detach(self):
        self.cloud.hook_overrides["sync_unmount"] = {"unmounted": False}
        result, _ = self.execute()
        self.assertEqual(result["error"], "clean_xfs_unmount_required")
        self.assertNotIn("DetachDisk", self.api_names())

    def test_poll_timeout_preserves_failure_without_reattach(self):
        self.cloud.forever_detaching = True
        result, instance = self.execute()
        self.assertEqual(result["error"], "poll_timeout:Available")
        self.assertNotIn("AttachDisk", self.api_names())
        self.assertTrue(result["operator_inspection_required"])
        self.assertIsNone(result["pause_to_first_correct_tool_s"])
        self.assertEqual(json.loads((instance.output / "result.json").read_text())["status"], "error")

    def test_wrong_attachment_is_not_ready(self):
        self.cloud.wrong_attachment = True
        result, _ = self.execute()
        self.assertEqual(result["error"], "target_attachment_mismatch")
        self.assertNotIn(["hook-resolve_mount_verify"], self.cloud.calls)

    def test_mount_success_with_wrong_tool_is_not_success(self):
        self.cloud.hook_overrides["resume_tool"] = {"result_sha256": "d" * 64}
        result, _ = self.execute()
        self.assertEqual(result["error"], "first_correct_tool_not_observed")
        self.assertFalse(result["measurement_valid"])
        self.assertIsNone(result["pause_to_first_correct_tool_s"])
        self.assertEqual(self.api_names().count("AttachDisk"), 1, "must not attempt automatic rollback")

    def test_content_mismatch_prevents_resume(self):
        self.cloud.hook_overrides["resolve_mount_verify"] = {"session_sha256": "d" * 64}
        result, _ = self.execute()
        self.assertEqual(result["error"], "restored_state_mismatch:session_sha256")
        self.assertNotIn(["hook-resume_tool"], self.cloud.calls)

    def test_cloud_error_request_id_is_recorded(self):
        self.cloud.api_error = "DetachDisk"
        result, _ = self.execute()
        self.assertEqual(result["error"], "cloud_api_error_or_missing_request_id")
        self.assertTrue(result["api_request_ids"])
        self.assertNotIn("secret message", json.dumps(result))
        self.assertNotIn("AttachDisk", self.api_names())

    def test_missing_hook_and_existing_output_refused(self):
        del self.cmd["hooks"]["resume_tool"]
        with self.assertRaises(handoff.HandoffError):
            self.execute()
        self.assertFalse((self.root / "run").exists())
        self.cmd = commands()
        (self.root / "run").mkdir()
        with self.assertRaises(FileExistsError):
            self.execute()

    def test_subprocess_argv_is_literal(self):
        script = self.root / "echo.py"
        script.write_text("import json,sys\nprint(json.dumps({'literal':sys.argv[1]}))")
        literal = "x ; & | $(whoami) `echo test`"
        response = handoff.SubprocessTransport()([sys.executable, str(script), literal], None, 5, self.root / "literal")
        self.assertEqual(response["literal"], literal)

    def test_subprocess_timeout_is_bounded(self):
        with self.assertRaisesRegex(handoff.HandoffError, "command_timeout"):
            handoff.SubprocessTransport()([sys.executable, "-c", "import time;time.sleep(10)"], None, 0.15, self.root / "timeout")


if __name__ == "__main__":
    unittest.main()
