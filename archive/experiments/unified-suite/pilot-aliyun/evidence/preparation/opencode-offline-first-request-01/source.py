#!/usr/bin/env python3
"""Capture only aggregate fields from one pinned native OpenCode resume request.

Run as Linux root (for namespaces), with the original checkpoint archive,
original native-resume started.json, and a new result path. OpenCode itself
runs unprivileged. No provider configuration or real credential is accepted.
"""
import argparse
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import resource
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time


MODEL = "experiment/deepseek-v4-flash"
PORT = 18443
FAKE_KEY = "offline-diagnostic-placeholder"
ROLES = {"system", "developer", "user", "assistant", "tool", "function"}
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


class ProbeError(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise ProbeError(reason)


def digest(path):
    with path.open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def compact_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def summarize(body):
    obj = json.loads(body)
    messages = obj["messages"]
    tools = obj.get("tools", [])
    require(obj["model"] == MODEL.split("/")[1] and isinstance(messages, list)
            and isinstance(tools, list) and type(obj["stream"]) is bool, "unexpected request shape")
    roles = [row.get("role") for row in messages]
    role_counts = Counter(r if isinstance(r, str) and r in ROLES else "other" for r in roles)
    limits = {}
    for field in ("max_tokens", "max_completion_tokens"):
        value = obj.get(field)
        require(value is None or type(value) is int, "non-numeric token limit")
        limits[field] = {"present": field in obj, "value": value}
    choice = obj.get("tool_choice")
    if isinstance(choice, dict):
        choice = "object"
    elif choice not in (None, "auto", "none", "required"):
        choice = "other"
    thinking = obj.get("thinking")
    if isinstance(thinking, dict):
        thinking = thinking.get("type")
        if thinking not in ("enabled", "disabled", "auto", "adaptive"):
            thinking = "other"
    elif thinking is not None and type(thinking) is not bool:
        thinking = "other"
    return {"body_bytes": len(body), "messages": len(messages),
            "messages_compact_json_bytes": compact_size(messages), "roles": dict(role_counts),
            "tools": len(tools), "tools_compact_json_bytes": compact_size(tools),
            "tool_choice": choice, "stream": obj["stream"], "thinking": thinking,
            "thinking_present": "thinking" in obj, **limits}


class FirstRequest(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.server.received = True
        self.connection.settimeout(2)
        try:
            length = int(self.headers.get("Content-Length", 0))
            require(self.path == "/v1/chat/completions"
                    and self.client_address[0] == "127.0.0.1"
                    and self.headers.get("Authorization") == f"Bearer {FAKE_KEY}"
                    and 0 < length <= 512 * 1024, "unexpected offline HTTP request")
            body = self.rfile.read(length)
            require(len(body) == length, "incomplete offline HTTP request")
            self.server.stats = summarize(body)
        except (KeyError, ValueError, TypeError, AttributeError, OSError):
            self.server.stats = None
        response = b'{"error":{"message":"Offline diagnostic complete","type":"invalid_request_error"}}'
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)
        self.wfile.flush()
        self.server.sent_400 = True


def command(args, check=True):
    return subprocess.run(args, check=check, env=SAFE_ENV, capture_output=True)


def namespaces():
    return {name: os.readlink(f"/proc/self/ns/{name}") for name in ("net", "mnt", "pid")}


def network_guard(parent_ns):
    own = namespaces()
    require(os.getpid() == 1 and all(own[k] != parent_ns[k] for k in own),
            "private network, mount and PID namespaces required")
    command(["ip", "link", "set", "lo", "up"])
    interfaces = [row["ifname"] for row in json.loads(command(["ip", "-j", "link"]).stdout)]
    require(interfaces == ["lo"], "unexpected network interface")
    route_counts = {}
    for version, sample in (("-4", "198.51.100.1"), ("-6", "2001:db8::1")):
        routes = json.loads(command(["ip", "-j", version, "route", "show", "table", "main"]).stdout)
        route_counts[version] = len(routes)
        require(not routes and command(["ip", version, "route", "get", sample], False).returncode != 0,
                "unexpected non-loopback route")
    return {"private_net_mount_pid": True, "interfaces": interfaces, "main_routes": route_counts,
            "external_route_lookups_failed": True}


def copy_checkpoint(archive, destination):
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        names = [Path(m.name) for m in members]
        require(len(names) == len(set(names)) and all(
            not p.is_absolute() and ".." not in p.parts
            and (p == Path("checkpoint.json") or (p.parts and p.parts[0] == "workspace"))
            and (m.isdir() or m.isreg()) for p, m in zip(names, members)),
            "unexpected archive member")
        require(sum(m.size for m in members) <= 64 * 1024 * 1024, "checkpoint exceeds local limit")
        bundle.extractall(destination, members, filter="data")


def project_files(project):
    return {str(p.relative_to(project)): digest(p) for p in project.rglob("*")
            if ".git" not in p.relative_to(project).parts and p.is_file() and not p.is_symlink()}


def history_guard(workspace, checkpoint):
    exported = workspace / "session-export.json"
    require(digest(exported) == checkpoint["session_export_sha256"], "session export pin mismatch")
    history = json.loads(exported.read_bytes())
    session_id = checkpoint["session_id"]
    require(history["info"]["id"] == session_id, "session ID mismatch")
    messages = history["messages"]
    expected_messages = {m["info"]["id"]: m["info"]["role"] for m in messages}
    expected_tools = {p["id"]: p["state"]["status"] for m in messages for p in m["parts"]
                      if p["type"] == "tool"}
    require(messages and messages[-1]["info"]["role"] == "assistant"
            and messages[-1]["info"].get("time", {}).get("completed")
            and all(state in ("completed", "error") for state in expected_tools.values()),
            "unclosed session history")
    db = workspace / "source/data/opencode/opencode.db"
    require(db.with_name("opencode.db-wal").stat().st_size == 0, "unexpected nonempty WAL")
    with sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        require(conn.execute("PRAGMA quick_check").fetchall() == [("ok",)], "SQLite check failed")
        require(conn.execute("SELECT id FROM session").fetchall() == [(session_id,)],
                "unexpected persisted session")
        db_messages = dict(conn.execute("SELECT id,json_extract(data,'$.role') FROM message WHERE session_id=?",
                                        (session_id,)))
        db_tools = dict(conn.execute("SELECT id,json_extract(data,'$.state.status') FROM part"
                                     " WHERE json_extract(data,'$.type')='tool'"))
        require(db_messages == expected_messages and db_tools == expected_tools,
                "database disagrees with the pinned export")
    return {"stored_messages": len(expected_messages), "closed_tool_parts": len(expected_tools),
            "pending_tool_parts": 0, "export_sha256": digest(exported), "sqlite_quick_check": True}


def limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))


