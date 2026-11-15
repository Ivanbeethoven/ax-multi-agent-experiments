#!/usr/bin/env python3
"""Prepare or check ONE readonly fio step; an operator runs fio between steps.

The CLI never starts fio, advances phases, mounts, or creates cloud resources.
"""
import argparse
import hashlib
import json
from pathlib import Path

def file_inventory(files):
    if not files or len(set(map(str, files))) != len(files):
        raise ValueError('nonempty unique explicit files required')
    result = []
    for path in files:
        path = path.resolve(strict=True)
        if not path.is_file() or any(c in str(path) for c in '\n\r:'):
            raise ValueError('regular fio-safe path required')
        if path.stat().st_size < 4096 or path.stat().st_size % 4096:
            raise ValueError('prefilled length must be positive and 4 KiB aligned')
        result.append({'path': str(path), 'bytes': path.stat().st_size})
    if len({x['path'] for x in result}) != len(result):
        raise ValueError('files resolve to duplicate paths')
    return result

def job_text(inventory, mode, seconds=None):
    lines = ['[global]', 'ioengine=libaio', 'direct=1', 'thread=1', 'numjobs=1',
             'iodepth=32', 'bs=4k', 'readonly=1', 'allow_file_create=0',
             'randrepeat=1', 'randseed=20261029', 'rw='+mode]
    if seconds is not None:
        lines += ['time_based=1', 'runtime='+str(seconds)]
    for i, item in enumerate(inventory):
        lines += [f'[file-{i}]', 'filename='+item['path'], 'size='+str(item['bytes'])]
    return '\n'.join(lines)+'\n'

def qualify_result(value, inventory, full_coverage=False):
    jobs = value.get('jobs', [])
    if len(jobs) != len(inventory):
        raise ValueError('missing fio jobs')
    by_name = {j['jobname']: j for j in jobs}
    if set(by_name) != {f'file-{i}' for i in range(len(inventory))}:
        raise ValueError('fio job identity mismatch')
    bw = 0
    for i, item in enumerate(inventory):
        job = by_name[f'file-{i}']; read = job['read']
        if job.get('error', -1) != 0 or read.get('io_bytes', 0) <= 0 or read.get('bw_bytes', 0) <= 0:
            raise ValueError('fio failed or performed zero read work')
        if job.get('write', {}).get('io_bytes', 0) != 0:
            raise ValueError('readonly pass wrote data')
        if full_coverage and read['io_bytes'] != item['bytes']:
            raise ValueError('full warm coverage incomplete')
        bw += read['bw_bytes']
    return bw

def prepare_step(files, out, phase, seconds=30):
    if phase not in ('coverage', 'warm', 'measure') or seconds <= 0:
        raise ValueError('invalid phase or duration')
    inventory = file_inventory(files)
    config = job_text(inventory, 'read' if phase == 'coverage' else 'randread',
                      None if phase == 'coverage' else seconds)
    out = Path(out).resolve()
    if any(out == Path(x['path']) or out in Path(x['path']).parents for x in inventory):
        raise ValueError('step output must not contain workload files')
    out.mkdir(parents=True, exist_ok=False)
    (out/'job.fio').write_bytes(config.encode())
    receipt = {'phase':phase, 'files':inventory, 'seconds':None if phase=='coverage' else seconds,
               'job_sha256':hashlib.sha256(config.encode()).hexdigest(),
               'fio_argv':['fio', '--readonly', '--output-format=json',
                           '--output='+str(out/'fio.json'), str(out/'job.fio')],
               'status':'prepared_not_executed', 'automatic_next_step':False}
    (out/'step.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


def check_step(directory):
    directory = Path(directory).resolve(strict=True)
    receipt = json.loads((directory/'step.json').read_text())
    if hashlib.sha256((directory/'job.fio').read_bytes()).hexdigest() != receipt['job_sha256']:
        raise ValueError('fio configuration changed')
    if file_inventory([Path(x['path']) for x in receipt['files']]) != receipt['files']:
        raise ValueError('working set changed')
    raw = (directory/'fio.json').read_bytes()
    value = json.loads(raw)
    bandwidth = qualify_result(value, receipt['files'], receipt['phase']=='coverage')
    result = {'phase':receipt['phase'], 'passed':True, 'bw_bytes_s':bandwidth,
              'raw_sha256':hashlib.sha256(raw).hexdigest(),
              'scope':'one fio step only; cache convergence, content and resources require separate review',
              'automatic_next_step':False}
    with (directory/'checked.json').open('x') as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action', required=True)
    prepare=sub.add_parser('prepare', help='write one job and stop; never invoke fio')
    prepare.add_argument('--files', type=Path, nargs='+', required=True)
    prepare.add_argument('--output', type=Path, required=True)
    prepare.add_argument('--phase', choices=['coverage','warm','measure'], required=True)
    prepare.add_argument('--seconds', type=int, default=30)
    check=sub.add_parser('check', help='check one completed job and stop')
    check.add_argument('--step', type=Path, required=True)
    args=p.parse_args()
    result=(prepare_step(args.files,args.output,args.phase,args.seconds) if args.action=='prepare'
            else check_step(args.step))
    print(json.dumps(result))
