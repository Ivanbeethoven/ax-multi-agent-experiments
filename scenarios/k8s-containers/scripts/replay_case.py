#!/usr/bin/env python3
"""Prepare/verify an unpacked, trusted full-FUSE case. Replay stays in Rust.

This is an identical-start RQ3 helper, not a fork/save/migration implementation.
The OSS archive SHA256 and inventory must be checked before using this helper.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


def tree(root):
    values = {}
    for path in sorted(root.rglob('*')):
        s = path.lstat()
        if path.is_symlink():
            value = {'type': 'link', 'target': os.readlink(path)}
        elif path.is_dir():
            value = {'type': 'directory'}
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            value = {'type': 'file', 'bytes': s.st_size, 'sha256': digest.hexdigest()}
        else:
            raise ValueError('Unsupported inode type')
        value['mode'] = s.st_mode & 0o7777
        values[path.relative_to(root).as_posix()] = value
    return values


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def prepare(case, root):
    if root.exists() or root.is_symlink():
        raise ValueError('Use a new dedicated work root; never overwrite existing data')
    for name in ('initial', 'capture/events.jsonl', 'capture/blobs', 'initial-tree.json',
                 'final-tree.json', 'metrics.json'):
        if not (case / name).exists():
            raise ValueError(f'Missing case member: {name}')
    if root.is_relative_to(case) or case.is_relative_to(root):
        raise ValueError('Work root and source case must be disjoint')
    shutil.copytree(case / 'initial', root, symlinks=True)
    if tree(root) != read(case / 'initial-tree.json'):
        raise ValueError('Initial tree mismatch')
    return {'valid': True, 'cache_state': 'prepared_and_verified; not cold',
            'initial_tree_equal': True, 'hardlink_identity': 'not_in_tree_oracle'}


def verify(case, root, replay):
    result, metrics = read(replay), read(case / 'metrics.json')
    if not result.get('valid') or result.get('remaining_handles') != 0:
        raise ValueError('Replay did not finish validly with zero handles')
    for key in ('actual_read_bytes', 'actual_write_bytes'):
        if result.get(key) != metrics[key]:
            raise ValueError(f'Byte mismatch: {key}')
    if result.get('operations') != metrics['callbacks']:
        raise ValueError('Operation count mismatch')
    if tree(root) != read(case / 'final-tree.json'):
        raise ValueError('Final tree mismatch')
    return {'valid': True, 'final_tree_equal': True, 'callbacks': result['operations'],
            'actual_read_bytes': result['actual_read_bytes'],
            'actual_write_bytes': result['actual_write_bytes'],
            'captured_repeated_read_ratio': metrics['repeated_read_ratio'],
            'repeat_scope': 'input workload property, not backend cache hit ratio',
            'agent_resume': 'not_tested', 'sqlite_integrity': 'not_rechecked'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['prepare', 'verify'])
    p.add_argument('--case', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--replay', type=Path)
    a = p.parse_args()
    case, root = a.case.resolve(strict=True), a.root.resolve()
    if a.mode == 'prepare':
        result = prepare(case, root)
    else:
        if a.replay is None:
            p.error('verify requires --replay')
        result = verify(case, root, a.replay)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
