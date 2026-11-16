"""Read diagnostic fio summaries and report per-pass client object traffic."""

import argparse
import json
from pathlib import Path


def metric(path: Path, series: str) -> int:
    prefix = series + " "
    for line in path.read_text().splitlines():
        if line.startswith(prefix):
            return int(float(line[len(prefix):].split()[0]))
    raise ValueError(f"{path}: missing {series}")


def sync_stages(case: Path, phase: str) -> dict:
    before = case / f"{phase}-server-before.prom"
    after = case / f"{phase}-server-after.prom"
    if not before.is_file() or not after.is_file():
        return {}

    def aggregate(lines: list[str], stage: str, suffix: str) -> float:
        prefix = f"rustfs_s3_put_object_stage_duration_ms_{suffix}" + "{"
        return sum(
            float(line.split()[-1])
            for line in lines
            if line.startswith(prefix) and f'stage="{stage}"' in line
        )

    start = before.read_text().splitlines()
    end = after.read_text().splitlines()
    result = {}
    for stage in ("disk_fsync_worker_dispatch_wait", "disk_fsync_worker_execute"):
        count = aggregate(end, stage, "count") - aggregate(start, stage, "count")
        total_ms = aggregate(end, stage, "sum") - aggregate(start, stage, "sum")
        if count > 0:
            result[stage] = {"samples": int(count), "mean_ms": total_ms / count}
    return result


def report(root: Path):
    traffic = {
        "get_requests": 'brewfs_object_get_total{kind="upper"}',
        "get_bytes": 'brewfs_object_get_bytes_total{kind="upper"}',
        "put_requests": "brewfs_object_put_total",
        "put_bytes": "brewfs_object_put_bytes_total",
    }
    for summary in sorted(root.glob("fio-*/*/*/summary.json")):
        record = json.loads(summary.read_text())
        case = summary.parent
        parameters = (case.parent.parent / "parameters.txt").read_text().splitlines()
        for stage, metrics in record["passes"].items():
            before = case / f"{stage}-before.prom"
            after = case / f"{stage}-after.prom"
            delta = {
                name: metric(after, series) - metric(before, series)
                for name, series in traffic.items()
            }
            yield {
                "run": case.parent.parent.name,
                "operation": case.name,
                "pass": stage,
                "rustfs_binary": parameters[0],
                "fsync_workers": int(parameters[1]),
                "read_quantum": parameters[3] == "true",
                **{key: value for key, value in record.items() if key != "passes"},
                **metrics,
                **delta,
                "sync_stages": sync_stages(case, stage),
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostic_root", type=Path)
    args = parser.parse_args()
    for row in report(args.diagnostic_root):
        print(json.dumps(row))


if __name__ == "__main__":
    main()
