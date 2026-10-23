"""Run four preselected harder SWE-bench Verified tasks in disposable Docker containers.

Linux controller only. Provider configuration is read from stdin, never CLI arguments.
The dataset and grading material stay on the host; only the issue prompt reaches the agent.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import traceback

IDS = ["django__django-10554", "pytest-dev__pytest-10356", "scikit-learn__scikit-learn-25102", "sympy__sympy-13878"]
DATA_SHA = "030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25"
HARNESS = "02e7a74ffd0b707aab73d203fe87bdc7c76afc8e"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_rows(source):
    import pyarrow.parquet as pq
    if sha(source.read_bytes()) != DATA_SHA:
        raise RuntimeError("Unexpected dataset bytes")
    rows = pq.read_table(source).to_pylist()
    repos = ["django/django", "pytest-dev/pytest", "scikit-learn/scikit-learn", "sympy/sympy"]
    selected = []
    for repo in repos:
        choices = [r for r in rows if r["repo"] == repo and r["difficulty"] in ("1-4 hours", ">4 hours")]
        selected.append(sorted(choices, key=lambda r: (0 if r["difficulty"] == ">4 hours" else 1, r["instance_id"]))[0])
    if [r["instance_id"] for r in selected] != IDS:
        raise RuntimeError("Selection changed")
    return selected


def prompt_for(row, max_steps=None, timeout_s=3600):
    return ("Fix the following issue in this repository. Work in /testbed. The testbed conda environment is "
            "already prepared; use /opt/miniconda3/envs/testbed/bin/python and the existing project tests. "
            "Inspect the code, implement the fix, and run relevant tests. Do not fetch upstream fixes, "
            "look up benchmark answers, or change git history. Do not access credentials or unrelated files. "
            "Leave your changes in the working tree and finish with a concise account of tests run. " +
            (f"You have at most {max_steps} model steps. " if max_steps else "There is no model step limit. ") +
            f"The execution time limit is {timeout_s} seconds.\n\n" + row["problem_statement"])


def freeze(rows, output, max_steps=None, timeout_s=3600):
    tasks = []
    for r in rows:
        task = {k: r[k] for k in ("instance_id", "repo", "difficulty", "base_commit", "image", "problem_statement")}
        task.update(prompt=prompt_for(r, max_steps, timeout_s), prompt_sha256=sha(prompt_for(r, max_steps, timeout_s).encode()),
                    gold_patch_sha256=sha(r["patch"].encode()), test_patch_sha256=sha(r["test_patch"].encode()),
                    eval_script_sha256=sha(r["eval_script"].encode()))
        tasks.append(task)
    manifest = {"dataset": "SWE-bench/SWE-bench_Verified", "dataset_sha256": DATA_SHA,
                "harness_commit": HARNESS, "selection": "One per named repository: highest available difficulty then instance_id lexicographically; selected before inference, no replacements",
                "agent": "OpenCode 1.19.4", "model": "deepseek-v4-flash", "attempts_per_task": 1,
                "max_steps": max_steps, "timeout_s": timeout_s, "concurrency": 2, "container_cpus": 4, "container_memory_gib": 6,
                "agent_network": "host (WSL proxy listens on loopback); only private agent-state and executable mounted; no host HOME, dataset, grading material or Docker socket",
                "tasks": tasks}
    write_json(output / "selection.json", manifest)


def put_file(container, name, data):
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))
    container.put_archive("/tmp", blob.getvalue())


def command(container, argv, timeout=1200, environment=None):
    args = ["docker", "exec"]
    env = os.environ.copy()
    for key, value in (environment or {}).items():
        env[key] = value
        args += ["-e", key]
    args += [container.id] + argv
    try:
        result = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired as exc:
        container.kill()
        return 124, (exc.stdout or b"") + (exc.stderr or b"") + b"\nCONTROLLER_TIMEOUT\n"


def grade(client, row, image, patch, folder, label):
    from swebench.types import TestSpec
    from swebench.harness.grading import get_eval_report
    folder.mkdir(parents=True, exist_ok=True)
    c = client.containers.run(image, ["sleep", "infinity"], detach=True, working_dir="/testbed",
                              mem_limit="6g", nano_cpus=4_000_000_000, labels={"ax-swe4": "grading"})
    try:
        code, output = command(c, ["git", "-C", "/testbed", "reset", "--hard", row["base_commit"]])
        if code:
            raise RuntimeError("Grading reset failed")
        if patch:
            put_file(c, "prediction.patch", patch.encode())
            code, output = command(c, ["git", "-C", "/testbed", "apply", "--verbose", "/tmp/prediction.patch"])
            (folder / "patch-apply.txt").write_bytes(output)
            if code:
                return {"resolved": False, "patch_apply_failed": True}
        put_file(c, "eval.sh", row["eval_script"].encode())
        started = time.monotonic()
        code, output = command(c, ["bash", "/tmp/eval.sh"])
        log = folder / "test_output.txt"
        log.write_bytes(output)
        spec = TestSpec(instance_id=row["instance_id"], image=row["image"], eval_script_list=[],
                        repo=row["repo"], version=row["version"],
                        FAIL_TO_PASS=json.loads(row["FAIL_TO_PASS"]) if isinstance(row["FAIL_TO_PASS"], str) else row["FAIL_TO_PASS"],
                        PASS_TO_PASS=json.loads(row["PASS_TO_PASS"]) if isinstance(row["PASS_TO_PASS"], str) else row["PASS_TO_PASS"],
                        log_parser=row["log_parser"], eval_type=row["eval_type"])
        pred = {"instance_id": row["instance_id"], "model_name_or_path": label, "model_patch": patch}
        report = get_eval_report(spec, pred, str(log), True)[row["instance_id"]]
        report.update(script_exit_code=code, elapsed_s=round(time.monotonic()-started, 3), timed_out=code == 124)
        if code == 124:
            report["resolved"] = False
        write_json(folder / "report.json", report)
        return report
    finally:
        c.remove(force=True)


def run_one(row, args, provider):
    import docker
    client = docker.from_env(timeout=120)
    folder = args.output / row["instance_id"]
    folder.mkdir(parents=True, exist_ok=True)
    def phase(name):
        write_json(folder / "progress.json", {"phase": name, "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    try:
        phase("image_check")
        image = client.images.get(row["image"])
        write_json(folder / "image.json", {"id": image.id, "repo_digests": image.attrs.get("RepoDigests", []), "tag": row["image"]})
        phase("baseline_grading")
        baseline = grade(client, row, image.id, "", folder / "baseline", "unmodified")
        phase("gold_grading")
        gold = grade(client, row, image.id, row["patch"], folder / "gold", "dataset-reference")
        if baseline["resolved"] or not gold["resolved"]:
            raise RuntimeError("Environment qualification failed: expected baseline fail and reference pass")
        phase("agent_running")
        state = folder / "private-agent-state"
        state.mkdir()
        config = {"$schema": "https://opencode.ai/config.json", "provider": {"experiment": {
                    "npm": provider["npm"], "models": {"deepseek-v4-flash": provider["models"]["deepseek-v4-flash"]},
                    "options": {"baseURL": provider["options"]["baseURL"], "apiKey": "{env:AX_SWE_API_KEY}"}}},
                  "model": "experiment/deepseek-v4-flash", "small_model": "experiment/deepseek-v4-flash",
                  "share": "disabled",
                  "permission": {"*": "allow", "webfetch": "deny", "websearch": "deny", "codesearch": "deny", "task": "deny"}}
        if args.max_steps:
            config["agent"] = {"build": {"steps": args.max_steps}}
        write_json(state / "config/opencode/opencode.json", config)
        env = {"HOME": "/agent-state/home", "XDG_DATA_HOME": "/agent-state/data", "XDG_CONFIG_HOME": "/agent-state/config",
               "XDG_STATE_HOME": "/agent-state/state", "XDG_CACHE_HOME": "/agent-state/cache",
               "OPENCODE_CONFIG_DIR": "/agent-state/config/opencode", "OPENCODE_DISABLE_AUTOUPDATE": "1",
               "OPENCODE_DISABLE_SHARE": "1", "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
               "PATH": "/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        env.update({k: v for k, v in os.environ.items() if k.lower() in ("http_proxy", "https_proxy", "no_proxy")})
        c = client.containers.run(image.id, ["sleep", "infinity"], detach=True, working_dir="/testbed",
                                  environment=env, mem_limit="6g", nano_cpus=4_000_000_000,
                                  network_mode="host",
                                  volumes={str(args.opencode): {"bind": "/opt/opencode", "mode": "ro"},
                                           str(state): {"bind": "/agent-state", "mode": "rw"}},
                                  labels={"ax-swe4": "agent", "task": row["instance_id"]})
        try:
            code, output = command(c, ["bash", "-lc", "mkdir -p /agent-state/home; git config --global --add safe.directory /testbed; git reset --hard " + row["base_commit"] + "; git remote remove origin 2>/dev/null || true; /opt/opencode --version"])
            (folder / "agent-setup.txt").write_bytes(output)
            if code or not output.rstrip().endswith(b"1.19.4"):
                raise RuntimeError("Agent setup/version check failed")
            code, output = command(c, ["/opt/miniconda3/envs/testbed/bin/python", "-c",
                "import urllib.request; print(urllib.request.urlopen('https://registry.npmjs.org',timeout=15).status)"], timeout=30)
            (folder / "network-check.txt").write_bytes(output)
            if code:
                raise RuntimeError("Agent proxy/network preflight failed before model invocation")
            prompt = prompt_for(row, args.max_steps, args.timeout_s)
            put_file(c, "issue.txt", prompt.encode())
            started = time.monotonic()
            proc = subprocess.Popen(["docker", "exec", "-e", "AX_SWE_API_KEY", c.id, "/opt/opencode", "run", "--format", "json", "--model", "experiment/deepseek-v4-flash", "--agent", "build", prompt],
                                    env={**os.environ, "AX_SWE_API_KEY": provider["options"]["apiKey"]},
                                    stdout=(folder / "agent-events.jsonl").open("wb"), stderr=(folder / "agent-stderr.txt").open("wb"))
            try:
                code = proc.wait(timeout=args.timeout_s)
            except subprocess.TimeoutExpired:
                c.kill()
                proc.wait(timeout=30)
                code = 124
                c.start()  # Only read the final filesystem; do not restart the agent.
            elapsed = round(time.monotonic()-started, 3)
            command(c, ["git", "-C", "/testbed", "add", "-N", "."])
            diff = c.exec_run(["git", "-C", "/testbed", "diff", "--binary", row["base_commit"]]).output.decode()
            (folder / "prediction.patch").write_text(diff)
            events = []
            for line in (folder / "agent-events.jsonl").read_text(errors="replace").splitlines():
                try: events.append(json.loads(line))
                except ValueError: pass
            usage = [e["part"] for e in events if e.get("type") == "step_finish"]
            summary = {"exit_code": code, "elapsed_s": elapsed, "timed_out": code == 124,
                       "session_ids": sorted(set(e["sessionID"] for e in events if e.get("sessionID"))),
                       "model_steps": len(usage), "tool_calls": sum(e.get("type") == "tool_use" for e in events),
                       "usage": [{k: u[k] for k in ("tokens", "cost", "reason") if k in u} for u in usage],
                       "patch_bytes": len(diff.encode()), "patch_sha256": sha(diff.encode())}
            write_json(folder / "agent-summary.json", summary)
            from export_swebench_trace import export_trace
            export_trace(folder, c)
        finally:
            c.remove(force=True)
        phase("prediction_grading")
        prediction = {"instance_id": row["instance_id"], "model_name_or_path": "opencode-1.19.4_deepseek-v4-flash", "model_patch": diff}
        write_json(folder / "prediction.json", prediction)
        graded = grade(client, row, image.id, diff, folder / "grading", prediction["model_name_or_path"])
        result = {"instance_id": row["instance_id"], "status": "graded", "resolved": graded["resolved"], "agent": summary, "grading": graded}
        write_json(folder / "result.json", result)
        phase("complete")
        return result
    except Exception as exc:
        result = {"instance_id": row["instance_id"], "status": "error", "resolved": False, "error": str(exc)}
        write_json(folder / "result.json", result)
        (folder / "controller-error.txt").write_text(traceback.format_exc())
        phase("error")
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/home/hxy/research/agent-fuse-swebench/verified.parquet"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--opencode", type=Path, default=Path("/home/hxy/ax-opencode-resume-runtime/node_modules/opencode-linux-x64/bin/opencode"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--instance-ids", nargs="*", choices=IDS)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--timeout-s", type=int, default=3600)
    args = parser.parse_args()
    args.output = args.output.resolve()
    rows = load_rows(args.source)
    if args.instance_ids:
        rows = [r for r in rows if r["instance_id"] in args.instance_ids]
    freeze(rows, args.output, args.max_steps, args.timeout_s)
    if args.prepare_only:
        print(json.dumps({"selected": [r["instance_id"] for r in rows], "output": str(args.output)})); return
    provider = json.load(sys.stdin)
    for r in rows:
        if (args.output / r["instance_id"]).exists():
            raise RuntimeError("Refusing to overwrite an existing task attempt")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_one, row, args, provider) for row in rows]
        results = []
        for f in as_completed(futures):
            result = f.result(); results.append(result)
            print(json.dumps({k: result[k] for k in ("instance_id", "status", "resolved")}), flush=True)
    write_json(args.output / ("batch-" + str(int(time.time())) + ".json"), results)


if __name__ == "__main__":
    main()
