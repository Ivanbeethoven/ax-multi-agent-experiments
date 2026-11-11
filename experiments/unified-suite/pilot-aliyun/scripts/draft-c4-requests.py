#!/usr/bin/env python3
"""Write concrete C4 requests from frozen coordinator files; never run workers."""
import argparse
import copy
import hashlib
import json
from pathlib import Path


PREFIX='experiments/unified-suite/pilot-aliyun/scripts/'


def ref(path):return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def write(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def draft(s):
    out=Path(s['out']);out.mkdir(parents=True,exist_ok=False)
    code=Path(s['runtime_repo']);manifest_path=Path(s['code_manifest']['path'])
    if ref(manifest_path)!=s['code_manifest']:raise ValueError('Changed frozen code manifest')
    manifest=json.loads(manifest_path.read_text());control=Path(s['control'])
    original=Path(s['original_preparation_draft']);preparation=json.loads(original.read_text())
    if preparation['pool']!='pool-b' or preparation['product']!='juicefs':raise ValueError('Concrete pool-b JuiceFS source draft required')
    def pin(name,client=False):
        relative=PREFIX+name;path=('/srv/ax-client/'+code.name+'/'+relative if client else str(code/relative))
        if not client and hashlib.sha256((code/relative).read_bytes()).hexdigest()!=manifest[relative]:
            raise ValueError('Coordinator source differs from actual frozen manifest')
        return dict(path=path,sha256=manifest[relative])
    preparation=copy.deepcopy(preparation);preparation.update(reviewed=False,
        source_tool_sha256=pin('run-c4-fork-baseline.py')['sha256'],fork_plan=ref(control/'suite/plan.json'),
        out=str(control/'preparation/base-jfs-01'),other_lease_controls=s['other_lease_controls'])
    worker=preparation['worker'];worker.update(mode='prepare',tool=pin('run-c4-fork-baseline.py',True),
        node_tool=pin('pilot-node.py',True),asset='/srv/ax-client/a/c4-fork-jfs-base-01',
        out='/srv/ax-client/preparation/c4-fork-jfs-base-01',database=200,attempt='c4-jfs-base-01')
    worker['plan']=dict(path='/srv/ax-client/preparation/c4-runtime-15-01/plan.json',sha256=preparation['fork_plan']['sha256'])
    worker['inventory']=dict(path='/srv/ax-client/preparation/c4-runtime-15-01/inventory.json',sha256=preparation['inventory']['sha256'])
    worker['runtime_pins']={n:manifest[n] for n in worker['runtime_pins']}
    preparation['frozen_runtime']=dict(coordinator=str(code),client='/srv/ax-client/'+code.name,manifest=s['code_manifest'])
    preparation['draft_reason']='Actual frozen coordinator bytes and original inputs; client deployment, phase barriers, live DB/slot and runtime proofs remain required.'
    for name in ('draft_prepared_at',):preparation.pop(name,None)
    write(out/'preparation.request.draft.json',preparation)
    def pending(path):return dict(path=str(path),sha256=None)
    outer=dict(schema='c4-fork-batch-v1',reviewed=False,control=str(control),cell_id=preparation['cell_id'],initialize_state=True,
        preparation_request=ref(out/'preparation.request.draft.json'),asset=worker['asset'],qualification=str(control/'qualification/pool-b-fork-01'),
        runtime_control=pending(control/'runtime-15-01/binding.json'),hardware=pending(control/'hardware-15-01/hardware.json'),
        cross_pool_qualification=pending(control/'hardware-15-01/cross-pool.json'),shared_qualification=s['shared_qualification'],
        tools={key:pin(name) for key,name in dict(baseline='run-c4-fork-baseline.py',capacity='capacity-check.py',prepare='prepare-c4-controller.py',
            calibrate='calibrate-c4.py',check='check-c4-calibration.py',admit='admit-c4.py',run='run-c4.py').items()},
        node_tool=worker['node_tool'],fork_tool=pin('c4-fork-node.py',True),observer_tool_name='make-observer.py',
        observer_tool_sha256=pin('make-observer.py')['sha256'],objects_container=s['objects_container'],
        collector_name=s['collector_name'],collector_out=s['collector_out'],out=str(control/'batch-jfs-01'),formal_attempt='c4-b-jfs-formal-01')
    trials=[];collector=dict(collector_name=s['collector_name'],collector_out=s['collector_out'])
    for index,mode in enumerate(('off','on','on','off','off','on')):
        name=('off1','on1','on2','off2','off3','on3')[index]
        trial=dict(mode=mode,attempt='c4-b-jfs-'+name,out=str(control/'calibration'/('jfs-'+name)),**collector)
        if index in (1,5):
            collector=dict(collector_name='ax-pilot-c4-b-jfs-'+name+'-collector',collector_out='/srv/ax-objects-0/q/c4-b-jfs-'+name+'-collector')
            trial.update(new_collector_name=collector['collector_name'],new_collector_out=collector['collector_out'])
        trials.append(trial)
    outer['trials']=trials
    outer['pending_before_review']=['Prior C3 full closure plus C2 staging','New B code15 install and actual same-boot runtime probes',
        'New plan-bound hardware/cross-pool derivations','Recheck exact initial collector and empty DB200','New reviewed preparation reference and complete proof SHA256s']
    write(out/'batch.request.draft.json',outer)
    write(out/'result.json',dict(passed=True,executable=False,worker_started=False,state_created=False,
        original_draft=ref(original),code_manifest=s['code_manifest'],source_tool=ref(Path(__file__)),
        preparation=ref(out/'preparation.request.draft.json'),batch=ref(out/'batch.request.draft.json')))
    return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',type=Path,required=True);p.add_argument('--sha256',required=True);a=p.parse_args()
    if ref(a.request)['sha256']!=a.sha256:raise ValueError('Changed draft configuration')
    print(json.dumps(draft(json.loads(a.request.read_text()))))
