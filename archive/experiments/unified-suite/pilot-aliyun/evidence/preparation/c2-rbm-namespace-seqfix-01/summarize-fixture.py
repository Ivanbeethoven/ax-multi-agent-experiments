"""Produce an allowlisted summary from one exact C2 namespace fixture attempt."""
import argparse
import json
import math
from pathlib import Path
import statistics


def load(path):
    return json.loads(path.read_bytes())


def describe(values, n):
    if len(values) != n or any(type(v) is not int or v <= 0 for v in values):
        raise ValueError("wrong number or type of latency samples")
    ordered = sorted(values)
    return {"samples": n, "median_ns": statistics.median(values),
            "p99_nearest_rank_ns": ordered[math.ceil(.99 * n) - 1]}


def summary(directory):
    result = load(directory / "result.json")
    execution = load(directory / "execution.json")
    cleanup = result["cleanup"]
    if not (result["candidate_binary"] and result["namespace_pair"] and
            result["strict_frame_hash"] and result["timed_passes"] == 128 and
            result["retained_payload_cap_bytes"] == 8388608 and
            result["no_model"] and result["no_cloud"] and result["formal_sample"] is False):
        raise ValueError("wrong frozen diagnostic")
    if not (cleanup["mount_absent"] and cleanup["scratch_removed"] and
            cleanup["owned_containers_absent"] == {"rustfs": True, "redis": True} and
            execution["docker_query_exit_code"] == 0 and not execution["matching_running_containers"]):
        raise ValueError("fixture cleanup incomplete")
    out = dict(schema="c2-local-hot-namespace-summary-v1", formal_sample=False,
               source_commit=result["source_commit"], binary_sha256=result["binary_sha256"],
               image_sha256=result["image_sha256"], passed=result["passed"],
               mount_absent=True, owned_containers_absent=True, scratch_removed=True, arms={})
    for arm, a in result.get("lower_warmth_arms", {}).items():
        if arm not in {"overlay-read", "lower-read"}:
            raise ValueError("unexpected control arm")
        raw = load(directory / f"result.{arm}.lower" / "result.json")
        if a != raw:
            raise ValueError("raw per-arm record differs from main receipt")
        timed = a["timed_extent_reads"]
        data = out["arms"][arm] = {"hot": a["hot"], "timed_passed": timed["passed"],
                                  "timed_status": timed["status"],
                                  "cold_backend": {k: a["first"][k] for k in ("gets", "ranges", "heads", "bytes")},
                                  "warm_backend": {k: a["second"][k] for k in ("gets", "ranges", "heads", "bytes")}}
        if timed["passed"]:
            ops = timed["namespace_ns"]
            if a["writable_open"] != "EROFS" or not a["expected_extent_path"] or not a["expected_namespace_path"]:
                raise ValueError("no independent mode or read-only proof")
            prior_extent, prior_ns = a["extent_points"][-1], a["namespace_points"][-1]
            extent = {k: v - prior_extent[k] for k, v in timed["final_extent"].items()}
            namespace = {k: v - prior_ns[k] for k, v in timed["final_namespace"].items()}
            s3 = timed["independent_delta"]
            if (timed["passes"] != 128 or s3["brewfs_fuse_read_ops_total"] != 256 or
                    s3["brewfs_fuse_read_bytes_total"] != 9856 or
                    any(s3[k] for k in ("gets", "ranges", "heads", "bytes", "errors"))):
                raise ValueError("wrong timed FUSE/backend activity")
            data.update(passes=128, elapsed_ns=timed["elapsed_ns"], independent_delta=s3,
                        native_delta=timed["native_delta"], extent_delta=extent, namespace_delta=namespace,
                        pread=describe(timed["pread_ns"], 256),
                        open_and_list=describe([v["open_and_list_ns"] for v in ops], 128),
                        lookup_and_stat=describe([v["lookup_and_stat_ns"] for v in ops], 128))
    if result["passed"]:
        if set(out["arms"]) != {"overlay-read", "lower-read"} or not all(
                a["hot"] and a["timed_passed"] for a in out["arms"].values()):
            raise ValueError("pair claimed passed without both timings")
    elif "failed_phase" in result:
        out["failed_phase"] = result["failed_phase"]
        # Keep exact raw text separately; this summary has no unbounded logs.
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    args = p.parse_args()
    print(json.dumps(summary(args.directory), indent=2))
