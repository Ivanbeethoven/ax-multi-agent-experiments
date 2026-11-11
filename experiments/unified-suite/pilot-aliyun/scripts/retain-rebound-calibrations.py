#!/usr/bin/env python3
"""Retain historical six trials after the explicitly rebound new formal attempt."""
import argparse
import base64
from datetime import datetime,timezone
import json
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.pilot_pools import resource_guard,active_conflicts,alias
from bench import suite_state,archive_check,experiment_step

def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def boundary(control,source,cell,unit,verify_retention=False):
    result=load(control/'one-cell-runs'/cell['id']/'result.json')
    closing=load(pinned(result['closing']))
    if result.get('passed') is not True or closing.get('outcome')!='complete' or closing.get('cell_id')!=cell['id']:raise ValueError('Actual new formal must have closed normally')
    for root in (control,source):
        if active_conflicts(root,cell):raise ValueError('A physical pool lease remains')
    state=dict(line.split('=',1) for line in subprocess.check_output(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','SubState','-p','ExecMainStatus'],text=True).splitlines())
    if state['MainPID']!='0' or state['ExecMainStatus']!='0' or (state['ActiveState'],state['SubState']) not in (('active','exited'),('inactive','dead')):raise ValueError('Actual successful formal unit must have exited')
    if verify_retention:experiment_step.verify(closing['retention']['root'],closing['retention']['manifest']['path'],closing['retention']['manifest']['sha256'])
    return dict(passed=True,unit=state,completed=ref(control/'one-cell-runs'/cell['id']/'result.json'),closing=result['closing'])


def run(control,unit,out):
    prepared=load(control/'prepared.json');original=load(pinned(prepared['original_request']));source=Path(original['control'])
    oldplan=load(source/'suite/plan.json');newplan=load(control/'suite/plan.json');cell=next(c for c in newplan['cells'] if c['id']==original['cell_id']);pool=cell['pool']
    bridge=load(pinned(prepared['condition_rebind']))
    if bridge.get('passed') is not True or bridge.get('full_cell_equal') is not True or cell!=next(c for c in oldplan['cells'] if c['id']==cell['id']):raise ValueError('Exact unchanged original cell required')
    if out.parent!=control/'retained-original-calibrations' or not out.is_absolute():raise ValueError('Explicit new retention output required')
    retainer_path=Path(original['tools']['run']['path']).with_name('retain-calibrations.py');code=retainer_path.read_bytes();retainer=runpy.run_path(str(retainer_path))
    trials=original['trials']
    if len(trials)!=6 or [t['mode'] for t in trials]!=['off','on','on','off','off','on']:raise ValueError('Original complete fixed six trials required')
    with resource_guard(control,cell,newplan):
        closed=boundary(control,source,cell,unit,True)
        if subprocess.check_output(['ssh','-F',str(control/'ssh.conf'),alias(pool,'client'),'cat /sys/fs/cgroup/ax-slot-1/cgroup.procs'],text=True).strip():raise ValueError('Agent slot not empty')
        out.mkdir(parents=True,exist_ok=False);write_new(out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),original_request=prepared['original_request'],
            original_plan=ref(source/'suite/plan.json'),execution_plan=ref(control/'suite/plan.json'),condition_rebind=prepared['condition_rebind'],boundary=closed,source_tool=ref(__file__),node_retainer=ref(retainer_path),new_measurements=False))
        archives=[];results=[]
        for trial in trials:
            boundary(control,source,cell,unit);tag=trial['attempt'];root=Path(trial['out']);end=load(root/'finished.json');cohort=load(root/'cohort.json')
            if root!=source/'calibration'/tag or end.get('passed') is not True or load(pinned(end['cohort'])).get('passed') is not True:raise ValueError('Original successful calibration required')
            for entry in end.values():
                if isinstance(entry,dict) and {'path','sha256'}<=entry.keys():pinned(entry)
            directory=out/tag;directory.mkdir();files,skipped=retainer['file_list'](source,['calibration/'+tag]);before=retainer['stable_snapshot'](source,files)
            for name in files:
                target=directory/'controller'/Path(name).relative_to('calibration/'+tag);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,target)
            for key in ('inventory','metric_contract','workload_contract','allocation_qualification','hardware_qualification','input_qualification','filesystem_qualification'):
                target=directory/'contracts'/(key+'.json');target.parent.mkdir(exist_ok=True);shutil.copyfile(pinned(cohort[key]),target)
            observers=retainer['observer_roles'](cohort,pool);roles=['client'] if trial['mode']=='off' else ['client','metadata','objects']
            for role in roles:
                peer=cohort['workers'][0]['peer'] if role=='client' else observers[role]['peer']
                cfg=dict(code=peer['repo'],pool=pool,root={'client':'/srv/ax-client','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}[role],role=role,
                    tag=tag,mode=trial['mode'],bundle=out.name,cell_id=cell['id'],marks=cell['observation_marks'],code_pins=peer['pins'],
                    worker_spec=cohort['workers'][0]['spec'] if role=='client' else None,observer_sha256=observers[role]['spec']['sha256'] if role in observers else None)
                encoded=base64.b64encode(json.dumps(cfg).encode()).decode();argv=['ssh','-F',str(control/'ssh.conf'),alias(pool,role),shlex.join(['python3','-','--node-config',encoded])]
                write_new(directory/(role+'-intent.json'),dict(argv=argv,automatic_retry=False))
                with (directory/(role+'-stdout')).open('xb') as stdout,(directory/(role+'-stderr')).open('xb') as stderr:
                    status=subprocess.run(argv,input=code,stdout=stdout,stderr=stderr).returncode
                write_new(directory/(role+'-exit.json'),dict(exit_code=status))
                if status:raise RuntimeError('Inspect historical node retention failure: '+tag+'/'+role)
                response=json.loads((directory/(role+'-stdout')).read_text());archive=response['archive'];target=directory/(role+'.tar.gz')
                subprocess.run(['scp','-F',str(control/'ssh.conf'),alias(pool,role)+':'+archive['archive'],str(target)],check=True)
                checked=archive_check.verify(target,archive['sha256']);write_new(directory/(role+'-archive-check.json'),checked);archives.append(ref(target))
            retainer['assert_unchanged'](source,['calibration/'+tag],files,skipped,before);results.append(dict(tag=tag,mode=trial['mode'],roles=roles,passed=True))
        write_new(out/'result.json',dict(passed=True,cell_id=cell['id'],original_request=prepared['original_request'],original_plan=ref(source/'suite/plan.json'),
            execution_rebind=prepared['condition_rebind'],historical_overhead=prepared['overhead'],trials=results,node_archives=archives,new_measurements=False,
            scope='Original six calibrations, original source plan/times/paths and raw streams. New formal is separately closed under the explicit unchanged-condition rebind. No original completed outcome is rewritten.'))
        files=sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file());checked=archive_check.pack(out,files,out.parent/(out.name+'.tar.gz'),cell['id'],'historical-six-calibrations')
        write_new(out.parent/(out.name+'-archive-check.json'),checked);print(json.dumps(checked))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--control',type=Path,required=True);p.add_argument('--unit',required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();run(a.control,a.unit,a.out)
