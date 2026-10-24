#!/usr/bin/env python3
"""Run one trusted, unpacked full-FUSE case with separate phase observations."""
import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case', type=Path, required=True)
    p.add_argument('--workspace-parent', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--worker-id', required=True)
    p.add_argument('--timeout', type=float, default=1800)
    a = p.parse_args()
    if not math.isfinite(a.timeout) or a.timeout <= 0:
        p.error('timeout must be positive and finite')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', a.run_id):
        p.error('run-id must be a simple path-free identifier')
    source = a.case.resolve(strict=True)
    parent = a.workspace_parent.resolve(strict=True)
    binary = a.binary.resolve(strict=True)
    output = a.out.resolve()
    root = parent / a.run_id
    if output.is_relative_to(parent) or parent.is_relative_to(output):
        p.error('Measurement directory and workspace parent must be disjoint')
    if output.is_relative_to(source) or source.is_relative_to(output):
        p.error('Measurement directory and source case must be disjoint')
    if root.exists():
        p.error('Workspace already exists; use a new run ID')
    output.mkdir(parents=True, exist_ok=False)
    scripts = Path(__file__).resolve().parent
    helper = [sys.executable, str(scripts / 'replay_case.py')]
    case_args = ['--case', str(source), '--root', str(root)]
    phases = [
        ('prepare', parent, helper + ['prepare'] + case_args),
        ('replay', root, [str(binary), str(source / 'capture/events.jsonl'),
                          str(source / 'capture/blobs'), str(root), str(output / 'replay.json')]),
        ('verify', root, helper + ['verify'] + case_args + ['--replay', str(output / 'replay.json')])]
    reports = []
    started = time.monotonic()
    deadline = started + a.timeout
    for phase, work, command in phases:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reports.append({'phase': phase, 'observer_exit_code': None, 'timed_out_before_start': True})
            break
        status = subprocess.run([sys.executable, str(scripts / 'observe.py'), 'run',
                                 '--run-id', a.run_id, '--worker-id', a.worker_id,
                                 '--phase', phase, '--work-root', str(work),
                                 '--out', str(output / phase), '--timeout', str(remaining),
                                 '--', *command]).returncode
        reports.append({'phase': phase, 'observer_exit_code': status})
        if status:
            break
    result = {'run_id': a.run_id, 'worker_id': a.worker_id, 'phases': reports,
              'valid': len(reports) == 3 and all(r['observer_exit_code'] == 0 for r in reports),
              'wall_seconds': time.monotonic() - started, 'total_timeout_s': a.timeout,
              'scope': 'single local case, strict replay and tree verification; no distributed coordination'}
    (output / 'case-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    sys.exit(main())
