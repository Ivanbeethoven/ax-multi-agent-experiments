#!/usr/bin/env python3
"""Benchmark overlay-workspace fork cost against a metadata-level clone.

What we measure
---------------
`brewfs workspace fork <revision> --count N` creates N overlay workspaces on top of
an immutable revision.  It writes catalog records only -- no file data and no
per-file inode copy -- so its cost is O(1) in the size of the base tree.  This is
the BrewFS counterpart of `juicefs clone`, which is O(number of files).

Two numbers matter for a fork/handoff story:
  * fork latency        -- command wall time, and per-fork amortisation for --count N
  * fork-to-usable time -- fork plus mounting the new workspace until it serves a readdir

The revision string is `<layer_id>:<sealed_version>:<root_hash_hex>`, which is not
documented in `--help`; it is parsed out of `workspace list`/`inspect` JSON here.

Gotchas encoded below
---------------------
* `workspace fork` refuses a *live* workspace: only a sealed revision can be forked.
  If the base workspace has never been sealed, the fork succeeds but is empty.
* `--data-config` is required by `fork`, while `mount` ignores the YAML `meta:` block
  and only honours CLI flags -- pass connection details explicitly to `mount`.
* A single fork costs ~60 ms, of which ~40 ms is CLI start-up; use `--count` to see
  the marginal cost.

Example
-------
    python3 bench_workspace_fork.py \
        --binary /srv/bin/brewfs \
        --meta-backend tikv --meta-tikv-pd-endpoints http://10.0.0.12:2379 \
        --workspace-namespace c4ws --data-config /etc/bench/data.yaml \
        --counts 1 10 100 --mount-check --output results/fork

Outputs
-------
`<output>/fork.tsv`   count, wall_seconds, per_fork_ms, created
`<output>/e2e.tsv`    fork_ms, mount_ms, first_readdir_ms (when --mount-check)
`<output>/*.json`     raw CLI output per invocation
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def say(msg, log_path):
    print(msg, flush=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def run(cmd, timeout=600):
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return proc, time.time() - started


def extract_json_objects(text):
    """Return every parseable JSON object/array embedded in noisy CLI output."""
    clean = ANSI.sub("", text)
    found = []
    for start in [m.start() for m in re.finditer(r"[\[{]", clean)]:
        for end in range(len(clean), start, -1):
            if clean[end - 1] not in "}]":
                continue
            try:
                value = json.loads(clean[start:end])
            except Exception:
                continue
            found.append(value)
            break
    return found


def workspace_records(text):
    for value in extract_json_objects(text):
        if isinstance(value, list) and value and isinstance(value[0], dict) and "workspace_id" in value[0]:
            return value
        if isinstance(value, dict) and "workspace_id" in value:
            return [value]
    return []


def revision_string_from(records):
    for record in records:
        revision = record.get("base_revision") or record.get("fork_base")
        if not revision:
            continue
        root_hash = revision.get("root_hash")
        if isinstance(root_hash, list):
            root_hash = "".join(f"{byte:02x}" for byte in root_hash)
        if revision.get("layer_id") and revision.get("sealed_version") and root_hash:
            return f"{revision['layer_id']}:{revision['sealed_version']}:{root_hash}"
    return ""


def parse_timestamp_ns(text):
    for value in extract_json_objects(text):
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict) and item.get("workspace_id"):
                return item["workspace_id"]
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--meta-backend", default="tikv")
    parser.add_argument("--meta-tikv-pd-endpoints", default="")
    parser.add_argument("--workspace-namespace", default="brewfs")
    parser.add_argument("--data-config", required=True)
    parser.add_argument("--revision", default="", help="layer:version:root_hash; derived from list when omitted")
    parser.add_argument("--counts", nargs="+", type=int, default=[1, 10, 100])
    parser.add_argument("--owner", default="fork-bench")
    parser.add_argument("--mount-check", action="store_true", help="also mount one fork and time first readdir")
    parser.add_argument("--mount-point", default="/mnt/fork-bench")
    parser.add_argument("--volume-format", default="workspace-v1")
    parser.add_argument("--s3-bucket", default="")
    parser.add_argument("--s3-endpoint", default="")
    parser.add_argument("--s3-region", default="us-east-1")
    parser.add_argument("--output", default="results/fork")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    log_path = os.path.join(args.output, "run.log")

    common = ["--meta-backend", args.meta_backend,
              "--workspace-namespace", args.workspace_namespace]
    if args.meta_tikv_pd_endpoints:
        common += ["--meta-tikv-pd-endpoints", args.meta_tikv_pd_endpoints]

    revision = args.revision
    if not revision:
        proc, _ = run([args.binary, "workspace", "list"] + common, timeout=300)
        records = workspace_records((proc.stdout or b"").decode("utf-8", "ignore"))
        revision = revision_string_from(records)
        if not revision:
            sys.exit("could not derive a sealed revision; pass --revision explicitly")
        say(f"derived revision: {revision[:60]}...", log_path)

    fork_tsv = os.path.join(args.output, "fork.tsv")
    with open(fork_tsv, "w", encoding="utf-8") as fh:
        fh.write("count\twall_seconds\tper_fork_ms\tcreated\n")

    created_ids = []
    for count in args.counts:
        cmd = [args.binary, "workspace", "fork", revision, "--count", str(count),
               "--data-config", args.data_config, "--owner", args.owner] + common
        proc, wall = run(cmd, timeout=900)
        text = (proc.stdout or b"").decode("utf-8", "ignore")
        with open(os.path.join(args.output, f"fork-{count}.json"), "w", encoding="utf-8") as fh:
            fh.write(text)
        created = len(workspace_records(text))
        per_fork = wall * 1000.0 / count
        say(f"fork count={count} wall={wall:.3f}s per_fork={per_fork:.2f}ms created={created} rc={proc.returncode}",
            log_path)
        with open(fork_tsv, "a", encoding="utf-8") as fh:
            fh.write(f"{count}\t{wall:.3f}\t{per_fork:.2f}\t{created}\n")
        if created and not created_ids:
            created_ids.append(parse_timestamp_ns(text))

    if args.mount_check and created_ids and created_ids[0]:
        if not (args.s3_bucket and args.s3_endpoint):
            say("mount check skipped: --s3-bucket/--s3-endpoint are required to mount", log_path)
        else:
            workspace_id = created_ids[0]
            os.makedirs(args.mount_point, exist_ok=True)
            subprocess.run(["umount", "-l", args.mount_point], capture_output=True)
            mount_cmd = [args.binary, "mount", "--privileged",
                         "--volume-format", args.volume_format,
                         "--workspace", workspace_id,
                         "--data-backend", "s3",
                         "--s3-bucket", args.s3_bucket,
                         "--s3-endpoint", args.s3_endpoint,
                         "--s3-region", args.s3_region,
                         "--s3-force-path-style", "true"] + common + [args.mount_point]
            started = time.time()
            proc = subprocess.Popen(mount_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ready_ms = None
            while time.time() - started < 120:
                if os.path.ismount(args.mount_point):
                    ready_ms = (time.time() - started) * 1000.0
                    break
                time.sleep(0.1)
            readdir_ms = None
            if ready_ms is not None:
                t0 = time.time()
                try:
                    os.listdir(args.mount_point)
                except OSError:
                    pass
                readdir_ms = (time.time() - t0) * 1000.0
            e2e = os.path.join(args.output, "e2e.tsv")
            with open(e2e, "w", encoding="utf-8") as fh:
                fh.write("workspace_id\tmount_ms\tfirst_readdir_ms\n")
                fh.write(f"{workspace_id}\t{ready_ms if ready_ms is not None else 'FAILED'}"
                         f"\t{readdir_ms if readdir_ms is not None else 'FAILED'}\n")
            say(f"mount workspace={workspace_id} mount_ms={ready_ms} readdir_ms={readdir_ms}", log_path)
            subprocess.run(["umount", args.mount_point], capture_output=True)
            if proc.poll() is None:
                proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
