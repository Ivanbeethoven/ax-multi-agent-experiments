"""Repeat selected conditions after the current suite and related services finish."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import cluster_lib as cl
from reset_suite_groups import reset
from run_suite import save


def blockers(plan, status, required, units, disk, wait_terminal=()):
    terminal = set(wait_terminal)
    pending = [job['id'] for job in plan['jobs']
               if status['jobs'].get(job['id'], {}).get('state') not in
               (('passed', 'failed') if job['id'] in terminal else ('passed',))]
    pending += [job for job in required
                if status['jobs'].get(job, {}).get('state') != 'passed' and job not in pending]
    return {'unfinished': pending, 'active_services': units,
            'disk_space_sufficient': disk.free >= max(16 * 1024**3, disk.total * .12)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', action='append', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--require-job', action='append', default=[])
    parser.add_argument('--wait-terminal-job', action='append', default=[],
                        help='Independent condition that must finish but need not pass')
    parser.add_argument('--wait-service', action='append', default=[])
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--expected-run')
    args = parser.parse_args()
    if args.expected_run and args.expected_run != cl.RUN_ID:
        parser.error('The configured run differs from the queued run')
    if args.worker and not args.expected_run:
        parser.error('The worker requires its queued run identifier')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.label):
        parser.error('Use letters, digits, underscores or hyphens for the label')
    jobs = list(dict.fromkeys(args.job))
    plan = json.loads((cl.RUN_ROOT / 'plan.json').read_text())
    if set(jobs) - {job['id'] for job in plan['jobs']}:
        parser.error('A selected condition is absent from the current plan')
    terminal = set(args.wait_terminal_job)
    if terminal - {job['id'] for job in plan['jobs']} or terminal.intersection(jobs + args.require_job):
        parser.error('--wait-terminal-job must name independent conditions in this plan')
    state_path = cl.RUN_ROOT / 'continuations' / (args.label + '.json')
    entry = Path(__file__).with_name('run_suite.py')
    if args.worker:
        archive = cl.RUN_ROOT / 'archive/results' / args.label
        if not archive.exists():
            reset(cl.RUN_ROOT, [], args.label, jobs=jobs)
        else:
            selected = json.loads((archive / 'selection.json').read_text())['conditions']
            if {row['job'] for row in selected} != set(jobs):
                raise ValueError('Existing archive selects different conditions')
        for job in jobs:
            save(state_path, {'state': 'running', 'current': job, 'jobs': jobs})
            subprocess.run([sys.executable, str(entry), '--job', job], check=True)
        save(state_path, {'state': 'completed', 'jobs': jobs, 'completed_at': cl.utc_iso()})
        return
    unit = 'ax-' + cl.RUN_ID + '.service'
    while True:
        active = [name for name in [unit, *args.wait_service] if subprocess.run(
            ['systemctl', 'is-active', name], capture_output=True, text=True
        ).stdout.strip() in {'active', 'activating', 'deactivating'}]
        plan = json.loads((cl.RUN_ROOT / 'plan.json').read_text())
        status = json.loads((cl.RUN_ROOT / 'status.json').read_text())
        waiting = blockers(plan, status, args.require_job, active, shutil.disk_usage(cl.RUN_ROOT), terminal)
        save(state_path, {'state': 'waiting', 'jobs': jobs, **waiting})
        if not args.execute:
            print(json.dumps(waiting, indent=2))
            return
        if not waiting['unfinished'] and not active and waiting['disk_space_sufficient']:
            command = ['systemd-run', '--unit=' + unit, '--collect',
                       '-p', 'MemoryMax=8G', '-p', 'MemorySwapMax=0',
                       '-p', 'LimitNOFILE=1048576',
                       '-p', 'StandardOutput=append:' + str(cl.RUN_ROOT / 'logs/suite.log'),
                       '-p', 'StandardError=append:' + str(cl.RUN_ROOT / 'logs/suite.log'),
                       sys.executable, str(Path(__file__).resolve()), '--worker',
                       '--expected-run', cl.RUN_ID, '--label', args.label]
            for job in jobs:
                command += ['--job', job]
            launch = subprocess.run(command, capture_output=True, text=True)
            if launch.returncode == 0:
                return
            save(state_path, {'state': 'waiting', 'jobs': jobs, 'launch_message': launch.stderr})
        time.sleep(30)


if __name__ == '__main__':
    main()
