"""Run outstanding Packed reads sequentially after the replay group releases the cluster."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def waiting(status, jobs, active_units, binary_ready, free_bytes):
    replay = [j['id'] for j in jobs if j['group'] == '04-agent-replay']
    reasons = [name for name in replay if status['jobs'].get(name, {}).get('state') != 'passed']
    if not replay:
        reasons.append('no replay conditions in plan')
    reasons.extend(active_units)
    if not binary_ready:
        reasons.append('optimized binary')
    if free_bytes < 16 * 2**30:
        reasons.append('coordinator disk space')
    return reasons


def main():
    import cluster_lib as cl
    from run_suite import save

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-run', required=True)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--job', required=True, action='append')
    parser.add_argument('--wait-service', action='append', default=[])
    parser.add_argument('--worker', action='store_true')
    a = parser.parse_args()
    root = cl.RUN_ROOT
    if a.expected_run != cl.RUN_ID or not a.binary.is_absolute():
        parser.error('Set the active run and absolute binary path')
    jobs = json.loads((root/'plan.json').read_text())['jobs']
    packed = {j['id'] for j in jobs if j['group'] == '03-packed' and
              j['parameters'].get('system') == 'brewfs-packed' and j['parameters'].get('phase') == 'read'}
    if len(set(a.job)) != len(a.job) or set(a.job) - packed:
        parser.error('Specify distinct Packed read jobs from the plan')
    state = root/'continuations/packed-read-formal.json'
    unit = 'ax-' + cl.RUN_ID + '.service'

    def status():
        return json.loads((root/'status.json').read_text())

    def emit(phase, **fields):
        save(state, dict(state=phase, time=cl.utc_iso(), jobs=a.job, binary=str(a.binary), **fields))

    if not a.worker:
        previous = None
        while True:
            services = [n for n in [unit, *a.wait_service] if subprocess.run(
                ['systemctl', 'is-active', n], capture_output=True, text=True
            ).stdout.strip() in ('active','activating','deactivating','reloading')]
            reasons = waiting(status(), jobs, services, os.access(a.binary, os.X_OK)
                              and (a.binary.parent/'build.json').is_file(), shutil.disk_usage(root).free)
            if reasons != previous:
                emit('waiting', pending=reasons)
                previous = reasons
            if not reasons:
                # Using the existing unit name reserves the cluster before distributing
                # the binary or starting any readers; another launcher cannot win twice.
                cmd = ['systemd-run', '--unit='+unit, '--collect', '-p', 'MemoryMax=8G',
                       '-p', 'LimitNOFILE=1048576', '-p', 'StandardOutput=append:'+str(root/'logs/suite.log'),
                       '-p', 'StandardError=inherit', sys.executable, str(Path(__file__).resolve()),
                       *sys.argv[1:], '--worker']
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if not proc.returncode:
                    return
                emit('waiting', launch_error=proc.stderr.strip())
            time.sleep(15)

    try:
        reasons = waiting(status(), jobs, [], a.binary.is_file(), shutil.disk_usage(root).free)
        if reasons:
            raise RuntimeError(str(reasons))
        emit('distributing')
        build = json.loads((a.binary.parent/'build.json').read_text())
        if build['optimized'] is not True or build['filesize'] != a.binary.stat().st_size:
            raise ValueError('Expected the optimized Packed build')
        for host in cl.ALL_HOST_NAMES:
            if host != 'A366':
                stage = str(a.binary)+'.incoming'
                cl.sync_file_to_host(host, a.binary, stage)
                cl.run_on_host(host, ['chmod','755',stage])
                cl.run_on_host(host, ['mv','-T',stage,str(a.binary)])
            length = cl.run_on_host(host, ['stat','-Lc','%s',str(a.binary)]).stdout
            if int(length) != build['filesize']:
                raise ValueError('Incomplete binary on '+host)
            cl.run_on_host(host, [str(a.binary),'--version'])
        prior = root/'continuations/packed-before-formal.json'
        if not prior.exists():
            save(prior, {name: status()['jobs'][name] for name in a.job})
        env = dict(os.environ, AX_PACKED_BREWFS_BINARY=str(a.binary))
        failed = []
        for job in a.job:
            if status()['jobs'][job]['state'] == 'passed':
                continue
            emit('running', current=job, build=build, failed=failed)
            command = [sys.executable, str(cl.SCRIPTS_DIR/'run_suite.py'), '--job', job]
            proc = subprocess.run(command, env=env)
            out = Path(status()['jobs'][job]['output'])
            save(out/'packed-build.json', build)
            if proc.returncode:
                failed.append(job)
                stops_path = out/'reader-stops.json'
                stops = json.loads(stops_path.read_text()) if stops_path.exists() else []
                if len(stops) != 32 or not all(x.get('unmounted_cleanly') for x in stops):
                    raise RuntimeError('Reader shutdown requires inspection: '+job)
                continue
            result = json.loads((out/'result.json').read_text())
            params = result['parameters']
            expected = params['files'] * params['epochs']
            readers = result['readers']
            if (len(readers) != params['readers'] or
                any(not x['loader']['passed'] or x['loader']['read_ops'] != expected or
                    x['loader']['actual_read_bytes'] != expected * params['file_bytes'] for x in readers)
                or len(result['stops']) != len(readers) or
                not all(x['unmounted_cleanly'] for x in result['stops'])):
                raise RuntimeError('Incomplete formal read or unmount: '+job)
        if failed:
            raise RuntimeError('Read failed: '+', '.join(failed))
        emit('completed')
    except Exception as error:
        emit('failed', error=str(error))
        raise


if __name__ == '__main__':
    main()
