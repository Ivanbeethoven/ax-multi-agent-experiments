"""Check a running mount against its generated settings without exposing credentials."""

import argparse
import json
import os
from pathlib import Path
import shlex

from .mounts import POOLS


def runtime_details(proc, cgroup):
    environment = dict(item.split("=", 1) for item in proc.joinpath("environ").read_bytes().decode().split("\0") if "=" in item)
    quotas = []
    current = cgroup.resolve()
    while current.is_relative_to("/sys/fs/cgroup"):
        path = current / "cpu.max"
        if path.exists():
            quotas.append({"cgroup": str(current), "cpu_max": path.read_text().strip()})
        if current == Path("/sys/fs/cgroup"):
            break
        current = current.parent
    executable = proc.joinpath("exe").resolve()
    stat = executable.stat()
    build_path = executable.with_name(executable.name + '.build.json')
    build = json.loads(build_path.read_text()) if build_path.exists() else None
    names = {}
    for path in proc.joinpath("task").glob("*/comm"):
        try:
            name = path.read_text().strip()
        except FileNotFoundError:
            continue
        names[name] = names.get(name, 0) + 1
    return {"environment": {key: environment.get(key) for key in
            ("TOKIO_WORKER_THREADS", "RAYON_NUM_THREADS", "GOMAXPROCS", "GOMEMLIMIT")},
            "allowed_cpus": sorted(os.sched_getaffinity(int(proc.name))),
            "cpu_quotas": quotas, "thread_names": names,
            "binary_path": str(executable), "binary_bytes": stat.st_size,
            "binary_modified_ns": stat.st_mtime_ns, "binary_build": build}


def parse_proc_cmdline(raw: bytes, cgroup: Path | None = None) -> list[str]:
    tokens = [t for t in raw.decode(errors="replace").strip("\0").split("\0") if t]
    # The systemd unit preserves complete launch arguments when a worker changes its process title.
    if cgroup is not None:
        unit_file = Path("/run/systemd/transient") / cgroup.name
        if unit_file.is_file():
            for line in unit_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("ExecStart=") and line != "ExecStart=":
                    full_argv = shlex.split(line.split("=", 1)[1])
                    if full_argv:
                        return full_argv
    if tokens and " " in tokens[0]:
        return [t for t in tokens[0].split(" ") if t]
    return tokens


def argument(argv, name):
    flag = "--" + name
    for index, token in enumerate(argv):
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
        if token == flag and index + 1 < len(argv):
            return argv[index + 1]
    return None


def validate_effective(record, actual):
    if Path(actual["binary"]).resolve() != Path(record["binary"]).resolve():
        raise ValueError("Running executable differs from the mount record")
    if actual["memory_max"] != record["memory_bytes"] or actual["swap_max"] != 0:
        raise ValueError("Actual cgroup memory settings differ")
    for key, value in record["environment"].items():
        if actual["environment"].get(key) != value:
            raise ValueError("Process environment differs: " + key)
    for key, value in record["cache"].items():
        if actual["cache"].get(key) != value:
            raise ValueError("Effective configuration differs: cache." + key)
    for key, value in record["options"].items():
        if argument(actual["argv"], key) != str(value):
            raise ValueError("Mount option differs: " + key)
    metrics = actual.get("metrics", {})
    expected = {}
    if record["engine"] == "graftfs-packed":
        env = record["environment"]
        expected["graftfs_packed_index_cache_configured_bytes"] = int(env["GRAFTFS_PACKED_METADATA_CACHE_BYTES"])
        expected["graftfs_packed_v3_payload_retention_capacity_bytes"] = int(env["GRAFTFS_PACKED_RETAINED_PAYLOAD_BYTES"])
        expected.update({f"graftfs_packed_budget_{pool.lower()}_capacity_bytes":int(env[f"GRAFTFS_PACKED_{pool}_BUDGET_BYTES"]) for pool in POOLS})
        for option, variable in (("packed-cache", "GRAFTFS_PACKED_CACHE"), ("packed-cache-bytes", "GRAFTFS_PACKED_CACHE_BYTES")):
            value = argument(actual["argv"], option)
            if value is not None and value != env[variable]:
                raise ValueError("Command line overrides the Packed configuration")
    if record["engine"] in {"graftfs-native-overlay", "graftfs-packed"}:
        expected["graftfs_workspace_row_cache_capacity_bytes"] = int(record["environment"]["GRAFTFS_WORKSPACE_ROW_CACHE_BYTES"])
    for key, value in expected.items():
        if metrics.get(key) != value:
            raise ValueError("Native capacity is missing or different: " + key)
    return {"passed":True,"binary":actual["binary"],"memory_max":actual["memory_max"],
            "cache":record["cache"],"environment":record["environment"],"options":record["options"],
            "native_capacities":expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mounts", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--cgroup", type=Path, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    record = next(r for r in json.loads(args.mounts.read_text())["mounts"] if r["id"] == args.id)
    proc = Path("/proc") / str(args.pid)
    argv = parse_proc_cmdline(proc.joinpath("cmdline").read_bytes(), args.cgroup)
    environment = dict(item.split("=", 1) for item in proc.joinpath("environ").read_bytes().decode().strip("\0").split("\0") if "=" in item)
    membership = proc.joinpath("cgroup").read_text().strip().splitlines()
    relative = "/" + args.cgroup.resolve().relative_to("/sys/fs/cgroup").as_posix()
    if "0::" + relative not in membership:
        raise ValueError("Process is outside the expected cgroup")
    config = argument(argv, "config")
    cache = json.loads(Path(config).read_text()).get("cache", {}) if config else {}
    metrics = {}
    for line in args.stats.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and not line.startswith("#") and "{" not in fields[0]:
            try:
                metrics[fields[0]] = float(fields[1])
            except ValueError:
                pass
    actual = {"binary":str(proc.joinpath("exe").readlink()), "argv":argv,
              "environment":environment,"cache":cache,"metrics":metrics,
              "memory_max":int((args.cgroup / "memory.max").read_text()),
              "swap_max":int((args.cgroup / "memory.swap.max").read_text())}
    result = validate_effective(record, actual)
    result["runtime"] = runtime_details(proc, args.cgroup)
    result["rustfs_ec_block_size_hint"] = argument(argv, "s3-rustfs-ec-block-size-hint")
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result,stream,indent=2)
        stream.write("\n")
    print(json.dumps({"mount":args.id,"passed":True}))


if __name__ == "__main__":
    main()
