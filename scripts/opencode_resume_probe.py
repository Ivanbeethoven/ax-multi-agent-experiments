"""Small Windows -> WSL native-session probe. Credentials travel on stdin, not in the archive.

Run on Windows with --source-config, --windows-cli and --linux-cli. Raw data stays in runs/.
This tests a completed-turn checkpoint, not interruption of a running tool or a cloud migration.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def manifest(root):
    root = native_path(root)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def native_path(root):
    return Path("\\\\?\\" + str(root.resolve())) if os.name == "nt" and not str(root).startswith("\\\\?\\") else root


def isolated_env(workspace, home, key):
    home.mkdir(parents=True, exist_ok=True)
    env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT",
           "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy") if k in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home),
               XDG_DATA_HOME=str(workspace / "agent-state/data"),
               XDG_STATE_HOME=str(workspace / "agent-state/state"),
               XDG_CONFIG_HOME=str(workspace / "agent-state/config"),
               XDG_CACHE_HOME=str(workspace / "agent-state/cache"),
               OPENCODE_CONFIG_DIR=str(workspace / "agent-state/config/opencode"),
               AX_RESUME_API_KEY=key, OPENCODE_DISABLE_AUTOUPDATE="1",
               OPENCODE_DISABLE_SHARE="1", OPENCODE_DISABLE_LSP_DOWNLOAD="1",
               NO_COLOR="1")
    tmp = home / "tmp"
    tmp.mkdir(exist_ok=True)
    env.update(TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp))
    if os.name != "nt":
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    return env


def run_agent(cli, workspace, home, key, model, prompt, output, session=None, db=None):
    env = isolated_env(workspace, home, key)
    if db:
        env["OPENCODE_DB"] = str(db)
    args = [cli, "--print-logs", "--log-level", "DEBUG", "run", "--format", "json", "--model", model, "--agent", "build"]
    if session:
        args += ["--session", session]
    args += [prompt]
    output.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        args = ["strace", "-f", "-e", "trace=%file", "-o", str(output / "file-access.trace")] + args
    started = time.monotonic()
    proc = subprocess.Popen(args, cwd=workspace / "repo", env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=os.name != "nt")
    try:
        stdout, stderr = proc.communicate(timeout=240)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        # Stop the child agent too, rather than leaving it spending tokens after strace exits.
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout, stderr = proc.communicate()
        code = 124
    output.mkdir(parents=True, exist_ok=True)
    (output / "stdout.jsonl").write_bytes(stdout)
    (output / "stderr.txt").write_bytes(stderr)
    events = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    ids = [e.get("sessionID") for e in events if e.get("sessionID")]
    usage = [e.get("part", {}) for e in events if e.get("type") == "step_finish"]
    summary = {"exit_code": code, "elapsed_s": round(time.monotonic()-started, 3),
               "session_id": ids[0] if ids else None, "event_count": len(events),
               "usage": [{k: u[k] for k in ("tokens", "cost", "reason") if k in u} for u in usage],
               "tool_calls": [e.get("part", {}).get("tool") for e in events if e.get("type") == "tool_use"],
               "error_events": [e for e in events if e.get("type") == "error"]}
    write_json(output / "summary.json", summary)
    return summary


def linux_worker(payload):
    target = Path(payload["target"])
    if target.exists():
        raise RuntimeError("Target must not exist")
    target.mkdir(parents=True)
    workspace = target / "workspace"
    workspace.mkdir()
    with tarfile.open(payload["archive"]) as archive:
        archive.extractall(workspace, filter="data")
    before = manifest(workspace)
    source = payload["manifest"]
    if before != source:
        raise RuntimeError("Workspace bytes changed in transfer")
    home = target / "empty-home"
    home.mkdir()
    home_empty = not any(home.iterdir())
    session = payload["session"]
    db = None
    imported = None
    if payload["transfer_mode"] == "import":
        db = workspace / "agent-state/imported/opencode.db"
        db.parent.mkdir(parents=True)
        env = isolated_env(workspace, home, payload["key"])
        env["OPENCODE_DB"] = str(db)
        started = time.monotonic()
        imp = subprocess.run([payload["cli"], "import", str(workspace / "run-state/session-export.json")],
                             cwd=workspace / "repo", env=env, capture_output=True, timeout=60)
        (target / "import.stdout.txt").write_bytes(imp.stdout)
        (target / "import.stderr.txt").write_bytes(imp.stderr)
        imported = {"exit_code": imp.returncode, "elapsed_s": round(time.monotonic()-started,3)}
        if imp.returncode:
            raise RuntimeError("Native session import failed; see target import.stderr.txt")
    prompt = ("Continue the saved task. Write final.txt with ONLY the exact secret token "
              "given in the previous conversation, followed by a newline. Do not modify checkpoint.txt. "
              "Do not search outside this project or invent a token. Then stop with a brief reply.")
    if payload["announce_relocation"]:
        prompt = (f"Runtime relocation notice: you are now on Linux. The current project directory is {workspace / 'repo'}. "
                  "Windows paths in old tool results are historical. For all new file operations use this current Linux "
                  "project directory; do not reuse the Windows path.\n" + prompt)
    resumed = run_agent(payload["cli"], workspace, home, payload["key"], payload["model"],
                        prompt, target / "resume", session, db)
    final = workspace / "repo/final.txt"
    checkpoint = workspace / "repo/checkpoint.txt"
    actual = final.read_text().strip() if final.exists() else None
    # With no agent state, the exact same session ID must fail to load.
    negative = target / "negative-workspace"
    shutil.copytree(workspace / "repo", negative / "repo", ignore=shutil.ignore_patterns("final.txt"))
    config = Path("agent-state/config/opencode/opencode.json")
    (negative / config).parent.mkdir(parents=True)
    shutil.copyfile(workspace / config, negative / config)
    neg = run_agent(payload["cli"], negative, target / "negative-empty-home", payload["key"],
                    payload["model"], prompt, target / "negative", session)
    neg_text = (target / "negative/stdout.jsonl").read_text(errors="replace") + (target / "negative/stderr.txt").read_text(errors="replace")
    negative_rejected = "session not found" in neg_text.lower() and not neg["usage"] and not (negative / "repo/final.txt").exists()
    access = (target / "resume/file-access.trace").read_text(errors="replace")
    forbidden_access = [line for line in access.splitlines() if '"/mnt/c/' in line or '"/home/hxy/.config/' in line or '"/home/hxy/.local/share/' in line]
    report = {"workspace_hashes_match": before == source, "home_empty_before_start": home_empty,
              "target_path": str(workspace), "target_filesystem": subprocess.check_output(["findmnt", "-no", "FSTYPE", "-T", str(workspace)], text=True).strip(),
              "resume": resumed, "native_import": imported, "forbidden_file_access_count": len(forbidden_access),
              "secret_recovered": actual == payload["secret"],
              "checkpoint_unchanged": checkpoint.exists() and checkpoint.read_text().strip() == "ready",
              "negative_without_state": neg, "negative_rejected_without_model_usage": negative_rejected}
    report["passed"] = all(report[k] for k in ("workspace_hashes_match", "home_empty_before_start", "secret_recovered", "checkpoint_unchanged", "negative_rejected_without_model_usage")) and resumed["exit_code"] == 0 and resumed["session_id"] == session and not forbidden_access
    write_json(target / "result.json", report)
    print(json.dumps(report))


def main():
    if sys.argv[1:] == ["--linux-worker"]:
        linux_worker(json.load(sys.stdin))
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--windows-cli", required=True)
    parser.add_argument("--linux-cli", required=True)
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--distro", default="Ubuntu-24.04")
    parser.add_argument("--resume-source-run", type=Path, help="Reuse a successful source checkpoint without another model call")
    parser.add_argument("--transfer-mode", choices=("direct", "import"), default="direct")
    parser.add_argument("--announce-relocation", action="store_true", help="Tell the agent its new OS/path, without repeating task memory")
    args = parser.parse_args()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    out = args.resume_source_run.resolve() if args.resume_source_run else ROOT / "runs/opencode-resume" / run_id
    workspace = out / "workspace"
    repo = workspace / "repo"
    if not args.resume_source_run:
        repo.mkdir(parents=True)
    source_config = json.loads(args.source_config.read_text(encoding="utf-8-sig"))
    provider = source_config["provider"][args.provider]
    key = provider["options"]["apiKey"]
    if key.startswith("{"):
        raise RuntimeError("This probe requires a literal key in the external source config; it never prints it")
    # Avoid built-in provider model aliases changing the requested API model ID.
    model = "resumeprobe/" + args.model
    if args.resume_source_run:
        source = json.loads((out / "source-result.json").read_text())
        secret = json.loads((out / "expected.local.json").read_text())["secret"]
        return transfer(args, out, workspace, key, model, source, secret, out.name)
    safe_provider = {"npm": provider["npm"], "models": {args.model: provider["models"][args.model]},
                     "options": {"baseURL": provider["options"]["baseURL"], "apiKey": "{env:AX_RESUME_API_KEY}"}}
    config = {"$schema": "https://opencode.ai/config.json", "provider": {"resumeprobe": safe_provider},
              "model": model, "small_model": model, "share": "disabled",
              "agent": {"build": {"steps": 4}}, "permission": "allow"}
    write_json(workspace / "agent-state/config/opencode/opencode.json", config)
    (repo / "README.md").write_text("A minimal native agent resume test. Work only in this directory.\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Resume Probe", "-c", "user.email=probe@example.invalid", "commit", "-qm", "Initial task"], check=True)
    secret = "resume-" + secrets.token_hex(8)
    write_json(out / "expected.local.json", {"secret": secret})
    prompt = (f"Small two-stage task. Remember this exact secret token in our conversation: {secret}. "
              "Do NOT write the token to any project file or execute a command containing it. "
              "Now create checkpoint.txt containing only ready and a newline. Do not create final.txt. "
              "Then stop. In the next turn I will ask you to put the remembered token in final.txt. Reply briefly.")
    cli = str(Path(args.windows_cli).resolve())
    source = run_agent(cli, workspace, out / "source-empty-home", key, model, prompt, out / "source")
    write_json(out / "source-result.json", source)
    if source["exit_code"] or not source["session_id"] or not (repo / "checkpoint.txt").exists():
        print(json.dumps({"passed": False, "stage": "source", "result_dir": str(out), "source": source}))
        return 1
    if (repo / "checkpoint.txt").read_text().strip() != "ready" or any(secret.encode() in p.read_bytes() for p in repo.rglob("*") if p.is_file()):
        raise RuntimeError("Source task invalid: secret leaked into project or checkpoint incorrect")
    write_json(workspace / "run-state/checkpoint.json", {"session_id": source["session_id"], "agent_version": "1.19.4", "completed_turns": 1, "model": model})
    return transfer(args, out, workspace, key, model, source, secret, run_id)


def transfer(args, out, workspace, key, model, source, secret, run_id):
    exported = None
    if args.transfer_mode == "import":
        started = time.monotonic()
        exp = subprocess.run([str(Path(args.windows_cli).resolve()), "export", source["session_id"]],
                             cwd=workspace / "repo", env=isolated_env(workspace, out / "source-empty-home", key),
                             capture_output=True, timeout=60)
        if exp.returncode:
            raise RuntimeError("Native export failed")
        data = json.loads(exp.stdout)
        if data["info"]["id"] != source["session_id"]:
            raise RuntimeError("Wrong session exported")
        (workspace / "run-state/session-export.json").write_bytes(exp.stdout)
        exported = {"exit_code": exp.returncode, "elapsed_s": round(time.monotonic()-started,3), "bytes": len(exp.stdout)}
    snapshot = manifest(workspace)
    if any(key.encode() in p.read_bytes() for p in native_path(workspace).rglob("*") if p.is_file()):
        raise RuntimeError("Credential present in workspace; refusing transfer")
    archive = out / "workspace.tar"
    with tarfile.open(archive, "w") as tar:
        for p in sorted(workspace.iterdir()):
            # OpenCode's Git snapshot paths can exceed Windows MAX_PATH.
            native = "\\\\?\\" + str(p) if os.name == "nt" else str(p)
            tar.add(native, arcname=p.name)
    def linux_path(path):
        return subprocess.check_output(["wsl", "-d", args.distro, "--", "wslpath", "-a", path.as_posix()], text=True).strip()
    payload = {"archive": linux_path(archive), "manifest": snapshot, "target": "/tmp/ax-opencode-resume-" + run_id + "-" + secrets.token_hex(3),
               "cli": args.linux_cli, "key": key, "model": model, "session": source["session_id"], "secret": secret,
               "transfer_mode": args.transfer_mode, "announce_relocation": args.announce_relocation}
    start = time.monotonic()
    worker = subprocess.run(["wsl", "-d", args.distro, "--", "python3", linux_path(Path(__file__).resolve()), "--linux-worker"],
                            input=json.dumps(payload), text=True, capture_output=True, timeout=600)
    (out / "linux-worker.stderr.txt").write_text(worker.stderr, encoding="utf-8")
    if worker.returncode:
        print(json.dumps({"passed": False, "stage": "linux_worker", "result_dir": str(out), "exit_code": worker.returncode}))
        return 1
    report = json.loads(worker.stdout)
    report.update(source=source, run_id=run_id, version="1.19.4", model=model,
                  transfer_mode=args.transfer_mode, announce_relocation=args.announce_relocation, native_export=exported,
                  workspace_bytes=sum(p.stat().st_size for p in native_path(workspace).rglob("*") if p.is_file()),
                  transferred_files=len(snapshot), target_total_s=round(time.monotonic()-start, 3),
                  scope="Windows to WSL2; separate native Linux files, completed turn; not a cloud/failure/performance result")
    write_json(out / "result.json", report)
    write_json(out / ("result-" + args.transfer_mode + ("-relocated" if args.announce_relocation else "") + ".json"), report)
    print(json.dumps({"passed": report["passed"], "result_dir": str(out), "result": report}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
