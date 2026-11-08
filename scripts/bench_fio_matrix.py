#!/usr/bin/env python3
"""FIO matrix benchmark for comparing two mounted filesystems.

This encodes the protocol that produced trustworthy numbers after several broken
attempts.  Each of the rules below exists because we got a wrong answer without it.

Protocol rules (and why)
------------------------
1. **One fresh file per cell** (`--filename=<cell>.dat`, deleted afterwards).
   Overwriting a previously written file is dramatically faster than creating one:
   measured on the same mount, create-new 168-257 MiB/s vs overwrite 383-386 MiB/s.
   Mixing the two silently inflates "write" bandwidth.

2. **`--percentile_list=50:95:99` with COLONS.**
   fio 3.36 accepts a comma list but then only emits the 50th percentile; the
   colon form yields all requested percentiles.  Percentiles are read from
   `job[<direction>].clat_ns.percentile` (not `lat_ns`, which only has min/max/mean).

3. **Cold reads restart the object store.**
   `drop_caches` alone leaves the object-store page cache intact, so reads stay
   warm.  Pass `--object-restart-cmd` and it is invoked before every cold-read cell.

4. **Disk guard.** Abort rather than fill the object store: 1 GiB cells times dozens
   of cells is enough to hit a full disk, and a full object store produces nonsense
   (fsync stalls of many minutes, FUSE mount death).

5. **Systems alternate** within a repetition so that drift affects both equally.

6. **Serial only.** Running two lanes against one object store collapses throughput
   (observed JuiceFS 603 -> 14.3 MiB/s under parallel load).

Example
-------
    python3 bench_fio_matrix.py \
        --system juicefs=/mnt/jfs-bench --system brewfs=/mnt/bfs-bench \
        --sizes 1M 4M 64M --ops write randwrite randrw coldread --reps 3 \
        --cell-size 1G --disk-path /srv/data --min-free-gb 80 \
        --object-restart-cmd "bash scripts/object_store_ctl.sh restart \
            --ssh-host root@10.0.0.13 --process rustfs \
            --start '/srv/bin/rustfs --address 0.0.0.0:9000 /srv/data' \
            --health http://127.0.0.1:9000/health" \
        --output results/fio-matrix

Outputs
-------
`<output>/cells.tsv`     one row per cell (system, op, bs, rep, bw, iops, p50/95/99, wall, bytes, errors)
`<output>/summary.md`    per (system, op, bs) median table
`<output>/fio/*.json`    raw fio output per cell
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict

DIRECT_ARGS = [
    "--direct=1",
    "--ioengine=libaio",
    "--iodepth=16",
    "--numjobs=1",
    "--group_reporting=1",
    "--percentile_list=50:95:99",
]

OPS = {
    "write": ["--rw=write", "--end_fsync=1"],
    "randwrite": ["--rw=randwrite", "--end_fsync=1"],
    "randrw": ["--rw=randrw", "--rwmixread=70"],
    "read": ["--rw=read"],
}


def say(msg, log_path):
    print(msg, flush=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def free_gb(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e9


def drop_caches():
    try:
        with open("/proc/sys/vm/drop_caches", "w", encoding="ascii") as fh:
            fh.write("3")
    except OSError:
        pass


def percentile_ms(direction, key):
    lat = (direction or {}).get("clat_ns") or {}
    value = (lat.get("percentile") or {}).get(key)
    if not isinstance(value, (int, float)):
        return None
    return round(value / 1e6, 3)


def run_fio(mount_dir, name, bs, op, size, out_dir):
    json_path = os.path.join(out_dir, f"{name}.json")
    cmd = [
        "fio",
        f"--name={name}",
        f"--directory={mount_dir}",
        f"--filename={name}.dat",
        f"--bs={bs}",
        f"--size={size}",
        "--output-format=json",
        f"--output={json_path}",
    ] + DIRECT_ARGS + OPS[op]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, timeout=3600)
    wall = time.time() - started
    if proc.returncode != 0 or not os.path.exists(json_path):
        return None, wall, (proc.stderr or b"")[:400].decode("utf-8", "ignore")
    job = json.load(open(json_path))["jobs"][0]
    write, read = job.get("write", {}) or {}, job.get("read", {}) or {}
    dominant = write if write.get("io_bytes", 0) >= read.get("io_bytes", 0) else read
    return {
        "bw_mib": (write.get("bw_bytes", 0) + read.get("bw_bytes", 0)) / 2 ** 20,
        "iops": write.get("iops", 0) + read.get("iops", 0),
        "p50": percentile_ms(dominant, "50.000000"),
        "p95": percentile_ms(dominant, "95.000000"),
        "p99": percentile_ms(dominant, "99.000000"),
        "read_mib": read.get("io_bytes", 0) / 2 ** 20,
        "write_mib": write.get("io_bytes", 0) / 2 ** 20,
        "errors": (read.get("total_errs", 0) or 0) + (write.get("total_errs", 0) or 0),
    }, wall, None


def cleanup(directory):
    for entry in os.listdir(directory):
        if entry.endswith(".dat"):
            try:
                os.remove(os.path.join(directory, entry))
            except OSError:
                pass


def median_or_dash(rows, key):
    values = [float(r[key]) for r in rows if r[key] not in ("", "None", None)]
    return f"{statistics.median(values):.1f}" if values else "—"


def build_summary(rows, systems):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["system"], row["op"], row["bs"])].append(row)

    ops = [op for op in ("write", "randwrite", "randrw", "coldread", "read")]
    sizes = sorted({row["bs"] for row in rows}, key=lambda s: int(s.rstrip("Mm")))
    header = ["op", "bs"]
    for system in systems:
        header += [f"{system} bw", f"{system} p50", f"{system} p95", f"{system} p99"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for op in ops:
        for size in sizes:
            cells = [op, size]
            any_row = False
            for system in systems:
                group = grouped.get((system, op, size), [])
                if group:
                    any_row = True
                cells += [
                    median_or_dash(group, "bw_mib_s"),
                    median_or_dash(group, "p50_ms"),
                    median_or_dash(group, "p95_ms"),
                    median_or_dash(group, "p99_ms"),
                ]
            if any_row:
                lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--system", action="append", required=True, metavar="NAME=DIR",
                        help="mounted filesystem to benchmark; repeat for each system")
    parser.add_argument("--sizes", nargs="+", default=["1M", "4M", "64M"])
    parser.add_argument("--ops", nargs="+", default=["write", "randwrite", "randrw", "coldread"],
                        choices=["write", "randwrite", "randrw", "coldread", "read"])
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--cell-size", default="1G")
    parser.add_argument("--disk-path", default="/",
                        help="path whose free space guards the run")
    parser.add_argument("--min-free-gb", type=float, default=80.0)
    parser.add_argument("--object-restart-cmd", default="",
                        help="shell command that restarts the object store (cold reads only)")
    parser.add_argument("--output", default="results/fio-matrix")
    args = parser.parse_args()

    systems = []
    for spec in args.system:
        if "=" not in spec:
            parser.error(f"--system expects NAME=DIR, got {spec!r}")
        name, directory = spec.split("=", 1)
        if not os.path.isdir(directory):
            parser.error(f"mount directory does not exist: {directory}")
        systems.append((name, directory))

    raw_dir = os.path.join(args.output, "fio")
    os.makedirs(raw_dir, exist_ok=True)
    log_path = os.path.join(args.output, "run.log")
    tsv_path = os.path.join(args.output, "cells.tsv")

    with open(tsv_path, "w", encoding="utf-8") as fh:
        fh.write("system\top\tbs\trep\tbw_mib_s\tiops\tp50_ms\tp95_ms\tp99_ms\twall_s"
                 "\tread_mib\twrite_mib\terrors\n")

    rows = []
    say(f"start: systems={[s for s, _ in systems]} sizes={args.sizes} ops={args.ops} reps={args.reps}", log_path)

    for size in args.sizes:
        for op in args.ops:
            for rep in range(1, args.reps + 1):
                for name, directory in systems:
                    remaining = free_gb(args.disk_path)
                    if remaining < args.min_free_gb:
                        say(f"ABORT: free space {remaining:.0f} GB < {args.min_free_gb:.0f} GB", log_path)
                        return 2

                    name_base = f"{name}-{op}-{size}-r{rep}"
                    if op == "coldread":
                        if not args.object_restart_cmd:
                            say("coldread requires --object-restart-cmd; skipping", log_path)
                            continue
                        prep, wall, err = run_fio(directory, f"prep-{name_base}", size, "write",
                                                  args.cell_size, raw_dir)
                        if prep is None:
                            say(f"{name_base}: PREP FAILED {err}", log_path)
                            continue
                        restart = subprocess.run(args.object_restart_cmd, shell=True,
                                                 capture_output=True, timeout=300)
                        restart_out = (restart.stdout or b"").decode("utf-8", "ignore").strip().splitlines()
                        drop_caches()
                        result, wall, err = run_fio(directory, name_base, size, "read", args.cell_size, raw_dir)
                        note = restart_out[-1] if restart_out else f"rc={restart.returncode}"
                    else:
                        result, wall, err = run_fio(directory, name_base, size, op, args.cell_size, raw_dir)
                        note = ""

                    if result is None:
                        say(f"{name_base}: FAILED {err}", log_path)
                        continue

                    say(f"{name_base}: bw={result['bw_mib']:.1f}MiB/s iops={result['iops']:.0f} "
                        f"p50={result['p50']}ms p95={result['p95']}ms p99={result['p99']}ms "
                        f"wall={wall:.1f}s errs={result['errors']} {note}", log_path)

                    recorded_op = "coldread" if op == "coldread" else op
                    rows.append({
                        "system": name,
                        "op": recorded_op,
                        "bs": size,
                        "rep": rep,
                        "bw_mib_s": f"{result['bw_mib']:.2f}",
                        "iops": f"{result['iops']:.0f}",
                        "p50_ms": result["p50"],
                        "p95_ms": result["p95"],
                        "p99_ms": result["p99"],
                        "wall_s": f"{wall:.2f}",
                        "read_mib": f"{result['read_mib']:.1f}",
                        "write_mib": f"{result['write_mib']:.1f}",
                        "errors": result["errors"],
                    })
                    with open(tsv_path, "a", encoding="utf-8") as fh:
                        fh.write(
                            f"{name}\t{recorded_op}\t{size}\t{rep}\t{result['bw_mib']:.2f}\t{result['iops']:.0f}\t"
                            f"{result['p50']}\t{result['p95']}\t{result['p99']}\t{wall:.2f}\t"
                            f"{result['read_mib']:.1f}\t{result['write_mib']:.1f}\t{result['errors']}\n")
                    cleanup(directory)

    summary_path = os.path.join(args.output, "summary.md")
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write("# fio matrix summary (medians)\n\n")
        fh.write(build_summary(rows, [name for name, _ in systems]))
        fh.write("\n")
    say(f"done: {len(rows)} cells -> {tsv_path}", log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
