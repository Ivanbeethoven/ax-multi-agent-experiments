#!/usr/bin/env python3
"""Benchmark a BrewFS volume that uses Redis as its metadata backend.

Why this exists
---------------
BrewFS can keep metadata in TiKV, Redis or SQLite.  Every performance number in the
main experiments came from TiKV, so the Redis/SQLite backends had only correctness
tests.  This script produces the missing "metadata backend" datapoint: how fast can a
Redis-backed volume absorb small files, and how much memory does the metadata cost?

Things that bit us, encoded here
--------------------------------
1. **Do not rely on the YAML `meta:` block for `mount`.**  `brewfs mount --config`
   ignores it and silently falls back to `sqlite::memory:`, which fails with
   "Failed to parse Redis URL sqlite::memory:".  Pass `--meta-backend/--meta-url`
   explicitly (this script does).
2. **`--s3-force-path-style` takes a value** (`true`/`false`), not a bare flag;
   a bare flag makes clap treat the mount point as its value.
3. **A remote Redis may be unreachable** even inside the same VPC (security groups).
   Prefer a local Redis on the client, or verify connectivity before mounting.
4. **Do not blame Redis for `EIO`.**  If the object store behind the volume is down,
   writes fail with `pipeline upload failed ... Connection refused`; the client only
   sees EIO.  Always start the object store first.

Usage
-----
    python3 bench_redis_meta.py \
        --binary /srv/bin/brewfs --mount-point /mnt/bfs-redis \
        --redis-url redis://127.0.0.1:6379/1 \
        --s3-bucket redis-bench --s3-endpoint http://10.0.0.13:9000 \
        --files 10000 --output results/redis-meta

Outputs
-------
`<output>/result.json`  mount_ms, files, seconds, files_per_s, redis_dbsize, redis_memory
`<output>/run.log`
"""

import argparse
import json
import os
import subprocess
import sys
import time


def say(msg, log_path):
    print(msg, flush=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def redis_cli(redis_url, command):
    """Run redis-cli against the URL's host:port/db, returning stdout or ''."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(redis_url)
    except Exception:
        return ""
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6379
    db = (parsed.path or "/0").lstrip("/") or "0"
    binary = os.environ.get("REDIS_CLI", "redis-cli")
    try:
        proc = subprocess.run([binary, "-h", host, "-p", str(port), "-n", db] + command,
                              capture_output=True, timeout=30)
    except FileNotFoundError:
        return ""
    return (proc.stdout or b"").decode("utf-8", "ignore").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--mount-point", default="/mnt/bfs-redis")
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/1")
    parser.add_argument("--s3-bucket", required=True)
    parser.add_argument("--s3-endpoint", required=True)
    parser.add_argument("--s3-region", default="us-east-1")
    parser.add_argument("--files", type=int, default=10000)
    parser.add_argument("--dirs", type=int, default=100)
    parser.add_argument("--file-size", type=int, default=4096)
    parser.add_argument("--output", default="results/redis-meta")
    parser.add_argument("--flush-db", action="store_true", help="FLUSHDB the metadata db first")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    log_path = os.path.join(args.output, "run.log")
    os.makedirs(args.mount_point, exist_ok=True)

    if args.flush_db:
        say(f"flushdb: {redis_cli(args.redis_url, ['flushdb']) or 'skipped (no redis-cli)'}", log_path)

    subprocess.run(["umount", "-l", args.mount_point], capture_output=True)
    mount_cmd = [
        args.binary, "mount", "--privileged",
        "--meta-backend", "redis", "--meta-url", args.redis_url,
        "--data-backend", "s3", "--s3-bucket", args.s3_bucket,
        "--s3-endpoint", args.s3_endpoint, "--s3-region", args.s3_region,
        "--s3-force-path-style", "true",
        args.mount_point,
    ]
    started = time.time()
    mount_proc = subprocess.Popen(mount_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mount_ms = None
    while time.time() - started < 120:
        if os.path.ismount(args.mount_point):
            mount_ms = (time.time() - started) * 1000.0
            break
        time.sleep(0.1)
    if mount_ms is None:
        say("mount FAILED", log_path)
        if mount_proc.poll() is None:
            mount_proc.terminate()
        return 1
    say(f"mount ok in {mount_ms:.0f} ms", log_path)

    payload = b"x" * args.file_size
    per_dir = max(1, args.files // args.dirs)
    base = os.path.join(args.mount_point, "bench")

    write_start = time.time()
    written = 0
    errors = 0
    for dir_index in range(args.dirs):
        directory = os.path.join(base, f"d{dir_index:04d}")
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            errors += 1
            continue
        for file_index in range(per_dir):
            path = os.path.join(directory, f"f{file_index:05d}")
            try:
                with open(path, "wb") as fh:
                    fh.write(payload)
                written += 1
            except OSError as exc:
                errors += 1
                say(f"write failed at {path}: errno={exc.errno}", log_path)
                break
    write_seconds = time.time() - write_start
    rate = written / write_seconds if write_seconds else 0.0
    say(f"wrote {written} files in {write_seconds:.1f}s ({rate:.0f} files/s), errors={errors}", log_path)

    dbsize = redis_cli(args.redis_url, ["dbsize"])
    memory = redis_cli(args.redis_url, ["info", "memory"])
    used_memory = ""
    for line in memory.splitlines():
        if line.startswith("used_memory_human"):
            used_memory = line.split(":", 1)[1].strip()

    result = {
        "mount_ms": round(mount_ms, 1),
        "files": written,
        "seconds": round(write_seconds, 2),
        "files_per_s": round(rate, 1),
        "errors": errors,
        "redis_dbsize": dbsize,
        "redis_used_memory": used_memory,
    }
    with open(os.path.join(args.output, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    say(f"result: {result}", log_path)

    subprocess.run(["umount", args.mount_point], capture_output=True)
    if mount_proc.poll() is None:
        mount_proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
