#!/usr/bin/env python3
"""Derive compact, auditable statistics from the untouched qualification JSON.

Usage: python3 analyze.py RAW_EVIDENCE_DIRECTORY > summary.json
The directory may contain plain pair-*.json or gzip-compressed pair-*.json.gz.
"""
import gzip
import hashlib
import json
import pathlib
import re
import statistics
import sys

RUNS = ("pair-128-05", "pair-128-06", "pair-10000-01", "pair-10000-02")


def load(root, name):
    path = root / (name + ".json")
    raw = path.read_bytes() if path.exists() else gzip.decompress(path.with_suffix(".json.gz").read_bytes())
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def all_true(value):
    return all(all_true(v) for v in value.values()) if isinstance(value, dict) else value is True


def timing(arm):
    return {
        "cold_ms": arm["phases"]["cold"]["wall_ns"] / 1e6,
        "warm_ms": arm["phases"]["warm"]["wall_ns"] / 1e6,
        "cold_readdir_ms": arm["phases"]["cold"]["readdir_ns"] / 1e6,
        "warm_readdir_ms": arm["phases"]["warm"]["readdir_ns"] / 1e6,
        "mount_ms": arm["mount_ns"] / 1e6,
        "idle_observation_ms": arm["background_drain_ns"] / 1e6,
        "unmount_ms": arm["unmount_ns"] / 1e6,
        "end_to_end_ms": arm["end_to_end_ns"] / 1e6,
    }


def summarize(root):
    result = {
        "schema": "packed-cache-derived-summary-20261112",
        "measured_source_commit": "6912a796abddbd96c60aad6cc672fcd926a13712",
        "status": "candidate performance evidence; final default-on qualification pending failed-entry retry fix",
        "definitions": {
            "cold": "Fresh client mount/cache directory; server and host caches are not dropped.",
            "cold_read_amplification": "cold S3 response body bytes / (files * 4096)",
            "end_to_end_transfer_per_logical_read": "E2E S3 response body bytes / (2 * files * 4096)",
            "download_per_unique_working_set": "E2E S3 response body bytes / (files * 4096)",
            "cache_hit_fraction": "Whole-object boundary hits / (hits + misses), over the entire mount.",
            "paired_reduction_percent": "100 * (1 - on / off), pairing adjacent off/on arms in either order.",
            "end_to_end": "Mount + two readdir/read passes + approximately 500ms idle observation + unmount/drain.",
        },
        "runs": {},
        "aggregate": {},
    }
    reference = None
    groups = {}
    for name in RUNS:
        raw, sha = load(root, name)
        invocation, _ = load(root, name + "-invocation")
        assert raw["passed"] and all_true(raw["cleanup"]), name
        assert all(a["passed"] for a in raw["readbacks"].values()), name
        assert invocation["source_commit"] == result["measured_source_commit"], name
        identity = {key: raw[key] for key in ("binary_sha256", "image_sha256", "rustfs_revision", "budgets")}
        identity["scripts"] = invocation["scripts"]
        if reference is None:
            reference = identity
        assert identity == reference, (name, "different binary/image/budget/harness")
        files = raw["files"]
        arms = {}
        for key, arm in raw["readbacks"].items():
            if not re.fullmatch(r"(?:off|on)-[0-3]", key):
                continue
            cache = arm["cache"]
            total = cache.get("hits", 0) + cache.get("misses", 0)
            arms[key] = {
                "timing": timing(arm),
                "s3": {"mount": arm["mount_s3"], "cold": arm["phases"]["cold"]["s3"],
                       "warm": arm["phases"]["warm"]["s3"], "unmount": arm["unmount_s3"],
                       "end_to_end": arm["end_to_end_s3"]},
                "cache": cache,
                "cache_hit_fraction": cache["hits"] / total if total else None,
                "cold_read_amplification": arm["phases"]["cold"]["s3"]["bytes"] / (files * 4096),
                "end_to_end_transfer_per_logical_read": arm["end_to_end_s3"]["bytes"] / (2 * files * 4096),
                "download_per_unique_working_set": arm["end_to_end_s3"]["bytes"] / (files * 4096),
                "vm_hwm_kib": arm["vm_hwm_kib"],
                "cgroup_memory_peak_bytes": arm["cgroup_memory_peak_bytes"],
            }
        assert len(arms) == 4, name
        pairs = []
        for start in (0, 2):
            candidates = {k.split("-")[0]: k for k in arms if int(k[-1]) in (start, start + 1)}
            off, on = (arms[candidates[m]]["timing"] for m in ("off", "on"))
            pairs.append({
                "off_arm": candidates["off"], "on_arm": candidates["on"],
                "reduction_percent": {metric: 100 * (1 - on[metric] / off[metric]) for metric in off},
                "speedup": {metric: off[metric] / on[metric] for metric in off},
            })
        correctness = {key: {"passed": arm["passed"], "cache": arm["cache"],
                             "end_to_end_s3": arm["end_to_end_s3"]}
                       for key, arm in raw["readbacks"].items() if key not in arms}
        remounts = [a for key, a in correctness.items() if key.endswith("-remount")]
        assert len(remounts) == 1 and remounts[0]["cache"]["downloaded"] == 0, name
        sizes = {}
        for obj in raw["object_sizes"]:
            kind = "/".join(obj["key"].split("/")[:3])
            sizes.setdefault(kind, []).append(obj["bytes"])
        run = {"raw_sha256": sha, "files": files, "order": raw["order"],
               "input_sha256": raw["input_sha256"], "manifest_key": raw["manifest_key"],
               "exact_revision": raw["exact_revision"], "arms": arms, "pairs": pairs,
               "correctness": correctness, "cleanup": raw["cleanup"],
               "object_sizes": {k: {"count": len(v), "total_bytes": sum(v), "min_bytes": min(v), "max_bytes": max(v)}
                                for k, v in sizes.items()}}
        result["runs"][name] = run
        groups.setdefault(files, []).append(run)
    result["frozen_identity"] = reference
    for files, runs in groups.items():
        assert len({r["input_sha256"] for r in runs}) == 1, files
        modes = {}
        for mode in ("off", "on"):
            arms = [a for r in runs for key, a in r["arms"].items() if key.startswith(mode + "-")]
            modes[mode] = {
                "count": len(arms),
                "mean_timing_ms": {metric: statistics.mean(a["timing"][metric] for a in arms) for metric in arms[0]["timing"]},
                "median_timing_ms": {metric: statistics.median(a["timing"][metric] for a in arms) for metric in arms[0]["timing"]},
                "range_timing_ms": {metric: [min(a["timing"][metric] for a in arms), max(a["timing"][metric] for a in arms)] for metric in arms[0]["timing"]},
                "vm_hwm_kib_range": [min(a["vm_hwm_kib"] for a in arms), max(a["vm_hwm_kib"] for a in arms)],
                "cgroup_memory_peak_bytes_range": [min(a["cgroup_memory_peak_bytes"] for a in arms), max(a["cgroup_memory_peak_bytes"] for a in arms)],
            }
        metrics = next(iter(runs[0]["arms"].values()))["timing"]
        paired = {metric: [p["reduction_percent"][metric] for r in runs for p in r["pairs"]] for metric in metrics}
        result["aggregate"][str(files)] = {"modes": modes, "paired_reductions_percent": paired,
            "reduction_of_mean_percent": {metric: 100 * (1 - modes["on"]["mean_timing_ms"][metric] / modes["off"]["mean_timing_ms"][metric]) for metric in metrics}}
    return result


if __name__ == "__main__":
    print(json.dumps(summarize(pathlib.Path(sys.argv[1])), indent=2, ensure_ascii=False))
