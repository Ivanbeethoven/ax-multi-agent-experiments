#!/usr/bin/env python3
"""Retain the two A bundles the original watcher never started."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys

from bench.runtime.common import digest, load, pinned, write_new


def ref(path):
    return dict(path=str(path), sha256=digest(path))


def main(a):
    original = dict(path=str(a.request), sha256=a.request_sha256)
    s = load(pinned(original))
    if s.get('schema') != 'a-retain-original-calibrations-v1' or s.get('reviewed') is not True:
        raise ValueError('Expected the original reviewed archive request')
    tools = runpy.run_path(str(pinned(dict(path=str(a.request.with_name('watch-a-retention.py')),
        sha256=a.watch_tool_sha256))))
    old = a.request.with_name('run')
    first = s['c1_read']
    old_error = (old/'c1-mixed/stderr').read_text()
    packed = load(pinned(dict(path=str(a.packed_release),sha256=a.packed_release_sha256)))
    failure = pinned(packed['pipeline_failure'])
    if (packed.get('passed') is not True or packed.get('pool') != 'pool-a' or
            packed.get('formal_packed_sample_accepted') is not False or
            packed.get('ready_for_next_workload') is not True or packed.get('no_active_pool_lease') is not True or
            packed.get('agent_slot_empty') is not True or packed.get('all_six_calibration_trials_clean') is not True or
            'Unknown cell in original started invocation: '+str(failure.with_name('started.json')) not in old_error):
        raise ValueError('Require the exact historical Packed closure that stopped retention')
    original_fail = load(old/'failed.json')
    if (original_fail['passed'] is not False or load(old/'c1-read/exit.json')['exit_code'] != 0 or
            load(old/'c1-mixed/exit.json')['exit_code'] == 0 or (old/'c3').exists()):
        raise ValueError('Only one completed original archive and two untouched outputs are eligible')
    if any(Path(s[k]['out']).exists() for k in ('c1_mixed','c3')):
        raise ValueError('A remaining archive destination already exists')
    systemd = subprocess.check_output(['systemctl','show',packed['unit'],'-p','MainPID','-p','SubState'],text=True)
    if dict(row.split('=',1) for row in systemd.splitlines()) not in (
            dict(MainPID='0',SubState='dead'),dict(MainPID='0',SubState='exited'),dict(MainPID='0',SubState='failed')):
        raise ValueError('Historical Packed coordinator is still live')
    def bundle(path):
        check = Path(str(path)+'-archive-check.json')
        c = load(check)
        if c.get('passed') is not True:
            raise ValueError('Bundle failed')
        pinned(dict(path=c['archive'],sha256=c['sha256']))
        return ref(check)
    records = dict(c1_read=bundle(first['out']))
    out = a.out
    out.mkdir(parents=True,exist_ok=False)
    tools['wait'](s,out)
    write_new(out/'started.json',dict(at=tools['now'](),original=original,original_failure=ref(old/'failed.json'),
        packed_release=ref(a.packed_release),reused=records['c1_read'],automatic_retry=False))
    try:
        for key in ('c1_mixed','c3'):
            row = s[key]
            closed = tools['bind_completed'](Path(s['control']),row['request'],row['cell'],row['completed'])
            request = row['request']
            directory = out/key
            directory.mkdir()
            tool = pinned(dict(path=str(a.retainer),sha256=a.retainer_sha256))
            argv = [sys.executable,str(tool),'--request',request['path'],'--request-sha256',request['sha256'],
                '--completed-sha256',closed['sha256'],'--unit',row.get('unit',s['unit']),'--out',row['out']]
            write_new(directory/'intent.json',dict(argv=argv,automatic_retry=False))
            with (directory/'stdout').open('xb') as stdout, (directory/'stderr').open('xb') as stderr:
                result = subprocess.run(argv,stdout=stdout,stderr=stderr)
            write_new(directory/'exit.json',dict(exit_code=result.returncode))
            result.check_returncode()
            records[key] = bundle(row['out'])
        write_new(out/'result.json',dict(at=tools['now'](),passed=True,archives=records,
            original=original,original_failure=ref(old/'failed.json'),automatic_retry=False,next_cell=False))
    except Exception as exc:
        write_new(out/'failed.json',dict(at=tools['now'](),passed=False,error=str(exc),automatic_retry=False))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('request','retainer','packed-release','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('request','retainer','packed-release','watch-tool'):
        parser.add_argument('--'+name+'-sha256',required=True)
    main(parser.parse_args())
