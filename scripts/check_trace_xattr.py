#!/usr/bin/env python3
"""Pre-flight check: does a trace bundle contain operations the target mount cannot serve?

Why this exists
---------------
`full-fuse-replay` compares every replayed operation against the recorded result and
aborts on the first mismatch.  A trace captured on a filesystem that implements
extended attributes will therefore die immediately on a filesystem that does not:

    Error: "mismatch at 203: \"getxattr\" Err(Os { code: 95, kind: Unsupported ... })"

That happened with the pytest trace (getxattr expected ENODATA=61, BrewFS TiKV
MetaStore returned EOPNOTSUPP=95) after the replay had already copied the initial
tree.  Counting the risky operations first turns a wasted replay into a two-second
check.

Usage
-----
    python3 check_trace_xattr.py /srv/traces/extracted/pytest-full-02
    python3 check_trace_xattr.py --json /srv/traces/extracted/* --fail-on-xattr

Exit codes: 0 no risky ops, 3 risky ops found (with --fail-on-xattr), 2 usage error.
"""

import argparse
import collections
import glob
import json
import os
import sys

XATTR_OPS = {"getxattr", "setxattr", "listxattr", "removexattr"}


def scan(events_path):
    counts = collections.Counter()
    first_examples = {}
    total = 0
    with open(events_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            op = event.get("op")
            counts[op] += 1
            if op in XATTR_OPS and op not in first_examples:
                first_examples[op] = {
                    "seq": event.get("seq"),
                    "args": event.get("args"),
                    "errno": event.get("errno"),
                }
    return counts, first_examples, total


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundles", nargs="+", help="trace bundle directories (contain capture/events.jsonl)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--fail-on-xattr", action="store_true",
                        help="exit 3 when the bundle contains xattr operations")
    args = parser.parse_args()

    rows = []
    for bundle in args.bundles:
        for expanded in sorted(glob.glob(bundle)) or [bundle]:
            events = os.path.join(expanded, "capture", "events.jsonl")
            if not os.path.exists(events):
                rows.append({"bundle": expanded, "error": "capture/events.jsonl not found"})
                continue
            counts, examples, total = scan(events)
            xattr_total = sum(counts[op] for op in XATTR_OPS)
            rows.append({
                "bundle": expanded,
                "events": total,
                "xattr_ops": xattr_total,
                "xattr_breakdown": {op: counts[op] for op in XATTR_OPS if counts[op]},
                "first_examples": examples,
            })

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for row in rows:
            if "error" in row:
                print(f"{row['bundle']}: {row['error']}")
                continue
            verdict = "REQUIRES XATTR SUPPORT" if row["xattr_ops"] else "ok for non-xattr mounts"
            print(f"{row['bundle']}")
            print(f"  events       : {row['events']}")
            print(f"  xattr ops    : {row['xattr_ops']} ({row['xattr_breakdown'] or 'none'})")
            print(f"  verdict      : {verdict}")
            for op, example in row["first_examples"].items():
                print(f"    first {op}: seq={example['seq']} args={example['args']} errno={example['errno']}")

    if args.fail_on_xattr and any(row.get("xattr_ops") for row in rows):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
