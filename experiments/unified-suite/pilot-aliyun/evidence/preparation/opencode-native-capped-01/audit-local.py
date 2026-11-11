#!/usr/bin/env python3
"""Read the retained local qualification; copy only non-content receipts.

This does not mount a filesystem, contact a model, restart a job or delete data.
Private transcripts/configuration remain outside this repository.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
SCRIPTS = OUT.parents[2] / "scripts"
PRIVATE = Path("/home/hxy/ax-unified-pilot-prep/qualification")
RUN = PRIVATE / "session-shared-juicefs-cap512-01"
EXECUTION = PRIVATE / "session-shared-cap512-execution-01"
CHECKPOINT = Path("/home/hxy/ax-unified-pilot-prep/inputs/opencode-checkpoint/checkpoint.json")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write(name, data):
    with (OUT / name).open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def main():
    spec = importlib.util.spec_from_file_location("review_capped", SCRIPTS / "qualify-session-shared-capped.py")
    capped = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(capped)
    assert sha((SCRIPTS / "qualify-session-shared-capped.py").read_bytes()) == read(EXECUTION / "intent.json")["script_sha256"]
    original = capped.pinned_source(SCRIPTS / "qualify-session-shared-local.py")
    assert capped.adapt(original).encode() == (RUN / "executed-source.py").read_bytes()
    for name in capped.PINNED:
        capped.pinned_source(SCRIPTS / name)
    checkpoint, result = read(CHECKPOINT), read(RUN / "result.json")
    assert result["passed"] and read(EXECUTION / "exit.json")["exit_code"] == 0
    assert sha(CHECKPOINT.read_bytes()) == result["checkpoint"]["sha256"]
    before, cold, after, final = [read(RUN / name / "export.json") for name in
                                ("export-before-stop", "cold-export", "export-after-resume", "cold-final-export")]
    prefix = checkpoint["session_prefix"]
    def msgsha(message):
        return sha(json.dumps(message, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    for export in (before, cold, after, final):
        assert export["info"]["id"] == checkpoint["session_id"]
        messages = export["messages"]
        n = len(prefix["message_ids"])
        assert [m["info"]["id"] for m in messages[:n]] == prefix["message_ids"]
        assert [msgsha(m) for m in messages[:n]] == prefix["message_hashes"]
        assert len({m["info"]["id"] for m in messages}) == len(messages)
    assert before == cold and after == final
    assert sha((RUN / "cold-export/export.json").read_bytes()) == checkpoint["session_export_sha256"]
    def tool_ids(export):
        return {p["callID"] for m in export["messages"] for p in m["parts"]
                if p.get("type") == "tool" and p.get("state", {}).get("status") == "completed"}
    new_calls = tool_ids(final) - tool_ids(before)
    events = [json.loads(line) for line in (RUN / "native-resume/stdout").read_text().splitlines()
              if line.startswith("{")]
    event_calls = {e["part"]["callID"] for e in events if e.get("type") == "tool_use"
                   and e.get("sessionID") == checkpoint["session_id"]
                   and e.get("part", {}).get("state", {}).get("status") == "completed"}
    call_shas = sorted(sha(value.encode()) for value in new_calls)
    assert len(new_calls) == 1 and event_calls == new_calls
    assert sorted(result["capped_native_proof"]["completed_call_sha256"]) == call_shas
    assert read(RUN / "native-resume/result.json")["final_sha256"] == checkpoint["expected_final_sha256"]
    closed = [read(RUN / name) for name in ("source-closed.json", "resumed-closed.json", "final-closed.json")]
    assert all(x == {"passed": True, "exit_code": 0, "mount_absent": True} for x in closed)
    exits = {p.parent.name: read(p) for p in RUN.glob("*/exit.json")}
    assert all(x["exit_code"] == 0 for x in exits.values())
    calls = [json.loads(line) for line in (RUN / "relay-calls.jsonl").read_text().splitlines()]
    assert len(calls) == 2 and calls == result["relay_calls"]
    assert all(x["status"] == 200 and x["outcome"] == "complete"
               and x["stream_completion"]["done"] and not x["automatic_retry"] for x in calls)
    process_pids = {p.parent.name: read(p)["pid"] for p in RUN.glob("*/process.json")}
    pid_paths_present = {name: Path("/proc", str(pid)).exists() for name, pid in process_pids.items()}
    groups = sorted(p.name for p in Path("/sys/fs/cgroup").glob("ax-c4-capped-*"))
    visible_fuse_mounts = [line.split()[4] for line in Path("/proc/self/mountinfo").read_text().splitlines()
                          if " - fuse.juicefs " in line and line.split()[4] == "/srv/ax-pilot-session"]
    assert not any(pid_paths_present.values()) and not groups and not visible_fuse_mounts
    selected = ["result.json", "started.json", "source-closed.json", "resumed-closed.json", "final-closed.json",
                "relay-calls.jsonl", "relay-reservations.jsonl", "native-resume/result.json", "native-resume/exit.json"]
    exact_copies = []
    def copy(path, relative):
        target = OUT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        assert not target.exists()
        shutil.copyfile(path, target)
        assert path.read_bytes() == target.read_bytes()
        exact_copies.append({"source": str(path), "published": relative, "sha256": sha(path.read_bytes()),
                             "bytes": path.stat().st_size})
    for name in selected:
        copy(RUN / name, "receipts/" + name)
    copy(EXECUTION / "exit.json", "receipts/outer-exit.json")
    copy(EXECUTION / "intent.json", "receipts/outer-intent.json")
    copy(PRIVATE / "session-shared-cap512-fuse-permission-01.json", "receipts/fuse-permission.json")
    copy(PRIVATE / "local-services-03-closure-audit-01/result.json", "receipts/local-services-current.json")
    raw_sources = []
    for directory in (RUN, EXECUTION):
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                raw_sources.append(dict(source=str(path), bytes=path.stat().st_size, sha256=sha(path.read_bytes()),
                                        content_published=any(str(path) == x["source"] for x in exact_copies)))
    service_status = PRIVATE / "local-services-03-closure-audit-01/result.json"
    raw_sources.append(dict(source=str(service_status), bytes=service_status.stat().st_size,
                            sha256=sha(service_status.read_bytes()), content_published=True))
    write("source-manifest.json", dict(schema="local-native-resume-audit-v1", raw_sources=raw_sources,
                                       exact_copies=exact_copies,
                                       checkpoint_sha256=sha(CHECKPOINT.read_bytes()),
                                       scripts=[dict(path=str(SCRIPTS / name), sha256=sha((SCRIPTS / name).read_bytes()),
                                                     normalized_sha256=sha((SCRIPTS / name).read_bytes().replace(b"\r\n", b"\n")))
                                                for name in ["qualify-session-shared-capped.py", *capped.PINNED]]))
    usage = [dict(type=e["type"], tokens=e["part"].get("tokens")) for e in events
             if e.get("type") == "step_finish" and isinstance(e.get("part", {}).get("tokens"), dict)]
    write("audit.json", dict(at=datetime.now(timezone.utc).isoformat(), passed=True,
          independent_checks=dict(executed_cap_script_matches_outer_intent=True,
              executed_adapter_matches_pinned_source=True, original_history_prefix_unchanged=True,
              before_and_cold_exports_equal=True, after_and_final_exports_equal=True,
              native_event_and_cold_export_new_completed_call_ids_equal=True,
              completed_call_sha256=call_shas, final_hash_matches_independent_checkpoint=True,
              normal_unmount_receipts=3, all_native_and_mount_command_exits_zero=True,
              model_requests=2),
          current_state_observation=dict(original_process_pid_paths_present=pid_paths_present,
              sandbox_cgroup_directories_remaining=groups, canonical_fuse_mount_in_audit_namespace=visible_fuse_mounts,
              scope="Current absence only; cannot establish past normal exit or its cause. The three original closed/command exit receipts establish the recorded normal unmounts."),
          runtime_checks_read_from_result=dict(cold_sqlite_tool_matches=result["capped_native_proof"]["cold_sqlite_tool_matches"],
              cold_full_resumed_workspace_equal=result["cold_full_resumed_workspace_equal"],
              original_checkpoint_unchanged=result["original_checkpoint_unchanged"],
              original_canonical_parent_restored=result["original_canonical_parent_restored"]),
          native_reported_usage=usage,
          scope="Same WSL host, empty HOME, original native DB on JuiceFS, normal unmount/remount; not cross-host or performance.",
          no_new_model_calls=True, no_mounts_or_service_restarts=True, no_data_deletion=True))
    print(json.dumps(dict(passed=True, exact_copies=len(exact_copies), raw_source_hashes=len(raw_sources),
                          new_completed_tools=len(new_calls), model_requests=len(calls))))


if __name__ == "__main__":
    main()
