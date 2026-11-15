"""Run the approved conditions, retaining commands, metrics and raw results."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sys
import time
import traceback

import cluster_lib as cl
sys.path.insert(0, str(cl.REPO_ROOT))
import rustfs_layout_tools as layout
import run_c1 as fio

SYSTEMS = {'brewfs-native': 'brewfs-native-overlay', 'juicefs-redis': 'juicefs',
           'juicefs-overlay': 'juicefs-overlay'}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def select_backend(dynamic_enabled, out):
    name = 'dynamic' if dynamic_enabled else 'official'
    cl.select_rustfs_backend(name)
    config = cl.RUSTFS_BACKENDS[name]
    nodes = layout.discover_rustfs_nodes()
    for endpoint in config['endpoints']:
        cl.make_s3_client(endpoint).list_buckets()
    selection = dict(config, name=name)
    path = out / 'backend.json'
    save(path, dict(selection, node_details=nodes))
    selected = out / 'collector-backend.json'
    save(selected, selection)
    for host in config['nodes']:
        cl.sync_file_to_host(host, selected, '/srv/ax-cluster/direct/runtime/rustfs-backend.json')
    return nodes


def run_io(job, out, small=False):
    p = job['parameters']
    nodes = select_backend(p['dynamic'] == 'on', out)
    if p['jobs'] != fio.JOBS or p['working_set_bytes'] % p['jobs']:
        raise ValueError('Fio input must divide evenly across its eight jobs')
    fio.BYTES_PER_JOB = 4 * 1024 * 1024 if small else p['working_set_bytes'] // p['jobs']
    pool = fio.ParentPool(nodes, policy=cl.RUSTFS_BACKEND + f'-{fio.JOBS * fio.BYTES_PER_JOB}b' + ('-check' if small else ''))
    family = 'block-' + p['pattern']
    condition = {'id': job['id'], 'family': family, 'kind': 'measurement', 'parameters': copy.deepcopy(p)}
    if small:
        condition['parameters']['io_bytes'] = 16 * 1024 * 1024
    hint = ('off' if p['dynamic'] == 'off' else
            str(p['block_bytes']) if isinstance(p['block_bytes'], int) else 'auto')
    fio.execute_fio_family_run(condition, SYSTEMS[p['system']], job['repeat'], pool,
        brewfs_hint=hint, output=out, seed_override=job['seed'])
    return json.loads((out / 'result.json').read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['01-io', '02-fork', '03-packed', '04-agent-replay'])
    parser.add_argument('--job')
    parser.add_argument('--check', action='store_true', help='Use a small input to check the same execution path')
    options = parser.parse_args()
    plan = json.loads((cl.RUN_ROOT / 'plan.json').read_text())
    status_path = cl.RUN_ROOT / ('check-status.json' if options.check else 'status.json')
    status = json.loads(status_path.read_text()) if status_path.exists() else {'jobs': {}}
    from suite_groups import run_fork, run_packed, run_replay
    functions = {'01-io': run_io, '02-fork': run_fork, '03-packed': run_packed,
                 '04-agent-replay': run_replay}
    for job in plan['jobs']:
        if options.group and job['group'] != options.group:
            continue
        if options.job and job['id'] != options.job:
            continue
        if status['jobs'].get(job['id'], {}).get('state') == 'passed':
            continue
        output = cl.RUN_ROOT / ('checks' if options.check else '') / job['output']
        output.mkdir(parents=True, exist_ok=True)
        attempts = sorted(output.glob('attempt-*'))
        out = output / f'attempt-{len(attempts)+1:02d}'
        out.mkdir()
        save(out / 'job.json', job)
        save(out / 'source-record.json', json.loads((cl.REPO_ROOT / 'source-record.json').read_text()))
        status['current'] = job['id']
        status['jobs'][job['id']] = {'state': 'running', 'started_at': cl.utc_iso(), 'output': str(out)}
        save(status_path, status)
        print(f"START {job['id']} {job['parameters']}", flush=True)
        try:
            result = functions[job['group']](job, out, small=options.check)
            result.update(job_id=job['id'], group=job['group'], parameters=job['parameters'],
                          state='passed', validation_only=options.check)
            save(out / 'result.json', result)
            save(output / 'result.json', result)
            status['jobs'][job['id']].update(state='passed', ended_at=cl.utc_iso())
            print(f"DONE {job['id']}", flush=True)
        except Exception as exc:
            detail = {'state': 'failed', 'error': str(exc), 'traceback': traceback.format_exc()}
            save(out / 'failure.json', detail)
            status['jobs'][job['id']].update(detail)
            save(status_path, status)
            raise
        status['completed_runs'] = sum(item['state'] == 'passed' for item in status['jobs'].values())
        save(status_path, status)
        for host in cl.ALL_HOST_NAMES:
            cl.collector_rpc(host, {'action': 'flush'})
    status['current'] = None
    save(status_path, status)


if __name__ == '__main__':
    main()
