#!/usr/bin/env python3
"""Summarize validated workload CSVs with cache and kernel-cache labels."""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

HEADER = ["cell_id", "config", "workload", "params", "rep", "throughput_mibps", "iops", "p99_us", "read_bytes", "status", "note"]


def paths_from(values: list[str], default: Path) -> list[Path]:
    if values:
        out: list[Path] = []
        for value in values:
            out.extend(Path(x) for x in glob.glob(value, recursive=True))
            if not glob.glob(value, recursive=True): out.append(Path(value))
        return out
    return sorted(default.glob("*.csv"))


def load_ceiling(path: Path | None) -> dict[str, Any]:
    if path is None: return {}
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    result: dict[str, Any] = {}
    for f in files:
        try:
            with f.open(encoding="utf-8") as fh: obj=json.load(fh)
        except (OSError, json.JSONDecodeError): continue
        result[f.stem] = obj
    return result


def ceiling_for(ceilings: dict[str, Any], config: str, workload: str) -> float | None:
    wanted = 'write' if workload == 'write' else 'randread' if workload == 'randread' else None
    # Match by config name (C2/C3/C4/C5) where possible, then fall back to the
    # sole supplied file.  Both historical and current G2 schemas are accepted.
    config_key = config.split('-', 1)[0].lower()
    candidates = [v for k, v in ceilings.items() if config_key in k.lower()]
    candidates += list(ceilings.values()) if not candidates else []
    for obj in candidates:
        item = obj.get('seq_write_1MiB') if workload == 'write' else obj.get('rand_read_4KiB') if workload == 'randread' else None
        if item is None and wanted:
            item = next((x for x in obj.get('workloads', []) if x.get('workload') == wanted), None)
        if item:
            if item.get('throughput_mibps') is not None: return float(item['throughput_mibps'])
            if item.get('bw_bytes') is not None: return float(item['bw_bytes']) / 1048576
    return None


def fnum(value: str) -> float | None:
    try: return float(value) if value != '' else None
    except ValueError: return None


def fmt(value: float | None) -> str:
    return '' if value is None else f'{value:.3f}'


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('files', nargs='*', help='CSV files/globs; default is fixtures')
    ap.add_argument('--fixtures', type=Path, default=Path(__file__).with_name('fixtures'))
    ap.add_argument('--ceilings', type=Path, help='ceilings.json or directory containing per-config ceilings')
    ap.add_argument('--output-md', type=Path, help='markdown output (default stdout)')
    ap.add_argument('--output-json', type=Path, help='summary JSON output')
    args=ap.parse_args()
    paths=paths_from(args.files, args.fixtures)
    if not paths: ap.error('no CSV inputs')
    ceilings=load_ceiling(args.ceilings)
    groups: dict[tuple[str,str,str], list[dict[str,Any]]] = defaultdict(list)
    for path in paths:
        with path.open(newline='', encoding='utf-8-sig') as fh:
            reader=csv.DictReader(fh)
            if reader.fieldnames != HEADER: raise SystemExit(f'{path}: validate.py must pass before summarize.py')
            for row in reader:
                row['_throughput']=fnum(row['throughput_mibps']); row['_iops']=fnum(row['iops']); row['_p99']=fnum(row['p99_us'])
                annotations=[]
                limit=ceiling_for(ceilings, row['config'], row['workload'])
                if limit is not None and row['_throughput'] is not None and row['_throughput'] > limit:
                    annotations.append('cache-hit')
                if row['workload']=='stat' and row['_iops'] is not None and row['_iops'] > 100000:
                    annotations.append('kernel-cache')
                row['_annotations']=annotations
                groups[(row['workload'], row['params'], row['config'])].append(row)
    entries=[]
    for (workload, params, config), rows in sorted(groups.items()):
        def stats(key):
            vals=[r[key] for r in rows if r[key] is not None]
            return {'min':min(vals) if vals else None, 'median':statistics.median(vals) if vals else None, 'max':max(vals) if vals else None}
        annotations=sorted({a for r in rows for a in r['_annotations']})
        entries.append({'workload':workload,'params':params,'config':config,'throughput_mibps':stats('_throughput'),'iops':stats('_iops'),'p99_us':stats('_p99'),'annotations':annotations,'rows':len(rows)})
    md=['| workload | params | config | throughput MiB/s (min / median / max) | IOPS (min / median / max) | p99 us (min / median / max) | labels |','|---|---|---|---:|---:|---:|---|']
    for e in entries:
        def triple(key):
            s=e[key]; return ' / '.join(fmt(s[x]) for x in ('min','median','max'))
        params = e['params'].replace('|', '\\|')
        md.append(f"| {e['workload']} | {params} | {e['config']} | {triple('throughput_mibps')} | {triple('iops')} | {triple('p99_us')} | {', '.join(e['annotations'])} |")
    text='\n'.join(md)+'\n'
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True); args.output_md.write_text(text,encoding='utf-8')
    else: print(text, end='')
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps({'inputs':[str(p) for p in paths],'entries':entries},indent=2)+'\n',encoding='utf-8')
    return 0

if __name__=='__main__': raise SystemExit(main())
