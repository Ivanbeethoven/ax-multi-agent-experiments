#!/usr/bin/env python3
"""Create a separate C4 control; preserve observations and refresh one idle pool.

control copies small frozen evidence only, never SQLite. runtime explicitly
probes the selected pool under canonical guards; unchanged other-pool evidence
keeps its original bytes/time. No cloud API request or service change occurs.
"""
import argparse
import copy
from datetime import datetime,timezone
from pathlib import Path
import re
import shutil
import sys

bootstrap=argparse.ArgumentParser(add_help=False);bootstrap.add_argument('--runtime-repo',type=Path)
bootstrap_args,_=bootstrap.parse_known_args()
REPO=(bootstrap_args.runtime_repo.resolve(strict=True) if bootstrap_args.runtime_repo else Path(__file__).resolve().parents[4])
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import fleet,pilot_context
from bench.runtime.pilot_pools import active_conflicts,resource_guard,pool_of,cross_pool_proof
from bench.suite_plan import check_plan


def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def placement(value):
    return dict(value,nodes=[{k:v for k,v in n.items() if k!='repo'} for n in value['nodes']])


def retained_copy(source,target,rows):
    source=Path(source);target=Path(target)
    if source.is_symlink() or not source.is_file() or target.exists():raise ValueError('Original regular evidence and fresh destination required')
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    if digest(source)!=digest(target):raise ValueError('Evidence changed while copied')
    rows.append(dict(original=ref(source),retained=ref(target)))


def control(s):
    source=Path(s['source_control']).resolve(strict=True)
    runtime=pilot_context.select(source,s['source_runtime'])
    plan=check_plan(load(pinned(s['fork_plan'])));old=check_plan(load(source/'suite/plan.json'))
    out=Path(s['control'])
    if out.exists() or out==source or not out.is_absolute():raise ValueError('New C4 control required')
    if ({c['workload'] for c in plan['cells']}!={'fork'} or
            any(plan['config'][k]!=old['config'][k] for k in ('build_manifest_sha256','input_manifest_sha256'))):
        raise ValueError('Fork-only new plan, same actual binaries and inputs required')
    inventory=source/'provenance/inventory.json'
    if any(c['inventory_sha256']!=digest(inventory) for c in plan['config']['clusters']):
        raise ValueError('Actual original cloud inventory required')
    if {c['id']:c['resource_ids'] for c in plan['config']['clusters']}!={c['id']:c['resource_ids'] for c in old['config']['clusters']}:
        raise ValueError('New phase may not change physical host/disk resources')
    out.mkdir(parents=True);rows=[]
    retained_copy(pinned(s['fork_plan']),out/'suite/plan.json',rows)
    for name in ('build-manifest.json','input-manifest.json','inventory.json'):
        retained_copy(source/'provenance'/name,out/'provenance'/name,rows)
    for name in ('fleet.json','allocation.json','ssh.conf'):
        retained_copy(runtime/name,out/name,rows)
    for path in sorted((runtime/'probes').rglob('*')):
        if path.is_symlink():raise ValueError('Probe evidence symlink')
        if path.is_file():retained_copy(path,out/'probes'/path.relative_to(runtime/'probes'),rows)
    write_new(out/'setup.json',dict(schema='c4-control-setup-v1',passed=True,derived_at=datetime.now(timezone.utc).isoformat(),
        source_control=str(source),source_runtime=s['source_runtime'],canonical_control=str(source),
        fork_plan=ref(out/'suite/plan.json'),retained_original_evidence=rows,state_created=False,
        source_tool=ref(__file__),runtime_repo=str(REPO),
        scope='Original observation bytes and times retained unchanged; this is a new plan binding, not a new measurement.'))
    return ref(out/'setup.json')


