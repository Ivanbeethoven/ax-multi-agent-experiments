#!/usr/bin/env python3
"""After a specific successful A C3 invocation, retain closed original trials."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from bench.runtime.common import digest, load, pinned, write_new


def ref(path):
    return dict(path=str(path), sha256=digest(path))


def now():
    return datetime.now(timezone.utc).isoformat()


def wait(s, out):
    deadline = time.monotonic() + 7200
    while time.monotonic() < deadline:
        fields = ('InvocationID','MainPID','ActiveState','SubState','ExecMainCode','ExecMainStatus','Result')
        raw = subprocess.check_output(['systemctl','show',s['unit'],*['-p'+p for p in fields]],text=True)
        state = dict(row.split('=',1) for row in raw.splitlines())
        if state['InvocationID'] != s['invocation']:
            raise ValueError('Original A C3 invocation changed or vanished')
        if (state['ActiveState'],state['SubState']) == ('active','exited'):
            if (state['MainPID'],state['ExecMainCode'],state['ExecMainStatus'],state['Result']) != ('0','1','0','success'):
                raise ValueError('Original A C3 did not exit normally')
            write_new(out/'original-unit-exit.json',dict(at=now(),**state))
            return
        if state['ActiveState'] not in ('active','activating'):
            raise ValueError('Original A C3 did not finish successfully; no automatic retention')
        time.sleep(15)
    raise TimeoutError('No A C3 closure within two hours; workers untouched')


def bind_completed(control, request_pin, cell_id, path):
    request = load(pinned(request_pin))
    if request.get('reviewed') is not True or request['control'] != str(control) or request['cell_id'] != cell_id:
        raise ValueError('Expected original reviewed request in the canonical campaign')
    path = Path(path)
    result = load(path)
    close = load(pinned(result['closing']))
    started = load(Path(request['out'])/'started.json')
    if (path != Path(request['out'])/'result.json' or started['request'] != request_pin or
            result.get('passed') is not True or result['cell_id'] != cell_id or
            close['outcome'] != 'complete' or close['cell_id'] != cell_id):
        raise ValueError('Original formal invocation has not closed')
    with sqlite3.connect((control/'state.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT status,result FROM cells WHERE id=?',(cell_id,)).fetchone()
    if not row or row[0] != 'complete' or json.loads(row[1]) != result['closing']:
        raise ValueError('Original SQLite cell not closed with the same proof')
    return ref(path)


def main(a):
    request = dict(path=str(a.request),sha256=a.sha256)
    s = load(pinned(request))
    if s.get('schema') != 'a-retain-original-calibrations-v1' or s.get('reviewed') is not True:
        raise ValueError('Reviewed retention only request required')
    control = Path(s['control'])
    out = a.out
    if not out.is_absolute() or out.parent != a.request.parent:
        raise ValueError('Use a separate absolute result directory under the request')
    tools = {key:pinned(pin) for key,pin in s['tools'].items()}
    c3 = s['c3']
    out.mkdir(parents=True,exist_ok=False)
    write_new(out/'started.json',dict(at=now(),request=request,workers_contacted_while_waiting=False,automatic_retry=False))
    try:
        wait(s,out)
        fresh = bind_completed(control,c3['request'],c3['cell'],c3['completed'])
        c1 = s['c1_mixed']
        prior = bind_completed(control,c1['request'],c1['cell'],c1['completed'])
        reb = s['c1_read']
        if (Path(reb['control'])/'runner-locks').resolve() != (control/'runner-locks').resolve():
            raise ValueError('Calibration retainer must use the canonical locks')
        def archive(name, key, args, dest):
            work = out/name
            work.mkdir()
            argv = [sys.executable,str(pinned(s['tools'][key])),*map(str,args),'--out',dest]
            write_new(work/'intent.json',dict(at=now(),argv=argv,automatic_retry=False))
            with (work/'stdout').open('xb') as stdout, (work/'stderr').open('xb') as stderr:
                run = subprocess.run(argv,stdout=stdout,stderr=stderr)
            write_new(work/'exit.json',dict(exit_code=run.returncode))
            run.check_returncode()
            check = Path(dest).with_name(Path(dest).name+'-archive-check.json')
            value = load(check)
            if value.get('passed') is not True:
                raise ValueError('Retainer did not verify its actual archive')
            pinned(dict(path=value['archive'],sha256=value['sha256']))
            return ref(check)
        records = {}
        records['c1_read'] = archive('c1-read','rebound',
            ['--control',reb['control'],'--unit',reb['unit']],reb['out'])
        for name, row, result, unit in [('c1_mixed',c1,prior,c1['unit']),('c3',c3,fresh,s['unit'])]:
            original = row['request']
            records[name] = archive(name.replace('_','-'),'original',
                ['--request',original['path'],'--request-sha256',original['sha256'],
                 '--completed-sha256',result['sha256'],'--unit',unit],row['out'])
        write_new(out/'result.json',dict(at=now(),passed=True,request=request,closed_c3=fresh,
            archives=records,automatic_retry=False,next_cell=False))
    except Exception as exc:
        write_new(out/'failed.json',dict(at=now(),passed=False,error=str(exc),automatic_retry=False))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--out',type=Path,required=True)
    main(parser.parse_args())
