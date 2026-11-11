#!/usr/bin/env python3
"""Derive C4 child expectations from the closed input, before touching a filesystem.

Same challenge as bench/fs_step.py mutate; no mount, copy, network or model call.
The CLI requires the complete explicitly selected 100k or million-file fixture.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, pinned, write_new


def file_entry(data, mode=0o644):
    return dict(type='file', bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=mode)


def derive(base, proof, tag, required_files=1000000):
    if (not isinstance(tag, str) or not tag or len(tag)>128 or not tag.isascii() or
            proof.get('files') != required_files or proof.get('bytes') != required_files*4096 or required_files < 5):
        raise ValueError('Fixed 4 KiB fork fixture and unique short ASCII child tag required')
    if len(base) != required_files+(required_files+999)//1000:
        raise ValueError('Full original directory tree required')
    for directory in range((required_files+999)//1000):
        if base.get(f'd{directory:06}') != dict(type='directory', mode=0o755):
            raise ValueError('Original fixture directory differs')
    for index in range(required_files):
        key = f'd{index//1000:06}/f{index:08}'
        entry = base.get(key, {})
        if (set(entry) != {'type','bytes','sha256','mode'} or entry.get('type')!='file' or
                entry.get('bytes')!=4096 or entry.get('mode')!=0o644 or
                not re.fullmatch(r'[0-9a-f]{64}', str(entry.get('sha256')))):
            raise ValueError('Original fixture file differs: '+key)
    # Reconstruct changed bytes from the published fixture formula. The tested
    # filesystem supplies none of these values.
    original = [hashlib.shake_256(f'ax-F-v1:{index}'.encode()).digest(4096) for index in range(5)]
    for index, data in enumerate(original):
        if base[f'd000000/f{index:08}'] != file_entry(data):
            raise ValueError('Input tree does not match the fork fixture formula')
    change = tag.encode('ascii'); result = dict(base)
    result['d000000/f00000000'] = file_entry(change+original[0][len(change):])
    result['d000000/f00000001'] = file_entry(original[1]+change)
    result['d000000/f00000002'] = file_entry(original[2][:7])
    del result['d000000/f00000003']
    result['renamed-child-file'] = result.pop('d000000/f00000004')
    result['new-child-file'] = file_entry(b'new-child:'+change)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('base-tree','base-tree-sha256','fixture-proof','fixture-proof-sha256','tag','out'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--files',type=int,choices=[100000,1000000],default=1000000)
    a = p.parse_args()
    tree_ref = dict(path=a.base_tree, sha256=a.base_tree_sha256)
    proof_ref = dict(path=a.fixture_proof, sha256=a.fixture_proof_sha256)
    base = load(pinned(tree_ref)); proof = load(pinned(proof_ref))
    out = Path(a.out)
    if not out.is_absolute() or out.exists() or out.is_symlink():
        raise ValueError('New absolute outside output directory required')
    expected = derive(base, proof, a.tag, required_files=a.files)
    out.mkdir(parents=True)
    write_new(out/'expected-tree.json', expected)
    first = base['d000000/f00000000']
    result = dict(passed=True, at=datetime.now(timezone.utc).isoformat(), files=a.files,
        tag=a.tag, base_tree=tree_ref, fixture=proof_ref,
        expected_tree=dict(path=str(out/'expected-tree.json'), sha256=digest(out/'expected-tree.json')),
        first_read=dict(path='d000000/f00000000', bytes=first['bytes'], sha256=first['sha256']),
        changed_operations=['overwrite','append','truncate','unlink','rename','new-file'],
        source_tree_mutated=False, tested_filesystem_accessed=False,
        source_tool_sha256=digest(__file__),
        scope='Expected complete names/types/modes/bytes only. Actual fork timing, isolation, cold readback, ownership and durable exit require separate checks.')
    write_new(out/'result.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
