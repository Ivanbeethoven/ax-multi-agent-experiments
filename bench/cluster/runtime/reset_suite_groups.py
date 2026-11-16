"""Preserve completed results and schedule selected groups for another run."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

import cluster_lib as cl
from run_suite import save


def reset(root, groups, label, jobs=None):
    root = Path(root).resolve()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', label) or label in {'.', '..'}:
        raise ValueError('Archive label must be a directory name')
    plan = json.loads((root / 'plan.json').read_text())
    status = json.loads((root / 'status.json').read_text())
    known = {job['group'] for job in plan['jobs']}
    selected_jobs = set(jobs or [])
    if bool(groups) == bool(selected_jobs):
        raise ValueError('Select either groups or individual conditions')
    if set(groups) - known:
        raise ValueError('Unknown experiment group')
    if selected_jobs - {job['id'] for job in plan['jobs']}:
        raise ValueError('Unknown experiment condition')
    affected_groups = set(groups) | {job['group'] for job in plan['jobs'] if job['id'] in selected_jobs}
    if any(value.get('state') == 'running' for value in status['jobs'].values()):
        raise ValueError('Finish the current condition before resetting its status')
    archive = root / 'archive' / 'results' / label
    archive.mkdir(parents=True, exist_ok=False)
    for name in ('config', 'versions'):
        if (root / name).exists():
            shutil.copytree(root / name, archive / name)
    for name in ('status.json', 'plan.json', 'inventory.json'):
        if (root / name).exists():
            shutil.copy2(root / name, archive / name)
    selected = []
    for job in plan['jobs']:
        if job['group'] not in groups and job['id'] not in selected_jobs:
            continue
        output = (root / job['output']).resolve()
        if not output.is_relative_to(root) or output == root:
            raise ValueError('Result directory must belong to this run')
        previous = status['jobs'].get(job['id'], {})
        selected.append({'job': job['id'], 'previous': previous})
        result = output / 'result.json'
        if result.exists():
            target = archive / result.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            result.rename(target)
        status['jobs'][job['id']] = {'state': 'planned'}
    for group in affected_groups:
        summary = root / group / 'summary.json'
        if summary.exists():
            target = archive / group / 'summary.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            summary.rename(target)
    status['current'] = None
    status['completed_runs'] = sum(v.get('state') == 'passed' for v in status['jobs'].values())
    save(archive / 'selection.json', {'groups': sorted(affected_groups), 'conditions': selected,
                                    'raw_results': 'Original attempt directories remain in place'})
    save(root / 'status.json', status)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--group', action='append', default=[])
    selection.add_argument('--job', action='append', default=[])
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    active = subprocess.run(['systemctl', 'is-active', 'ax-' + cl.RUN_ID],
                            capture_output=True, text=True)
    if active.stdout.strip() in {'active', 'activating', 'deactivating'}:
        raise RuntimeError('Stop the suite at a condition boundary first')
    print(reset(cl.RUN_ROOT, args.group, args.label, jobs=args.job))


if __name__ == '__main__':
    main()
