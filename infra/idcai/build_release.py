"""Build an isolated BrewFS release on an existing Linux build host."""

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time


def main():
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base-repository", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--lock", type=Path, action="append", required=True)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    if len(args.commit) != 40 or any(c not in "0123456789abcdef" for c in args.commit):
        parser.error("A full source revision is required")
    if not 1 <= args.jobs <= 16:
        parser.error("jobs must be between 1 and 16")
    root = args.root.resolve()
    source = root / "sources" / ("brewfs-" + args.commit[:8])
    output = root / "releases" / args.commit[:8]
    if source.exists() or output.exists():
        parser.error("Source and release directories must be new")
    locks = []
    for lock in sorted(args.lock):
        stream = lock.open("a+")
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(stream)
    output.mkdir(parents=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {"source_commit": args.commit, "repository": "https://github.com/brewfs/brewfs",
              "host": socket.gethostname(), "source": str(source), "passed": False,
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "commands": [], "jobs": args.jobs}
    env = os.environ.copy()
    env.update({"PATH": str(args.toolchain) + os.pathsep + env.get("PATH", ""),
                "RUSTC": str(args.toolchain / "rustc"), "CARGO_HOME": str(args.cargo_home),
                "CARGO_TARGET_DIR": str(root / "target"), "CARGO_BUILD_JOBS": str(args.jobs),
                "CARGO_INCREMENTAL": "0", "RUSTFLAGS": "-C target-cpu=x86-64",
                "GIT_TERMINAL_PROMPT": "0"})

    def run(label, command, cwd=None, timeout=3600):
        report["commands"].append({"label": label, "argv": command})
        with (output / (label + ".log")).open("w", encoding="utf-8") as log:
            subprocess.run(command, cwd=cwd, env=env, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=timeout)

    try:
        run("clone", ["git", "clone", "--shared", "--no-checkout", str(args.base_repository), str(source)], timeout=120)
        exists = subprocess.run(["git", "-C", str(source), "cat-file", "-e", args.commit + "^{commit}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not exists:
            run("fetch", ["git", "-C", str(source), "fetch", "https://github.com/brewfs/brewfs", "main"], timeout=180)
        run("checkout", ["git", "-C", str(source), "checkout", "--detach", args.commit], timeout=120)
        run("rustc", [str(args.toolchain / "rustc"), "-Vv"], timeout=30)
        run("cargo", [str(args.toolchain / "cargo"), "-V"], timeout=30)
        features = "workspace,observability,rustfs-ec-block-size-hint"
        run("build", [str(args.toolchain / "cargo"), "build", "--locked", "--offline", "--release",
                      "--jobs", str(args.jobs), "--features", features,
                      "--bin", "brewfs", "--bin", "packed_snapshot_fixture"], cwd=source)
        for name in ("brewfs", "packed_snapshot_fixture"):
            original = root / "target" / "release" / name
            run("strip-" + name, ["objcopy", "--strip-debug", str(original), str(output / name)], timeout=60)
            (output / name).chmod(0o755)
            run("help-" + name, [str(output / name), "--help"], timeout=30)
        run("mount-help", [str(output / "brewfs"), "mount", "--help"], timeout=30)
        run("workspace-help", [str(output / "brewfs"), "workspace", "--help"], timeout=30)
        report.update({"passed": True, "profile": "release", "target_cpu": "x86-64",
                       "default_features": True, "features": features.split(","),
                       "artifacts": {name: {"path": str(output / name), "bytes": (output / name).stat().st_size}
                                     for name in ("brewfs", "packed_snapshot_fixture")}})
        shutil.copyfile(source / "Cargo.lock", output / "Cargo.lock")
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (output / "build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
