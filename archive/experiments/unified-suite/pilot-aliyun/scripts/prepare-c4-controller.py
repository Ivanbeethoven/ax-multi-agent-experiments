#!/usr/bin/env python3
"""Collect once-imported C4 base evidence; build actual pool/workload contracts."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import load,pinned,digest,write_new
from bench.runtime.pilot_pools import alias,fleet_node
from bench.runtime import pilot_context
from bench.suite_plan import check_plan
from bench.suite_mount import matches


def ref(path):return dict(path=str(path),sha256=digest(path))


def prepare(s):
    control=Path(s['control']);runtime=pilot_context.select(control,s.get('runtime_control'))
    plan=check_plan(load(control/'suite/plan.json'));cell=next(c for c in plan['cells'] if c['id']==s['cell_id'])
    if cell['workload']!='fork' or cell['arm_id'] not in ('native','juicefs'):raise ValueError('Actual flat C4 baseline required')
    pool=cell['pool'];out=Path(s['qualification']);out.mkdir(parents=True,exist_ok=False)
    q=out/cell['arm_id'];q.mkdir();route=out/'route';route.mkdir();transfers=[]
    ssh=runtime/'ssh.conf'
    def get(source,target):
        target=out/target;target.parent.mkdir(parents=True,exist_ok=True)
        wanted=subprocess.check_output(['ssh','-F',str(ssh),alias(pool,'client'),shlex.join(['sha256sum',source])],text=True).split()[0]
        subprocess.run(['scp','-F',str(ssh),alias(pool,'client')+':'+source,str(target)],check=True)
        if digest(target)!=wanted:raise ValueError('Original C4 preparation evidence changed in transfer')
        transfers.append(dict(source=source,**ref(target)));return target
    source=pinned(s['preparation_coordinator_result']);prepared=load(source)
    if prepared.get('passed') is not True or prepared.get('cell_id')!=cell['id']:
        raise ValueError('Original coordinator preparation must have passed')
    original=s['fork']['preparation'];actual=get(original['path'],'route/result.json')
    if digest(actual)!=original['sha256']:raise ValueError('Original preparation ref changed')
    base=load(actual)
    if (base.get('purpose')!='asset-preparation' or not base['passed'] or base['cell_id']!=cell['id'] or
            base['files']!=cell['params']['files'] or base['bytes']!=cell['params']['files']*4096 or
            base['work_tag']!='child-01'):
        raise ValueError('Full matching C4 base preparation required')
    prefix=str(Path(original['path']).parent)
    for name in ('source-full-check.json','cold-base-check.json','normal-stop-1.json','normal-stop-2.json',
                 'import/exit.json','import-fsync/exit.json','import-debt/result.json','oracle-result/result.json'):
        get(prefix+'/'+name,'route/'+name)
    asset=get(s['asset']+'/asset.json','assets/'+cell['arm_id']+'/asset.json');value=load(asset)
    if value['plan']['sha256']!=digest(control/'suite/plan.json') or value['pool']!=pool or base['asset']['sha256']!=digest(asset):
        raise ValueError('Actual C4 asset/plan/pool differs')
    matches(value['config'],cell['client_settings'])
    expected=('6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc' if cell['arm']['product']=='juicefs' else
        next(v['sha256'] for v in load(control/'provenance/build-manifest.json')['files'] if v['file']=='brewfs'))
    if value['filesystem_binary']['sha256']!=expected:raise ValueError('Actual binary differs from frozen build/release')
    for name in ('source-full-check.json','cold-base-check.json'):
        if load(route/name)['passed'] is not True:raise ValueError('Full original base check failed')
    for name in ('normal-stop-1.json','normal-stop-2.json'):
        end=load(route/name)
        if not end['passed'] or end['exit_code']!=0 or not end['mount_absent']:raise ValueError('Preparation did not close normally')
    # Retain the two independent manifests once per base, not once per child.
    oracle=load(route/'oracle-result/result.json')
    for source,name in ((base['source_tree'],'source-tree.json'),(oracle['expected_tree'],'expected-child-tree.json')):
        if digest(get(source['path'],'route/'+name))!=source['sha256']:raise ValueError('Original independent tree changed')
    shared=Path(s['shared_qualification']);hardware=load(pinned(s['hardware']))
    if hardware.get('passed') is not True or hardware['inventory_sha256']!=digest(runtime/'fleet.json'):
        raise ValueError('Actual refreshed-fleet hardware qualification required; do not relabel old evidence')
    shutil.copyfile(pinned(s['hardware']),out/'hardware.json')
    shutil.copyfile(shared/cell['arm_id']/'metrics.json',q/'metrics.json')
    metrics=load(q/'metrics.json')
    if {v['observer_id'] for v in metrics['bindings']}!={pool+'-'+r for r in ('client','metadata','objects')}:
        raise ValueError('Actual three-role pool metric definitions required')
    work=dict(schema='experiment-workload-contract-v1',reviewed=True,kind='fork',params=cell['params'],
        seed=plan['config']['seed'],work_tag='child-01',binary_pins=[s['fork']['tool'],value['filesystem_binary']],
        input_pins=[ref(control/'suite/plan.json'),original,base['source_tree'],base['oracle']],
        scope='One native clone/full copy, continuous first successful content read, full child fsync and identical fixed mutation; fresh paths only.')
    write_new(q/'workload.json',work)
    write_new(q/'input.json',dict(passed=True,cell_id=cell['id'],source_check=ref(route/'source-full-check.json'),
        files=base['files'],bytes=base['bytes'],source_tree=base['source_tree']))
    write_new(q/'filesystem.json',dict(passed=True,cell_id=cell['id'],complete_base=ref(route/'cold-base-check.json'),
        normal_exits=[ref(route/n) for n in ('normal-stop-1.json','normal-stop-2.json')],
        scope='Complete original C4 base only; clone/isolation proof must additionally come from an actual C4 trial.'))
    provenance=dict(passed=True,cell_id=cell['id'],actual_asset=ref(asset),filesystem_binary=value['filesystem_binary'],
        cache_settings=cell['client_settings'],source_pins=[ref(control/'provenance'/n) for n in
            ('build-manifest.json','input-manifest.json','inventory.json')],actual_allocation=ref(runtime/'allocation.json'))
    if (digest(control/'provenance/build-manifest.json')!=plan['config']['build_manifest_sha256'] or
            digest(control/'provenance/input-manifest.json')!=plan['config']['input_manifest_sha256']):
        raise ValueError('Frozen C4 provenance changed')
    write_new(q/'provenance.json',provenance)
    write_new(q/'asset-receipt.json',dict(schema='suite-asset-v1',plan_sha256=plan['plan_sha256'],asset_id=cell['asset_id'],
        native_binding=dict(asset=ref(asset),base_path=value['mount_point']+'/base',bucket=value['bucket'],database=value['database']),
        timings_seconds={k:base[k] for k in ('import_seconds','import_fsync_seconds','cold_verify_seconds')},
        stages={name:dict(evidence=ref(path),equals={'passed':True}) for name,path in {
            'source-verify':q/'input.json','import':q/'filesystem.json',
            'full-base-verify':route/'cold-base-check.json','cache-reset':q/'filesystem.json'}.items()}))
    write_new(out/'transfers.json',transfers)
    return ref(q/'asset-receipt.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    a=p.parse_args();s=load(pinned(dict(path=a.request,sha256=a.sha256)))
    if s.get('reviewed') is not True:raise ValueError('Reviewed explicit C4 preparation collection required')
    prepare(s)
