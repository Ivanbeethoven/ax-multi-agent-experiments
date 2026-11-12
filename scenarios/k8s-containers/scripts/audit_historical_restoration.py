#!/usr/bin/env python3
"""Cross-check the historical OSS restoration and a downloaded metadata bundle."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.parse import urlsplit


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def index(rows, field):
    result = {row[field]: row for row in rows}
    require(len(result) == len(rows), f"duplicate {field}")
    return result


def audit(root, metadata):
    catalog, catalog_sha = load(root / "archives.json")
    migration, migration_sha = load(root / "validation/drive-migration-2026-11-10.json")
    receipts = root / "validation/historical-oss-restored"
    restore, restore_sha = load(receipts / "restoration.json")
    public, public_sha = load(receipts / "public-readback-20261112.json")
    prefix = migration["source"]["prefix"]
    bucket = migration["source"]["bucket"]
    require(restore["complete"] and restore["source_manifest_sha256"] == migration_sha
            and public["restoration_sha256"] == restore_sha
            and public["catalog_sha256"] == catalog_sha
            and restore["bucket"] == catalog["bucket"] == bucket
            and restore["prefix"] == public["prefix"] == catalog["prefix"] + "/" == prefix,
            "catalog, migration, restoration and anonymous receipt pins differ")
    original = index(migration["objects"], "oss_key")
    uploaded = index(restore["objects"], "oss_key")
    heads = index(public["heads"], "key")
    require(original.keys() == uploaded.keys() == heads.keys() and len(original) == 28,
            "different restored object set")
    total = sum(row["bytes"] for row in original.values())
    require(total == migration["source"]["bytes"] == restore["bytes"]
            == public["source_bytes"] == 10480588662, "restored byte count differs")
    require(public["public_list"] == {
        "http": 200, "keys": 28, "truncated": False, "extra": [], "missing": []
    }, "anonymous listing differs")
    for key, obj in original.items():
        up, head = uploaded[key], heads[key]
        require(key.startswith(prefix) and obj["bytes"] == up["bytes"] == head["bytes"]
                and obj["sha256"] == up["sha256"] == head["sha256_meta"]
                and up["verified"] and head["http"] == 200 and head["matches"]
                and up["local_crc64"] and up["local_crc64"] == up["remote_crc64"] == head["crc64"]
                and up["remote_etag"] == head["etag"],
                f"restoration size, SHA or CRC differs: {key}")
    manifest_bodies = index(public["manifest_bodies"], "key")
    for key, row in manifest_bodies.items():
        require(row["sha256"] == original[key]["sha256"] and row["bytes"] == original[key]["bytes"]
                and row["digest_matches"] and row["entries_match_restoration"],
                f"downloaded standalone manifest differs: {key}")
    ranges = index(public["archives"], "case")
    endpoint = f"https://{bucket}.oss-cn-hangzhou.aliyuncs.com"
    deepseek = index(public["standalone_deepseek_manifests"], "case")
    require(len(catalog["archives"]) == 7 and len(catalog["metadata"]) == 1
            and len(ranges) == 8 and len(deepseek) == 3 and len(manifest_bodies) == 7
            and public["endpoint"] == endpoint, "unexpected case scope")
    case_rows = []
    for entry in catalog["archives"] + catalog["metadata"]:
        key = urlsplit(entry["uri"]).path.lstrip("/")
        ranged = ranges[entry.get("case", "metadata")]
        require(entry["uri"] == f"oss://{bucket}/{key}" and entry["url"] == entry["https_url"] == f"{endpoint}/{key}"
                and ranged["key"] == key and ranged["whole_bytes"] == entry["bytes"] == original[key]["bytes"]
                and ranged["sha256"] == entry["sha256"] == original[key]["sha256"]
                and ranged["range_http"] == 206 and ranged["range_bytes"] == 1
                and ranged["catalog_matches"] and ranged["range_matches"], f"archive path or range differs: {key}")
        if entry in catalog["archives"]:
            case_rows.append({"case": entry["case"], "key": key, "bytes": entry["bytes"],
                              "sha256": entry["sha256"], "range_request_id": ranged["request_id"],
                              "downloaded_archive_sha256": None})
    meta_entry = catalog["metadata"][0]
    require(metadata.stat().st_size == meta_entry["bytes"], "metadata bundle length differs")
    payload = metadata.read_bytes()
    meta_sha = hashlib.sha256(payload).hexdigest()
    require(meta_sha == meta_entry["sha256"], "downloaded metadata bundle SHA differs")
    standalone = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        members = index([{"path": m.name, "member": m} for m in tar], "path")
        for row in case_rows:
            key = row["key"]
            if row["case"] not in deepseek:
                manifest_key = key.removesuffix(".tar.gz") + ".manifest.json"
                require(manifest_key in manifest_bodies, f"missing standalone manifest: {key}")
                row["manifest"] = {"key": manifest_key, "sha256": manifest_bodies[manifest_key]["sha256"]}
                standalone += 1
                continue
            entry = next(e for e in catalog["archives"] if e["case"] == row["case"])
            base = f"metadata/cases/{entry['root_dir']}"
            info = deepseek[row["case"]]
            require(info["http"] == 404 and info["catalog_verification_source"] == entry["verification_source"]
                    == f"{base}/archive-manifest.json and result.json", f"unexpected nested location: {key}")
            body_refs = {}
            values = {}
            for name in ("archive-manifest.json", "result.json"):
                path = f"{base}/{name}"
                member = members[path]["member"]
                require(member.isfile() and 0 < member.size < 4096, f"unexpected metadata member: {path}")
                raw = tar.extractfile(member).read()
                values[name] = json.loads(raw)
                body_refs[name] = {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            files = index(values["archive-manifest.json"]["files"], "name")
            archive_name = key.rsplit("/", 1)[1]
            filelist_name = archive_name.removesuffix(".tar.gz") + ".files.jsonl.gz"
            require(files.keys() == {archive_name, filelist_name}, f"unexpected manifest entries: {key}")
            for name, file_row in files.items():
                expected = original[key.rsplit("/", 1)[0] + "/" + name]
                require((file_row["bytes"], file_row["sha256"]) == (expected["bytes"], expected["sha256"]),
                        f"nested manifest entry differs: {name}")
            row["manifest"] = body_refs["archive-manifest.json"]
            row["result"] = body_refs["result.json"]
    require(standalone == 4, "unexpected standalone manifest count")
    return {
        "schema": "ax-historical-trace-manifest-audit-v1",
        "source_pins": {"catalog": catalog_sha, "migration": migration_sha, "restoration": restore_sha,
                        "anonymous_readback": public_sha},
        "original_prefix": f"oss://{bucket}/{prefix}", "restored_objects": len(original),
        "restored_bytes": total, "crc_size_metadata_and_listing_match": True,
        "downloaded_metadata": {"key": urlsplit(meta_entry["uri"]).path.lstrip("/"),
                                "bytes": len(payload), "sha256": meta_sha},
        "standalone_archive_manifests": standalone, "nested_archive_manifests": len(deepseek),
        "whole_trace_archive_downloads_verified_in_this_audit": 0, "cases": case_rows,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--metadata", type=Path, required=True, help="verified metadata.tar.gz from download_archives.py")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    report = audit(Path(__file__).resolve().parents[1], args.metadata)
    with args.out.open("x", encoding="utf-8", newline="\n") as dest:
        json.dump(report, dest, indent=2)
        dest.write("\n")


if __name__ == "__main__":
    main()
