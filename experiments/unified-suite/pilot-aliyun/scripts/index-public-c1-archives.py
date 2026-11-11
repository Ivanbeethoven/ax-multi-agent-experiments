#!/usr/bin/env python3
"""Build the C1 public index offline from pinned sources and verified OSS receipts."""
import argparse
import json
import re
from pathlib import Path

SCHEMA = "pilot-public-archives-v1"
TRANSFER = "Coordinator to Hangzhou OSS via internal network; full internal SHA256 readback and anonymous public HEAD."


def load(path):
    return json.loads(path.read_bytes())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def entry(receipt, pin, prefix, bucket):
    name = pin["name"]
    require(re.fullmatch(r"[a-z][a-z0-9-]*\.tar\.gz", name), "Invalid archive name")
    sha, count = pin["sha256"], pin["bytes"]
    require(re.fullmatch(r"[0-9a-f]{64}", sha) and type(count) is int and count > 0, "Invalid source pin")
    key = f"{prefix}/c1-{pin['cell']}/{name}"
    url = f"https://{bucket}.oss-cn-hangzhou.aliyuncs.com/{key}"
    prepared, uploaded = receipt["prepared"], receipt["upload"]
    require(
        receipt["schema"] == "pilot-cloud-oss-archive-v1"
        and receipt["bucket"] == bucket
        and receipt["key"] == key
        and receipt["public_url"] == url
        and receipt["anonymous_public_head"] is True
        and receipt["signed_url_persisted"] is False
        and receipt["access_key_secret_sent_to_cloud"] is False
        and receipt["bytes_downloaded_to_local"] == 0,
        f"Invalid public receipt for {name}",
    )
    require(
        prepared["passed"] is True
        and prepared["source"] == uploaded["source"] == pin["source"]
        and prepared["sha256"] == uploaded["sha256"] == uploaded["readback_sha256"] == sha
        and prepared["bytes"] == uploaded["bytes"] == count
        and prepared["credential_scan"] == "passed"
        and uploaded["passed"] is True
        and uploaded["overwrite"] is False
        and uploaded["full_internal_readback"] is True,
        f"Archive source or full readback differs for {name}",
    )
    return {"name": name + ".json", "public_url": url, "bytes": count,
            "sha256": sha, "receipt": name + ".json"}


def build(manifest, cell, receipts, bucket, scope, base=None):
    require(manifest["schema"] == "pilot-coordinator-c1-publication-input-v1", "Invalid source manifest")
    require(re.fullmatch(r"pilot-results/[0-9]{8}", manifest["prefix"]), "Invalid OSS prefix")
    require(re.fullmatch(r"[0-9a-f]{16}-r[1-9][0-9]*-[a-z][a-z0-9-]*", cell), "Invalid cell")
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket), "Invalid OSS bucket")
    index = dict(base) if base is not None else {
        "schema": SCHEMA, "cell_id": cell, "archives": [], "metadata": [],
        "archive_bytes": 0, "bytes_downloaded_to_local": 0, "transfer": TRANSFER,
    }
    require(index["schema"] == SCHEMA and index["cell_id"] == cell, "Wrong existing cell index")
    archives = {}
    for old in index["archives"]:
        name = old["name"]
        require(re.fullmatch(r"[a-z][a-z0-9-]*\.tar\.gz\.json", name), "Invalid existing receipt name")
        require(name == old["receipt"] and name not in archives, "Duplicate or ambiguous receipt")
        doc = load(receipts / name)
        pin = dict(cell=cell, name=name.removesuffix(".json"), bytes=old["bytes"],
                   sha256=old["sha256"], source=doc["prepared"]["source"])
        expected = entry(doc, pin, manifest["prefix"], bucket)
        require(all(old[k] == value for k, value in expected.items()), "Existing index differs from receipt")
        archives[name] = old
    require(sum(a["bytes"] for a in archives.values()) == index["archive_bytes"], "Existing archive total differs")
    pending = [pin for pin in manifest["files"] if pin["cell"] == cell]
    require(pending, "No pinned archive for this cell")
    seen = set()
    for pin in pending:
        require(re.fullmatch(r"[a-z][a-z0-9-]*\.tar\.gz", pin["name"]), "Invalid source archive name")
        name = pin["name"] + ".json"
        require(name not in seen, "Duplicate pinned archive")
        seen.add(name)
        new = entry(load(receipts / name), pin, manifest["prefix"], bucket)
        if name in archives:
            require(all(archives[name][k] == value for k, value in new.items()), "Existing and pinned archives differ")
        else:
            archives[name] = new
    index["scope"] = scope
    index["archives"] = list(archives.values())
    index["archive_bytes"] = sum(a["bytes"] for a in index["archives"])
    return index


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--cell", required=True)
    p.add_argument("--receipts", type=Path, required=True)
    p.add_argument("--bucket", default="brewfs-agent-traces-20261023-4ccdae73")
    p.add_argument("--scope", required=True)
    p.add_argument("--base", type=Path)
    p.add_argument("--out", type=Path, required=True, help="New output path; never overwrites existing index")
    a = p.parse_args()
    index = build(load(a.manifest), a.cell, a.receipts, a.bucket, a.scope,
                  load(a.base) if a.base else None)
    with a.out.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
