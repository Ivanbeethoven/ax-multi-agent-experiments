#!/usr/bin/env python3
"""Install a new versioned runner tree; never overwrite an earlier code bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args()
    for path,wanted in [(a.archive,a.sha256),(a.manifest,a.manifest_sha256)]:
        with path.open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest()!=wanted:raise ValueError('Code input changed')
    expected=json.loads(a.manifest.read_text())
    with tarfile.open(a.archive) as t:
        members=t.getmembers()
        if len(members)!=len(expected) or {m.name for m in members}!=set(expected):
            raise ValueError('Code member allowlist differs')
        for m in members:
            path=Path(m.name)
            if path.is_absolute() or '..' in path.parts or not m.isfile() or hashlib.sha256(t.extractfile(m).read()).hexdigest()!=expected[m.name]:
                raise ValueError('Code archive member differs')
        a.destination.mkdir(parents=True,exist_ok=False)
        t.extractall(a.destination,filter='data')
    for path,wanted in expected.items():
        if hashlib.sha256((a.destination/path).read_bytes()).hexdigest()!=wanted:raise ValueError('Installed code differs')
    (a.destination/'.code-manifest.json').write_bytes(a.manifest.read_bytes())
    print(json.dumps(dict(passed=True,members=len(expected),archive_sha256=a.sha256,manifest_sha256=a.manifest_sha256,destination=str(a.destination))))


if __name__=='__main__':
    main()
