#!/usr/bin/env python3
"""Fold a closed sample tree into one NDJSON stream, byte for byte.

A finished observation lives as ``sample-<%06d>/`` directories holding about 45
files each, so a five-hour series is 132,939 archive members and the frozen
packer's one manifest cannot carry it. This tool rewrites the *closed* tree into
a single ``samples.ndjson`` where each line holds the sample record and every
retained payload verbatim, then updates ``finished.json`` with the stream hash
and the per-line hashes. Every source byte is hashed before and after, and any
disagreement stops the fold without touching the tree.

The result is what ``bench.runtime.observe_ndjson.audit`` reads, and the
directory then holds a handful of members instead of 132,939.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

STREAM = 'samples.ndjson'
RECEIPT = 'fold-receipt.json'
HISTORY_KEYS = ('index', 'monotonic_start_ns', 'monotonic_end_ns', 'passed', 'errors')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def samples(directory):
    found = sorted(directory.glob('sample-*'))
    require(found, 'no closed samples under '+str(directory))
    for index, sample in enumerate(found):
        require(sample.is_dir() and not sample.is_symlink(), 'unexpected sample entry: '+str(sample))
        require(sample.name == 'sample-%06d' % index, 'non-contiguous sample sequence: '+sample.name)
    return found


def fold(directory, now=None):
    """Rewrite one closed tree into a single stream; return the receipt."""
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), 'canonical closed tree required')
    stream_path = directory/STREAM
    require(not stream_path.exists(), 'the tree is already folded: '+str(stream_path))
    finished_path = directory/'finished.json'
    finished = json.loads(finished_path.read_bytes())
    history = finished.get('history') or []
    tree = samples(directory)
    require(len(tree) == finished.get('samples'), 'finished.json sample count differs from the tree')
    require(len(history) == len(tree), 'finished.json history length differs from the tree')

    lines, records = [], []
    staging = directory/'.fold-staging'
    require(not staging.exists(), 'stale fold staging directory')
    staging.mkdir()
    try:
        for index, sample in enumerate(tree):
            raw = {}
            for path in sorted(sample.iterdir()):
                require(path.is_file() and not path.is_symlink(), 'unexpected telemetry path')
                raw[path.name] = path.read_bytes()
            record = json.loads(raw['sample.json'].decode())
            require(record['index'] == index, 'sample record index differs from its directory')
            for name, expected in record['files'].items():
                body = raw.get(name)
                require(body is not None, 'recorded payload missing: '+name)
                require(len(body) == expected['bytes'] and sha(body) == expected['sha256'],
                        'payload bytes changed: '+name)
            row = {key: record[key] for key in HISTORY_KEYS}
            row['files'] = {name: {'sha256': sha(raw[name]), 'bytes': len(raw[name]),
                                   'text': raw[name].decode('utf-8', 'surrogateescape')}
                            for name in raw}
            row['capacity'] = record.get('capacity') or {}
            row['counters'] = record.get('counters') or {}
            row['target_states'] = record.get('target_states') or {}
            row['scope'] = 'folded from the closed sample directory; payload bytes are verbatim'
            line = json.dumps(row, sort_keys=True, separators=(',', ':')).encode()
            lines.append(line)
            records.append(dict(index=index, directory=sample.name,
                                members={name: file_sha(sample/name) for name in raw},
                                line_sha256=sha(line), line_bytes=len(line)))
            (staging/('%06d.ndjson' % index)).write_bytes(line+b'\n')
        data = b''.join(line+b'\n' for line in lines)
        with stream_path.open('xb') as sink:
            sink.write(data)
            sink.flush()
            os.fsync(sink.fileno())
        require(file_sha(stream_path) == sha(data), 'stream write differs from the computed bytes')
        updated = dict(finished)
        updated['history'] = [dict({key: history[row['index']][key] for key in HISTORY_KEYS if key in history[row['index']]},
                                   line_sha256=row['line_sha256'], line_bytes=row['line_bytes'])
                              for row in records]
        updated['stream'] = {'name': STREAM, 'sha256': sha(data), 'bytes': len(data)}
        updated['folded'] = {'source_samples': len(tree), 'source_members': sum(len(r['members']) for r in records),
                             'scope': 'closed tree rewritten into one stream; source bytes preserved verbatim'}
        finished_path.write_bytes(json.dumps(updated).encode())
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    for sample in tree:
        shutil.rmtree(sample)
    receipt = dict(schema='c4-observer-fold-v1', passed=True, directory=str(directory),
                   stream={'name': STREAM, 'sha256': sha(data), 'bytes': len(data)},
                   samples=len(records), source_members=sum(len(r['members']) for r in records),
                   records=records,
                   scope='fold only; the sample directories were removed after every byte was retained')
    with (directory/RECEIPT).open('x', encoding='utf-8') as sink:
        json.dump(receipt, sink, indent=2)
        sink.write('\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='closed o/<tag> tree')
    parser.add_argument('--out', type=Path, help='optional receipt copy path')
    args = parser.parse_args()
    receipt = fold(args.directory)
    if args.out:
        args.out.write_bytes(json.dumps(receipt, indent=2).encode()+b'\n')
    print(json.dumps({k: receipt[k] for k in ('passed', 'samples', 'source_members', 'stream')}, indent=2))


if __name__ == '__main__':
    main()
