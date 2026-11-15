#!/usr/bin/env python3
"""Read controller state and one running request without claiming or changing a cell."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess


def now():return datetime.now(timezone.utc).isoformat()
def load(path):return json.loads(Path(path).read_bytes())
def ref(path):return dict(path=str(path),sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
def verified(pin):
    if ref(Path(pin['path']))!=pin:raise ValueError('Changed evidence: '+pin['path'])
    return load(pin['path'])


def controller(path):
    database=path/'state.sqlite'
    with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        plan=json.loads(db.execute('select plan from campaign').fetchone()[0]);declared={c['id']:c for c in plan['cells']}
        rows=[dict(r) for r in db.execute('select id,status,result from cells order by id')]
        leases=[dict(r) for r in db.execute('select cell_id,pool,owner from cell_leases order by pool')]
        events=[dict(r) for r in db.execute('select sequence,utc,kind,payload from events order by sequence desc limit 5')]
        for event in events:event['payload']=json.loads(event['payload'])
    accepted=0
    for row in rows:
        cell=declared[row['id']];row.update(pool=cell.get('pool'),workload=cell['workload'],arm=cell['arm_id'])
        if row['result'] is None:continue
        row['result']=json.loads(row['result']);closed=verified(row['result'])
        row['closure_outcome']=closed['outcome'];row['closure_sha_verified']=True
        if row['status']=='complete':
            sample=verified(closed['sample'])
            accepted_sample=(closed['outcome']=='complete' and sample.get('uninterrupted') is True and sample.get('recovered') is False)
            row['sample']=closed['sample'];row['uninterrupted_accepted_sample']=accepted_sample
            if not accepted_sample:raise ValueError('Complete status lacks uninterrupted sample')
            accepted+=1
    counts=dict(Counter(row['status'] for row in rows))
    return dict(control=str(path),observed_at=now(),database_read_mode='ro, consistent read transaction',
        plan_sha256=plan['plan_sha256'],counts=counts,accepted_formal_samples=accepted,active_cell_leases=leases,
        active_cell_lease_count=len(leases),cells=rows,latest_events=events)


def request_progress(path,unit):
    request=load(path);out=Path(request['out']);trials=[]
    for number,trial in enumerate(request['trials'],1):
        entry=dict(number=number,attempt=trial['attempt'],mode=trial['mode'],directory=trial['out'])
        directory=Path(trial['out']);entry['exists']=directory.exists()
        for name in ('started.json','finished.json','result.json','failed.json'):
            source=directory/name
            if source.exists():entry[name]=dict(evidence=ref(source),value=load(source))
        stdout=out/f'trial-{number}'/'stdout'
        trial_exit=out/f'trial-{number}'/'exit.json'
        if trial_exit.exists():entry['trial_exit']=dict(evidence=ref(trial_exit),value=load(trial_exit))
        if stdout.exists() and stdout.stat().st_size:
            entry['stdout']=dict(evidence=ref(stdout),mtime_utc=datetime.fromtimestamp(stdout.stat().st_mtime,timezone.utc).isoformat(),tail=stdout.read_text()[-1200:])
        commands=sorted((directory/'commands').glob('*/intent.json'))
        if commands:
            latest=commands[-1];entry['latest_command']=dict(evidence=ref(latest),value=load(latest))
            exit_path=latest.parent/'exit.json'
            entry['latest_command']['exit']=load(exit_path) if exit_path.exists() else None
        trials.append(entry)
    show=subprocess.run(['systemctl','show',unit,'--property=ActiveState,SubState,MainPID,ExecMainStatus,ActiveEnterTimestamp,CPUUsageNSec,MemoryCurrent'],capture_output=True,text=True)
    journal=subprocess.run(['journalctl','-u',unit,'-n','5','--no-pager','-o','short-iso'],capture_output=True,text=True)
    return dict(observed_at=now(),request=ref(path),cell_id=request['cell_id'],pool=request.get('pool'),unit=unit,
        systemd=dict(exit_code=show.returncode,stdout=show.stdout,stderr=show.stderr),
        journal=dict(exit_code=journal.returncode,stdout=journal.stdout,stderr=journal.stderr),trials=trials,
        scope='Trials are observation-overhead calibration; not formal accepted cells.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control',action='append',type=Path,required=True)
    p.add_argument('--request',type=Path)
    p.add_argument('--unit')
    a=p.parse_args()
    if bool(a.request)!=bool(a.unit):p.error('request and unit are a pair')
    result=dict(schema='pilot-readonly-progress-v1',observed_at=now(),controllers=[controller(path) for path in a.control])
    if a.request:result['running_request']=request_progress(a.request,a.unit)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
