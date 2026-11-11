#!/usr/bin/env python3
"""Generate deterministic C3/C4 sources and download the full trace before timing."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time


def trace_member(member,destination):
    checked=tarfile.data_filter(member,destination)
    if checked is not None and (checked.isfile() or checked.isdir()):
        if member.mode & ~0o777:
            raise ValueError('Unexpected special permission bits in trace archive')
        checked.mode=member.mode
    return checked


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets',type=Path,required=True)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--files',type=int,choices=[100000,1000000],default=1000000)
    p.add_argument('--sources-only',action='store_true',help='Prepare only C3/C4 sources, without downloading or rerunning C2')
    a=p.parse_args()
    a.root.mkdir(parents=True,exist_ok=False)
    repo=a.assets/'repo'
    receipt=a.root/'receipts'; receipt.mkdir()
    started=dict(at=datetime.now(timezone.utc).isoformat(),scope='Unmeasured deterministic input preparation',automatic_retry=False)
    (receipt/'started.json').write_text(json.dumps(started)+'\n')
    def run(argv,name):
        begin=time.monotonic()
        with (receipt/(name+'.log')).open('xb') as log:
            r=subprocess.run(list(map(str,argv)),cwd=repo,stdout=log,stderr=subprocess.STDOUT)
        (receipt/(name+'-exit.json')).write_text(json.dumps(dict(exit_code=r.returncode,wall_seconds=time.monotonic()-begin))+'\n')
        r.check_returncode()
    small=a.assets/'bin/smallfiles'
    source=a.root/('smallfiles-source' if a.files==100000 else 'million-source')
    run([small,'prepare','--root',source,'--output',receipt/'c3-prepare','--files',str(a.files)],'c3-prepare')
    run([small,'verify-base','--root',source,'--output',receipt/'c3-verify','--files',str(a.files)],'c3-verify')
    run(['python3','bench/fs_step.py','fixture','--root',a.root/'fork-source','--files',str(a.files),'--file-bytes','4096','--output',receipt/'c4-fixture.json'],'c4-fixture')
    run(['python3','bench/fs_step.py','snapshot','--root',a.root/'fork-source','--output',receipt/'c4-fixture-tree.json'],'c4-snapshot')
    if a.sources_only:
        with (receipt/'c4-fixture-tree.json').open('rb') as f:
            tree_sha=hashlib.file_digest(f,'sha256').hexdigest()
        (receipt/'result.json').write_text(json.dumps(dict(passed=True,at=datetime.now(timezone.utc).isoformat(),
            c3_files=a.files,c4_files=a.files,bytes_per_input=a.files*4096,c4_tree_sha256=tree_sha,
            trace=None,sources_only=True,scope=started['scope']),indent=2)+'\n')
        return
    run(['python3','scenarios/k8s-containers/scripts/download_archives.py','--out',a.root/'archives','--cases','pytest-full-02','--internal'],'trace-download')
    cases=a.root/'cases'; cases.mkdir()
    with tarfile.open(a.root/'archives/pytest-full-02.tar.gz') as t:
        t.extractall(cases,filter=trace_member)
    # Full strict replay is a qualification, kept separate from the timed FUSE run.
    run(['python3','experiments/unified-suite/pilot-aliyun/scripts/qualify-trace.py','--case',cases/'pytest-full-02',
         '--binary',a.assets/'bin/full-fuse-replay','--root',a.root/'strict-replay','--out',receipt/'strict-trace'],'strict-trace')
    with (receipt/'c4-fixture-tree.json').open('rb') as f:
        tree_sha=hashlib.file_digest(f,'sha256').hexdigest()
    (receipt/'result.json').write_text(json.dumps(dict(passed=True,at=datetime.now(timezone.utc).isoformat(),
        c3_files=a.files,c4_files=a.files,c4_tree_sha256=tree_sha,trace='pytest-full-02',scope=started['scope']),indent=2)+'\n')


if __name__=='__main__':
    main()
