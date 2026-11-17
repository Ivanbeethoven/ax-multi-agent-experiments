"""Extract structured four-way Aliyun ecs.i5 object-storage benchmark data."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_HTML = Path(
    r"D:\free\wechat\xwechat_files\wxid_2gy7ek57jl6t21_f243\msg\file\2026-11\aliyun-i5-dynamic-block-proof-dashboard.html"
)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", type=Path, default=DEFAULT_HTML)
    ap.add_argument("--out", type=Path, default=ROOT / "data.json")
    args = ap.parse_args()

    text = args.html.read_text(encoding="utf-8")
    m = re.search(r"const DB = (\{.*?\});", text)
    if not m:
        raise RuntimeError(f"Could not find embedded DB JSON in {args.html}")
    db = json.loads(m.group(1))

    # Sanity-check expected schemas and positive measurements (no checksums/hashes).
    assert db["rangeSizes"][:7] == ["4KiB", "16KiB", "32KiB", "64KiB", "128KiB", "256KiB", "1MiB"]
    assert db["fullSizes"] == [
        "1KiB", "4KiB", "16KiB", "128KiB", "1MiB",
        "4MiB", "8MiB", "16MiB", "32MiB", "64MiB",
    ]
    for sz in db["rangeSizes"][:7]:
        for bucket, series in [
            ("r88", "dyn"), ("r88", "plain"), ("r88", "minioStock88"), ("r88", "minioDyn"),
            ("r124", "dyn"), ("r124", "dyn256"), ("r124", "plain"), ("r124", "minio"), ("r124", "minioDyn"),
        ]:
            rec = db[bucket][series][sz]
            assert rec["rps"] > 0 and rec["tp"] > 0 and rec["p99"] > 0
        for geo in ("12+4", "8+8"):
            drec = db["disk"]["range"][geo][sz]
            assert drec["stockKB"] > 0 and drec["minioDynKB"] > 0 and drec["rustDynKB"] > 0
            assert drec["mStockDiskBW"] > 0 and drec["mDynDiskBW"] > 0
            assert drec["rPlainDiskBW"] > 0 and drec["rDynDiskBW"] > 0
    assert len(db["disk"]["writeCap"]) >= 8

    payload = {
        "cluster": {
            "provider_region": "Alibaba Cloud cn-hangzhou-j",
            "nodes": 4,
            "instance_type": "ecs.i5.16xlarge",
            "per_node_limits": {
                "physical_cores": 6,
                "memory_gib": 12,
                "nvme_drives": 4,
                "network_gbps": 163.84,
            },
            "aggregate_drives": 16,
            "aggregate_memory_gib": 48,
            "workload_driver": "MinIO Warp",
            "concurrency": 64,
            "total_runs_reported": 213,
            "cold_range_dataset_gib": 50,
            "cold_range_cache_policy": "drop_caches=3 before each run",
        },
        "contenders": {
            "rustfs_dyn": "Dynamic RustFS ({64KiB, 256KiB, 1MiB, 4MiB} tiers + frame-aligned direct shard read, PR #3 1fbd48a39)",
            "rustfs_plain": "Fixed-1MiB RustFS baseline (dynamic flags off for whole-object; 1.0.5-preview.1 for cold range)",
            "minio_stock": "Stock MinIO RELEASE.2025-10-15T17-29-55Z (fixed 1MiB coding block, 12+4 and matched 8+8)",
            "minio_dyn": "Dynamic MinIO Scheme B ({64KiB, 256KiB, 1MiB, 4MiB} tiers + cross-shard direct read with 100ms hedged RS fallback)",
        },
        "rangeSizes": db["rangeSizes"],
        "fullSizes": db["fullSizes"],
        "r88": db["r88"],
        "r124": db["r124"],
        "full": {
            k: db["full"][k] for k in ("dyn", "plain", "minio", "minioDyn")
        },
        "forensic": db["forensic"],
        "disk": db["disk"],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
