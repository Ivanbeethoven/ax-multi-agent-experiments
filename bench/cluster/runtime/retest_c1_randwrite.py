"""Repeat run-002 C1 condition 04 with cross-host, byte-exact verification."""
import argparse
import json
from pathlib import Path
import re

import cluster_lib as cl
import run_c1 as fio
from run_suite import save, select_backend


class RandomWriteCheck:
    def __init__(self, verifier, reference, pattern):
        self.verifier = verifier
        self.reference = reference
        self.pattern = pattern

    def log_writes(self, job_text, output):
        sections = re.findall(r"^\[job(\d+)\]$", job_text, re.MULTILINE)
        if sections != [str(i) for i in range(8)]:
            raise ValueError("Expected eight fio jobs")
        return re.sub(r"^\[job(\d+)\]$", lambda m:
            f"{m[0]}\nwrite_iolog={output}/writes-{m[1]}.iolog", job_text, flags=re.MULTILINE)

    def verify(self, host, mount, output):
        for i in range(8):
            log = output / f"writes-{i}.iolog"
            cl.sync_file_to_host(host, log, str(log))
        command = [str(self.verifier), mount, str(self.reference), str(output), str(self.pattern)]
        result = cl.run_on_host(host, command, timeout=1800, check=False)
        (output / "cold-readback.log").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"Cross-host readback failed on {host}: {result.stderr[-600:]!r}")
        counts = json.loads(result.stdout)
        if counts != {"written_pages": 16384, "unchanged_pages": 507904, "bytes_compared": 2147483648}:
            raise ValueError(f"Incomplete verification on {host}: {counts}")
        return dict(counts, passed=True, host=host, matched_original_offsets=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("binary", "verifier", "reference", "pattern", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    historical = cl.RUN_ROOT / "01-io/04/r1/attempt-04"
    job = json.loads((historical / "job.json").read_text())
    params = job["parameters"]
    expected = dict(system="graftfs-native", operation="write", dynamic="on", pattern="random",
                    io_bytes=64*1024**2, request_kib=4, jobs=8, working_set_bytes=2*1024**3)
    if any(params[k] != v for k, v in expected.items()):
        raise ValueError("Historical C1 condition differs from the scheduled workload")
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output / "job.json", job)
    check = RandomWriteCheck(args.verifier, args.reference, args.pattern)
    target = "node-meta"
    for path in (args.binary, args.verifier, args.pattern, *(args.reference / f"writes-{i}.iolog" for i in range(8))):
        cl.sync_file_to_host(target, path, str(path))
    cl.run_on_host(target, ["chmod", "0755", str(args.binary), str(args.verifier)])
    original = cl.generate_mounts_json
    def configure(requests, path):
        result = original(requests, path)
        for record in result["mounts"]:
            role = {"initial":"input", "measure":"m", "verify":"v"}[record["phase"]]
            old = json.loads(next(historical.glob("*-" + role + "-audit.json")).read_text())
            cache_root = record["cache"]["cache_root"]
            record["binary"] = str(args.binary)
            record["environment"] = old["environment"]
            record["cache"] = dict(old["cache"], cache_root=cache_root, native_write_coalesce_window_ms=50)
            if record["memory_bytes"] != old["memory_max"]:
                raise ValueError(f"Memory allocation differs for {role}")
        save(path, result)
        return result
    cl.generate_mounts_json = configure
    with cl.MeasurementLock():
        nodes = select_backend(True, args.output)
        if any(not e.endswith(":9000") for e in cl.RUSTFS_BACKENDS["dynamic"]["endpoints"]):
            raise ValueError("Dynamic requires the four-node RustFS service on port 9000")
        fio.BYTES_PER_JOB = 256 * 1024**2
        pool = fio.ParentPool(nodes, policy="dynamic-2147483648b")
        system = "graftfs-native-overlay"
        parent = pool.ensure_io_parent(system, "auto")
        inspected = cl.inspect_native_fork_parent(parent)
        save(args.output / "native-parent.json", inspected)
        condition = dict(id=job["id"] + "-unified", family="block-random", kind="measurement", parameters=params)
        fio.execute_fio_family_run(condition, system, job["repeat"], pool, graftfs_hint="auto",
                                   output=args.output, seed_override=job["seed"], hold_lock=False,
                                   random_write_check=check)
    result = json.loads((args.output / "result.json").read_text())
    if result["application"]["write_bytes"] != expected["io_bytes"] or not result["verification"]["passed"]:
        raise ValueError("Incomplete formal result")
    measurements = {}
    for label, path in (("graftfs_after", args.output / "result.json"),
                        ("graftfs_before", historical / "result.json"),
                        ("juicefs", cl.RUN_ROOT / "01-io/08/r1/attempt-02/result.json")):
        row = json.loads(path.read_text())
        app = row["application"]
        measurements[label] = {
            "result": str(path), "write_bytes": app["write_bytes"],
            "fio_mib_s": app["fio_write_throughput_mib_s"],
            "p99_ms": app["fio_direction_percentiles_us"]["write"]["p99_us"] / 1000,
            "drain_seconds": row["timing"]["upload_drain_seconds"],
            "put_requests": row["backend_delta"]["rustfs_s3_put_requests"],
            "put_bytes": row["backend_delta"]["s3_put_bytes"], "verification": row["verification"]}
    save(args.output / "comparison.json", {
        "measurements": measurements,
        "candidate_binary": str(args.binary), "coalesce_window_ms": 50,
        "original_condition": job["id"], "verification": result["verification"]})


if __name__ == "__main__":
    main()
