"""Read existing replay results, mount logs and collector segments for a truncate timeout."""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path


GAUGES = ("writeback_dirty_bytes", "writeback_remote_upload_inflight_bytes",
          "writeback_pending_upload_bytes", "writeback_metadata_inflight",
          "request_object_put_queue_wait_queue_depth", "request_object_put_queue_depth",
          "request_metadata_rpc_wait_queue_depth", "request_metadata_commit_queue_depth",
          "request_file_lock_wait_queue_depth", "request_flush_wait_queue_depth")
COUNTERS = ("s3_put_ops_total", "s3_put_bytes_total", "s3_put_lat_us_total",
            "request_metadata_commit_ns_count", "request_metadata_commit_ns_sum",
            "writeback_commit_wait_upload_us_total")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def read_json(path):
    return json.loads(path.read_text())


def failures(directory, tag):
    failed = {}
    for p in directory.glob("c2-tasks/" + tag + "-*/a*-replay.json"):
        try:
            row = read_json(p)
        except json.JSONDecodeError:
            continue
        if row.get("completed") is False:
            failed[p.name.split("-")[0]] = dict(seq=row.get("seq"), event=row.get("event", {}).get("op"),
                                                actual=row.get("actual"), replay=str(p))
    return failed


def collect(directory, tag, failed):
    windows = {}
    for task, result in failed.items():
        if result["event"] != "truncate":
            continue
        logs = list(directory.glob(f"mount-logs/{tag}-*-{task}.log"))
        if len(logs) != 1:
            continue
        with logs[0].open(errors="replace") as stream:
            for line in stream:
                if "truncate path: flush failed" not in line:
                    continue
                line = ANSI.sub("", line).strip()
                end = dt.datetime.fromisoformat(line.split()[0].replace("Z", "+00:00")).timestamp()
                elapsed = int(re.search(r"elapsed_ms: (\d+)", line)[1]) / 1000
                result.update(log=str(logs[0]), error_line=line, elapsed_s=elapsed,
                              timeout_utc=end, samples=[])
                windows[logs[0].stem] = (end - elapsed - 8, end + 20, result)
                break
    if not windows:
        return failed
    first = min(v[0] for v in windows.values())
    last = max(v[1] for v in windows.values())
    for segment in directory.glob("samples-seg-*.jsonl.zst"):
        # Closed segments are timestamped on compression. Use the sample UTC
        # itself for selection, allowing two minutes for segment rotation.
        stamp = segment.stat().st_mtime
        if not first - 5 <= stamp <= last + 120:
            continue
        with subprocess.Popen(["zstd", "-qdc", str(segment)], stdout=subprocess.PIPE) as child:
            for line in child.stdout:
                snapshot = json.loads(line)
                ts = snapshot["utc_ns"] / 1e9
                if not first <= ts <= last:
                    continue
                for mount in snapshot["mounts"]:
                    window = windows.get(mount["id"])
                    if window is None or not window[0] <= ts <= window[1]:
                        continue
                    item = window[2]
                    metrics = mount.get("metrics") or {}
                    item["samples"].append(dict(relative_s=round(ts-item["timeout_utc"], 3),
                        segment=segment.name, index=snapshot["index"],
                        scrape_error=mount.get("scrape_error"),
                        cpu_stat=(mount.get("cgroup") or {}).get("cpu_stat"),
                        metrics={k:metrics["brewfs_"+k] for k in GAUGES+COUNTERS if "brewfs_"+k in metrics}))
            if child.wait():
                raise RuntimeError(f"Cannot decode {segment}")
    for _, _, item in windows.values():
        item["samples"].sort(key=lambda x:x["relative_s"])
    return failed


def coordinator(args):
    import cluster_lib as cl
    from run_suite import save
    root = cl.RUN_ROOT
    tag = args.job + "-" + args.attempt
    dest = root / "diagnostics" / tag
    dest.mkdir(exist_ok=True, parents=True)
    receipt = dest / "truncate-window.json"
    code = Path(__file__).read_bytes()
    previous = None
    while True:
        row = read_json(root/"status.json")["jobs"][args.job]
        current = Path(row.get("output", ".")).name
        if current != args.attempt:
            if not args.watch:
                raise ValueError(f"current attempt is {current}")
            if current > args.attempt or row["state"] == "passed":
                return
            time.sleep(45)
            continue
        terminal = row["state"] in ("passed","failed")
        placement = Path(row["output"]) / "replay-placement.json"
        if args.watch and not terminal and not placement.is_file():
            time.sleep(10)
            continue
        hosts = read_json(placement)["hosts"]
        def query(host, detailed):
            argv = ["nice","-n","15","python3","-","--worker",str(root/"hosts"/host),"--job",args.job,
                    "--attempt",args.attempt] + (["--detail"] if detailed else [])
            return json.loads(cl.run_on_host(host, argv, input_bytes=code, log_cmd=False, timeout=180).stdout)
        if terminal and row["state"] == "passed":
            checks = [t["cross_host_verify"] for f in Path(row["output"]).glob("batch-*/tasks.json") for t in read_json(f)]
            save(receipt, dict(job=args.job,attempt=current,state="passed",observed_at=cl.utc_iso(),
                               cross_host_verified=sum(bool(c["passed"] and c["source_host"]!=c["verify_host"]) for c in checks),
                               result=str(Path(row["output"])/"result.json")))
            return
        issues = {h:query(h,False) for h in hosts}
        signature = {h:sorted(v) for h,v in issues.items()}
        if signature != previous or terminal or not args.watch:
            for host, errors in issues.items():
                if errors:
                    issues[host] = query(host,True)
            save(receipt,dict(job=args.job,attempt=current,state=row["state"],observed_at=cl.utc_iso(), hosts=issues))
        if not args.watch or terminal:
            return
        previous = signature
        time.sleep(90)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--job",required=True)
    p.add_argument("--attempt",required=True)
    p.add_argument("--worker",type=Path)
    p.add_argument("--detail",action="store_true")
    p.add_argument("--watch",action="store_true")
    args = p.parse_args()
    if args.worker:
        tag = args.job+"-"+args.attempt
        failed = failures(args.worker,tag)
        print(json.dumps(collect(args.worker,tag,failed) if args.detail else failed))
    else:
        coordinator(args)
