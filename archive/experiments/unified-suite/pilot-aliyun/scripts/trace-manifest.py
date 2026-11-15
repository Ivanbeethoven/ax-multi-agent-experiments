#!/usr/bin/env python3
"""Hash one complete trusted trace outside its input tree; never runs a workload."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import json
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO));sys.path.insert(0,str(REPO/'bench'))
from bench.runtime.common import digest,load,write_new
from bench.trace_checks import inputs


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();case=a.case.resolve(strict=True);out=a.out.absolute()
    if out.is_relative_to(case) or case.is_relative_to(out):raise ValueError('Output must be outside the case')
    metrics=load(case/'metrics.json')
    if metrics['callbacks']<=0 or set(metrics['namespaces'])!={'repo','state'}:
        raise ValueError('Complete repository and Agent-state trace required')
    required=['repo','state/home','state/data','state/state','state/config','state/cache']
    if any(not (case/'initial'/name).is_dir() for name in required):raise ValueError('Initial repository/HOME/XDG tree missing')
    out.mkdir(parents=True,exist_ok=False)
    files={}
    paths=[case/name for name in ('metrics.json','initial-tree.json','final-tree.json','hardlink-check.json','capture/events.jsonl')]
    paths.extend((case/'capture/blobs').rglob('*'));paths.extend((case/'initial').rglob('*'))
    for f in sorted(paths):
        if f.is_symlink():
            if not f.resolve(strict=True).is_relative_to(case):raise ValueError('Input link escapes case')
            continue
        if f.is_file():files[f.relative_to(case).as_posix()]=digest(f)
    manifest=out/'manifest.json';write_new(manifest,files)
    checked=inputs(case,manifest,digest(manifest))
    write_new(out/'result.json',dict(passed=checked['passed'],at=datetime.now(timezone.utc).isoformat(),
        case=str(case),manifest=dict(path=str(manifest),sha256=digest(manifest)),regular_files=len(files),
        callbacks=metrics['callbacks'],actual_read_bytes=metrics['actual_read_bytes'],actual_write_bytes=metrics['actual_write_bytes'],
        namespaces=required,scope='Required replay inputs and repository/HOME/XDG hashes; generated backing/replayed trees, grading files and historical outputs excluded; no workload or route qualification'))
    print(json.dumps(load(out/'result.json')))


if __name__=='__main__':main()
