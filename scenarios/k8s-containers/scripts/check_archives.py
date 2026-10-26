#!/usr/bin/env python3
"""Verify complete downloaded OSS archives against the checked-in receipt."""
import argparse
import hashlib
import json
from pathlib import Path


def verify_archive(path, entry):
    if path.stat().st_size != entry['bytes']:
        raise ValueError(f'{path.name}: incorrect archive size')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != entry['sha256']:
        raise ValueError(f'{path.name}: incorrect SHA256')
    return {'case': entry.get('case', 'metadata:' + entry.get('batch', 'unknown')),
            'sha256': entry['sha256'], 'verified': True}


if __name__ == '__main__':
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'archives.json').read_text(encoding='utf-8'))
    entries = {r['case']: r for r in manifest['archives']}
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--cases', nargs='+', choices=list(entries))
    a = p.parse_args()
    for name in a.cases or entries:
        r = entries[name]
        print(json.dumps(verify_archive(a.directory / r['file'], r)))
