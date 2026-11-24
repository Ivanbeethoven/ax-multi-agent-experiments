"""Compare the two completed 64-agent GraftFS runs by task ID on the coordinator."""
import argparse
import csv
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


def load(p):
    return json.loads(p.read_text())


def live_mounts(tag):
    found = {}
    for f in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = f.read_bytes().decode(errors="replace").split("\0")
            if len(args) < 4 or args[1] != "mount" or "--config" not in args:
                continue
            config = Path(args[args.index("--config")+1])
            if not config.stem.startswith(tag+"-") or config.stem.endswith("-ver"):
                continue
            agent = config.stem.split("-")[-1]
            data = load(config)
            found[agent] = dict(pid=int(f.parent.name), mount=data["mount_point"],
                endpoint_port=urlsplit(data["data"]["s3"]["endpoint"]).port,
                hint=args[args.index("--s3-rustfs-ec-block-size-hint")+1])
        except (FileNotFoundError, ProcessLookupError):
            continue
    return found


def paired(left, right):
    tables = []
    for p, mode, port in ((left, "on", 9000), (right, "off", 19000)):
        result = load(p/"result.json")
        if (result["state"] != "passed" or result["completed_tasks"] != 64 or
                result["parameters"]["system"] != "graftfs-native" or
                result["parameters"]["dynamic"] != mode or load(p/"backend.json")["port"] != port):
            raise ValueError(f"Unexpected replay or backend: {p}")
        rows = load(p/"batch-01/tasks.json")
        by_id = {t["task_id"]: t for t in rows}
        if len(rows) != 64 or len(by_id) != 64:
            raise ValueError(f"Expected 64 distinct agents: {p}")
        tables.append(by_id)
    l, r = tables
    if l.keys() != r.keys():
        raise ValueError("Task IDs differ")
    for agent in sorted(l):
        t, u = l[agent], r[agent]
        if any(t[k] != u[k] for k in ("case", "host", "slot", "planned_start_delay_ms")):
            raise ValueError(f"Placement or input differs: {agent}")
        if load(left/"pacing"/(agent+".json")) != load(right/"pacing"/(agent+".json")):
            raise ValueError(f"Wait schedules differ: {agent}")
        row = dict(task_id=agent, case=t["case"], source_host=t["host"])
        for task, suffix in ((t,"on"), (u,"off")):
            v = task["cross_host_verify"]
            if not v["passed"] or v["source_host"] != task["host"] or v["verify_host"] == task["host"]:
                raise ValueError(f"Cross-host verification missing: {agent}")
            if v["verify_host"] != t["cross_host_verify"]["verify_host"]:
                raise ValueError(f"Verification hosts differ: {agent}")
            rep = task["replay"]
            if any(rep[k] != t["replay"][k] for k in ("operations","actual_read_bytes","actual_write_bytes")):
                raise ValueError(f"I/O differs: {agent}")
            for label, value in (("wall",task["wall_app_seconds"]), ("fs",rep["fs_operation_seconds"]),
                    ("think",rep["think_time"]["actual_sleep_seconds"]), ("drain",task["upload_drain_seconds"]),
                    ("verify",v["verify_seconds"])):
                row[f"{label}_{suffix}_s"] = round(value,6)
        row["wall_off_minus_on_s"] = round(u["wall_app_seconds"]-t["wall_app_seconds"],6)
        yield row


def watch(root):
    import cluster_lib as cl
    from run_suite import save
    on, off = ("04-agent-replay-"+i+"-r1" for i in ("10","13"))
    dest = root/"diagnostics"/"dynamic-64-agent-comparison"
    dest.mkdir(exist_ok=True,parents=True)
    active_attempt = None
    while True:
        jobs = load(root/"status.json")["jobs"]
        if not jobs[off].get("output"):
            time.sleep(30)
            continue
        p = Path(jobs[off]["output"])
        if active_attempt != p:
            active_attempt, seen = p, {}
        if (p/"batch-01/mounts.json").is_file() and len(seen) < 64 and jobs[off]["state"] == "running":
            placement = {t["task_id"]:t["host"] for t in load(p/"tasks-plan.json")}
            for host in sorted(set(placement.values())):
                if all(a in seen for a,h in placement.items() if h == host):
                    continue
                cmd = ["python3","-","--probe",off+"-"+p.name]
                rows = json.loads(cl.run_on_host(host,cmd,input_bytes=Path(__file__).read_bytes(),log_cmd=False).stdout)
                for agent, record in rows.items():
                    if placement.get(agent) != host or record["endpoint_port"] != 19000 or record["hint"] != "off":
                        raise ValueError(f"Unexpected active mount: {host} {agent} {record}")
                    seen[agent] = dict(host=host, **record)
            save(dest/f"live-mounts-{p.name}.json",seen)
        if jobs[off]["state"] == "failed":
            raise RuntimeError(f"Replay failed: {p}")
        if jobs[off]["state"] == "passed":
            rows = list(paired(Path(jobs[on]["output"]),p))
            out = dest/"agent-times.csv"
            with out.with_suffix(".tmp").open("w",newline="") as f:
                writer = csv.DictWriter(f,fieldnames=rows[0])
                writer.writeheader()
                writer.writerows(rows)
            out.with_suffix(".tmp").replace(out)
            return
        time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--probe")
    group.add_argument("--watch",type=Path)
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(live_mounts(args.probe)))
    else:
        watch(args.watch)
