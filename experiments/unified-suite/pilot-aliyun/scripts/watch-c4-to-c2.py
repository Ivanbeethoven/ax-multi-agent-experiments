#!/usr/bin/env python3
"""On the coordinator only, accept one actual C4 release and start one C2 unit."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time


def read(path, expected=None):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ValueError('Expected an absolute ordinary evidence file: '+str(path))
    raw = path.read_bytes()
    pin = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())
    if expected is not None and pin != expected:
        raise ValueError('Pinned coordinator evidence changed: '+str(path))
    return json.loads(raw), pin


def pinned(pin):
    return read(pin['path'], pin)[0]


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def validate(s, handoff, recipe):
    pool, cell = s['pool'], s['c4_cell']
    if (handoff.get('schema') != 'c4-pool-handoff-v1' or handoff.get('passed') is not True or
            handoff.get('safe_for_handoff') is not True or handoff.get('pool') != pool or
            handoff.get('cell_id') != cell or handoff.get('request') != s['c4_original_request']):
        raise ValueError('No actual accepted original C4 release for this pool')
    if s.get('next_owner') and handoff.get('next_owner') != s['next_owner']:
        raise ValueError('Release addressed to another owner')
    original = pinned(handoff['request'])
    if original['cell_id'] != cell or original['control'] != s['fork_control']:
        raise ValueError('Original C4 batch belongs to another cell/control')
    if handoff.get('continuation'):
        pinned(handoff['continuation'])
    argv = recipe['commands']['draft_six_and_formal']
    for field in ('collector_name', 'collector_out'):
        flag = '--'+field.replace('_','-')
        if handoff.get(field) != argv[argv.index(flag)+1]:
            raise ValueError('Actual C4 collector differs from frozen C2 recipe: '+field)
    closing = pinned(handoff['closing'])
    formal = pinned(handoff['formal_closing'])
    acceptance = pinned(handoff['acceptance'])
    if (closing.get('passed') is not True or closing.get('pool') != pool or closing.get('cell_id') != cell or
            closing.get('closing') != handoff['formal_closing'] or formal.get('outcome') != 'complete' or
            formal.get('cell_id') != cell or formal.get('sample') != closing['sample'] or
            acceptance.get('accepted') is not True or acceptance.get('sample') != closing['sample']):
        raise ValueError('Formal C4 completion/acceptance chain differs')
    sample = pinned(closing['sample'])
    if sample.get('cell_id') != cell or sample.get('uninterrupted') is not True or sample.get('recovered') is not False:
        raise ValueError('Formal sample was not accepted as an original uninterrupted sample')
    unit = handoff['unit']
    start, stop, systemd = (unit[k] for k in ('start','stop','systemd'))
    if (systemd.get('MainPID','0') != '0' or systemd.get('ExecMainStatus','0') != '0' or
            (systemd.get('ActiveState'),systemd.get('SubState')) not in (('active','exited'),('inactive','dead')) or
            start['UNIT'] != stop['UNIT'] or start['INVOCATION_ID'] != stop['INVOCATION_ID'] or
            start['_BOOT_ID'] != stop['_BOOT_ID'] or
            stop['MESSAGE'] != stop['UNIT']+': Deactivated successfully.' or
            int(start['__MONOTONIC_TIMESTAMP']) >= int(stop['__MONOTONIC_TIMESTAMP'])):
        raise ValueError('Original unit normal exit proof missing')
    boundaries = handoff['boundaries']
    if sorted(row['role'] for row in boundaries) != ['client','metadata','objects']:
        raise ValueError('Three original B boundary proofs required')
    for row in boundaries:
        value = pinned(row['source'])
        if value.get('passed') is not True or value.get('live') or value.get('mounted') or value.get('slot'):
            raise ValueError('Original role not completely released: '+row['role'])
        if row['role'] == 'objects':
            collector = value['collector']
            if (collector.get('running') is not True or collector['name'] != handoff['collector_name'] or
                    collector['out'] != handoff['collector_out']):
                raise ValueError('Original live collector proof differs')
    db_path = Path(s['fork_control'])/'state.sqlite'
    with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as db:
        row = db.execute('SELECT status,result FROM cells WHERE id=?',(cell,)).fetchone()
        busy = db.execute('SELECT 1 FROM cell_leases WHERE pool=? OR cell_id=?',(pool,cell)).fetchone()
        if not row or row[0] != 'complete' or json.loads(row[1]) != handoff['formal_closing'] or busy:
            raise ValueError('Original C4 SQLite cell must be closed and B unleased')
    return dict(closing=handoff['closing'],formal_closing=handoff['formal_closing'],
                acceptance=handoff['acceptance'],sample=closing['sample'],c4_unit=unit['name'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--out',type=Path,required=True)
    a = p.parse_args()
    s = pinned(dict(path=str(a.request),sha256=a.sha256))
    if (s.get('schema') != 'c4-to-c2-coordinator-watch-v1' or s.get('reviewed') is not True or
            not re.fullmatch(r'ax-pilot-[a-z0-9-]+',s['runner_unit']) or s['pool'] != 'pool-b'):
        raise ValueError('Explicit one-shot B continuation request required')
    recipe = pinned(s['recipe'])
    if (recipe.get('schema') != 'c2-juicefs-coordinator-draft-v1' or recipe.get('pool') != s['pool'] or
            recipe.get('cell_id') != s['c2_cell'] or recipe.get('execution_enabled') is not False):
        raise ValueError('Original frozen B C2 recipe required')
    original = pinned(s['c4_original_request'])
    if original['cell_id'] != s['c4_cell'] or original['control'] != s['fork_control']:
        raise ValueError('Unexpected original C4 batch')
    # The executable is Python, not JSON; hash without loading its content.
    runner = Path(s['runner']['path'])
    def validate_runner():
        if (not runner.is_absolute() or runner.is_symlink() or
                hashlib.sha256(runner.read_bytes()).hexdigest()!=s['runner']['sha256']):
            raise ValueError('Frozen C2 continuation changed')
    validate_runner()
    handoff_path = Path(s['handoff'])
    if (not handoff_path.is_absolute() or handoff_path.parent.parent!=Path(s['fork_control']) or
            handoff_path.name!='result.json'):
        raise ValueError('Exact one-owner C4 handoff result path required')
    if not a.out.is_absolute():
        raise ValueError('New absolute watcher evidence directory required')
    a.out.mkdir(parents=True,exist_ok=False)
    write(a.out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),handoff=str(handoff_path),
        request=dict(path=str(a.request),sha256=a.sha256),poll_seconds=15,deadline_hours=6,
        workers_contacted_while_waiting=False,automatic_retry=False))
    deadline = time.monotonic()+6*3600
    try:
        while time.monotonic() < deadline:
            if handoff_path.is_symlink():
                raise ValueError('Refuse a symlinked handoff')
            if handoff_path.is_file():
                # The producer creates its JSON with O_EXCL. Wait briefly for a
                # stable observation so an incomplete open file is not treated
                # as a failed completed receipt.
                f = handoff_path.stat()
                time.sleep(1)
                g = handoff_path.stat()
                if (f.st_ino,f.st_size,f.st_mtime_ns)==(g.st_ino,g.st_size,g.st_mtime_ns):
                    handoff, pin = read(handoff_path)
                    break
            time.sleep(15)
        else:
            raise TimeoutError('No C4 handoff arrived within six hours; C2 not started')
        proof = validate(s,handoff,recipe)
        read(handoff_path,pin)
        pinned(s['recipe'])
        validate_runner()
        if Path(s['runner_output']).exists():
            raise ValueError('C2 output already exists; no second attempt')
        show = subprocess.check_output(['systemctl','show',s['runner_unit'],'-pLoadState','-pMainPID'],text=True)
        if dict(line.split('=',1) for line in show.splitlines()) != dict(LoadState='not-found',MainPID='0'):
            raise ValueError('C2 unit already exists; no second attempt')
        argv = ['systemd-run','--unit',s['runner_unit'],'--collect','--property=CPUAccounting=yes',
            '--property=MemoryAccounting=yes','--property=IOAccounting=yes','/usr/bin/python3',
            str(runner),'--runtime-repo',s['runtime_repo'],'--recipe',s['recipe']['path'],
            '--recipe-sha256',s['recipe']['sha256'],'--handoff',pin['path'],'--handoff-sha256',pin['sha256'],
            '--out',s['runner_output']]
        write(a.out/'launch-intent.json',dict(at=datetime.now(timezone.utc).isoformat(),argv=argv,
            handoff=pin,c4_completion=proof,automatic_retry=False))
        result = subprocess.run(argv,capture_output=True,text=True)
        write(a.out/'launch-exit.json',dict(exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr))
        result.check_returncode()
        write(a.out/'result.json',dict(passed=True,launched=True,unit=s['runner_unit'],handoff=pin,
            c4_completion=proof,automatic_retry=False,watcher_owns_runner_process=False,
            scope='Only launch accepted; C2 result and formal closure remain independent.'))
    except Exception as error:
        write(a.out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False,
            launch_intent_exists=(a.out/'launch-intent.json').exists()))
        raise


if __name__ == '__main__':
    main()
