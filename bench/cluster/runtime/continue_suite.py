"""Complete fio, measure sampling overhead, then run the remaining groups."""

import json
from pathlib import Path
import subprocess
import sys

import cluster_lib as cl
from run_suite import save


def main():
    scripts = Path(__file__).resolve().parent
    subprocess.run([sys.executable, str(scripts / 'run_suite.py'), '--group', '01-io'], check=True)
    status = json.loads((cl.RUN_ROOT / 'status.json').read_text())
    measured = Path(status['jobs']['01-io-04-r1']['output'])
    before = json.loads((measured / 'result.json').read_text())
    calibration = cl.RUN_ROOT / 'calibration' / ('sampling-' + measured.name)
    report = calibration / 'comparison.json'
    if not report.exists():
        calibration.mkdir(parents=True, exist_ok=True)
        attempts = list(calibration.glob('off-*'))
        out = calibration / f'off-{len(attempts)+1:02d}'
        profile = json.loads((cl.REPO_ROOT / 'configs/mount-profiles.json').read_text())
        workers = profile['graftfs_environment']['TOKIO_WORKER_THREADS']
        subprocess.run([sys.executable, str(scripts / 'run_fio_candidate.py'),
                        '--workers', workers, '--binary', '/srv/ax-cluster/direct/bin/graftfs',
                        '--job', '01-io-04-r1', '--out', str(out), '--periodic', 'off'], check=True)
        after = json.loads((out / 'summary.json').read_text())['results'][0]
        on_seconds = before['timing']['app_done_seconds']
        off_seconds = after['timing']['app_done_seconds']
        save(report, {'periodic_on': str(measured), 'periodic_off': str(out),
                      'on_seconds': on_seconds, 'off_seconds': off_seconds,
                      'time_difference_percent': 100 * (on_seconds / off_seconds - 1),
                      'on_latency_us': before['application']['latency_us'],
                      'off_latency_us': after['application']['latency_us'],
                      'pairs': 1})
        print('SAMPLING ' + str(report), flush=True)
    subprocess.run([sys.executable, str(scripts / 'run_suite.py')], check=True)


if __name__ == '__main__':
    main()
