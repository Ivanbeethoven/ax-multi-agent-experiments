#!/usr/bin/env python3
"""Admit one C4 baseline from its actual base, trials, capacity and pool proof."""
import argparse
from pathlib import Path
import sys
REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import load,pinned,digest,write_new
from bench.runtime.pilot_pools import cross_pool_proof
from bench import suite_state


def ref(path):return dict(path=str(path),sha256=digest(path))


def admit(request):
    s=load(pinned(request));control=Path(s['control']);plan=load(control/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['id']==s['cell_id']);q=Path(s['qualification'])/cell['arm_id']
    if s.get('reviewed') is not True or cell['workload']!='fork' or cell['arm_id'] not in ('native','juicefs'):
        raise ValueError('Reviewed explicit C4 baseline required')
    from bench.runtime import pilot_context
    runtime=pilot_context.select(control,s.get('runtime_control'))
    over=load(pinned(s['overhead']))
    if (over.get('passed') is not True or over['max_relative_change']>.05 or
            over['metric_contract_sha256']!=digest(q/'metrics.json') or over['workload_contract_sha256']!=digest(q/'workload.json')):
        raise ValueError('Actual C4-specific calibration required')
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
    if route.get('schema')!='c4-fork-route-v1' or route.get('passed') is not True or route.get('cell_id')!=cell['id'] or route.get('pool')!=cell['pool']:
        raise ValueError('Actual completed matching C4 trial required for route/isolation')
    for key in ('work','client_verify','parent_verify','native_exit','verify_exit','closure'):
        proof=load(pinned(route[key]))
        if proof.get('passed') is not True or proof.get('cell_id')!=cell['id']:
            raise ValueError('Incomplete original C4 trial evidence: '+key)
    trial_work=load(pinned(route['work']))
    from bench.suite_plan import fingerprint
    if trial_work['workload']!='fork' or trial_work['workload_signature']!=fingerprint(cell['params']):
        raise ValueError('Trial clone/copy work differs from this exact C4 cell')
    gates={
        'exact-build-input-inventory':q/'provenance.json','native-route-and-metadata':q/'filesystem.json',
        'effective-cache-and-durability':q/'provenance.json','host-capacity-and-maintenance':Path(s['qualification'])/'hardware.json',
        'complete-telemetry-and-overhead':pinned(s['overhead']),
        'native-fork-or-copy-qualified':pinned(s['route']),
        'parent-sibling-isolation':pinned(s['route']),
        'capacity-for-eager-copy':pinned(s['capacity'])}
    if 'cross-pool-hardware-equivalence' in cell['required_gates']:
        cross=pinned(s['cross_pool_qualification']);cross_pool_proof(cross,plan,runtime/'fleet.json')
        gates['cross-pool-hardware-equivalence']=cross
    if set(gates)!=set(cell['required_gates']):raise ValueError('All and only frozen C4 gates required')
    out=Path(s['out']);out.mkdir(parents=True,exist_ok=False)
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
