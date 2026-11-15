"""Summarize original/candidate regression logs and complete CI commands."""
import argparse
import json
from pathlib import Path
import re
import tarfile


def read_results(path):
    if path.is_dir():
        files = {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}
    else:
        with tarfile.open(path) as archive:
            files = {}
            for item in archive:
                if item.isfile():
                    if item.name in files:
                        raise ValueError("duplicate archive member: " + item.name)
                    files[item.name] = archive.extractfile(item).read()
    return files, json.loads(files["report.json"])


def summarize(path):
    files, report = read_results(path)
    artifacts = []
    for line in files["compile-tests.log"].decode().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("reason") == "compiler-artifact" and item.get("executable") and item["target"]["name"] == "brewfs" and item["profile"]["test"]:
            assert not item.get("fresh"), "BrewFS test artifact must be compiled for this source"
            artifacts.append(item)
    assert len(artifacts) == 1
    outcomes = []
    for command in report["commands"]:
        if re.search(r"-[123]$", command["name"]):
            log = files[command["name"] + ".log"].decode()
            summaries = re.findall(r"test result: .*", log)
            assert len(summaries) == 1
            assert "1 passed;" in summaries[0] or "1 failed;" in summaries[0]
            outcomes.append(dict(name=command["name"], exit_code=command["exit_code"],
                                 expected_exit=command["expected_exit"], summary=summaries[0]))
    assert len(outcomes) == 30
    full = {}
    for flavor in ("default", "features"):
        name = flavor + "-all-tests.log"
        if name in files:
            full[flavor] = re.findall(r"test result: .*", files[name].decode())
    return dict(base=report["base_commit"], compiled_for_this_source=True, diagnostic_outcomes=outcomes,
                full_test_summaries=full,
                commands=[{key: command.get(key) for key in ("name", "exit_code", "expected_exit", "seconds")} for command in report["commands"]],
                kernel_traces=sum(data.count(b"KERNEL_TRACE at=") for name, data in files.items() if name.endswith(".log")),
                kernel_control_traces=sum(data.count(b"KERNEL_CONTROL_TRACE cache=") for name, data in files.items() if name.endswith(".log")),
                redis_mounts=sum(data.count(b"KERNEL_BACKEND redis cache=") for name, data in files.items() if name.endswith(".log")),
                lock_released_utc=report.get("lock_released_utc"),
                redis_exit=report.get("redis", {}).get("exit_code"),
                runner_completed=report.get("completed", False),
                runner_error=report.get("error")), report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    original, _ = summarize(args.original)
    candidate, report = summarize(args.candidate)
    assert original["base"] == candidate["base"]
    paired = all(command["exit_code"] == command["expected_exit"]
                 for stage in (original, candidate) for command in stage["diagnostic_outcomes"])
    required = {"toolchain", "clean-package", "compile-tests", "inventory", "redis-before", "redis-after", "fmt", "cargo-metadata",
                "fuse-tokio-runtime", "fuse-io-uring-runtime"}
    for flavor in ("default", "features"):
        required.update(flavor + "-" + operation for operation in ("check", "build", "all-tests", "clippy"))
    required.update("syntax-" + name for name in ("run_perf_in_container.sh", "run_redis_perf.sh", "run_juicefs_perf_in_container.sh", "run_juicefs_perf.sh"))
    required.update(("test_perf_report_delta.sh", "test_juicefs_direct_matrix.sh", "test_juicefs_perf_report.sh"))
    if report.get("rfuse3_workspace_member"):
        required.update("rfuse3-" + runtime for runtime in ("tokio-runtime", "io-uring-runtime", "async-io-runtime"))
    present = {command["name"] for command in candidate["commands"]}
    missing = sorted(required - present)
    all_checks = (candidate["runner_completed"] and not candidate["runner_error"] and not missing
                  and all(command["exit_code"] == 0 for command in candidate["commands"])
                  and all(candidate["full_test_summaries"].get(flavor) for flavor in ("default", "features")))
    result = dict(paired_regressions_passed=paired, complete_ci_passed=bool(all_checks),
                  missing_commands=missing, original=original, candidate=candidate)
    with args.out.open("x") as output:
        json.dump(result, output, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key not in ("original", "candidate")}, indent=2))


if __name__ == "__main__":
    main()
