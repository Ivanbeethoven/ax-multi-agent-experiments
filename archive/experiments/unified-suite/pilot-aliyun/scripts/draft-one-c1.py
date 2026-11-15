#!/usr/bin/env python3
"""Write one exact flat C1/C2/C3 request and six fixed trials; never executes it."""
import argparse
import json
import re
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,write_new
from bench.runtime import pilot_context as pilot_runtime
from bench.runtime.pilot_pools import pool_of,cross_pool_proof


def ref(p):return dict(path=str(p),sha256=digest(p))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('control','qualification','calibrator','checker','admitter','runner','out'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('cell','node-tool','node-tool-sha256','observer-tool-name','observer-tool-sha256','collector-name','collector-out'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--latest-check',type=Path)
    p.add_argument('--cross-pool-qualification',type=Path)
    pilot_runtime.arguments(p)
    p.add_argument('--attempt-suffix',default='',help='New complete calibration set after an explicitly revised method')
    p.add_argument('--run-directory',type=Path,help='New one-cell attempt directory; original attempts stay intact')
    a=p.parse_args();plan=load(a.control/'suite/plan.json');cell=next(c for c in plan['cells'] if c['id']==a.cell)
    runtime_ref=pilot_runtime.reference(a);runtime=pilot_runtime.select(a.control,runtime_ref)
    pool=pool_of(cell)
    if 'cross-pool-hardware-equivalence' in cell['required_gates']:
        if not a.cross_pool_qualification:raise ValueError('Explicit cross-pool equivalence evidence required')
        cross_pool_proof(a.cross_pool_qualification,plan,runtime/'fleet.json')
    if a.attempt_suffix and not re.fullmatch('[a-z0-9]{1,6}',a.attempt_suffix):raise ValueError('Short attempt suffix required')
    if cell['workload'] not in ('fio','trace','smallfiles') or cell['arm_id'] not in ('native','juicefs'):raise ValueError('Flat C1/C2/C3 native or JuiceFS only')
    if cell['workload']!='fio' and not a.latest_check:raise ValueError('Explicit latest-branch receipt required for C2/C3')
    q=a.qualification/cell['arm_id']
    if load(q/'workload.json')['params']!=cell['params']:raise ValueError('Exact workload contract required')
    case={'trace':'tr','smallfiles':'sf'}.get(cell['workload']) or {'fio-read':'rd','fio-write':'wr','fio-randread':'rr','fio-randwrite':'rw','fio-randrw':'mx'}[cell['case']]
    tag=('n-' if cell['arm_id']=='native' else 'j-')+case
    if len(plan['config']['clusters'])>1:tag=pool+'-'+tag
    if a.attempt_suffix:tag+='-'+a.attempt_suffix
    prefix='cal-'+tag;name1='ax-pilot-'+prefix+'-on1-collector';name3='ax-pilot-'+prefix+'-on3-collector'
    out1='/srv/ax-objects-0/q/'+prefix+'-on1-collector'
    trials=[]
    for suffix,mode,collector,collector_out,new in [
        ('off1','off',a.collector_name,a.collector_out,None),
        ('on1','on',a.collector_name,a.collector_out,name1),
        ('on2','on',name1,out1,None),('off2','off',name1,out1,None),
        ('off3','off',name1,out1,None),('on3','on',name1,out1,name3)]:
        attempt=prefix+'-'+suffix
        trials.append(dict(attempt=attempt,mode=mode,collector_name=collector,collector_out=collector_out,
                           new_collector_name=new,out=str(a.control/'calibration'/attempt)))
    request=dict(reviewed=True,cell_id=cell['id'],control=str(a.control),qualification=str(a.qualification),
        out=str(a.run_directory or a.control/'one-cell-runs'/cell['id']),node_tool=dict(path=a.node_tool,sha256=a.node_tool_sha256),
        observer_tool=dict(name=a.observer_tool_name,sha256=a.observer_tool_sha256),
        tools={name:ref(path) for name,path in [('calibrate',a.calibrator),('check',a.checker),('admit',a.admitter),('run',a.runner)]},
        trials=trials,final_collector_name=name3,
        final_collector_out='/srv/ax-objects-0/q/'+prefix+'-on3-collector',
        review=dict(reviewer='pilot-operator',note='One explicit frozen flat cell, three predeclared off/on pairs in order off-on/on-off/off-on; no trial reuse, next cell or automatic retry.'))
    if a.latest_check:request['latest_check']=ref(a.latest_check)
    if a.cross_pool_qualification:request['cross_pool_qualification']=ref(a.cross_pool_qualification)
    if len(plan['config']['clusters'])>1:request.update(pool=pool,owner='pilot-operator-'+pool)
    if runtime_ref:request['runtime_control']=runtime_ref
    a.out.parent.mkdir(parents=True,exist_ok=True);write_new(a.out,request)
    print(json.dumps(dict(cell_id=cell['id'],request=ref(a.out),final_collector_name=name3,final_collector_out=request['final_collector_out'])))


if __name__=='__main__':main()
