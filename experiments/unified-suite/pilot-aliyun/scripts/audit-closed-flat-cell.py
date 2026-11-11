#!/usr/bin/env python3
"""Independently verify a closed flat cell without rewriting its sealed evidence."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench import archive_check,experiment_step,suite_report
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.pilot_pools import alias,fleet_node
from bench.suite_state import predicates

def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def audit(control,cell_id,unit,out):
    plan=load(control/'suite/plan.json');cell=next(c for c in plan['cells'] if c['id']==cell_id);pool=cell['pool']
    result=load(control/'one-cell-runs'/cell_id/'result.json');closing=load(pinned(result['closing']))
    if result.get('passed') is not True or closing.get('outcome')!='complete':raise ValueError('Normally closed formal cell required')
    with sqlite3.connect('file:'+str(control/'state.sqlite')+'?mode=ro',uri=True) as db:
        status,encoded=db.execute('SELECT status,result FROM cells WHERE id=?',(cell_id,)).fetchone()
        if status!='complete' or json.loads(encoded)!=result['closing'] or db.execute('SELECT 1 FROM cell_leases WHERE cell_id=?',(cell_id,)).fetchone():raise ValueError('State not normally closed without a lease')
        q=load(pinned(json.loads(db.execute('SELECT receipt FROM qualifications WHERE cell_id=?',(cell_id,)).fetchone()[0])))
    for check in q['checks']:predicates(load(pinned(check['evidence'])),check['equals'])
    experiment_step.verify(closing['retention']['root'],closing['retention']['manifest']['path'],closing['retention']['manifest']['sha256'])
    archives=[archive_check.verify(a['path'],a['sha256']) for a in closing['archives']]
    if any(a['cell_id']!=cell_id for a in archives):raise ValueError('Archive cell identity differs')
    checked=suite_report.sample(closing['sample'],plan,cell)
    if checked.get('accepted') is not True:raise ValueError('Independent sample check rejected: '+repr(checked['reasons']))
    state=dict(line.split('=',1) for line in subprocess.check_output(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','SubState','-p','ExecMainStatus'],text=True).splitlines())
    if state['MainPID']!='0' or state['ExecMainStatus']!='0' or (state['ActiveState'],state['SubState']) not in (('active','exited'),('inactive','dead')):raise ValueError('Original successful unit remains live')
    context=load(control/'cells'/cell_id/'context.json')
    if context['control']!=str(control) or context['cell_id']!=cell_id:raise ValueError('Original context identity differs')
    namespace=hashlib.sha256((str(control.resolve())+'\0'+digest(control/'suite/plan.json')).encode()).hexdigest()[:12]
    tag={'fio':'c1-','trace':'c2-','smallfiles':'c3-'}[cell['workload']]+namespace+'-'+cell_id
    fleet=load(control/'fleet.json');boundaries=[]
    out.mkdir(parents=True,exist_ok=False)
    for role in ('client','metadata','objects'):
        node=fleet_node(fleet,pool,role);root={'client':'/srv/ax-client','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}[role]
        code='''import json,pathlib,sys
sys.path.insert(0,CODE)
from bench.runtime.common import alive,digest,mounts
p=pathlib.Path(ROOT)/'q'/(TAG+'-closure')/'result.json';v=json.loads(p.read_text())
identities=v['processes'];owned=[m['point'] for m in v['mount_points']] if 'mount_points' in v else v['owned_mounts']
if not identities or (ROLE=='client' and not owned):raise ValueError('Actual original process and client mount identities required')
current=dict(original_identities=identities,any_original_process_alive=any(alive(i) for i in identities),original_owned_mounts=owned,current_owned_mounts=[m for point in owned for m in mounts(point)],source=dict(path=str(p),sha256=digest(p)))
if ROLE=='client':current['slot_procs']=(pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip())
current['passed']=not current['any_original_process_alive'] and not current['current_owned_mounts'] and not current.get('slot_procs')
print(json.dumps(current))
'''
        code='\n'.join(k+'='+repr(v) for k,v in dict(CODE=node['repo'],ROOT=root,TAG=tag,ROLE=role).items())+'\n'+code
        argv=['ssh','-F',str(control/'ssh.conf'),alias(pool,role),'python3 -']
        write_new(out/(role+'-intent.json'),dict(argv=argv,stdin_sha256=hashlib.sha256(code.encode()).hexdigest(),automatic_retry=False))
        (out/(role+'-stdin.py')).write_text(code)
        value=subprocess.run(argv,input=code,text=True,capture_output=True);(out/(role+'-stdout')).write_text(value.stdout);(out/(role+'-stderr')).write_text(value.stderr)
        write_new(out/(role+'-exit.json'),dict(exit_code=value.returncode))
        if value.returncode:raise ValueError('Inspect '+role+' boundary command')
        observed=json.loads(value.stdout)
        if observed.get('passed') is not True:raise ValueError('Actual original process/mount/slot not closed: '+role)
        boundaries.append(dict(role=role,alias=alias(pool,role),evidence=ref(out/(role+'-stdout')),observed=observed))
    value=dict(schema='closed-flat-cell-independent-audit-v1',passed=True,at=datetime.now(timezone.utc).isoformat(),cell_id=cell_id,
        closing=result['closing'],retention=closing['retention'],archives=archives,sample=checked,current_boundaries=boundaries,unit=state,
        source_pins=[ref(__file__),ref(control/'suite/plan.json'),ref(control/'fleet.json')],scope='Original complete cell; independent retention/member/raw observer/check acceptance and current original process identities. No workload or sealed evidence modified.')
    write_new(out/'result.json',value);print(json.dumps(value))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--control',type=Path,required=True);p.add_argument('--cell',required=True);p.add_argument('--unit',required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();audit(a.control,a.cell,a.unit,a.out)
