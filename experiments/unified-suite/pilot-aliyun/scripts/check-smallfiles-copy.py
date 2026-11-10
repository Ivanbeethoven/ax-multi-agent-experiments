#!/usr/bin/env python3
"""Check C3 materialization with the unchanged source verifier and bad inputs."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


def ref(path):
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--copier',type=Path,required=True)
    p.add_argument('--smallfiles',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if os.geteuid()!=0:
        raise ValueError('Fixture owner must match the root-owned cloud source')
    a.out.mkdir(parents=True,exist_ok=False);rows=[]
    def run(name,argv,success=True):
        folder=a.out/name;folder.mkdir()
        (folder/'command.json').write_text(json.dumps(list(map(str,argv)))+'\n')
        with (folder/'stdout').open('xb') as stdout,(folder/'stderr').open('xb') as stderr:
            code=subprocess.run(list(map(str,argv)),stdout=stdout,stderr=stderr).returncode
        row=dict(name=name,exit_code=code,expected_success=success)
        (folder/'exit.json').write_text(json.dumps(row)+'\n');rows.append(row)
        if (code==0)!=success:
            raise RuntimeError('Inspect '+str(folder))
    run('prepare',[a.smallfiles,'prepare','--root',a.out/'source','--output',a.out/'prepared','--files','128'])
    run('copy',[a.copier,a.out/'source',a.out/'target',a.out/'copy.json','128','8'])
    run('verify',[a.smallfiles,'verify-base','--root',a.out/'target','--output',a.out/'verified','--files','128'])
    source=a.out/'source/small/d0000/f0000000';target=a.out/'target/small/d0000/f0000000'
    original=source.read_bytes()
    if (source.stat().st_dev,source.stat().st_ino)==(target.stat().st_dev,target.stat().st_ino):
        raise ValueError('Copy aliases the source inode')
    with target.open('r+b') as stream:
        stream.write(b'changed');stream.flush();os.fsync(stream.fileno())
    if source.read_bytes()!=original:
        raise ValueError('Child modification changed the source')
    run('reject-existing',[a.copier,a.out/'source',a.out/'target',a.out/'existing.json','128','8'],False)
    with source.open('r+b') as stream:
        stream.write(b'corrupt');stream.flush();os.fsync(stream.fileno())
    run('reject-corrupt',[a.copier,a.out/'source',a.out/'corrupt-copy',a.out/'corrupt.json','128','8'],False)
    if (a.out/'corrupt.json').exists():
        raise ValueError('Corrupt import produced a success receipt')
    (a.out/'source/extra').write_bytes(b'extra')
    run('reject-extra',[a.copier,a.out/'source',a.out/'extra-copy',a.out/'extra.json','128','8'],False)
    if (a.out/'extra-copy').exists():
        raise ValueError('Unexpected namespace was imported')
    result=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),checks=rows,
        verifier=ref(a.smallfiles),copier=ref(a.copier),helper=ref(Path(__file__)),
        original_full_readback=json.loads((a.out/'verified/result.json').read_text()),
        independent_inodes=True,source_unchanged_by_child=True,failed_copy_retained=True,
        scope='128-file functional check against original Rust verifier; no cloud capacity or performance result')
    (a.out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
