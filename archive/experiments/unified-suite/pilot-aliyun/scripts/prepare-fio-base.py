#!/usr/bin/env python3
"""Prepare both 64 MiB CRC bases and verify a fresh-cache reopen.

One new flat asset only. Never flush metadata, retry work or claim a formal cell.
The pinned node tool supplies the frozen runtime; this wrapper can live outside it.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

CLIENT = Path('/srv/ax-client')
SLOT = Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs')
BYTES = 67108864
GEOMETRIES = ((1048576, 'seq'), (4096, 'random'))


def load(path): return json.loads(Path(path).read_text())


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ref(path): return dict(path=str(path), sha256=digest(path))


def write_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')


def unused_metrics(product):
    try:
        connection = socket.create_connection(('127.0.0.1', 8910 if product=='brewfs' else 9567), timeout=1)
    except OSError: return
    connection.close()
    raise ValueError('Client metrics port is already occupied; do not disturb the existing mount')


def prepare(a):
    if not re.fullmatch(r'[a-z0-9-]{3,32}', a.attempt):
        raise ValueError('Short explicit preparation attempt required')
    for path, parent in ((a.asset, CLIENT/'a'), (a.out, CLIENT/'preparation')):
        if (not path.is_absolute() or path.parent!=parent or path.exists() or path.is_symlink() or
                not re.fullmatch(r'[a-z0-9-]{3,70}', path.name)):
            raise ValueError('New owned absolute asset and preparation paths required')
    if not 20<=a.database<256: raise ValueError('Unused pilot database 20..255 required')
    if digest(a.node_tool)!=a.node_tool_sha256 or digest(a.filesystem_binary)!=a.filesystem_binary_sha256:
        raise ValueError('Pinned preparation tool or filesystem binary changed')
    for path in (a.node_tool, a.filesystem_binary, a.plan, a.inventory):
        if not path.is_absolute() or path.is_symlink(): raise ValueError('Absolute regular pinned inputs required')
    sys.path.insert(0, str(a.node_tool.resolve(strict=True).parents[4]))
    from bench.fio_workload import validate_params, validate_raw, summarize
    from bench.runtime.common import pinned, alive, mounts
    from bench.runtime.observe import prometheus
    from bench.runtime.pilot_pools import endpoint_binding, pool_of
    plan = load(a.plan); arm = 'native' if a.product=='brewfs' else 'juicefs'
    cells = [cell for cell in plan['cells'] if cell['workload']=='fio' and
             cell['arm_id']==arm and pool_of(cell)==a.pool]
    if not cells: raise ValueError('Requested flat C1 product is not assigned to this pool')
    selected = next((cell for cell in cells if cell['case']=='fio-randread'), None)
    if selected is None: raise ValueError('Actual fio-randread binding required')
    source_cells = {}
    for block, label in GEOMETRIES:
        matches = [cell for cell in plan['cells'] if cell['workload']=='fio' and cell['params']['block_bytes']==block]
        if not matches or not any(cell in cells for cell in matches):
            raise ValueError('Both C1 CRC block geometries required for this pool')
        for cell in matches:
            params = cell['params']; validate_params(params)
            if (params['file_bytes_per_job']!=BYTES or params['io_bytes_per_job']!=BYTES or
                    params['numjobs']!=1):
                raise ValueError('This preparation requires the frozen single-job 64 MiB profile')
        # pilot-node fio-base selects the first frozen cell for this geometry.
        source_cells[block] = matches[0]
    binding = endpoint_binding(plan, selected, a.inventory)
    states = [CLIENT/'m'/(a.attempt+suffix) for suffix in ('-prepare', '-cold')]
    if any(path.exists() or path.is_symlink() for path in states):
        raise ValueError('Original preparation mount state already exists; do not retry')
    if SLOT.read_text().strip(): raise ValueError('Agent slot remains occupied')
    unused_metrics(a.product)
    a.out.mkdir(parents=True, exist_ok=False)
    write_new(a.out/'started.json', dict(at=datetime.now(timezone.utc).isoformat(), pool=a.pool,
        cell_id=selected['id'], plan=ref(a.plan), inventory=ref(a.inventory), node_tool=ref(a.node_tool),
        filesystem_binary=ref(a.filesystem_binary), wrapper=ref(Path(__file__)), database=a.database,
        geometries=[block for block, _ in GEOMETRIES], bytes_per_geometry=BYTES,
        purpose='asset-preparation', automatic_retry=False))
    active = None; stopping = False

    def run(name, argv):
        directory = a.out/name; directory.mkdir(); begin = time.monotonic()
        argv = list(map(str, argv))
        write_new(directory/'command.json', dict(argv=argv, automatic_retry=False))
        with (directory/'stdout').open('xb') as out, (directory/'stderr').open('xb') as err:
            child = subprocess.Popen(argv, stdout=out, stderr=err)
            write_new(directory/'process.json', dict(pid=child.pid)); code = child.wait()
        write_new(directory/'exit.json', dict(exit_code=code, wall_seconds=time.monotonic()-begin))
        if code: raise RuntimeError('Inspect original failed stage: '+str(directory))
        return directory

    def helper(action, name, **fields):
        argv = [sys.executable, a.node_tool, action, '--asset', a.asset]
        for key, value in fields.items():
            if value is True: argv += ['--'+key.replace('_', '-')]
            elif value is not None: argv += ['--'+key.replace('_', '-'), value]
        return run(name, argv)

    def check_fio(directory, block, phase):
        value = load(directory/'result.json'); raw = load(directory/'fio.json'); exit_ = load(directory/'exit.json')
        params = source_cells[block]['params']
        if (value.get('passed') is not True or value.get('completed') is not True or
                value.get('application_bytes')!=BYTES or value.get('jobs')!=1 or
                value.get('phase')!=phase or value.get('params')!=params or
                value.get('seed')!=plan['config']['seed'] or exit_.get('exit_code')!=0 or
                value.get('raw_sha256')!=digest(directory/'fio.json')):
            raise ValueError('Original CRC fio result differs from the fixed input profile')
        summarize(raw, 1, BYTES, exit_['exit_code'], value['wrapper_wall_seconds'])
        validate_raw(raw, params, plan['config']['seed'], phase)
        return ref(directory/'result.json')

    def stop(name, target):
        nonlocal active, stopping
        stopping = True
        helper('stop', name, mount_run=active, out=target)
        value = load(target)
        if (value.get('passed') is not True or value.get('exit_code')!=0 or
                value.get('mount_absent') is not True or value!=load(active/'stopped.json') or
                mounts(load(a.asset/'asset.json')['mount_point'])):
            raise ValueError('Original preparation mount has not exited normally')
        process = load(active/'process.json'); deadline = time.monotonic()+5
        while alive(process['process']) or alive(process['supervisor']):
            if time.monotonic()>=deadline:
                raise ValueError('Original preparation mount process or supervisor remains live')
            time.sleep(.05)
        active = None; stopping = False

    try:
        check = run('database-empty', ['redis-cli', '-u', binding['metadata']+'/'+str(a.database), 'DBSIZE'])
        if (check/'stdout').read_text().strip()!='0':
            raise ValueError('Metadata database is not empty; never flush or overwrite it')
        SLOT.write_text(str(os.getpid()))
        helper('asset-init', 'asset-init', product=a.product, database=a.database, plan=a.plan,
            cell=selected['id'], inventory=a.inventory, filesystem_binary=a.filesystem_binary,
            filesystem_binary_sha256=a.filesystem_binary_sha256, out=a.out/'asset-init-proof')
        asset = load(a.asset/'asset.json')
        if (asset['plan']!=ref(a.plan) or asset.get('pool')!=a.pool or asset['database']!=a.database or
                asset['filesystem_binary']!=ref(a.filesystem_binary)):
            raise ValueError('New asset identity differs from the frozen preparation inputs')
        proofs = []
        for index, state in enumerate(states):
            draft = a.asset/('prep-mount-'+str(index+1).zfill(2))
            helper('draft-mount', 'draft-'+str(index+1), cell=selected['id'], out=draft)
            active = state
            helper('start', 'start-'+str(index+1), spec=draft/'mount.reviewed.json', out=state, join_slot=True)
            for block, label in GEOMETRIES:
                if index==0:
                    directory = a.asset/('base-'+label+'-prepare')
                    helper('fio-base', 'prepare-'+label, block_bytes=block, out=directory)
                    proofs.append(check_fio(directory, block, 'prepare'))
                directory = a.asset/(('base-'+label+'-verify') if index==0 else ('cold-base-verify-'+str(block)))
                helper('fio-base-check', ('warm-' if index==0 else 'cold-')+label, block_bytes=block, out=directory)
                proofs.append(check_fio(directory, block, 'verify'))
            if index==0:
                helper('debt', 'base-debt', cell=selected['id'], out=a.asset/'base-debt')
                debt = load(a.asset/'base-debt/result.json')
                if debt.get('passed') is not True or len(debt.get('samples', []))!=2:
                    raise ValueError('Two original zero-debt observations required')
                samples = [load(pinned(entry)) for entry in debt['samples']]
                for value in samples:
                    if (value.get('passed') is not True or value.get('cell_id')!=selected['id'] or
                            any(type(value.get(key)) not in (int, float) or value[key]!=0
                                for key in ('pending_upload_bytes', 'pending_upload_operations'))):
                        raise ValueError('Original preparation upload debt is not zero')
                if samples[1]['monotonic_ns']-samples[0]['monotonic_ns']<1000000000:
                    raise ValueError('Zero-debt observations must be at least one second apart')
                port = 8910 if a.product=='brewfs' else 9567
                with build_opener(ProxyHandler({})).open('http://127.0.0.1:'+str(port)+'/metrics', timeout=5) as response:
                    raw = response.read(16*1024**2+1)
                if len(raw)>16*1024**2: raise ValueError('Unbounded native metrics response')
                names = {name.split('{', 1)[0] for name in prometheus(raw.decode())}
                required = ({'brewfs_fuse_op_total', 'brewfs_mount_info', 'brewfs_writeback_pending_upload_bytes'}
                    if a.product=='brewfs' else {'juicefs_uptime', 'juicefs_fuse_ops_total',
                    'juicefs_blockcache_hits', 'juicefs_blockcache_miss', 'juicefs_staging_blocks', 'juicefs_staging_block_bytes'})
                if not required.issubset(names): raise ValueError('Required native client metrics missing')
                with (a.asset/'native-endpoint.prom').open('xb') as stream: stream.write(raw)
            stop('stop-'+str(index+1), a.asset/('prep-exit-'+str(index+1).zfill(2)+'.json'))
        write_new(a.out/'result.json', dict(passed=True, pool=a.pool, cell_id=selected['id'],
            asset=ref(a.asset/'asset.json'), input_checks=proofs, bytes_per_geometry=BYTES,
            base_debt=ref(a.asset/'base-debt/result.json'), native_metrics=ref(a.asset/'native-endpoint.prom'),
            normal_exits=[ref(a.asset/('prep-exit-'+str(i).zfill(2)+'.json')) for i in (1, 2)],
            scope='Two complete CRC bases and fresh-cache cold reads; preparation only, no formal performance sample'))
        print(json.dumps(dict(passed=True, asset=str(a.asset), out=str(a.out))))
    except Exception as error:
        write_new(a.out/'failed.json', dict(passed=False, error_type=type(error).__name__, error=str(error),
            active_supervisor=str(active) if active else None, stop_already_attempted=stopping, automatic_retry=False))
        if active and not stopping and (active/'ready.json').is_file():
            try: stop('failure-normal-stop', a.out/'failure-normal-stop.json')
            except Exception as stop_error:
                write_new(a.out/'failure-stop-error.json', dict(error_type=type(stop_error).__name__, error=str(stop_error)))
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--product', choices=['brewfs', 'juicefs'], required=True)
    p.add_argument('--pool', required=True)
    for name in ('asset', 'plan', 'inventory', 'node-tool', 'filesystem-binary', 'out'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--node-tool-sha256', required=True)
    p.add_argument('--filesystem-binary-sha256', required=True)
    p.add_argument('--database', type=int, required=True)
    p.add_argument('--attempt', required=True)
    prepare(p.parse_args())


if __name__=='__main__': main()
