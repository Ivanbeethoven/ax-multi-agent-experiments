#!/usr/bin/env python3
"""Admit one C4 baseline from its actual base, trials, capacity and pool proof."""
import argparse
from pathlib import Path
import sys
REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import load,pinned,digest,write_new
from bench.c4_dispatch import require_allowed
from bench.runtime.pilot_pools import cross_pool_proof
from bench import suite_state


def ref(path):return dict(path=str(path),sha256=digest(path))


def process_key(value):
    return tuple(value[k] for k in ('boot_id','pid','start_ticks'))


def exit_binding(root,q,tag,plan_path,cell,asset,purpose,key,exit_pin):
    suffix,folder,receipt=('', 'main', 'exit.json') if key=='native_exit' else ('-v', 'verify-mount', 'verify-exit.json')
    observed=load(pinned(exit_pin));actual=q/receipt;mount=q/folder/'mount.reviewed.json';spec=load(mount)
    state=root/'m'/(tag+suffix);ready=load(state/'ready.json');proc=load(state/'process.json');stopping=load(state/'stopping.json')
    original_worker_spec='/srv/ax-client/q/'+tag+'/'+folder+'/mount.reviewed.json'
    spec_pin=dict(path=original_worker_spec,sha256=digest(mount))
    if (exit_pin!=ref(actual) or observed!=load(state/'stopped.json') or observed.get('passed') is not True or
            type(observed.get('exit_code')) is not int or observed['exit_code']!=0 or observed.get('mount_absent') is not True or
            observed.get('active_workers')!=[] or spec.get('schema')!='experiment-mount-v1' or spec.get('reviewed') is not True or
            spec['purpose']!=purpose or spec['suite_binding']['cell_id']!=cell['id'] or
            spec['suite_binding']['plan']['sha256']!=digest(plan_path) or spec['mount_point']!=asset+'/mnt' or
            ready.get('passed') is not True or ready['spec']!=spec_pin or proc['spec']!=spec_pin or
            not (observed['anchor']==spec['anchor']==ready['anchor']==stopping['anchor']) or
            not (observed['original_mount']==ready['mount']==stopping['original_mount']) or
            observed['original_mount']['point']!=spec['mount_point'] or
            not (process_key(ready['process'])==process_key(proc['process'])==process_key(stopping['process']))):
        raise ValueError('Original native C4 stop, mount spec or process identity differs: '+key)
    return dict(receipt=exit_pin,mount_spec=ref(mount),ready=ref(state/'ready.json'),process=ref(state/'process.json'),
                stopping=ref(state/'stopping.json'),stopped=ref(state/'stopped.json'),identity=list(process_key(proc['process'])))


def route_checks(route,plan,cell,asset,plan_path):
    if route.get('schema')!='c4-fork-route-v1' or route.get('passed') is not True or route.get('cell_id')!=cell['id'] or route.get('pool')!=cell['pool']:
        raise ValueError('Actual completed matching C4 trial required for route/isolation')
    trial_path=pinned(route['trial']);trial=load(trial_path)
    if trial.get('passed') is not True or trial.get('pool')!=cell['pool'] or trial.get('purpose')!='observer-calibration-off':
        raise ValueError('Original full first off trial required')
    for key in ('work','client_verify','parent_verify','native_exit','verify_exit','closure'):
        if route[key]!=trial[key]:raise ValueError('C4 route pins differ from original completed trial: '+key)
        proof=load(pinned(route[key]))
        if proof.get('passed') is not True or (key not in ('native_exit','verify_exit') and proof.get('cell_id')!=cell['id']):
            raise ValueError('Incomplete original C4 trial evidence: '+key)
    full=load(pinned(route['client_verify']))
    if full.get('full_cold_child') is not True or full.get('parent_unchanged') is not True or full['files']!=cell['params']['files']:
        raise ValueError('Formal admission requires an original full cold route trial')
    tag=full['child_name'];root=trial_path.parent/'raw/client';q=root/'q'/tag
    if route['client_verify']!=ref(q/'verify/result.json') or route['parent_verify']!=ref(q/'verify/parent.json'):
        raise ValueError('Original full route and parent proof are not in the same attempt archive')
    exits={key:exit_binding(root,q,tag,plan_path,cell,asset,trial['purpose'],key,route[key]) for key in ('native_exit','verify_exit')}
    if exits['native_exit']['identity']==exits['verify_exit']['identity']:
        raise ValueError('Actual independent mount process identities required; numeric mount IDs may be reused')
    closure=load(pinned(route['closure']));closed=[n for n in closure['nodes'] if 'debt_samples' in n]
    if (len(closed)!=1 or closure.get('all_exited') is not True or closure.get('all_unmounted') is not True or
            any(type(closure[k]) is not int or closure[k]!=0 for k in ('pending_upload_bytes','pending_upload_operations')) or
            not {tuple(e['identity']) for e in exits.values()}.issubset({process_key(p) for p in closed[0]['processes']})):
        raise ValueError('The exact original mount processes and zero native debt must be in the original closure')
    trial_work=load(pinned(route['work']))
    from bench.suite_plan import fingerprint
    if trial_work['workload']!='fork' or trial_work['workload_signature']!=fingerprint(cell['params']):
        raise ValueError('Trial clone/copy work differs from this exact C4 cell')
    return dict(passed=True,cell_id=cell['id'],pool=cell['pool'],trial=route['trial'],full=route['client_verify'],exits=exits)


