import fcntl
import json
import sqlite3
import time
from contextlib import ExitStack
from datetime import datetime,timezone
from pathlib import Path

root=Path('/srv/ax-pilot-control')
out=root/'preparation/a-c4-on1-publication-01'
recovery=root/'preparation/a-c4-on1-historical-archive-01/recovered-trial'
off=root/'preparation/a-c4-off1-publication-guard-01/held.json'

def stamp():
    return datetime.now(timezone.utc).isoformat()

def write(name, value):
    with (out/name).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2)
        stream.write('\n')

def states():
    return sorted(root.glob('*/state.sqlite'))

def leases(files):
    for f in files:
        with sqlite3.connect('file:'+str(f)+'?mode=ro',uri=True) as db:
            if db.execute('select count(*) from cell_leases').fetchone() != (0,):
                raise ValueError('Active lease: '+str(f))

old=json.loads(off.read_bytes())
finished=json.loads((recovery/'finished.json').read_bytes())
assert finished['passed'] is True and finished['purpose']=='observer-calibration-on'
expected={Path(x['path']) for x in old['locks']}
controls=states()
dirs={(f.parent/'runner-locks').resolve(strict=True) for f in controls}
locks=sorted({p for d in dirs for p in d.glob('*.lock')})
assert expected <= set(locks) and len(controls)>=5
with ExitStack() as stack:
    for p in locks:
        fd=stack.enter_context(p.open('rb'))
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    leases(controls)
    write('held.json',dict(schema='on1-historical-publication-guard-v1',at=stamp(),
        passed=True,control_count=len(controls),lock_count=len(locks),original_trial_unchanged=True,
        scope='Historical calibration archive only; no worker operation'))
    start=time.monotonic()
    while not (out/'release.json').exists() and time.monotonic()-start<1380:
        time.sleep(1)
    release=out/'release.json'
    assert controls==states()
    leases(controls)
    result=json.loads(release.read_bytes()) if release.exists() else {'publication':'operator-release-timeout'}
    write('result.json',dict(at=stamp(),lock_count=len(locks),leases=0,release=result,
        automatic_retry=False,passed=release.exists() and result.get('publication')=='verified'))
    if not release.exists():
        raise SystemExit(2)
