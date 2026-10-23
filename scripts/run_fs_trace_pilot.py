"""Gate a real-repository fixed tool task on successful syscall capture AND Rust replay."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from fs_trace_capture import capture, dump

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, capture_output=True, text=True, **kwargs).stdout


def fingerprint(binary, root):
    return json.loads(run([binary, "fingerprint", "--root", root]))["initial_tree_sha256"]


def replay(binary, trace, initial, dest, output, metrics):
    run([binary, "init", "--root", dest])
    shutil.copytree(initial, dest, dirs_exist_ok=True)
    validation = json.loads(run([binary, "validate", "--trace", trace]))
    dump(output.with_name(output.stem + "-validation.json"), validation)
    run([binary, "run", "--trace", trace, "--root", dest, "--output", output,
         "--workers", "1", "--pace", "asap", "--timing", "off"])
    report = json.loads(output.read_text())
    if not report["valid"]: raise RuntimeError("Rust replay rejected captured trace")
    for key in ("actual_read_bytes", "actual_write_bytes", "requested_read_bytes", "requested_write_bytes"):
        if report["totals"][key] != metrics[key]: raise RuntimeError("Replay byte count differs: " + key)
    if report["totals"]["attempted"] != metrics["replay_operations"]:
        raise RuntimeError("Replay operation count differs")
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--binary", type=Path, default=Path("/home/hxy/ax-fs-trace-build/release/fuse-replay"))
    args = p.parse_args()
    out, binary = args.output.resolve(), args.binary.resolve()
    out.mkdir(parents=True, exist_ok=False)
    probe = out / "probe-root"
    run([binary, "init", "--root", probe])
    initial = out / "probe-initial"
    shutil.copytree(probe, initial)
    metrics = capture(probe, out / "probe-capture", fingerprint(binary, probe),
                      [sys.executable, "-B", "-S", str(SCRIPTS / "fs_trace_workload.py"), "probe"])
    expected = {"actual_read_bytes": 10, "actual_write_bytes": 5, "repeated_read_bytes": 5, "repeated_read_ratio": 0.5}
    for key, value in expected.items():
        if metrics[key] != value: raise RuntimeError(f"Capture gate failed: {key}")
    replay(binary, out / "probe-capture/trace.jsonl", initial, out / "probe-replay", out / "probe-replay.json", metrics)
    if fingerprint(binary, probe) != fingerprint(binary, out / "probe-replay"):
        raise RuntimeError("Probe final tree mismatch")
    dump(out / "preflight-gate.json", {"passed": True, "expected": expected,
         "checks": ["actual returned bytes", "short read", "EOF", "overlap reads", "overwrite invalidation", "rename identity", "ENOENT", "Rust replay", "final tree"],
         "passed_at_unix": time.time()})
    print("Capture and Rust replay preflight passed; preparing task", flush=True)
    # No task work occurs before the gate above passes.
    import docker
    client = docker.from_env()
    old = REPO / "results/swebench-verified-hard4-20261023/pytest-dev__pytest-10356"
    image = json.loads((old / "image.json").read_text())["id"]
    c = client.containers.create(image, ["sleep", "infinity"], network_disabled=True,
                                 labels={"ax-swe4": "fs-trace-fixture"})
    original = out / "initial"
    try:
        run(["docker", "cp", c.id + ":/testbed", original])
    finally:
        c.remove(force=True)
    root = out / "task-root"
    run([binary, "init", "--root", root])
    shutil.copytree(original, root, dirs_exist_ok=True)
    before = fingerprint(binary, root)
    started = time.time()
    task_metrics = capture(root, out / "task-capture", before,
                           [sys.executable, "-B", "-S", str(SCRIPTS / "fs_trace_workload.py"), "task"])
    # Exact known successful model prediction is an independent output oracle.
    oracle = out / "oracle"
    shutil.copytree(original, oracle)
    run(["git", "apply", str(old / "prediction.patch")], cwd=oracle)
    if (oracle / "src/_pytest/mark/structures.py").read_bytes() != (root / "src/_pytest/mark/structures.py").read_bytes():
        raise RuntimeError("Fixed tool output differs from prior scored model prediction")
    report = replay(binary, out / "task-capture/trace.jsonl", original, out / "task-replay", out / "task-replay.json", task_metrics)
    after, replay_after = fingerprint(binary, root), fingerprint(binary, out / "task-replay")
    if after != replay_after: raise RuntimeError("Task final tree mismatch")
    dump(out / "result.json", {"valid": True, "task": "pytest-dev__pytest-10356",
         "scope": "fixed source inspection, recorded repair, and AST validation; not full Agent or test-suite capture",
         "model_calls": 0, "task_started_unix": started, "initial_tree_sha256": before,
         "captured_final_tree_sha256": after, "replayed_final_tree_sha256": replay_after,
         "matches_previously_scored_prediction": True, "metrics": task_metrics,
         "rust_replay": report, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
         "strace_version": run(["strace", "--version"]).splitlines()[0], "image_id": image})
    print(json.dumps({"valid": True, "output": str(out), "metrics": task_metrics}), flush=True)


if __name__ == "__main__":
    main()