def validate(s, *, require_overhead=True):
    control=Path(s['control']);plan=load(control/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['id']==s['cell_id']);q=Path(s['qualification'])/cell['arm_id']
    if s.get('reviewed') is not True or cell['workload']!='fork' or cell['arm_id'] not in ('native','juicefs'):
        raise ValueError('Reviewed explicit C4 baseline required')
    from bench.runtime import pilot_context
    runtime=pilot_context.select(control,s.get('runtime_control'))
    if require_overhead:
        over=load(pinned(s['overhead']))
        if (over.get('passed') is not True or over['max_relative_change']>.05 or
                over['metric_contract_sha256']!=digest(q/'metrics.json') or over['workload_contract_sha256']!=digest(q/'workload.json')):
            raise ValueError('Actual C4-specific calibration required')
        if 'verification' in over:
            verified=load(pinned(over['verification']))
            if len(verified.get('trials',[]))!=6:raise ValueError('Six retained verification receipts required')
            for trial in verified['trials']:pinned(trial['proof'])
    if load(q/'workload.json')['params']!=cell['params']:raise ValueError('C4 workload changed')
    capacity=load(pinned(s['capacity']))
    if capacity.get('passed') is not True or capacity['extra_full_copies']<7 or capacity['reserve_fraction']<.2:
        raise ValueError('Seven fresh children with 20% storage/metadata reserve required')
    for pin in capacity.get('source_pins',[]):pinned(pin)
    before=load(pinned(capacity['before']));after=load(pinned(capacity['after']))
    prep=after['nodes']['client']['preparation']
    if (before.get('cell_id')!=cell['id'] or before.get('files')!=cell['params']['files'] or
            before.get('pool')!=cell['pool'] or after.get('pool')!=cell['pool'] or
            prep['sha256']!=s['fork']['preparation']['sha256'] or
            prep['value'].get('cell_id')!=cell['id'] or prep['value'].get('purpose')!='asset-preparation'):
        raise ValueError('Capacity must measure this C4 base on this actual pool, not a prior C3 import')
    route=load(pinned(s['route']))
    verified_route=route_checks(route,plan,cell,s['asset'],control/'suite/plan.json')
    gates={
        'exact-build-input-inventory':q/'provenance.json','native-route-and-metadata':q/'filesystem.json',
        'effective-cache-and-durability':q/'provenance.json','host-capacity-and-maintenance':Path(s['qualification'])/'hardware.json',
        'native-fork-or-copy-qualified':pinned(s['route']),
        'parent-sibling-isolation':pinned(s['route']),
        'capacity-for-eager-copy':pinned(s['capacity'])}
    if require_overhead:gates['complete-telemetry-and-overhead']=pinned(s['overhead'])
    if 'cross-pool-hardware-equivalence' in cell['required_gates']:
        cross=pinned(s['cross_pool_qualification']);cross_pool_proof(cross,plan,runtime/'fleet.json')
        gates['cross-pool-hardware-equivalence']=cross
    expected=set(cell['required_gates'])
    if not require_overhead:expected.remove('complete-telemetry-and-overhead')
    if set(gates)!=expected:raise ValueError('All and only frozen C4 gates required')
    for path in gates.values():suite_state.predicates(load(path),{'passed':True})
    asset=load(q/'asset-receipt.json');wanted=next(a for a in plan['assets'] if a['id']==cell['asset_id'])
    if (asset['schema']!='suite-asset-v1' or asset['asset_id']!=wanted['id'] or asset['plan_sha256']!=plan['plan_sha256'] or
            set(asset['stages'])!=set(wanted['required_stages']) or not asset['native_binding'] or not asset['timings_seconds']):
        raise ValueError('Actual asset registration would reject C4 identity/stages')
    import math
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in asset['timings_seconds'].values()):
        raise ValueError('Invalid asset stage wall times')
    for stage in asset['stages'].values():suite_state.predicates(load(pinned(stage['evidence'])),stage['equals'])
    return control,plan,cell,q,gates,verified_route


def admit(request):
    s=load(pinned(request));control,plan,cell,q,gates,verified_route=validate(s)
    require_allowed(cell)
    out=Path(s['out']);out.mkdir(parents=True,exist_ok=False)
    write_new(out/'route-check.json',verified_route)
    qualification=dict(schema='suite-qualification-v1',plan_sha256=plan['plan_sha256'],cell_id=cell['id'],
        checks=[dict(gate=g,evidence=ref(path),equals={'passed':True}) for g,path in gates.items()])
    write_new(out/'qualification.json',qualification)
    suite_state.qualify(control/'state.sqlite',ref(out/'qualification.json'))
    with suite_state.transaction(control/'state.sqlite') as db:
        exists=db.execute('SELECT 1 FROM assets WHERE id=?',(cell['asset_id'],)).fetchone()
    if not exists:suite_state.register_asset(control/'state.sqlite',ref(q/'asset-receipt.json'))
    lease=suite_state.claim(control/'state.sqlite',cell['pool'],s.get('owner','pilot-operator'))
    if lease['cell']!=cell or lease['already_claimed']:raise ValueError('Unexpected existing/different C4 reservation; inspect without replay')
    write_new(out/'lease.json',lease)
    context=dict(s,attempt=s['formal_attempt'],purpose='experiment')
    context.pop('reviewed',None);context.pop('formal_attempt',None)
    write_new(out/'context.json',context)
    return ref(out/'context.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    a=p.parse_args();admit(dict(path=a.request,sha256=a.sha256))
