#!/usr/bin/env python3
"""F/M atomic filesystem steps. Native fork/clone/mount remain separate commands."""
import argparse
import hashlib
import os
from pathlib import Path
import sys
import time
from experiment_io import load, digest, write_new, contained

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from full_trace_common import tree


def fixture(root, files, size):
    if files < 1 or size < 1: raise ValueError('positive fixture dimensions required')
    root = Path(root); root.mkdir(exist_ok=False)
    for i in range(files):
        file = root/f'd{i//1000:06}'/f'f{i:08}'
        file.parent.mkdir(exist_ok=True)
        with file.open('xb') as out:
            out.write(hashlib.shake_256(f'ax-F-v1:{i}'.encode()).digest(size))
            out.flush(); os.fsync(out.fileno())
    return {'files': files, 'bytes': files*size, 'scope': 'fixture preparation; fsync and filesystem drain still checked separately'}


def first_read(root, relative, expected_sha):
    # Resolve only the approved root here; resolving the candidate through a
    # FUSE path before the timer would hide first-use lookup latency.
    root = Path(root).resolve(strict=True)
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts: raise ValueError('relative file required')
    started = time.monotonic_ns()
    data = (root/p).read_bytes()
    elapsed = (time.monotonic_ns()-started)/1e9
    if hashlib.sha256(data).hexdigest() != expected_sha: raise ValueError('first read content differs')
    return {'passed': True, 'seconds': elapsed, 'bytes': len(data), 'scope': 'open/read/close; hash comparison outside timer'}


def mutate(root):
    """Fixed child-only isolation challenge on fixture >= 5 files."""
    root = Path(root).resolve(strict=True)
    names = [f'd000000/f{i:08}' for i in range(5)]
    if (root/'new-child-file').exists() or (root/'renamed-child-file').exists():
        raise ValueError('challenge already applied; use a fresh child')
    paths = [contained(root, name) for name in names]
    for file, mode, action in ((paths[0],'r+b','overwrite'),(paths[1],'ab','append'),(paths[2],'r+b','truncate')):
        with file.open(mode) as out:
            if action == 'truncate': out.truncate(7)
            else: out.write(b'child-change')
            out.flush(); os.fsync(out.fileno())
    paths[3].unlink(); paths[4].rename(root/'renamed-child-file')
    with (root/'new-child-file').open('xb') as out:
        out.write(b'new-child'); out.flush(); os.fsync(out.fileno())
    for directory in (root/'d000000', root):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    return {'mutation_applied': True, 'scope': 'child changes only; compare against expected tree after remount and verify source/siblings separately'}


def durable_write(root, relative, payload_file):
    root = Path(root).resolve(strict=True); p = Path(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts: raise ValueError('relative new file required')
    target = root/p
    if not target.parent.resolve(strict=True).is_relative_to(root): raise ValueError('write escapes approved root')
    data = Path(payload_file).read_bytes(); started = time.monotonic_ns()
    with target.open('xb') as out:
        out.write(data); out.flush(); os.fsync(out.fileno())
    fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return {'passed': True, 'seconds': (time.monotonic_ns()-started)/1e9, 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'scope': 'new file write, file+parent fsync; remote durability requires reopen after remount'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    for name in ('fixture','snapshot','check','first-read','durable-write','mutate'):
        q = sub.add_parser(name); q.add_argument('--root', type=Path, required=True); q.add_argument('--output', type=Path, required=True)
        if name == 'fixture': q.add_argument('--files', type=int, required=True); q.add_argument('--file-bytes', type=int, default=4096)
        if name == 'check': q.add_argument('--expected', required=True); q.add_argument('--expected-sha256', required=True)
        if name in ('first-read','durable-write'): q.add_argument('--file', required=True)
        if name == 'first-read': q.add_argument('--sha256', required=True)
        if name == 'durable-write': q.add_argument('--payload', required=True)
    a = p.parse_args()
    if a.output.exists(): raise ValueError('new output path required before any mutation')
    if a.output.resolve().is_relative_to(a.root.resolve()): raise ValueError('evidence output must be outside the tested tree')
    if a.action == 'fixture': result = fixture(a.root,a.files,a.file_bytes)
    elif a.action == 'snapshot': result = tree(a.root.resolve(strict=True))
    elif a.action == 'check':
        if digest(a.expected) != a.expected_sha256: raise ValueError('expected tree changed')
        result = {'passed': tree(a.root.resolve(strict=True)) == load(a.expected), 'expected_sha256': a.expected_sha256}
    elif a.action == 'first-read': result = first_read(a.root,a.file,a.sha256)
    elif a.action == 'durable-write': result = durable_write(a.root,a.file,a.payload)
    else: result = mutate(a.root)
    write_new(a.output,result)
    if result.get('passed') is False: raise SystemExit(1)
