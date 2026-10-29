#!/usr/bin/env python3
"""Execute ONE reviewed argv, or seal/check explicitly selected result files.

No shell, retry, timeout, next step, upload or cleanup. Logs are private evidence.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from experiment_io import load, digest, write_new, contained


def execute(spec_path, expected_sha, output):
    if digest(spec_path) != expected_sha:
        raise ValueError('step spec changed after review')
    s = load(spec_path)
    if s.get('schema') != 'experiment-step-v1' or s.get('reviewed') is not True:
        raise ValueError('reviewed single-step spec required')
    argv = s['argv']
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or '\0' in x for x in argv):
        raise ValueError('nonempty argv list required')
    binary = Path(argv[0])
    if not binary.is_absolute() or digest(binary) != s['executable_sha256']:
        raise ValueError('pinned absolute executable required')
    if not Path(s['cwd']).is_absolute() or not Path(s['cwd']).is_dir():
        raise ValueError('existing absolute cwd required')
    if not s.get('inputs') or not s.get('cell_id') or not s.get('stage'):
        raise ValueError('cell, stage and pinned config/input references required')
    for item in s['inputs']:
        if not Path(item['path']).is_absolute() or digest(item['path']) != item['sha256']:
            raise ValueError('step input changed')
    for arg in argv:
        if re.search(r'(?i)(://[^/\s]*@|[?&](signature|token)=|--(password|secret|access-key)(=|$))', arg):
            raise ValueError('put credentials in protected files/environment, never argv')
    # Clean environment avoids silently inheriting layout/cache controls. Names
    # are reviewed in the spec; values (including credentials) are never saved.
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'SYSTEMROOT', 'TEMP', 'TMP') if k in os.environ}
    for key in s.get('inherit_environment', []):
        if key not in os.environ:
            raise ValueError('required environment variable missing: ' + key)
        env[key] = os.environ[key]
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    if os.name != 'nt': output.chmod(0o700)
    write_new(output/'spec.json', s)
    start = time.monotonic_ns()
    record = {'cell_id': s['cell_id'], 'stage': s['stage'], 'spec_sha256': expected_sha,
              'started_at': datetime.now(timezone.utc).isoformat(), 'wrapper_pid': os.getpid(),
              'total_timeout_s': None, 'automatic_retry': False}
    write_new(output/'started.json', record)
    try:
        with (output/'stdout.log').open('xb') as stdout, (output/'stderr.log').open('xb') as stderr:
            child = subprocess.Popen(argv, cwd=s['cwd'], env=env, stdout=stdout, stderr=stderr)
            write_new(output/'process.json', {'pid': child.pid, 'wrapper_pid': os.getpid(), 'argv0_sha256': s['executable_sha256']})
            code = child.wait()
        record.update(exit_code=code, completed=True, passed=code == 0,
                      wall_seconds=(time.monotonic_ns()-start)/1e9,
                      ended_at=datetime.now(timezone.utc).isoformat())
        write_new(output/'finished.json', record)
        return code
    except BaseException as error:
        # A missing finished.json is intentional: interruption is not proof the
        # child or its descendants stopped. Operator must inspect actual PIDs.
        write_new(output/'interrupted.json', {'error_type': type(error).__name__, 'process_state': 'unknown; inspect before any retry'})
        raise


def seal(root, files, output):
    root = Path(root).resolve(strict=True)
    if not files or len(files) != len(set(files)):
        raise ValueError('explicit unique file allowlist required')
    entries = {}
    for name in files:
        if re.search(r'(?i)(credential|secret|\.env$|id_(rsa|ed25519)|\.pem$)', name):
            raise ValueError('credential-like file must not enter result receipt')
        target = contained(root, name)
        if not target.is_file() or (root/name).is_symlink():
            raise ValueError('regular non-symlink evidence file required')
        entries[name] = {'bytes': target.stat().st_size, 'sha256': digest(target)}
    write_new(output, {'schema': 'experiment-retention-v1', 'files': entries,
                       'scope': 'allowlisted bytes; AI must check completeness/redaction; no cleanup authorization'})


def verify(root, manifest, expected_sha):
    if digest(manifest) != expected_sha:
        raise ValueError('retention manifest changed')
    data = load(manifest)
    if data.get('schema') != 'experiment-retention-v1' or not data.get('files'):
        raise ValueError('nonempty retention manifest required')
    for name, value in data['files'].items():
        file = contained(root, name)
        if file.stat().st_size != value['bytes'] or digest(file) != value['sha256']:
            raise ValueError('retained file differs: ' + name)
    return {'passed': True, 'files': len(data['files']), 'manifest_sha256': expected_sha,
            'scope': 'local retained bytes only; process termination and experiment acceptance are separate'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    e = sub.add_parser('run'); e.add_argument('--spec', required=True); e.add_argument('--spec-sha256', required=True)
    e.add_argument('--output', required=True)
    s = sub.add_parser('seal'); s.add_argument('--root', required=True); s.add_argument('--files', nargs='+', required=True)
    s.add_argument('--output', required=True)
    v = sub.add_parser('verify'); v.add_argument('--root', required=True); v.add_argument('--manifest', required=True)
    v.add_argument('--manifest-sha256', required=True); v.add_argument('--output', required=True)
    a = p.parse_args()
    if a.action == 'run': raise SystemExit(execute(a.spec, a.spec_sha256, a.output))
    elif a.action == 'seal': seal(a.root, a.files, a.output)
    else: write_new(a.output, verify(a.root, a.manifest, a.manifest_sha256))
