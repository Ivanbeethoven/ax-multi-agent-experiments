#!/usr/bin/env python3
"""Run six predeclared observer trials and one explicit flat C1/C2 cell.

Never selects another cell, retries a trial, releases cloud resources or repairs
a failure. Each underlying formal stage is recorded by the existing SQLite runner.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench import suite_state


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    a=p.parse_args()
    s=load(pinned(dict(path=str(a.request),sha256=a.sha256)))
    if s.get('reviewed') is not True:raise ValueError('Reviewed one-cell request required')
    control=Path(s['control']);plan=load(control/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['id']==s['cell_id'])
    if cell['workload'] not in ('fio','trace') or cell['arm_id'] not in ('native','juicefs'):raise ValueError('Flat C1/C2 only')
    latest=pinned(s['latest_check']) if s.get('latest_check') else None
    if cell['workload']=='trace' and not latest:raise ValueError('Explicit latest-branch receipt required for C2')
    product=cell['arm']['product'];q=Path(s['qualification'])
    contract=load(q/cell['arm_id']/'workload.json')
    if contract['params']!=cell['params']:raise ValueError('Exact workload qualification required')
    for tool in ('calibrate','check','admit','run'):pinned(s['tools'][tool])
    trials=s['trials']
    if len(trials)!=6 or [t['mode'] for t in trials]!=['off','on','on','off','off','on']:
        raise ValueError('Three predeclared counterbalanced pairs required')
    if len({t['attempt'] for t in trials})!=6 or len({t['out'] for t in trials})!=6:
        raise ValueError('Unique original trials required')
    with suite_state.transaction(control/'state.sqlite') as db:
        if db.execute('SELECT COUNT(*) FROM cell_leases').fetchone()[0]:raise ValueError('Another cell remains reserved')
        row=db.execute('SELECT status FROM cells WHERE id=?',(cell['id'],)).fetchone()
        if not row or row[0]!='pending':raise ValueError('Only an unstarted explicit cell may run')
    subprocess.run(['ssh','-F',str(control/'ssh.conf'),'pool-a-client',
        "test -z \"$(cat /sys/fs/cgroup/ax-slot-1/cgroup.procs)\""],check=True)
    out=Path(s['out']);out.mkdir(parents=True,exist_ok=False)
    write_new(out/'started.json',dict(cell_id=cell['id'],at=datetime.now(timezone.utc).isoformat(),
              request=dict(path=str(a.request),sha256=a.sha256),automatic_retry=False,next_cell=False))
    def run(name,argv):
        directory=out/name;directory.mkdir()
        write_new(directory/'intent.json',dict(argv=list(map(str,argv)),automatic_retry=False))
        with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
            code=subprocess.run(list(map(str,argv)),stdout=stdout,stderr=stderr).returncode
        write_new(directory/'exit.json',dict(exit_code=code))
        if code:raise RuntimeError('Stopped; inspect '+str(directory))
    try:
        for i,t in enumerate(trials):
            args=['python3',pinned(s['tools']['calibrate']),'--control',control,'--qualification',q,
                '--case',cell['case'],'--product',product,'--mode',t['mode'],'--attempt',t['attempt'],
                '--collector-name',t['collector_name'],'--collector-out',t['collector_out'],
                '--observer-tool-name',s['observer_tool']['name'],'--observer-tool-sha256',s['observer_tool']['sha256'],
                '--node-tool',s['node_tool']['path'],'--node-tool-sha256',s['node_tool']['sha256'],'--out',t['out']]
            if t.get('new_collector_name'):args+=['--new-collector-name',t['new_collector_name']]
            run('trial-'+str(i+1),args)
        pairs=[dict(off=trials[i]['out'],on=trials[j]['out']) for i,j in [(0,1),(3,2),(4,5)]]
        write_new(out/'pairs.json',pairs)
        run('check',['python3',pinned(s['tools']['check']),'--pairs',out/'pairs.json',
                    '--metric-contract',q/cell['arm_id']/'metrics.json','--workload-contract',q/cell['arm_id']/'workload.json',
                    '--out',out/'overhead'])
        admit=['python3',pinned(s['tools']['admit']),'--control',control,'--qualification',q,
                    '--overhead',out/'overhead/result.json','--cell',cell['id'],
                    '--node-tool',s['node_tool']['path'],'--node-tool-sha256',s['node_tool']['sha256'],
                    '--observer-tool-name',s['observer_tool']['name'],'--observer-tool-sha256',s['observer_tool']['sha256'],
                    '--collector-name',s['final_collector_name'],'--out',control/'cells'/cell['id']]
        if latest:admit+=['--latest-check',latest]
        run('admit',admit)
        run('formal',['python3',pinned(s['tools']['run']),'--context',control/'cells'/cell['id']/'context.json'])
        closing=control/'cells'/(cell['id']+'.close.json')
        write_new(out/'result.json',dict(passed=True,cell_id=cell['id'],closing=dict(path=str(closing),sha256=digest(closing)),
                  final_collector_name=s['final_collector_name'],automatic_retry=False,next_cell=False))
    except Exception as error:
        write_new(out/'failed.json',dict(passed=False,cell_id=cell['id'],error=str(error),automatic_retry=False,next_cell=False))
        raise


if __name__=='__main__':main()
