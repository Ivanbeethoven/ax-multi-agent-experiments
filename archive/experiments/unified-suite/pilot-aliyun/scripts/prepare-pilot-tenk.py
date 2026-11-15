#!/usr/bin/env python3
"""Generate and verify new C3/C4 pilot inputs. No trace, mount or cloud calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(REPO), str(REPO / 'bench')]
from bench.current_design import PILOT_FILES
from bench.runtime.common import digest, write_new
from fs_step import fixture
from scripts.full_trace_common import tree

FILE_BYTES = 4096
DIR = {'type': 'directory', 'mode': 0o755}


def require(ok, why):
    if not ok:
        raise ValueError(why)


def read(path):
    return json.loads(path.read_bytes())


def run_smallfiles(binary, mode, root, receipts, files):
    out = receipts / ('c3-' + mode)
    with (receipts / ('c3-' + mode + '.log')).open('xb') as log:
        subprocess.run([binary, mode, '--root', root, '--output', out, '--files', str(files)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    result = read(out / 'result.json')
    byte_key = 'bytes' if mode == 'prepare' else 'bytes_checked'
    require(result.get('completed') is True and result.get('passed') is True
            and result['files'] == files and result[byte_key] == files * FILE_BYTES,
            'smallfiles count, bytes or completion differs')
    return out / 'result.json'


def c3_shape(entries, files):
    directories = {'small', *(f'small/d{i:04d}' for i in range(min(files, 1024)))}
    paths = {f'small/d{i%1024:04d}/f{i:07d}' for i in range(files)}
    require(set(entries) == directories | paths and all(entries[p] == DIR for p in directories)
            and all((entries[p]['type'], entries[p]['bytes'], entries[p]['mode']) ==
                    ('file', FILE_BYTES, 0o644) for p in paths), 'C3 tree names, types, sizes or modes differ')
    return len(directories)


def c4_expected(files):
    # Independent of the created tree, using the existing ax-F-v1 payload formula.
    entries = {f'd{i:06d}': DIR for i in range((files + 999) // 1000)}
    for i in range(files):
        data = hashlib.shake_256(f'ax-F-v1:{i}'.encode()).digest(FILE_BYTES)
        entries[f'd{i//1000:06d}/f{i:08d}'] = dict(type='file', mode=0o644, bytes=FILE_BYTES,
                                                sha256=hashlib.sha256(data).hexdigest())
    return entries


def prepare(out, binary, binary_sha, files, smoke):
    require(os.name == 'posix', 'Linux/WSL required for exact POSIX file modes')
    require(out.is_absolute() and out.resolve() == out and out.parent.is_dir()
            and not out.exists() and not out.is_symlink(), 'new canonical absolute output directory required')
    binary = binary.resolve(strict=True)
    require(binary.is_file() and os.access(binary, os.X_OK) and digest(binary) == binary_sha,
            'pinned smallfiles executable required')
    out.mkdir(mode=0o700)
    receipts = out / 'receipts'
    receipts.mkdir()
    c3 = out / 'smallfiles-source'
    c4 = out / 'fork-source'
    run_smallfiles(binary, 'prepare', c3, receipts, files)
    c3_proof = run_smallfiles(binary, 'verify-base', c3, receipts, files)
    entries = tree(c3)
    c3_dirs = c3_shape(entries, files)
    c3_manifest = receipts / 'c3-tree.json'
    write_new(c3_manifest, entries)

    c4_proof = receipts / 'c4-fixture.json'
    write_new(c4_proof, fixture(c4, files, FILE_BYTES))
    expected = c4_expected(files)
    require(tree(c4) == expected, 'C4 exact path/mode/byte hash readback differs')
    c4_manifest = receipts / 'c4-fixture-tree.json'
    write_new(c4_manifest, expected)

    def ref(path):
        return dict(path=path.relative_to(out).as_posix(), sha256=digest(path))

    def row(root, manifest, proof, dirs):
        return dict(root=root.relative_to(out).as_posix(), manifest=ref(manifest), fixture_proof=ref(proof),
                    directories=dirs, files=files, logical_bytes=files * FILE_BYTES)

    result = dict(schema='ax-small-pilot-inputs-v1', passed=True, smoke_only=smoke,
                  formal_sample=False, files_per_tree=files, file_bytes=FILE_BYTES, smallfiles_sha256=binary_sha,
                  c3=row(c3, c3_manifest, c3_proof, c3_dirs),
                  c4=row(c4, c4_manifest, c4_proof, (files + 999) // 1000),
                  scope='new deterministic local input trees and full readback only')
    write_new(out / 'input-set.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--smallfiles', type=Path, required=True)
    p.add_argument('--smallfiles-sha256', required=True)
    p.add_argument('--smoke-files', type=int, choices=range(5, 33), metavar='5..32')
    a = p.parse_args()
    prepare(a.out, a.smallfiles, a.smallfiles_sha256, a.smoke_files or PILOT_FILES, a.smoke_files is not None)
