#!/usr/bin/env python3
"""Fold a closed sample tree into one NDJSON stream, byte for byte, as a stream.

A finished observation lives as ``sample-<%06d>/`` directories holding about 45
files each, so a five-hour series is 132,939 archive members and the frozen
packer's one manifest cannot carry it. This tool rewrites the *closed* tree into
a single ``samples.ndjson`` where each line holds the sample record and every
retained payload verbatim, then updates ``finished.json`` with the stream hash
and the per-line hashes. Every source byte is hashed before and after, and any
disagreement stops the fold without touching the tree.

The fold is deliberately streaming. Each sample is read, turned into one line and
appended straight to ``samples.ndjson`` while an incremental SHA-256 and byte
counter follow it; nothing ever accumulates a whole-file buffer or a
``b''.join`` of the stream. Per-sample detail (including each member hash) is
written to ``fold-receipt.ndjson`` one line at a time instead of being held in
memory, and ``fold-receipt.json`` keeps only the summary. The one input-sized
structure that stays in memory is ``finished.json``'s own ``history`` list, whose
per-line hashes are small (about 200 bytes per line) and are required there.

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
RECEIPT_DETAIL = 'fold-receipt.ndjson'
STAGING = '.fold-staging'
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


def fold(directory, now=None, dry_run=False, max_output_bytes=None, reserve_bytes=None):
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
    require(max_output_bytes is None or type(max_output_bytes) is int and max_output_bytes > 0,
            'invalid folded stream byte limit')
    require(reserve_bytes is None or type(reserve_bytes) is int and reserve_bytes >= 0,
            'invalid folded stream free-space reserve')

    if dry_run:
        # Verify every recorded payload without writing or removing anything.
        members, total = 0, 0
        for index, sample in enumerate(tree):
            record = json.loads((sample/'sample.json').read_bytes())
            require(record['index'] == index, 'sample record index differs from its directory')
            for name, expected in record['files'].items():
                path = sample/name
                require(path.is_file() and not path.is_symlink(), 'recorded payload missing: '+name)
                body = path.read_bytes()
                require(len(body) == expected['bytes'] and sha(body) == expected['sha256'],
                        'payload bytes changed: '+name)
                members += 1
                total += len(body)
        return dict(schema='c4-observer-fold-dry-run-v1', passed=True, dry_run=True,
                    directory=str(directory), samples=len(tree), source_members=members,
                    payload_bytes=total,
                    scope='verification only: nothing was written, moved or removed')

    # A crashed earlier attempt can leave the old staging directory behind; it is
    # residue, not input, so clear it and record the fact in the receipt.
    staging = directory/STAGING
    stale_staging_removed = staging.exists()
    if stale_staging_removed:
        shutil.rmtree(staging)
    require(not staging.exists(), 'stale fold staging directory could not be removed')

    detail_path = directory/RECEIPT_DETAIL
    stream_hash = hashlib.sha256()
    stream_bytes = 0
    source_members = detail_bytes = 0
    stream_committed = False
    stream_created = False
    try:
        # One append handle for the stream, one line per sample for the detail
        # receipt. Neither is ever buffered as a whole file in memory.
        sink = stream_path.open('xb')
        stream_created = True
        with sink, detail_path.open('w', encoding='utf-8') as detail:
            for index, sample in enumerate(tree):
                raw = {}
                for path in sorted(sample.iterdir()):
                    require(path.is_file() and not path.is_symlink(), 'unexpected telemetry path')
                    raw[path.name] = path.read_bytes()
                record = json.loads(raw['sample.json'].decode())
                require(record['index'] == index, 'sample record index differs from its directory')
                digests = {name: sha(body) for name, body in raw.items()}
                for name, expected in record['files'].items():
                    body = raw.get(name)
                    require(body is not None, 'recorded payload missing: '+name)
                    require(len(body) == expected['bytes'] and digests[name] == expected['sha256'],
                            'payload bytes changed: '+name)
                row = {key: record[key] for key in HISTORY_KEYS}
                row['files'] = {name: {'sha256': digests[name], 'bytes': len(raw[name]),
                                       'text': raw[name].decode('utf-8', 'surrogateescape')}
                                for name in raw}
                row['capacity'] = record.get('capacity') or {}
                row['counters'] = record.get('counters') or {}
                row['target_states'] = record.get('target_states') or {}
                row['scope'] = 'folded from the closed sample directory; payload bytes are verbatim'
                line = json.dumps(row, sort_keys=True, separators=(',', ':')).encode()
                chunk = line+b'\n'
                line_digest = sha(line)
                detail_row = json.dumps(dict(index=index, directory=sample.name,
                                             line_sha256=line_digest, line_bytes=len(line),
                                             members=digests),
                                        sort_keys=True, separators=(',', ':'))+'\n'
                detail_size = len(detail_row.encode('utf-8'))
                require(max_output_bytes is None or
                        stream_bytes+detail_bytes+len(chunk)+detail_size <= max_output_bytes,
                        'folded stream would exceed reviewed byte budget')
                if reserve_bytes is not None:
                    space = os.statvfs(directory)
                    # Allow each appended file's final fragment and inode metadata.
                    require(space.f_bavail*space.f_frsize-len(chunk)-detail_size-4*space.f_frsize >= reserve_bytes,
                            'folded stream would breach the filesystem reserve')
                sink.write(chunk)
                stream_hash.update(chunk)
                stream_bytes += len(chunk)
                detail_bytes += detail_size
                # Fold the per-line hashes into the parsed history rows in place:
                # one small string per line, no second list and no per-sample
                # counters/target states kept for the finished receipt.
                history[index]['line_sha256'] = line_digest
                history[index]['line_bytes'] = len(line)
                source_members += len(digests)
                detail.write(detail_row)
                # Hand each finished sample to the OS now: the stream is an
                # append-only prefix at every point, never a buffered batch.
                sink.flush()
                detail.flush()
            sink.flush()
            os.fsync(sink.fileno())
        require(stream_path.stat().st_size == stream_bytes,
                'stream length differs from the computed bytes')
        require(file_sha(stream_path) == stream_hash.hexdigest(),
                'stream write differs from the computed bytes')
        stream_committed = True

        # Only after the stream is durable and re-verified is finished.json
        # pointed at it. The history rows are trimmed and extended in place so
        # their per-line hashes stay without a second copy of the whole list.
        for row in history:
            kept = {key: row[key] for key in HISTORY_KEYS if key in row}
            kept['line_sha256'] = row['line_sha256']
            kept['line_bytes'] = row['line_bytes']
            row.clear()
            row.update(kept)
        updated = dict(finished)
        updated['history'] = history
        updated['stream'] = {'name': STREAM, 'sha256': stream_hash.hexdigest(),
                             'bytes': stream_bytes}
        updated['folded'] = {'source_samples': len(tree), 'source_members': source_members,
                             'stale_staging_removed': stale_staging_removed,
                             'scope': 'closed tree rewritten into one stream; '
                                      'source bytes preserved verbatim'}
        # Serialize straight to a sibling file and rename into place: no single
        # string or byte buffer of the whole finished.json is ever built.
        finished_tmp = directory/'.finished.json.tmp'
        with finished_tmp.open('w', encoding='utf-8') as sink:
            json.dump(updated, sink)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(finished_tmp, finished_path)
    except BaseException:
        # The sample tree is untouched. An incomplete (or unverified) stream and
        # its detail receipt are removed rather than left as a half-made fold.
        if not stream_committed and stream_created:
            for path in (stream_path, detail_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        try:
            (directory/'.finished.json.tmp').unlink()
        except FileNotFoundError:
            pass
        raise

    # The stream is durable and published; only now remove the sources.
    for sample in tree:
        shutil.rmtree(sample)
    receipt = dict(schema='c4-observer-fold-v1', passed=True, directory=str(directory),
                   stream={'name': STREAM, 'sha256': stream_hash.hexdigest(),
                           'bytes': stream_bytes},
                   samples=len(tree), source_members=source_members,
                   detail={'name': RECEIPT_DETAIL, 'lines': len(tree)},
                   stale_staging_removed=stale_staging_removed,
                   scope='fold only; the sample directories were removed after every byte '
                         'was retained')
    with (directory/RECEIPT).open('x', encoding='utf-8') as sink:
        json.dump(receipt, sink, indent=2)
        sink.write('\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='closed o/<tag> tree')
    parser.add_argument('--out', type=Path, help='optional receipt copy path')
    parser.add_argument('--dry-run', action='store_true', help='verify only; write and remove nothing')
    args = parser.parse_args()
    receipt = fold(args.directory, dry_run=args.dry_run)
    if args.out:
        args.out.write_bytes(json.dumps(receipt, indent=2).encode()+b'\n')
    keys = ('passed', 'dry_run', 'samples', 'source_members', 'payload_bytes', 'stream')
    print(json.dumps({k: receipt[k] for k in keys if k in receipt}, indent=2))


if __name__ == '__main__':
    main()