def collect(binary, argv, env, project):
    with HTTPServer(("127.0.0.1", PORT), FirstRequest) as server:
        server.timeout = 0.2
        server.received = server.sent_400 = False
        server.stats = None
        start = time.monotonic()
        process = subprocess.Popen(
            ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups",
             "--bounding-set=-all", "--no-new-privs", str(binary), *argv],
            cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, preexec_fn=limits)
        try:
            while not server.received and process.poll() is None and time.monotonic() - start < 20:
                server.handle_request()
            elapsed = time.monotonic() - start
        finally:
            # Stop accepting HTTP before allowing OpenCode to exit; there is no retry target.
            server.server_close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                break
        except ChildProcessError:
            break
    require(sorted(p for p in os.listdir("/proc") if p.isdecimal()) == ["1"],
            "unexpected surviving process in private PID namespace")
    require(server.stats is not None and server.sent_400,
            f"first request not captured (client exit {process.returncode})")
    return server.stats, {"local_requests": 1, "upstream_requests": 0, "diagnostic_http_status": 400,
                          "to_first_request_seconds": round(elapsed, 6), "client_exit": process.returncode,
                          "surviving_child_processes": 0}


def run_isolated(args, parent_ns):
    evidence = network_guard(parent_ns)
    require(digest(args.checkpoint) == args.checkpoint_sha256, "checkpoint archive pin mismatch")
    require(digest(args.started) == args.started_sha256, "native argv receipt pin mismatch")
    original = json.loads(args.started.read_bytes())
    binary = Path(original["binary"]["path"])
    require(binary.is_absolute() and digest(binary) == original["binary"]["sha256"],
            "original OpenCode binary pin mismatch")
    with tempfile.TemporaryDirectory(prefix="opencode-first-request-", dir="/var/tmp") as temp:
        temp = Path(temp)
        copy_checkpoint(args.checkpoint, temp)
        workspace = temp / "workspace"
        checkpoint = json.loads((temp / "checkpoint.json").read_bytes())
        require(checkpoint["opencode_binary_sha256"] == original["binary"]["sha256"]
                and checkpoint["source_process_exited"] is True and checkpoint["model"] == MODEL,
                "original checkpoint binary or session mismatch")
        sid = checkpoint["session_id"]
        argv = ["run", "--format", "json", "--model", MODEL, "--agent", "build",
                "--session", sid, checkpoint["resume_prompt"]]
        require(original["argv"] == [str(binary), *argv], "original resume argv mismatch")
        canonical = Path(checkpoint["canonical_workspace"])
        project = Path(checkpoint["canonical_project"])
        anchor = canonical.parent
        require(anchor.parent == Path("/srv") and anchor.is_dir() and anchor.resolve() == anchor
                and canonical == anchor / "workspace" and project == canonical / "repo",
                "unexpected canonical workspace")
        state = original["session_state"]
        config_path = canonical / "config/opencode.json"
        require((state["workspace"], state["project"], state["config"]) ==
                (str(canonical), str(project), str(config_path)), "original workspace or config mismatch")
        xdg = {f"XDG_{name.upper()}_HOME": str(canonical / "source" / name)
               for name in ("data", "state", "cache", "config")}
        require(state["xdg"] == {name: str(canonical / "source" / name)
                                 for name in ("data", "state", "cache", "config")},
                "original XDG layout mismatch")
        history = history_guard(workspace, checkpoint)
        expected_project = project_files(workspace / "repo")
        config = workspace / "config/opencode.json"
        settings = json.loads(config.read_bytes())
        provider = settings["provider"]["experiment"]
        require(set(settings["provider"]) == {"experiment"} and settings["model"] == MODEL
                and set(provider["options"]) == {"baseURL", "apiKey"}, "unexpected checkpoint provider")
        provider["options"].update(baseURL=f"http://127.0.0.1:{PORT}/v1", apiKey=FAKE_KEY)
        config.write_text(json.dumps(settings))
        home = temp / "empty-home"
        home.mkdir(mode=0o700)
        for directory, dirs, files in os.walk(workspace):
            os.chown(directory, 65534, 65534)
            for name in files:
                os.chown(Path(directory) / name, 65534, 65534)
        os.chown(home, 65534, 65534)
        temp.chmod(0o711)
        # Bind only the copy at the canonical original location; isolate runtime IPC and temp.
        command(["mount", "--bind", str(temp), str(anchor)])
        require((canonical.stat().st_dev, canonical.stat().st_ino) ==
                (workspace.stat().st_dev, workspace.stat().st_ino), "canonical bind verification failed")
        command(["mount", "-t", "tmpfs", "-o", "mode=755,nosuid,nodev,noexec,size=8m", "tmpfs", "/run"])
        command(["mount", "-t", "tmpfs", "-o", "mode=1777,nosuid,nodev,noexec,size=32m", "tmpfs", "/tmp"])
        home = anchor / "empty-home"
        require(not any(home.iterdir()), "HOME is not empty")
        env = dict(PATH="/usr/local/bin:/usr/bin:/bin", HOME=str(home), USERPROFILE=str(home),
                   TMPDIR="/tmp", OPENCODE_CONFIG=str(config_path),
                   OPENCODE_DISABLE_AUTOUPDATE="1", OPENCODE_DISABLE_SHARE="1",
                   OPENCODE_DISABLE_LSP_DOWNLOAD="1", OPENCODE_DISABLE_DEFAULT_PLUGINS="true",
                   OPENCODE_DISABLE_EXTERNAL_SKILLS="true", OPENCODE_DISABLE_CLAUDE_CODE="true",
                   OPENCODE_DISABLE_MODELS_FETCH="true", NO_COLOR="1",
                   NO_PROXY="127.0.0.1,localhost,10.20.0.10", no_proxy="127.0.0.1,localhost,10.20.0.10",
                   **xdg)
        summary, process = collect(binary, argv, env, project)
        require(project_files(project) == expected_project, "project files changed during diagnostic")
    require(digest(args.checkpoint) == args.checkpoint_sha256
            and digest(args.started) == args.started_sha256, "original input changed")
    return dict(schema="opencode-offline-first-request-v1", passed=True, network=evidence,
                original_argv_unchanged=True, canonical_workspace_unchanged=True, empty_home=True,
                original_tool_permissions_unchanged=True, fake_credential_verified=True,
                original_checkpoint_unchanged=True, copied_project_files_unchanged=True,
                history=history, request=summary, process=process,
                checkpoint_sha256=args.checkpoint_sha256, original_started_sha256=args.started_sha256,
                opencode_sha256=original["binary"]["sha256"],
                scope="Offline wire shape only; no model answer, tool execution or C4 acceptance.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--checkpoint-sha256", required=True)
    p.add_argument("--started", type=Path, required=True)
    p.add_argument("--started-sha256", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--isolated", help=argparse.SUPPRESS)
    args = p.parse_args()
    try:
        require(os.geteuid() == 0 and not args.out.exists(), "Linux root and new output required")
        if args.isolated is None:
            code = subprocess.run(
                ["unshare", "--net", "--mount", "--propagation", "private", "--pid", "--fork",
                 "--mount-proc", "--kill-child=SIGKILL", sys.executable, "-B", str(Path(__file__).resolve()),
                 *sys.argv[1:], "--isolated", json.dumps(namespaces())],
                env=SAFE_ENV, timeout=35).returncode
            return code
        result = run_isolated(args, json.loads(args.isolated))
        with args.out.open("x") as dst:
            json.dump(result, dst, indent=2, sort_keys=True)
            dst.write("\n")
        return 0
    except ProbeError as exc:
        print("offline probe refused:", exc, file=sys.stderr)
    except Exception as exc:
        print("offline probe failed:", type(exc).__name__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
