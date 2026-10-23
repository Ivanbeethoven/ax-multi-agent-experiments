"""Export persisted OpenCode sessions without model calls; retain full tool attachments."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def export_trace(folder, container):
    summary = json.loads((folder / "agent-summary.json").read_text())
    destination = folder / "trace"
    destination.mkdir(exist_ok=True)
    sessions = []
    exported_parts = []
    for sid in summary["session_ids"]:
        if not sid.replace("_", "").isalnum():
            raise ValueError("Invalid session ID")
        result = container.exec_run(["sh", "-c", "/opt/opencode export " + sid + " > /agent-state/export-session.json 2>/agent-state/export-stderr.txt"])
        data = (folder / "private-agent-state/export-session.json").read_bytes()
        # Some releases emit progress before the JSON document.
        start = data.find(b"{")
        if result.exit_code or start < 0:
            raise RuntimeError("Session export failed for " + sid)
        parsed = json.loads(data[start:])
        if parsed["info"]["id"] != sid:
            raise RuntimeError("Exported session does not match event log")
        exported_parts.extend(part for message in parsed["messages"] for part in message["parts"])
        (destination / (sid + ".json")).write_text(json.dumps(parsed, ensure_ascii=False, indent=2))
        sessions.append(sid)
    state = folder / "private-agent-state"
    for source in state.rglob("tool-output"):
        if source.is_dir():
            shutil.copytree(source, destination / "attachments" / source.relative_to(state), dirs_exist_ok=True)
    files = {p.relative_to(destination).as_posix(): {"bytes": p.stat().st_size,
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in destination.rglob("*") if p.is_file() and p.name != "manifest.json"}
    events = [json.loads(line) for line in (folder / "agent-events.jsonl").read_text().splitlines() if line.strip()]
    event_tools = {e["part"]["id"] for e in events if e.get("type") == "tool_use"}
    export_tools = {p["id"] for p in exported_parts if p.get("type") == "tool"}
    missing = sorted(event_tools - export_tools)
    if missing:
        raise RuntimeError("Native export is missing recorded tools: " + str(missing))
    by_id = {p["id"]: p for p in exported_parts if p.get("type") == "tool"}
    for event in events:
        if event.get("type") == "tool_use":
            part = event["part"]
            for key in ("input", "output", "status", "error"):
                if part.get("state", {}).get(key) != by_id[part["id"]].get("state", {}).get(key):
                    raise RuntimeError("Tool content differs between event and export: " + part["id"] + ":" + key)
    manifest = {"sessions": sessions, "files": files, "event_log": "../agent-events.jsonl",
                "event_tool_count": len(event_tools), "export_tool_count": len(export_tools), "missing_event_tools": missing,
                "tool_inputs_outputs_status_verified": True,
                "native_session_export": True, "tool_output_directories_copied": True,
                "posix_or_fuse_trace": False, "raw_model_http_trace": False,
                "note": "Recorded OpenCode events and persisted session content; not a filesystem replay trace."}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return {"task": folder.name, "sessions": len(sessions), "files": len(files)}


def main():
    import docker
    parser = argparse.ArgumentParser()
    parser.add_argument("folders", nargs="+", type=Path)
    args = parser.parse_args()
    client = docker.from_env()
    binary = "/home/hxy/ax-opencode-resume-runtime/node_modules/opencode-linux-x64/bin/opencode"
    for folder in args.folders:
        image = json.loads((folder / "image.json").read_text())["id"]
        env = {"HOME": "/agent-state/home", "XDG_DATA_HOME": "/agent-state/data",
               "XDG_CONFIG_HOME": "/agent-state/config", "XDG_STATE_HOME": "/agent-state/state",
               "XDG_CACHE_HOME": "/agent-state/cache", "OPENCODE_CONFIG_DIR": "/agent-state/config/opencode",
               "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_SHARE": "1"}
        c = client.containers.run(image, ["sleep", "infinity"], detach=True,
            working_dir="/testbed", environment=env, network_disabled=True,
            volumes={binary: {"bind": "/opt/opencode", "mode": "ro"},
                     str(folder.resolve() / "private-agent-state"): {"bind": "/agent-state", "mode": "rw"}},
            labels={"ax-swe4": "trace-export"})
        try:
            print(json.dumps(export_trace(folder, c)), flush=True)
        finally:
            c.remove(force=True)


if __name__ == "__main__":
    main()