def runtime(s):
    root=Path(s['control']);original=load(root/'fleet.json');plan=check_plan(load(root/'suite/plan.json'))
    cell=next(c for c in plan['cells'] if c['id']==s['cell_id']);pool=pool_of(cell)
    if cell['workload']!='fork' or cell['arm_id'] not in ('juicefs','native'):raise ValueError('Explicit supported C4 cell required')
    if not re.fullmatch('code-[0-9]{2}',s['code_directory']):raise ValueError('Frozen code-NN required')
    completed_close=False;completed_staging=False
    if len({(r['path'],r['sha256']) for r in s['phase_barriers']})!=len(s['phase_barriers']):
        raise ValueError('Distinct original closure/staging proofs required')
    for proof in s['phase_barriers']:
        value=load(pinned(proof))
        closed=value.get('schema')=='suite-cell-close-v1' and value.get('outcome')=='complete'
        completed_close=completed_close or closed;completed_staging=completed_staging or (value.get('passed') is True and not closed)
        if value.get('passed') is not True and not closed:
            raise ValueError('Original completed phase/staging proof required before contacting workers')
    if not completed_close or not completed_staging:raise ValueError('Explicit prior C3 closure and C2 staging proof required')
    canonical=Path(s['canonical_control']);controls=[canonical,*[Path(p) for p in s['other_lease_controls']]]
    if (root/'state.sqlite').exists():controls.append(root)
    out=Path(s['out'])
    if out.exists() or out==root:raise ValueError('New runtime evidence directory without state required')
    with resource_guard(canonical,cell,plan):
        if any(active_conflicts(p,cell,plan) for p in controls):raise ValueError('Selected physical pool still leased')
        value=copy.deepcopy(original)
        changed=[];reused=[]
        for node in value['nodes']:
            if node['cluster_id']==pool:
                node['repo']=node['work_root']+'/'+s['code_directory'];changed.append(node['id'])
            else:reused.append(node['id'])
        if placement(value)!=placement(original):raise ValueError('Only runtime code paths may change')
        out.mkdir(parents=True);write_new(out/'fleet.json',value);rows=[]
        retained_copy(root/'ssh.conf',out/'ssh.conf',rows)
        original_peer=load(root/'probes'/changed[0]/'peer.json')
        ssh=dict(original_peer['ssh'],config=ref(out/'ssh.conf'))
        probes=[]
        for node in value['nodes']:
            name=node['id'];slots=[None]+([node['slots'][0]['id']] if node['role']=='executor' else [])
            for slot in slots:
                directory=name+('-slot' if slot else '')
                target=out/'probes'/directory
                if name in changed:
                    proof=fleet.probe(out/'fleet.json',name,slot,ssh,target,REPO,host_observer=slot is None)
                    old=load(root/'probes'/directory/'probe.json')
                    if not proof['passed'] or proof['boot_id']!=old['boot_id']:
                        raise ValueError('Actual refreshed pool probe failed or host rebooted')
                else:
                    for path in sorted((root/'probes'/directory).iterdir()):
                        if path.is_file():retained_copy(path,target/path.name,rows)
                    proof=load(target/'probe.json')
                    if not proof['passed']:raise ValueError('Original unchanged peer was not qualified')
                probes.append(ref(target/'probe.json'))
        write_new(out/'allocation.json',dict(passed=True,inventory_sha256=digest(out/'fleet.json'),probes=probes,
            freshly_probed_nodes=changed,reused_unchanged_nodes=reused,retained_original_evidence=rows,
            scope='Fresh selected-pool probes; unchanged other-pool original observations retained with original times and inventory pins. No service restart or new hardware measurement on the other pool.'))
        binding=pilot_context.validate(root,out);write_new(out/'binding.json',binding)
        write_new(out/'code-only-delta.json',dict(passed=True,pool=pool,old_fleet=ref(root/'fleet.json'),new_fleet=ref(out/'fleet.json'),
            changed_nodes=changed,reused_unchanged_nodes=reused,placement_unchanged=placement(value)==placement(original),
            source_pins=[ref(root/'setup.json'),ref(out/'binding.json'),*probes],phase_barriers=s['phase_barriers']))
    return ref(out/'binding.json')


def hardware(s):
    root=Path(s['control']);runtime=pilot_context.select(root,s['runtime_control']);plan=check_plan(load(root/'suite/plan.json'))
    old_fleet=load(root/'fleet.json');new_fleet=load(runtime/'fleet.json');delta=load(runtime/'code-only-delta.json')
    if not delta['passed'] or placement(old_fleet)!=placement(new_fleet):raise ValueError('Explicit code-only actual placement proof required')
    old_cross=load(pinned(s['original_cross_pool']));old_hardware=load(pinned(s['original_hardware']))
    for pin in old_cross['source_pins']:pinned(pin)
    if (not old_cross['passed'] or not all(v is True for v in old_cross['checks'].values()) or not old_hardware['passed'] or
            old_cross['inventory_sha256']!=digest(root/'fleet.json') or old_hardware['inventory_sha256']!=digest(root/'fleet.json') or
            old_cross['cloud_inventory_sha256']!=digest(root/'provenance/inventory.json') or old_hardware['pool']!=delta['pool']):
        raise ValueError('Actual original matching hardware and cross-pool observations required')
    if not any(Path(pin['path']).name=='build-manifest.json' and pin['sha256']==digest(root/'provenance/build-manifest.json')
               for pin in old_cross['source_pins']):raise ValueError('Original cross-pool services used a different build')
    cell=next(c for c in plan['cells'] if c['id']==s['cell_id'])
    if cell['pool']!=delta['pool']:raise ValueError('Hardware proof belongs to another pool')
    source_pins=[s['original_cross_pool'],s['original_hardware'],ref(runtime/'code-only-delta.json'),
        ref(runtime/'binding.json'),ref(root/'suite/plan.json'),ref(root/'provenance/build-manifest.json')]
    out=Path(s['out']);out.mkdir(parents=True,exist_ok=False)
    # These are derived certificates, not newly timestamped native samples.
    # The original certificates, API data, role observations and timestamps are
    # kept intact and pinned transitively, with fresh same-boot runtime probes.
    hardware=dict(old_hardware,inventory_sha256=digest(runtime/'fleet.json'),source_pins=source_pins,
        derivation='Unchanged hardware plus actual same-boot selected-pool code-only runtime refresh.',
        original_observation=s['original_hardware'],derived_at=datetime.now(timezone.utc).isoformat())
    cross=dict(old_cross,plan_sha256=plan['plan_sha256'],inventory_sha256=digest(runtime/'fleet.json'),
        source_pins=old_cross['source_pins']+source_pins,original_observation=s['original_cross_pool'],
        derived_at=datetime.now(timezone.utc).isoformat(),
        derivation='Same instances/disks/slots/build/cloud inventory; only selected runtime code paths changed. Original native/API observation time is unchanged, not remeasured.')
    write_new(out/'hardware.json',hardware);write_new(out/'cross-pool.json',cross)
    cross_pool_proof(out/'cross-pool.json',plan,runtime/'fleet.json')
    return dict(hardware=ref(out/'hardware.json'),cross_pool=ref(out/'cross-pool.json'))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['control','runtime','hardware'])
    p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--runtime-repo',type=Path);a=p.parse_args()
    s=load(pinned(dict(path=a.request,sha256=a.sha256)))
    if s.get('schema')!='c4-setup-request-v1' or s.get('reviewed') is not True:raise ValueError('Reviewed explicit C4 setup request required')
    print(__import__('json').dumps(globals()[a.action](s)))


if __name__=='__main__':main()
