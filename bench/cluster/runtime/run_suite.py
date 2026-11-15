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


def set_dynamic(enabled, out):
    expected = 'true' if enabled else 'false'
    nodes = layout.discover_rustfs_nodes()
    if all(n['quantum_enable'] == expected and n['block_hint_enable'] == expected for n in nodes.values()):
        save(out / 'dynamic.json', {'enabled': enabled, 'nodes': nodes})
        return nodes
    source = '''
import json,subprocess,sys
from pathlib import Path
args=json.loads(sys.stdin.read());p=Path(args['compose_files']);d=json.loads(p.read_text())
service=d['services']['rustfs'];env=service['environment']
if isinstance(env,list):env=dict(item.split('=',1) for item in env)
for name in ['RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE','RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED','RUSTFS_EC_READ_QUANTUM_ENABLE','RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED']:
 env[name]=args['value']
service['environment']=env;p.write_text(json.dumps(d,indent=2)+'\\n')
subprocess.run(['docker','compose','-f',str(p),'up','-d','--no-deps','--force-recreate','rustfs'],cwd=args['compose_dir'],check=True)
'''
    def change(item):
        host, settings = item
        cl.run_on_host(host, ['python3', '-c', source],
            input_bytes=json.dumps({**settings, 'value': expected}).encode(), timeout=180)
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(change, nodes.items()))
    for attempt in range(60):
        try:
            for endpoint in cl.RUSTFS_ENDPOINTS:
                cl.make_s3_client(endpoint).list_buckets()
            break
        except Exception:
            if attempt == 59:
                raise
            time.sleep(2)
    actual = layout.discover_rustfs_nodes()
    if any(n['quantum_enable'] != expected or n['block_hint_enable'] != expected for n in actual.values()):
        raise RuntimeError('RustFS dynamic settings differ from the requested values')
    save(out / 'dynamic.json', {'enabled': enabled, 'nodes': actual})
    return actual


def run_io(job, out, small=False):
    p = job['parameters']
    nodes = set_dynamic(p['dynamic'] == 'on', out)
    pool = fio.ParentPool(nodes, policy=p['dynamic'] + ('-check' if small else ''))
    family = 'block-' + p['pattern']
    condition = {'id': job['id'], 'family': family, 'kind': 'measurement', 'parameters': copy.deepcopy(p)}
    if small:
        fio.BYTES_PER_JOB = 4 * 1024 * 1024
        condition['parameters']['io_bytes'] = 16 * 1024 * 1024
    hint = str(p['block_bytes']) if isinstance(p['block_bytes'], int) else 'auto'
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
