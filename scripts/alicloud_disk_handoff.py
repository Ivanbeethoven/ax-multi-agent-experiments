"""Plan, or explicitly execute, one ordinary ESSD/XFS disk handoff.

Default: --inventory inventory.json [--commands commands.json] prints a plan and
makes ZERO cloud calls. --execute additionally requires --commands and --output
pointing to a new directory. No provisioning, formatting, force, rollback, deletion
or automatic mutation retry exists. A failed run requires operator inspection.

Inventory schema 1 requires: region_id, zone_id, disk_id, source_instance_id,
target_instance_id, agent_id, owner_agent_ids:[agent_id], other_owners:[],
disk_per_agent:true, disk_category:cloud_essd, disk_type:data,
expected_state:{status:In_use,instance_id:<source>}, ownership_tag_key,
expected_tags:{<ownership_tag_key>:<agent_id>}, expected_xfs_uuid,
expected_tool_id and expected_tool_result_sha256. Existing ECS resources only.

Commands schema 1: aliyun_argv:["aliyun"], cloud_timeout_s:60,
poll_interval_s:2, detach_timeout_s:180, attach_timeout_s:180,
total_timeout_s:900, hooks:{<name>:{argv:[...],timeout_s:120,
reviewed_no_format_no_delete:true}}. Required hooks are quiesce, save_session,
sync_unmount, resolve_mount_verify, resume_tool. Credentials stay in the CLI
profile/environment. Hooks receive one JSON request on stdin and emit one JSON
object {status:"ok",evidence:{...}}; see validate_hook_evidence for exact receipts.
Hooks must synchronously finish remote commands and enforce their own remote
timeout/cancellation. Killing a local SSH client cannot prove remote termination.

API field references, checked 2026-10-23:
https://www.alibabacloud.com/help/en/ecs/developer-reference/api-ecs-2014-05-26-describedisks
https://www.alibabacloud.com/help/en/ecs/developer-reference/api-ecs-2014-05-26-attachdisk
https://www.alibabacloud.com/help/en/ecs/developer-reference/api-ecs-2014-05-26-detachdisk
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

HOOKS = ("quiesce", "save_session", "sync_unmount", "resolve_mount_verify", "resume_tool")
HASH = re.compile(r"[0-9a-f]{64}")
MAX_JSON = 8 * 1024 * 1024


class HandoffError(Exception):
    def __init__(self, code, request_id=None):
        self.code = code
        self.request_id = request_id
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise HandoffError(code)


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def validate_inventory(inv):
    require(isinstance(inv, dict) and inv.get("schema_version") == 1, "inventory_schema")
    for key in ("region_id", "zone_id", "disk_id", "source_instance_id", "target_instance_id", "agent_id", "expected_tool_id"):
        require(isinstance(inv.get(key), str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", inv[key]), "inventory_" + key)
    require(inv["disk_id"].startswith("d-") and all(inv[k].startswith("i-") for k in ("source_instance_id", "target_instance_id")), "inventory_resource_ids")
    require(inv["source_instance_id"] != inv["target_instance_id"], "source_equals_target")
    require(inv.get("owner_agent_ids") == [inv["agent_id"]] and inv.get("other_owners") == [] and inv.get("disk_per_agent") is True, "single_agent_disk_required")
    require(inv.get("disk_category") == "cloud_essd" and inv.get("disk_type") == "data", "ordinary_essd_data_disk_required")
    require(inv.get("expected_state") == {"status": "In_use", "instance_id": inv["source_instance_id"]}, "expected_initial_state_required")
    tags = inv.get("expected_tags")
    key = inv.get("ownership_tag_key")
    require(isinstance(tags, dict) and isinstance(key, str) and tags.get(key) == inv["agent_id"] and
            all(isinstance(k, str) and isinstance(v, str) for k, v in tags.items()), "ownership_tag_required")
    require(isinstance(inv.get("expected_xfs_uuid"), str) and re.fullmatch(r"[a-fA-F0-9-]{16,64}", inv["expected_xfs_uuid"]), "expected_xfs_uuid_required")
    require(isinstance(inv.get("expected_tool_result_sha256"), str) and HASH.fullmatch(inv["expected_tool_result_sha256"]), "expected_tool_result_hash_required")


def validate_argv(argv):
    require(isinstance(argv, list) and argv and all(isinstance(a, str) and a and "\x00" not in a for a in argv), "argv_array_required")
    executable = Path(argv[0]).name.lower()
    require(executable not in {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}
            and not executable.endswith((".cmd", ".bat", ".ps1")), "shell_command_not_allowed")


def validate_commands(commands):
    require(isinstance(commands, dict) and commands.get("schema_version") == 1, "commands_schema")
    validate_argv(commands.get("aliyun_argv"))
    for key in ("cloud_timeout_s", "poll_interval_s", "detach_timeout_s", "attach_timeout_s", "total_timeout_s"):
        require(positive(commands.get(key)), "positive_timeout_required:" + key)
    require(commands["poll_interval_s"] <= 60, "poll_interval_too_long")
    for name in HOOKS:
        hook = commands.get("hooks", {}).get(name, {})
        validate_argv(hook.get("argv"))
        require(positive(hook.get("timeout_s")), "hook_timeout_required:" + name)
        require(hook.get("reviewed_no_format_no_delete") is True, "reviewed_hook_required:" + name)


def build_plan(inv, commands=None):
    validate_inventory(inv)
    configured = False
    if commands is not None:
        try:
            validate_commands(commands)
            configured = True
        except HandoffError:
            pass
    return {"schema_version": 1, "mode": "plan", "cloud_calls_performed": 0,
            "disk_id": inv["disk_id"], "source_instance_id": inv["source_instance_id"],
            "target_instance_id": inv["target_instance_id"], "zone_id": inv["zone_id"],
            "hooks_configured": configured, "steps": [
                "DescribeDisks: exact disk, owner tag, ordinary ESSD, data, portable, PostPaid, source attachment",
                "DescribeInstances: both exact instances, same zone, Running, unlocked",
                "quiesce: stop new tools and finish active tools",
                "save_session: save AX session and content manifest on this disk",
                "sync_unmount: sync and normally unmount XFS; no active users",
                "DescribeDisks: recheck expected source attachment before detaching",
                "DetachDisk(DeleteWithInstance=false)", "poll DescribeDisks: Available and no attachments",
                "DescribeInstances: recheck target zone and Running", "AttachDisk(DeleteWithInstance=false,Bootable=false)",
                "poll DescribeDisks: In_use and actual InstanceId equals target",
                "resolve_mount_verify: identify disk and existing XFS UUID; mount; verify content and saved session",
                "resume_tool: run the specified next tool; require its expected result hash"],
            "timing": "one controller monotonic clock; pause begins before quiesce; ends at first correct tool receipt",
            "limitations": ["No cloud call until --execute.", "Operator must supply tested synchronous AX/host hooks.",
                            "A failed or timed-out hook may have changed remote state; no automatic retry or rollback."]}


def kill_tree(process):
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10, check=False)
        finally:
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class SubprocessTransport:
    def __call__(self, argv, payload, timeout_s, log_prefix):
        validate_argv(argv)
        stdout_path = log_prefix.with_suffix(".stdout.json")
        stderr_path = log_prefix.with_suffix(".stderr.log")
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            if os.name != "nt":
                stdout_path.chmod(0o600)
                stderr_path.chmod(0o600)
            kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, shell=False, **kwargs)
            try:
                proc.communicate(None if payload is None else json.dumps(payload, allow_nan=False).encode(), timeout=timeout_s)
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                proc.wait(timeout=10)
                raise HandoffError("command_timeout_remote_state_requires_inspection")
        require(proc.returncode == 0, "command_nonzero_exit")
        require(stdout_path.stat().st_size <= MAX_JSON, "response_too_large")
        try:
            data = json.loads(stdout_path.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise HandoffError("invalid_json_response")
        require(isinstance(data, dict), "response_object_required")
        return data


def unlocked(obj):
    locks = obj.get("OperationLocks", {}).get("OperationLock", [])
    require(isinstance(locks, list) and not locks, "resource_locked_or_lock_state_unknown")


def validate_disk(response, inv):
    disks = response.get("Disks", {}).get("Disk", [])
    require(isinstance(disks, list) and len(disks) == 1, "exact_disk_response_required")
    disk = disks[0]
    for cloud_key, key in (("DiskId", "disk_id"), ("RegionId", "region_id"), ("ZoneId", "zone_id"), ("Category", "disk_category"), ("Type", "disk_type")):
        require(disk.get(cloud_key) == inv[key], "disk_mismatch:" + cloud_key)
    require(disk.get("Portable") is True and disk.get("DiskChargeType") == "PostPaid", "portable_postpaid_disk_required")
    require(disk.get("MultiAttach") == "Disabled", "multiattach_not_allowed")
    require(not disk.get("MountInstances", {}).get("MountInstance", []), "legacy_shared_disk_not_allowed")
    unlocked(disk)
    tags = {tag.get("TagKey"): tag.get("TagValue") for tag in disk.get("Tags", {}).get("Tag", [])}
    require(all(tags.get(key) == value for key, value in inv["expected_tags"].items()), "disk_ownership_tags_mismatch")
    attachments = disk.get("Attachments", {}).get("Attachment", [])
    require(isinstance(attachments, list), "invalid_attachment_list")
    ids = [a.get("InstanceId") for a in attachments]
    require(all(isinstance(i, str) and i for i in ids) and len(ids) <= 1, "multiple_or_invalid_attachments")
    actual = disk.get("InstanceId") or None
    require(not ids or actual == ids[0], "attachment_instance_disagreement")
    require(actual in {None, inv["source_instance_id"], inv["target_instance_id"]}, "unexpected_disk_owner")
    return disk, actual, ids


def validate_hook_evidence(name, response, inv, checkpoint):
    require(response.get("status") == "ok" and isinstance(response.get("evidence"), dict), "hook_receipt_required:" + name)
    e = response["evidence"]
    target = name in {"resolve_mount_verify", "resume_tool"}
    for key, expected in (("disk_id", inv["disk_id"]), ("agent_id", inv["agent_id"]),
                          ("instance_id", inv["target_instance_id"] if target else inv["source_instance_id"])):
        require(e.get(key) == expected, "hook_identity_mismatch:" + name + ":" + key)
    if name == "quiesce":
        require(e.get("new_tools_blocked") is True and e.get("active_tools") == 0 and
                e.get("other_owners") == [], "source_not_quiescent")
    elif name == "save_session":
        for key in ("session_sha256", "content_manifest_sha256"):
            require(isinstance(e.get(key), str) and HASH.fullmatch(e[key]), "saved_hash_required:" + key)
        require(e.get("session_on_disk") is True and isinstance(e.get("checkpoint_id"), str) and e["checkpoint_id"], "saved_session_receipt_required")
    elif name == "sync_unmount":
        require(e.get("synced") is True and e.get("unmounted") is True and e.get("active_users") == 0
                and e.get("forced") is False and e.get("xfs_uuid") == inv["expected_xfs_uuid"], "clean_xfs_unmount_required")
    elif name == "resolve_mount_verify":
        require(e.get("mounted") is True and e.get("formatted") is False and e.get("filesystem") == "xfs"
                and e.get("xfs_uuid") == inv["expected_xfs_uuid"], "existing_xfs_mount_required")
        for key in ("session_sha256", "content_manifest_sha256", "checkpoint_id"):
            require(e.get(key) == checkpoint.get(key), "restored_state_mismatch:" + key)
        require(e.get("content_verified") is True and e.get("session_verified") is True, "restored_state_not_verified")
    elif name == "resume_tool":
        require(e.get("tool_id") == inv["expected_tool_id"] and e.get("exit_code") == 0
                and e.get("result_sha256") == inv["expected_tool_result_sha256"] and e.get("correct") is True
                and e.get("checkpoint_id") == checkpoint.get("checkpoint_id"), "first_correct_tool_not_observed")
    return e


class Handoff:
    def __init__(self, inv, commands, output, *, transport=None, clock=time.monotonic, sleep=time.sleep):
        validate_inventory(inv)
        validate_commands(commands)
        self.inv, self.commands = inv, commands
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.logs = self.output / "logs"
        self.logs.mkdir(mode=0o700)
        self.transport, self.clock, self.sleep = transport or SubprocessTransport(), clock, sleep
        self.started = clock()
        self.deadline = self.started + commands["total_timeout_s"]
        self.events, self.checkpoint, self.pause_started = [], {}, None

    def stage(self, name, action):
        begin = self.clock()
        event = {"stage": name, "started_monotonic_s": begin, "started_offset_s": begin - self.started,
                 "duration_s": None, "status": "error", "error": None, "api_request_id": None}
        try:
            require(begin < self.deadline, "total_timeout")
            result = action()
            event["status"] = "ok"
            if isinstance(result, dict):
                event["api_request_id"] = result.get("RequestId")
                event["receipt"] = result.get("evidence")
            return result
        except HandoffError as exc:
            event["error"] = exc.code
            event["api_request_id"] = exc.request_id
            raise
        except Exception as exc:
            event["error"] = type(exc).__name__
            raise HandoffError(type(exc).__name__)
        finally:
            event["duration_s"] = self.clock() - begin
            self.events.append(event)
            with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def invoke(self, name, argv, payload, timeout):
        remaining = min(timeout, self.deadline - self.clock())
        require(remaining > 0, "total_timeout")
        return self.transport(argv, payload, remaining, self.logs / f"{len(self.events):04d}-{name}")

    def api(self, operation, parameters, timeout_s=None):
        def action():
            argv = self.commands["aliyun_argv"] + ["ecs", operation, "--region", self.inv["region_id"]]
            for key, value in parameters.items():
                argv.extend(["--" + key, str(value)])
            result = self.invoke(operation, argv, None, timeout_s or self.commands["cloud_timeout_s"])
            if result.get("Code") or not isinstance(result.get("RequestId"), str) or not result["RequestId"]:
                raise HandoffError("cloud_api_error_or_missing_request_id", result.get("RequestId"))
            return result
        return self.stage(operation, action)

    def disk(self, timeout_s=None):
        response = self.api("DescribeDisks", {"RegionId": self.inv["region_id"], "DiskIds": json.dumps([self.inv["disk_id"]])}, timeout_s)
        return validate_disk(response, self.inv)

    def instances(self):
        ids = [self.inv["source_instance_id"], self.inv["target_instance_id"]]
        response = self.api("DescribeInstances", {"RegionId": self.inv["region_id"], "InstanceIds": json.dumps(ids)})
        instances = response.get("Instances", {}).get("Instance", [])
        require(len(instances) == 2 and {i.get("InstanceId") for i in instances} == set(ids), "exact_instances_required")
        for instance in instances:
            require(instance.get("ZoneId") == self.inv["zone_id"] and instance.get("Status") == "Running", "instance_zone_or_status_mismatch")
            unlocked(instance)

    def hook(self, name):
        spec = self.commands["hooks"][name]
        def action():
            response = self.invoke(name, spec["argv"], {"schema_version": 1, "stage": name,
                                   "inventory": self.inv, "checkpoint": self.checkpoint}, spec["timeout_s"])
            validate_hook_evidence(name, response, self.inv, self.checkpoint)
            return response
        return self.stage(name, action)["evidence"]

    def wait_disk(self, status, instance, timeout):
        deadline = min(self.deadline, self.clock() + timeout)
        allowed = {"Available", "Detaching", "In_use"} if status == "Available" else {"Available", "Attaching", "In_use"}
        while self.clock() < deadline:
            disk, actual, attachments = self.disk(min(self.commands["cloud_timeout_s"], deadline - self.clock()))
            require(disk.get("Status") in allowed, "unexpected_disk_transition")
            if disk.get("Status") == status and actual == instance and (instance is not None or not attachments):
                return
            # A completed attachment to the wrong owner is not a polling delay.
            if status == "In_use" and disk.get("Status") == "In_use":
                raise HandoffError("target_attachment_mismatch")
            if status == "Available" and actual == self.inv["target_instance_id"]:
                raise HandoffError("disk_moved_by_other_actor")
            self.sleep(min(self.commands["poll_interval_s"], max(0, deadline - self.clock())))
        raise HandoffError("poll_timeout:" + status)

    def execute(self):
        result = {"schema_version": 1, "mode": "execute", "status": "error", "error": None,
                  "disk_id": self.inv["disk_id"], "pause_to_first_correct_tool_s": None,
                  "measurement_valid": False, "automatic_rollback": False,
                  "started_utc": datetime.now(timezone.utc).isoformat()}
        try:
            disk, actual, _ = self.disk()
            require(disk.get("Status") == "In_use" and actual == self.inv["source_instance_id"], "initial_disk_state_changed")
            self.stage("validate_instances", self.instances)
            self.pause_started = self.clock()
            self.hook("quiesce")
            self.checkpoint = self.hook("save_session")
            self.hook("sync_unmount")
            disk, actual, _ = self.disk()
            require(disk.get("Status") == "In_use" and actual == self.inv["source_instance_id"], "disk_changed_before_detach")
            self.api("DetachDisk", {"InstanceId": self.inv["source_instance_id"], "DiskId": self.inv["disk_id"], "DeleteWithInstance": "false"})
            self.stage("wait_available", lambda: self.wait_disk("Available", None, self.commands["detach_timeout_s"]))
            self.stage("revalidate_instances", self.instances)
            self.api("AttachDisk", {"InstanceId": self.inv["target_instance_id"], "DiskId": self.inv["disk_id"], "DeleteWithInstance": "false", "Bootable": "false"})
            self.stage("wait_target_in_use", lambda: self.wait_disk("In_use", self.inv["target_instance_id"], self.commands["attach_timeout_s"]))
            self.hook("resolve_mount_verify")
            self.hook("resume_tool")
            result.update(status="ok", pause_to_first_correct_tool_s=self.clock() - self.pause_started,
                          measurement_valid=True, first_correct_tool_observed=True)
        except Exception as exc:
            error = exc.code if isinstance(exc, HandoffError) else type(exc).__name__
            result["error"] = error
            result["operator_inspection_required"] = True
            # Validations immediately after an API response are separate from API
            # transport success and must also appear in the event history.
            self.events.append({"stage": "handoff_stopped", "started_monotonic_s": self.clock(),
                                "started_offset_s": self.clock() - self.started, "duration_s": 0,
                                "status": "error", "error": error, "api_request_id": getattr(exc, "request_id", None)})
            with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(self.events[-1]) + "\n")
        finally:
            result["elapsed_s"] = self.clock() - self.started
            result["last_successful_stage"] = next((e["stage"] for e in reversed(self.events) if e["status"] == "ok"), None)
            result["api_request_ids"] = [e["api_request_id"] for e in self.events if e.get("api_request_id")]
            result["inventory_sha256"] = hashlib.sha256(json.dumps(self.inv, sort_keys=True).encode()).hexdigest()
            result["commands_sha256"] = hashlib.sha256(json.dumps(self.commands, sort_keys=True).encode()).hexdigest()
            (self.output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--commands", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        inv = json.loads(args.inventory.read_text(encoding="utf-8"))
        commands = json.loads(args.commands.read_text(encoding="utf-8")) if args.commands else None
        plan = build_plan(inv, commands)
        if not args.execute:
            print(json.dumps(plan, indent=2))
            return 0
        require(args.output is not None and commands is not None, "execute_requires_commands_and_new_output")
        handoff = Handoff(inv, commands, args.output)
        (handoff.output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        result = handoff.execute()
        print(json.dumps({"status": result["status"], "result_file": str(handoff.output / "result.json"), "error": result["error"]}))
        return 0 if result["status"] == "ok" else 2
    except (HandoffError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, HandoffError) else type(exc).__name__
        print(json.dumps({"status": "rejected", "error": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
