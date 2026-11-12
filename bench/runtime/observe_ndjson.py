"""One NDJSON line per observation sample, so the archive stays small.

The original observer writes about 45 files per sample (host, cgroup, pid and
target payloads). A five-hour five-second series therefore produced 132,939
members, and the frozen archive checker refuses a manifest above 16 MiB. This
module keeps the same evidence but writes one append-only line per sample into
``samples.ndjson``; the archive then holds single-digit members and the frozen
packer and checker keep their format.

Collection is delegated to :func:`bench.runtime.observe.snapshot` so the bytes
are exactly the ones the original observer would retain; the temporary sample
directory is removed once its contents are inside the line.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import socket
import subprocess
import sys
import threading
import time

from . import observe
from .common import digest, identity, load, private_dir, spec, write_new

# Everything the frozen callers import from the observer module is re-exported,
# so this file can stand in for bench/runtime/observe.py in a new batch.
from .observe import (HOST_FILES, CGROUP_FILES, NoRedirect, prometheus, required_values,
                     collect_target, mount_lifecycle, validate, request, overhead)  # noqa: F401

STREAM = 'samples.ndjson'


def _line_bytes(record):
    return json.dumps(record, sort_keys=True, separators=(',', ':')).encode()


def snapshot_ndjson(s, output, index, previous=None, stream_hash=None):
    """Collect one sample through the original collector, keep it as one line."""
    output = Path(output)
    staging = output/('sample-%06d' % index)
    value = observe.snapshot(s, output, index, previous)
    try:
        files = {}
        for name, expected in value['files'].items():
            path = staging/name
            if path.parent != staging or path.is_symlink():
                raise ValueError('unexpected telemetry path')
            raw = path.read_bytes()
            if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
                raise ValueError('telemetry bytes changed before retention')
            files[name] = {'sha256': expected['sha256'], 'bytes': expected['bytes'],
                           'text': raw.decode('utf-8', 'surrogateescape')}
        # Preserve the original UTC, PID and sampler CPU fields too.
        sample_path = staging/'sample.json'
        if sample_path.is_symlink():
            raise ValueError('unexpected telemetry sample path')
        sample = sample_path.read_bytes()
        files['sample.json'] = {'sha256': hashlib.sha256(sample).hexdigest(), 'bytes': len(sample),
                                'text': sample.decode('utf-8', 'surrogateescape')}
        record = {key: value[key] for key in ('index', 'monotonic_start_ns', 'monotonic_end_ns',
                                              'passed', 'errors', 'capacity', 'counters',
                                              'target_states')}
        record['files'] = files
        record['scope'] = 'same bytes as the original collector directory, retained as one line'
        line = _line_bytes(record)
        chunk = line+b'\n'
        with (output/STREAM).open('ab') as stream:
            stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if stream_hash is not None:
            stream_hash.update(chunk)
        summary = {key: value[key] for key in ('index', 'monotonic_start_ns', 'monotonic_end_ns',
                                               'passed', 'errors')}
        summary['line_sha256'] = hashlib.sha256(line).hexdigest()
        summary['line_bytes'] = len(line)
        summary['counters'] = value['counters']
        summary['target_states'] = value['target_states']
        return summary
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# The frozen callers call snapshot; in this module that is the stream variant.
snapshot = snapshot_ndjson


def serve(ref, output, format_sha256=None):
    s = spec(ref, 'experiment-observer-v1')
    observe.validate(s)
    output = Path(output)
    count, last, history, marks = 0, None, [], []
    format_pin = {'observer_format_sha256': format_sha256} if format_sha256 else {}
    stream_hash = hashlib.sha256()

    def sample():
        nonlocal count, last
        value = snapshot_ndjson(s, output, count, last['counters'] if last else None, stream_hash)
        history.append({key: value[key] for key in ('index', 'monotonic_start_ns',
                                                      'monotonic_end_ns', 'passed', 'errors',
                                                      'line_sha256', 'line_bytes')})
        last = value
        count += 1
        return value

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(output/'observer.sock'))
        os.chmod(output/'observer.sock', 0o600)
        server.listen(16)
        first = sample()
        write_new(output/'ready.json', {'passed': first['passed'], 'spec': ref,
                                       'process': identity(os.getpid()),
                                       'metric_contract_sha256': s['metric_contract_sha256'], **format_pin})
        next_sample = time.monotonic()+s['interval_seconds']
        while True:
            readable, _, _ = select.select([server], [], [], max(0, next_sample-time.monotonic()))
            if not readable:
                sample()
                next_sample = time.monotonic()+s['interval_seconds']
                continue
            with server.accept()[0] as conn:
                with conn.makefile('rb') as stream:
                    message = json.loads(stream.readline(65536))
                if message['action'] not in ('status', 'mark', 'stop'):
                    raise ValueError('observer action')
                current = sample()
                next_sample = time.monotonic()+s['interval_seconds']
                if message['action'] == 'mark':
                    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', message['label']):
                        raise ValueError('simple phase label')
                    marks.append({'label': message['label'], 'sample_index': current['index'],
                                  'monotonic_ns': time.monotonic_ns()})
                    write_new(output/('mark-%04d.json' % len(marks)), marks[-1])
                result = {'passed': all(x['passed'] for x in history),
                          'errors': [e for row in history for e in row['errors']],
                          'samples': count, 'latest_sample_ns': current['monotonic_end_ns'],
                          'spec_sha256': ref['sha256'],
                          'metric_contract_sha256': s['metric_contract_sha256'],
                          'marks': marks, 'target_states': current['target_states']}
                if message['action'] == 'stop':
                    result['history'] = history
                    result.update(format_pin)
                    stream_path = output/STREAM
                    result['stream'] = {'name': STREAM, 'sha256': stream_hash.hexdigest(),
                                        'bytes': stream_path.stat().st_size}
                    write_new(output/'finished.json', result)
                conn.sendall((json.dumps(result)+'\n').encode())
                if message['action'] == 'stop':
                    return


def start(ref, output, serve_argv=None, repo=None):
    """Spawn the stream collector where the original would spawn its own."""
    s = spec(ref, 'experiment-observer-v1')
    validate(s)
    output = private_dir(output)
    with (output/'collector.log').open('xb') as log:
        argv = serve_argv or [sys.executable, '-m', 'bench.runtime.observe_ndjson', 'serve',
                              '--spec', ref['path'], '--sha256', ref['sha256'], '--output', str(output)]
        child = subprocess.Popen(argv, cwd=repo or Path(__file__).resolve().parents[2],
                                 stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        if (output/'ready.json').exists():
            threading.Thread(target=child.wait, daemon=True).start()
            return request(output, 'status')
        if child.poll() is not None:
            raise ValueError('collector failed before ready')
        time.sleep(.05)
    raise ValueError('collector startup unresolved; inspect retained process')


def audit(directory, max_gap_seconds, required_marks):
    """Drop-in for ``bench.runtime.observe.audit``.

    A directory holding ``samples.ndjson`` is audited line by line; anything else
    falls through to the original directory audit, so one pinned module serves
    both the legacy layout and the stream layout and the caller does not have to
    know which one it is looking at.
    """
    directory = Path(directory)
    stream = directory/STREAM
    if not stream.exists() and not stream.is_symlink():
        return observe.audit(directory, max_gap_seconds, required_marks)
    end = load(directory/'finished.json')
    if max_gap_seconds <= 0 or not end['passed']:
        raise ValueError('failed telemetry samples or invalid maximum gap')
    if not stream.is_file() or stream.is_symlink():
        raise ValueError('observation stream missing')
    stream_pin = end.get('stream') or {}
    if stream_pin.get('name') != STREAM or stream_pin.get('bytes') != stream.stat().st_size:
        raise ValueError('observation stream length changed')
    history = end.get('history') or []
    marks = end['marks']
    marked_indices = {m['sample_index'] for m in marks}
    ends = {}
    previous = None
    count = 0
    stream_hash = hashlib.sha256()
    with stream.open('rb') as raw:
        for raw_line in raw:
            stream_hash.update(raw_line)
            if not raw_line.endswith(b'\n') or len(raw_line)==1:
                raise ValueError('incomplete or blank observation stream line')
            line = raw_line[:-1]
            record = json.loads(line)
            if count >= len(history):
                raise ValueError('observation stream holds more lines than its history')
            h = history[count]
            if record['index'] != count or h['index'] != count:
                raise ValueError('non-contiguous sample sequence')
            if len(line)!=h['line_bytes'] or hashlib.sha256(line).hexdigest() != h['line_sha256']:
                raise ValueError('sample record changed')
            if not record['passed'] or not record['files'] or (end.get('observer_format_sha256') and
                                                               'sample.json' not in record['files']):
                raise ValueError('invalid telemetry sample')
            if record['monotonic_end_ns'] < record['monotonic_start_ns']:
                raise ValueError('non-monotonic telemetry sample')
            if previous is not None and record['monotonic_start_ns'] <= previous:
                raise ValueError('non-monotonic telemetry clock')
            if previous is not None and (record['monotonic_start_ns']-previous)/1e9 > max_gap_seconds:
                raise ValueError('telemetry gap')
            previous = record['monotonic_start_ns']
            if count in marked_indices:
                ends[count] = record['monotonic_end_ns']
            for name, expected in record['files'].items():
                payload = expected['text'].encode('utf-8', 'surrogateescape')
                if '/' in name or name in ('.', '..') or expected.get('bytes') != len(payload):
                    raise ValueError('unexpected telemetry record')
                if hashlib.sha256(payload).hexdigest() != expected['sha256']:
                    raise ValueError('telemetry bytes changed')
            count += 1
    if stream_hash.hexdigest()!=stream_pin.get('sha256'):
        raise ValueError('observation stream changed')
    if count != end['samples'] or count != len(history) or count < 2:
        raise ValueError('incomplete sample sequence')
    for mark in marks:
        index = mark['sample_index']
        if index not in ends or mark['monotonic_ns'] < ends[index]:
            raise ValueError('marker is not bound to a completed sample')
    if not set(required_marks).issubset({m['label'] for m in marks}):
        raise ValueError('phase marker missing')
    return {'passed': True, 'samples': count, 'max_gap_seconds': max_gap_seconds,
            'marks': required_marks, 'finished_sha256': digest(directory/'finished.json'),
            'stream_sha256': stream_hash.hexdigest(),
            'metric_contract_sha256': end.get('metric_contract_sha256'),
            'scope': 'recorded coverage/hashes only; metric semantics, overhead and independent '
                     'remote readback are separate gates'}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('serve')
    run.add_argument('--spec', required=True)
    run.add_argument('--sha256', required=True)
    run.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if args.action == 'serve':
        serve(dict(path=args.spec, sha256=args.sha256), args.output)


if __name__ == '__main__':
    main()
