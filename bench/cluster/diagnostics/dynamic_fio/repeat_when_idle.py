"""Repeat fio with matched RustFS sync workers after the formal suite finishes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def readiness(status, suite_active, diagnostics_active):
    if suite_active:
        return 'formal-suite-running'
    jobs = status.get('jobs', {})
    if not jobs or any(row.get('state') != 'passed' for row in jobs.values()):
        return 'formal-suite-incomplete'
    if diagnostics_active:
        return 'diagnostics-running'
    return 'ready'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, default=Path('/srv/ax-cluster/runs/direct/run-002'))
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    args.out.resolve().relative_to(Path('/srv/ax-cluster/diagnostics'))
    scripts = Path(__file__).resolve().parent
    operations = ['seqread', 'seqwrite', 'randread', 'randwrite']
    commands = [['bash', str(args.out / 'package/run.sh'), str(args.candidate), str(workers),
                 'auto', 'true', '512', 'whole-object', *operations] for workers in (1, 8)]
    if not args.execute:
        print(json.dumps({'commands': commands, 'output': str(args.out)}, indent=2))
        return
    args.out.mkdir(parents=True, exist_ok=False)
    package = args.out / 'package'
    package.mkdir()
    shutil.copy2(Path(__file__), package / Path(__file__).name)
    for name in ['run.sh', 'fio.py', 'report.py', 'wait-io.sh']:
        shutil.copy2(scripts / name, package / name)
    state_path = args.out / 'queue.json'
    previous = None
    while True:
        status = json.loads((args.run / 'status.json').read_text())
        active = subprocess.run(['systemctl', 'is-active', 'ax-' + args.run.name + '.service'],
                                capture_output=True, text=True).stdout.strip() == 'active'
        other = subprocess.run(['systemctl', 'list-units', '--type=service', '--state=running',
                                '--no-legend', 'ax-dynamic-followup-*'], capture_output=True,
                               text=True, check=True).stdout.strip()
        reason = readiness(status, active, bool(other))
        if reason != previous:
            state_path.write_text(json.dumps({'state': reason, 'time_unix': time.time(),
                                             'commands': commands}, indent=2) + '\n')
            print(reason, flush=True)
            previous = reason
        if reason == 'ready':
            break
        time.sleep(60)
    if not args.candidate.is_file():
        raise FileNotFoundError(args.candidate)
    reference = args.run / 'versions/fio-tuning.json'
    if reference.exists():
        shutil.copy2(reference, args.out / 'fio-tuning.json')
    build = Path('/srv/ax-cluster/direct/bin/brewfs.build.json')
    if build.exists():
        shutil.copy2(build, args.out / 'brewfs.build.json')
    candidate_stat = args.candidate.stat()
    (args.out / 'candidate.json').write_text(json.dumps({
        'path': str(args.candidate), 'bytes': candidate_stat.st_size,
        'modified_ns': candidate_stat.st_mtime_ns}, indent=2) + '\n')
    environment = {**os.environ, 'DIAG_ROOT': str(args.out), 'DIAG_FORMAL_RUN': str(args.run),
                   'DIAG_BREWFS_BIN': '/srv/ax-cluster/direct/bin/brewfs', 'DIAG_OTEL': '0'}
    try:
        for command in commands:
            subprocess.run(['bash', str(package / 'wait-io.sh')], cwd=args.out, check=True)
            subprocess.run(command, cwd=args.out, env=environment, check=True)
        with (args.out / 'report.txt').open('w') as output:
            subprocess.run(['python3', str(package / 'report.py'), str(args.out)],
                           stdout=output, check=True)
        state = 'passed'
    except Exception as error:
        state_path.write_text(json.dumps({'state': 'failed', 'error': str(error),
                                         'time_unix': time.time()}, indent=2) + '\n')
        raise
    state_path.write_text(json.dumps({'state': state, 'time_unix': time.time(),
                                     'commands': commands}, indent=2) + '\n')


if __name__ == '__main__':
    main()
