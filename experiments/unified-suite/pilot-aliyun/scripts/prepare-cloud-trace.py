#!/usr/bin/env python3
"""Extract a verified trace with original permission bits, then strictly replay it."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


def preserve_permissions(member,destination):
    checked=tarfile.data_filter(member,destination)
    if checked is not None and (checked.isfile() or checked.isdir()):
        if member.mode & ~0o777:raise ValueError('Unexpected special mode bits')
        checked.mode=member.mode
    return checked


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--assets',type=Path,required=True)
    p.add_argument('--repo',type=Path,required=True)
    a=p.parse_args();a.root.mkdir(parents=True,exist_ok=False)
    with a.archive.open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest()!=a.sha256:raise ValueError('Archive differs')
    write=lambda name,value:(a.root/name).write_text(json.dumps(value,indent=2)+'\n')
    write('started.json',dict(at=datetime.now(timezone.utc).isoformat(),archive_sha256=a.sha256,automatic_retry=False,
        extraction='tarfile data safety checks with original ordinary permission bits retained; no special bits accepted'))
    cases=a.root/'cases';cases.mkdir()
    with tarfile.open(a.archive) as t:t.extractall(cases,filter=preserve_permissions)
    argv=['python3',str(a.repo/'experiments/unified-suite/pilot-aliyun/scripts/qualify-trace.py'),
        '--case',str(cases/'pytest-full-02'),'--binary',str(a.assets/'bin/full-fuse-replay'),
        '--root',str(a.root/'strict-replay'),'--out',str(a.root/'strict')]
    with (a.root/'strict.stdout').open('xb') as out,(a.root/'strict.stderr').open('xb') as err:
        result=subprocess.run(argv,stdout=out,stderr=err)
    write('exit.json',dict(exit_code=result.returncode,argv=argv))
    result.check_returncode()
    verified=json.loads((a.root/'strict/verification.json').read_text())
    if not verified['passed']:raise ValueError('Strict replay failed')
    write('result.json',dict(passed=True,at=datetime.now(timezone.utc).isoformat(),archive_sha256=a.sha256,
        callbacks=verified['callbacks'],actual_read_bytes=verified['actual_read_bytes'],actual_write_bytes=verified['actual_write_bytes'],
        scope='Exact input extraction and strict POSIX replay. This is not a FUSE performance result.'))
    print((a.root/'result.json').read_text())


if __name__=='__main__':
    main()
