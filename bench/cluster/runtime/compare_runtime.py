"""Compare BrewFS runtime workers on an existing read-only fio dataset."""

import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import cluster_lib as cl
import run_c1 as fio
import rustfs_layout_tools as layout


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def process_details(info):
    proc = Path('/proc') / str(info['pid'])
    environment = dict(item.split('=', 1) for item in proc.joinpath('environ').read_text().split('\0') if '=' in item)
    quotas = []
    directory = Path(info['cgroup'])
    while directory.is_relative_to('/sys/fs/cgroup'):
        path = directory / 'cpu.max'
        if path.exists():
            quotas.append({'path': str(path), 'value': path.read_text().strip()})
        if directory == Path('/sys/fs/cgroup'):
            break
        directory = directory.parent
    return {'pid': info['pid'], 'binary': str(proc.joinpath('exe').resolve()),
            'runtime_environment': {key: environment.get(key) for key in ('TOKIO_WORKER_THREADS', 'RAYON_NUM_THREADS')},
            'allowed_cpus': sorted(os.sched_getaffinity(info['pid'])), 'cpu_quotas': quotas}


def run(args):
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'runner.py').write_bytes(Path(__file__).read_bytes())
    original = json.loads((args.source / 'mounts.json').read_text())
    record = next(r for r in original['mounts'] if r['phase'] == 'measure')
    spec = json.loads((args.source / (record['id'] + '-spec.json')).read_text())
    if args.current_profile:
        sys.path.insert(0, str(cl.REPO_ROOT))
        from bench.cluster.mounts import render
        record = render(record, json.loads((cl.REPO_ROOT / 'configs/mount-profiles.json').read_text()),
                        json.loads((cl.REPO_ROOT / 'configs/cluster.json').read_text()))
    job = json.loads((args.source / 'job.json').read_text())
    if job['parameters']['operation'] != 'read' or job['parameters']['pattern'] != 'sequential':
        raise ValueError('Source must be a sequential-read fio condition')
    fio.BYTES_PER_JOB = job['parameters']['working_set_bytes'] // fio.JOBS
    condition = {'family': 'block-sequential', 'parameters': job['parameters']}
    results = []
    cl.select_rustfs_backend('dynamic')
    nodes = layout.discover_rustfs_nodes()
    with cl.MeasurementLock():
        for setting in args.workers:
            output = args.out / setting
            output.mkdir()
            current = copy.deepcopy(record)
            identifier = args.out.name + '-workers-' + setting
            current.update(id=identifier, binary=str(args.binary),
                           mount_directory=str(cl.RUN_ROOT / 'mounts' / identifier),
                           cache_root=str(cl.RUN_ROOT / 'cache' / identifier))
            current['cache']['cache_root'] = current['cache_root']
            current['cache']['verify_cache_checksum'] = 'none'
            current['environment'].pop('TOKIO_WORKER_THREADS', None)
            if setting != 'auto':
                current['environment']['TOKIO_WORKER_THREADS'] = setting
            mounts = output / 'mounts.json'
            save(mounts, dict(original, mounts=[current]))
            layout.evict_bucket_page_cache(nodes, [spec['s3_bucket']])
            started = cl.start_mounts(mounts, {identifier: spec}, output)
            info = started[0]
            result = {'workers': setting, 'periodic_sampling': args.periodic,
                      'source': str(args.source), 'binary': str(args.binary),
                      'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      'mount': info, 'runtime': process_details(info)}
            try:
                # The helper is compiled from the same runtime module and runs in the mount cgroup.
                env = os.environ.copy()
                env.pop('TOKIO_WORKER_THREADS', None)
                if setting != 'auto':
                    env['TOKIO_WORKER_THREADS'] = setting
                command = ['python3', str(cl.SCRIPTS_DIR / 'mount_driver.py'), 'exec-in-cgroup',
                           '--cgroup', info['cgroup'], '--', str(args.runtime_count)]
                result['runtime']['selected_workers'] = int(subprocess.check_output(command, env=env, text=True).strip())
                text, _, _, _ = fio.build_fio_jobfile(condition, info['mount_directory'], job['seed'])
                workload = output / 'workload.fio'
                workload.write_text(text)
                for phase in ('cold', 'hot'):
                    if args.periodic == 'off':
                        for host in cl.ALL_HOST_NAMES:
                            cl.collector_rpc(host, {'action': 'pause'})
                    before = fio.scrape_mount_prom(info['metrics_port'], info['mount_directory'], current['engine'])
                    snap0 = cl.collector_mark_all(identifier, phase + '-start')
                    started_at = time.monotonic()
                    cl.run_on_host('A366', ['python3', str(cl.SCRIPTS_DIR / 'mount_driver.py'),
                        'exec-in-cgroup', '--cgroup', info['cgroup'], '--', 'fio', '--output-format=json+',
                        '--output=' + str(output / (phase + '.json')), str(workload)], timeout=600)
                    elapsed = time.monotonic() - started_at
                    snap1 = cl.collector_mark_all(identifier, phase + '-end')
                    after = fio.scrape_mount_prom(info['metrics_port'], info['mount_directory'], current['engine'])
                    save(output / (phase + '-client-before.json'), before)
                    save(output / (phase + '-client-after.json'), after)
                    save(output / (phase + '-hosts-before.json'), snap0)
                    save(output / (phase + '-hosts-after.json'), snap1)
                    metrics = fio.fio_json_to_metrics(json.loads((output / (phase + '.json')).read_text()))
                    if metrics['read_bytes'] != job['parameters']['io_bytes']:
                        raise ValueError('fio did not complete the requested read size')
                    result[phase] = {'seconds': elapsed, 'application': metrics,
                                     'throughput_mib_s': metrics['read_bytes'] / 1048576 / elapsed,
                                     'client_delta': fio.diff_prom(before, after)}
            finally:
                result['stop'] = cl.stop_mounts(started, output, remove_cache=True)
                if args.periodic == 'off':
                    for host in cl.ALL_HOST_NAMES:
                        cl.collector_rpc(host, {'action': 'resume'})
            result['state'] = 'passed'
            save(output / 'result.json', result)
            results.append(result)
            save(args.out / 'summary.json', {'results': results})
            print(json.dumps({'workers': setting, 'selected_workers': result['runtime']['selected_workers'],
                              'cold_mib_s': result['cold']['throughput_mib_s'],
                              'hot_mib_s': result['hot']['throughput_mib_s']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--runtime-count', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', nargs='+', default=['1', '4', 'auto'])
    parser.add_argument('--periodic', choices=['on', 'off'], default='on')
    parser.add_argument('--current-profile', action='store_true')
    args = parser.parse_args()
    if any(value != 'auto' and (not value.isdigit() or int(value) < 1) for value in args.workers):
        parser.error('Worker counts must be positive integers or auto')
    run(args)


if __name__ == '__main__':
    main()
