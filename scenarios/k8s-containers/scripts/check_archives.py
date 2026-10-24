#!/usr/bin/env python3
"""Verify complete downloaded OSS archives against the checked-in receipt."""
import argparse
import hashlib
import json
from pathlib import Path

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--cases', nargs='+', default=['django', 'sklearn', 'sympy', 'pytest-full-02'])
    a = p.parse_args()
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'archives.json').read_text())
    entries = {r['case']: r for r in manifest['archives']}
    for name in a.cases:
        r = entries[name]
        path = a.directory / r['file']
        if path.stat().st_size != r['bytes']:
            raise ValueError(f'{name}: incorrect archive size')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != r['sha256']:
            raise ValueError(f'{name}: incorrect SHA256')
        print(json.dumps({'case': name, 'sha256': r['sha256'], 'verified': True}))
