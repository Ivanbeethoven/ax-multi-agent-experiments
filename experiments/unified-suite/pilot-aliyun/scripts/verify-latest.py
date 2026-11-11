#!/usr/bin/env python3
"""Check that the built source commits still equal the requested branch tips."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--git',default='git',help='Git executable; WSL may use the installed Windows git.exe')
    a=p.parse_args();data=json.loads(a.manifest.read_text());rows=[]
    expected={'brewfs':('https://github.com/brewfs/brewfs','main'),
        'rustfs':('https://github.com/Ivanbeethoven/rustfs','feat/dynamic-block-size-rebased')}
    if {s['component'] for s in data['sources']}!=set(expected):raise ValueError('Expected both source components')
    for source in data['sources']:
        repository,branch=expected[source['component']]
        if source['repository'].removesuffix('.git')!=repository or source['branch']!=branch:
            raise ValueError('Unexpected source repository/branch')
        error=None
        try:
            result=subprocess.run([a.git,'ls-remote',source['repository'],'refs/heads/'+branch],capture_output=True,text=True,timeout=45)
            code=result.returncode
            remote=result.stdout.split()[0] if code==0 and result.stdout.split() else None
        except subprocess.TimeoutExpired:
            code=124;remote=None;error='branch lookup timed out after 45 seconds'
        rows.append(dict(component=source['component'],repository=repository,branch=branch,
            built_commit=source['source_commit'],remote_commit=remote,matches=remote==source['source_commit'],
            lookup_exit_code=code,lookup_error=error))
    receipt=dict(passed=all(r['matches'] for r in rows),at=datetime.now(timezone.utc).isoformat(),
        build_manifest_sha256=hashlib.sha256(a.manifest.read_bytes()).hexdigest(),sources=rows,
        git_executable=a.git,
        policy='Verify before a batch. If the branch advanced during preparation, rebuild. Keep the paired measurement batch fixed after it starts.')
    with a.out.open('x') as f:json.dump(receipt,f,indent=2);f.write('\n')
    print(json.dumps(receipt,indent=2))
    if not receipt['passed']:raise ValueError('Branch changed or unavailable; do not start a new batch with stale binaries')


if __name__=='__main__':
    main()
