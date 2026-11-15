#!/usr/bin/env python3
"""Summarize AI FUSE logs and synthesize an equivalent workload, NOT event replay.

Only Python's standard library is required. Paths, payloads, FD lifetimes and
causal ordering are invented because they cannot be recovered from this source.
Source error statuses are reported, never guessed into an original errno oracle.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import heapq
import json
import math
from pathlib import Path
import random


MAPPING = {"lookup": "stat", "getattr": "stat", "read": "pread",
           "write": "pwrite", "open": "open_cycle", "create": "create_cycle",
           "rename": "rename_cycle", "readdir": "readdir", "fsync": "fsync",
           "mkdir": "directory_cycle", "rmdir": "directory_cycle",
           "unlink": "unlink_cycle"}


def number(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def distribution(hist):
    total = sum(hist.values())
    if not total:
        return {"count": 0, "histogram": {}}
    ordered = sorted(hist.items())
    def quantile(q):
        target = max(1, math.ceil(total * q))
        cumulative = 0
        for value, count in ordered:
            cumulative += count
            if cumulative >= target:
                return value
    return {"count": total, "min": ordered[0][0], "max": ordered[-1][0],
            "sum": sum(v * c for v, c in ordered), "p50": quantile(.5),
            "p90": quantile(.9), "p99": quantile(.99),
            "histogram": {str(v): c for v, c in ordered}}


def run_key(event):
    return tuple(str(event.get(k, "")) for k in ("task", "agent", "workload"))


def new_run(key):
    return {"key": list(key), "events": 0, "ops": Counter(), "status": Counter(),
            "hist": defaultdict(Counter), "inodes": set(), "names": set(),
            "handles": set(), "stream_ids": set(), "intervals": [],
            "io_joint": {"read": Counter(), "write": Counter()},
            "first": None, "last": None, "init_count": 0}


def summarize(source):
    runs = {}
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as f:
        for line_no, raw in enumerate(f, 1):
            digest.update(raw)
            try:
                event = json.loads(raw)
            except Exception as exc:
                raise ValueError(f"source line {line_no}: {exc}") from exc
            key = run_key(event)
            r = runs.setdefault(key, new_run(key))
            total += 1
            r["events"] += 1
            method = event["method"]
            r["ops"][method] += 1
            r["init_count"] += method == "init"
            r["status"][str(event.get("status"))] += 1
            a = event.get("args") or {}
            for field in ("inode", "parent", "newparent", "new_parent"):
                if field in a:
                    r["inodes"].add(str(a[field]))
            for field in ("name", "newname", "new_name"):
                if field in a:
                    r["names"].add(str(a[field]))
            if "fh" in a:
                r["handles"].add(str(a["fh"]))
            for field in ("thread", "thread_id", "tid", "stream", "stream_id"):
                if field in event:
                    r["stream_ids"].add((field, str(event[field])))
            if method in ("read", "write"):
                length = number(a.get("size") if method == "read" else a.get("data_len"))
                offset = number(a.get("offset"))
                if length is not None:
                    r["hist"][method + "_length"][length] += 1
                if offset is not None:
                    r["hist"][method + "_offset"][offset] += 1
                if length is not None and offset is not None:
                    r["io_joint"][method][(length, offset)] += 1
            latency = event.get("latency_ms")
            if isinstance(latency, (int, float)):
                r["hist"]["source_latency_us"][round(latency * 1000)] += 1
            start, end = event.get("started_at"), event.get("finished_at")
            if start and end:
                s, e = datetime.fromisoformat(start).timestamp(), datetime.fromisoformat(end).timestamp()
                r["intervals"].append((s, e))
                r["first"] = min(r["first"] or s, s)
                r["last"] = max(r["last"] or e, e)
    result = []
    for index, r in enumerate(runs.values()):
        active = []
        peak = 0
        for start, end in sorted(r["intervals"]):
            while active and active[0] <= start:
                heapq.heappop(active)
            heapq.heappush(active, end)
            peak = max(peak, len(active))
        result.append({"index": index, "key": r["key"], "events": r["events"],
                       "init_count": r["init_count"], "method_counts": dict(r["ops"]),
                       "method_fraction": {k: v / r["events"] for k, v in r["ops"].items()},
                       "status_counts": dict(r["status"]),
                       "distributions": {k: distribution(v) for k, v in r["hist"].items()},
                       "working_set_proxy": {"distinct_referenced_inode_ids": len(r["inodes"]),
                                             "distinct_name_strings": len(r["names"]),
                                             "distinct_handle_ids": len(r["handles"]),
                                             "actual_paths_and_bytes": None},
                       "concurrency": {"explicit_stream_ids": len(r["stream_ids"]),
                                       "peak_overlapping_request_intervals": peak,
                                       "interval_count": len(r["intervals"]),
                                       "logical_stream_count_recoverable": False},
                       "span_seconds": (r["last"] - r["first"]) if r["first"] else None})
    return {"source_bytes": source.stat().st_size, "source_sha256": digest.hexdigest(),
            "source_events": total, "run_definition": "(task, agent, workload); init_count checked separately",
            "runs": result}, list(runs.values())


def synthesize(raw_run, summary, out, seed, event_count, streams, max_files, max_extent):
    rng = random.Random(seed)
    files = max(streams, min(max_files, len(raw_run["inodes"])))
    files = max(streams, (files // streams) * streams)
    per_stream = files // streams
    source_weights = {k: v for k, v in raw_run["ops"].items() if k in MAPPING}
    total = sum(source_weights.values())
    # Largest remainder apportionment: exact source-method mixture to rounding.
    quotas = {k: event_count * v / total for k, v in source_weights.items()}
    allocated = {k: int(v) for k, v in quotas.items()}
    for key in sorted(quotas, key=lambda k: (-(quotas[k] - allocated[k]), k))[:event_count - sum(allocated.values())]:
        allocated[key] += 1
    plan = [k for k, n in allocated.items() for _ in range(n)]
    rng.shuffle(plan)
    joint = {}
    for method in ("read", "write"):
        pairs = sorted(raw_run["io_joint"][method].items())
        joint[method] = ([p for p, _ in pairs], [n for _, n in pairs])
    generated = Counter()
    changes = Counter()
    io_hist = defaultdict(Counter)
    scaffolding = Counter()
    # Preallocation is outside the measured replay, identically on both mounts.
    extents = []
    for method in ("read", "write"):
        extents.extend(min(max_extent, max(1, off) + max(1, length))
                       for length, off in raw_run["io_joint"][method])
    extent = max(4096, min(max_extent, max(extents, default=4096)))
    fixture = {"files": files, "streams": streams, "files_per_stream": per_stream,
               "file_extent_bytes": extent, "file_content": "deterministic SHAKE256 stream per file",
               "seed": seed, "directories": [f"s{s}" for s in range(streams)]}
    with out.open("x", encoding="utf-8", newline="\n") as f:
        def emit(s, op, scaffold=False, **kw):
            row = dict(phase=0, stream=s, op=op, **kw)
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            generated[op] += 1
            if scaffold:
                scaffolding[op] += 1
        header = {"format": "mounted-fs-replay", "version": 1,
                  "payload_provenance": "synthetic",
                  "description": "AI trace statistical synthesis, NOT event replay; 4 synthetic independent streams; mapped method mixture; invented paths/FD lifetimes/payload; see synthesis.json"}
        # The initial tree fingerprint is attached after generating the common fixture.
        f.write(json.dumps(header, separators=(",", ":")) + "\n")
        for s in range(streams):
            for n in range(per_stream):
                emit(s, "open", True, fd=n+1, path=f"s{s}/f{n}.bin", access="read_write")
        for i, method in enumerate(plan):
            s = i % streams
            n = rng.randrange(per_stream)
            fd = n + 1
            path = f"s{s}/f{n}.bin"
            temp = f"s{s}/temp-{i}.bin"
            category = MAPPING[method]
            if category == "stat":
                emit(s, "stat", path=path)
            elif category in ("pread", "pwrite"):
                values, weights = joint[method]
                length, offset = rng.choices(values, weights, k=1)[0] if values else (4096, 0)
                actual_len = max(1, min(length, 1048576, extent))
                actual_offset = max(0, min(offset, extent - actual_len))
                changes[method + "_length_clamped"] += actual_len != length
                changes[method + "_offset_clamped"] += actual_offset != offset
                io_hist[method + "_length"][actual_len] += 1
                io_hist[method + "_offset"][actual_offset] += 1
                opts = {"fill": 65 + s} if category == "pwrite" else {}
                emit(s, category, fd=fd, offset=actual_offset, len=actual_len,
                     expected_bytes=actual_len, **opts)
            elif category in ("open_cycle", "create_cycle", "unlink_cycle"):
                create = category != "open_cycle"
                emit(s, "open", fd=1000000, path=temp if create else path,
                     access="read_write", create=create, exclusive=create)
                emit(s, "close", True, fd=1000000)
                if create:
                    emit(s, "unlink", category == "create_cycle", path=temp)
            elif category == "rename_cycle":
                emit(s, "rename", **{"from": path, "to": temp})
                emit(s, "rename", True, **{"from": temp, "to": path})
            elif category == "readdir":
                emit(s, "readdir", path=f"s{s}", expected_entries=per_stream)
            elif category == "fsync":
                emit(s, "fsync", fd=fd)
            elif category == "directory_cycle":
                emit(s, "mkdir", path=temp)
                emit(s, "rmdir", True, path=temp)
        for s in range(streams):
            for n in range(per_stream):
                emit(s, "fsync", True, fd=n+1)
                emit(s, "close", True, fd=n+1)
    if out.stat().st_size > 50_000_000:
        raise ValueError("generated JSONL exceeds the 50 MB cap")
    return {"classification": "按 AI trace 统计特征合成的等价负载；不是事件重放",
            "source_run": summary, "seed": seed, "selected_source_method_counts": allocated,
            "mapping": MAPPING, "omitted_source_method_counts": {k: v for k, v in raw_run["ops"].items() if k not in MAPPING},
            "generated_operation_counts": dict(generated), "scaffolding_operation_counts": dict(scaffolding),
            "generated_io_distributions": {k: distribution(v) for k, v in io_hist.items()},
            "clamping": dict(changes), "fixture": fixture,
            "trace_bytes_before_fingerprint": out.stat().st_size,
            "limitations": ["request overlap is only a concurrency proxy; original logical streams are unknown",
                            "4 independent synthetic streams are a configured smoke-test choice",
                            "source error statuses are not reproduced; synthesized operations expect success",
                            "positive existing paths replace lookup/getattr; negative lookup locality not reproduced",
                            "source file working-set size is unknown; referenced inode IDs are only a proxy",
                            "working set capped to 64 files and per-file extent to 4 MiB",
                            "flush/release/xattrs/forget and other unsupported methods are counted then omitted",
                            "write bytes are repeated synthetic fill; compression/dedup effects are not representative",
                            "operations shuffled deterministically; no original dependency/timing/hotness reconstruction"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True, help="new directory")
    p.add_argument("--run", type=int, default=0)
    p.add_argument("--events", type=int, default=100000)
    p.add_argument("--seed", type=int, default=20261025)
    p.add_argument("--streams", type=int, default=4)
    a = p.parse_args()
    if a.events < 1 or a.streams < 1:
        p.error("events and streams must be positive")
    a.output.mkdir(parents=True, exist_ok=False)
    stats, raw = summarize(a.source)
    (a.output / "trace-statistics.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    synthesis = synthesize(raw[a.run], stats["runs"][a.run], a.output / "workload.jsonl",
                           a.seed, a.events, a.streams, 64, 4 * 1024 * 1024)
    (a.output / "synthesis.json").write_text(json.dumps(synthesis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_events": stats["source_events"], "runs": len(raw),
                      "selected_run": a.run, "trace_bytes": (a.output / "workload.jsonl").stat().st_size}))


if __name__ == "__main__":
    main()
