"""Append approved conditions after the current suite and diagnostics finish."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import cluster_lib as cl
sys.path.insert(0, str(cl.REPO_ROOT))
from bench.cluster.suite import build, extend
from run_suite import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-service', action='append', default=[])
    args = parser.parse_args()
    state_path = cl.RUN_ROOT / 'extensions' / 'state.json'
    previous = None
    while True:
        units = ['ax-' + cl.RUN_ID + '.service', *args.wait_service]
        active = [unit for unit in units if subprocess.run(
            ['systemctl', 'is-active', unit], capture_output=True, text=True
        ).stdout.strip() in {'active', 'activating', 'deactivating'}]
        status = json.loads((cl.RUN_ROOT / 'status.json').read_text())
        unfinished = [name for name, row in status['jobs'].items() if row['state'] != 'passed']
        state = {'waiting_services': active, 'unfinished_conditions': unfinished}
        if state != previous:
            save(state_path, state)
            previous = state
        if not active and not unfinished:
            break
        time.sleep(30)
    suite = json.loads((cl.REPO_ROOT / 'configs/experiment-suite.json').read_text())
    if suite['approval'] != 'approved':
        raise ValueError('Experiment configuration is not approved')
    added = extend(build(suite), cl.RUN_ROOT)
    save(state_path, {'state': 'checking', 'added': added})
    entrypoint = Path(__file__).with_name('run_suite.py')
    for identifier in added:
        subprocess.run([sys.executable, str(entrypoint), '--check', '--job', identifier], check=True)
    save(state_path, {'state': 'starting', 'added': added})
    command = ['systemd-run', '--unit=ax-' + cl.RUN_ID, '--collect',
               '-p', 'WorkingDirectory=' + str(cl.REPO_ROOT), '-p', 'MemoryMax=8G',
               '-p', 'MemorySwapMax=0', '-p', 'LimitNOFILE=1048576',
               '-p', 'StandardOutput=append:' + str(cl.RUN_ROOT / 'logs/suite.log'),
               '-p', 'StandardError=append:' + str(cl.RUN_ROOT / 'logs/suite.log'),
               sys.executable, str(entrypoint)]
    subprocess.run(command, check=True)
    save(state_path, {'state': 'started', 'added': added})


if __name__ == '__main__':
    main()
