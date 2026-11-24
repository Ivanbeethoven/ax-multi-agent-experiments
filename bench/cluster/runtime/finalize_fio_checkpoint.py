"""Finalize a verified fio measurement saved before the coordinator stopped."""
import argparse
import json
from pathlib import Path
import subprocess

from runtime_context import RUN_ROOT, RUN_ID


def read(path):
    return json.loads(path.read_text())


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    active = subprocess.run(['systemctl', 'is-active', 'ax-' + RUN_ID], capture_output=True, text=True)
    if active.stdout.strip() == 'active':
        raise RuntimeError('Stop the coordinator before finalizing a saved measurement')
    status = read(RUN_ROOT / 'status.json')
    item = status['jobs'][args.job]
    out = Path(item['output'])
    job = read(out / 'job.json')
    if job['group'] != '01-io' or job['id'] != args.job:
        raise ValueError('Select a fio job from this run')
    measurement = read(out / 'measurement.json')
    stops = [read(p) for p in out.glob('*-stop.json')]
    if not measurement['verification']['passed'] or len(stops) < 2 or not all(s['unmounted_cleanly'] for s in stops):
        raise RuntimeError('Measurement requires successful verification and completed unmounts')
    mounts = read(out / 'mounts.json')['mounts']
    mounted = next(m for m in mounts if m['phase'] == 'measure')
    stopped = next(s for s in stops if s['id'] == mounted['id'])
    cold = measurement['application']
    application = dict(cold, throughput_mib_s=(cold['read_bytes'] + cold['write_bytes']) / 1048576 / measurement['app_done_seconds'],
                       iops=cold['operations'] / measurement['app_done_seconds'])
    hot = measurement.get('repeat_read')
    if hot:
        application.update(hot_throughput_mib_s=(hot['read_bytes'] + hot['write_bytes']) / 1048576 / measurement['repeat_read_seconds'],
                           hot_iops=hot['operations'] / measurement['repeat_read_seconds'], hot_latency_us=hot['latency_us'])
    result = dict(schema='ax-run-result-v1', job_id=job['id'], condition_id=job['id'], group=job['group'],
        system=mounted['engine'], family='block-' + job['parameters']['pattern'], kind='measurement',
        client_mount=dict(peak_memory_bytes=stopped['final_cgroup']['memory_peak'], pending_at_exit=stopped['pending_at_exit']),
        state='passed', validation_only=False, repeat=job['repeat'], seed=job['seed'], parameters=job['parameters'],
        attempt=int(out.name.removeprefix('attempt-')), execution_mode='exclusive',
        started_at=item['started_at'], ended_at=measurement['measurement_completed_at'],
        application=application, timing=dict(app_done_seconds=measurement['app_done_seconds'], hot_pass_seconds=measurement['repeat_read_seconds']),
        backend_delta=measurement['backend_delta'], hot_backend_delta=measurement['repeat_backend_delta'],
        object_layout=measurement['object_layout']['summary'], verification=measurement['verification'],
        object_retention='until-run-archive', measurement_file='measurement.json')
    save(out / 'result.json', result)
    save(out.parent / 'result.json', result)
    item.update(state='passed', ended_at=measurement['measurement_completed_at'])
    status['completed_runs'] = sum(j['state'] == 'passed' for j in status['jobs'].values())
    status['current'] = None
    save(RUN_ROOT / 'status.json', status)
    print(json.dumps({'job': args.job, 'state': 'passed', 'application': application}))


if __name__ == '__main__':
    main()
