"""Check representative execution paths using the suite's small-input mode."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

GROUPS = {
    '01-io': list(range(1, 17)),
    '02-fork': [1, 2, 3, 5, 6, 7, 10, 11, 12],
    '03-packed': list(range(1, 12)),
    '04-agent-replay': [1, 2, 3],
}


def selected_jobs(group=None):
    return [f'{name}-{index:02d}-r1' for name, indices in GROUPS.items()
            if group is None or name == group for index in indices]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=GROUPS)
    parser.add_argument('--list', action='store_true')
    options = parser.parse_args()
    jobs = selected_jobs(options.group)
    if options.list:
        print(json.dumps({'count': len(jobs), 'jobs': jobs}, indent=2))
        return
    from runtime_context import RUN_ID
    active = subprocess.run(['systemctl', 'is-active', 'ax-' + RUN_ID], capture_output=True, text=True)
    if active.stdout.strip() == 'active':
        raise RuntimeError('Run functional checks before the performance service starts')
    runner = Path(__file__).with_name('run_suite.py')
    for job in jobs:
        subprocess.run([sys.executable, str(runner), '--check', '--job', job], check=True)
    print(json.dumps({'passed': True, 'selected_conditions': len(jobs)}))


if __name__ == '__main__':
    main()
