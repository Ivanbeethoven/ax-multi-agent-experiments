"""Offline complete-block reporting. Failed/recovered samples remain visible."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path

from .experiment_io import load, write_new
from .suite_state import transaction, plan_of, evidence, predicates
from .suite_plan import fingerprint, parallel
from .runtime.observe import audit

CHECKS=('work','correctness','isolation','remote_readback','drain','real_exit','cache_policy','observer_overhead')


def number(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError('finite nonnegative measurement required')
    return value


def sample(ref, plan, cell):
    s=evidence(ref); reasons=[]
    if s.get('schema')!='suite-sample-v1' or s.get('plan_sha256')!=plan['plan_sha256'] or s.get('cell_id')!=cell['id']:
        raise ValueError('sample identity differs')
    if s.get('uninterrupted') is not True or s.get('recovered') is not False:
        reasons.append('interrupted/recovered; excluded from uninterrupted performance pairs')
    if set(s.get('checks',{}))!=set(CHECKS):reasons.append('incomplete acceptance evidence')
    for key,check in s.get('checks',{}).items():
        try:
            v=evidence(check['evidence']);predicates(v,check['equals'])
            # Each adapter must expose an explicit success predicate. A pin or
            # a matching identity by itself is not evidence of correctness.
            if not any(k in check['equals'] and check['equals'][k] is True for k in ('passed','valid')):
                raise ValueError('missing successful check predicate')
            if key in ('correctness','isolation','remote_readback','drain','real_exit'):
                predicates(v,{'cell_id':cell['id']})
            if key=='work':
                predicates(v,{'passed':True,'cell_id':cell['id'],'workload':cell['workload'],
                              'workload_signature':fingerprint(cell['params'])})
            if key=='observer_overhead':
                if number(v.get('max_relative_change',1))>plan['config']['telemetry']['max_relative_overhead']:
                    raise ValueError('observer overhead tolerance exceeds frozen protocol')
        except (ValueError,KeyError,TypeError,OSError) as e:reasons.append(key+': '+str(e))
    observers=s.get('observers',[])
    required=set(s.get('required_observer_ids',[]))
    pool=next(p for p in plan['config']['clusters'] if p['id']==cell['pool'])
    if required!=set(pool['observer_ids']):reasons.append('observer coverage differs from frozen pool inventory')
    if not required or len({x['id'] for x in observers})!=len(observers) or {x['id'] for x in observers}!=required:
        reasons.append('explicit complete host/client/backend observer coverage required')
    for observer in observers:
        try:
            if not set(cell['observation_marks']).issubset(observer.get('marks',[])) or observer['max_gap_seconds']!=plan['config']['telemetry']['max_gap_seconds']:
                raise ValueError('predeclared gap <=30s and phase marks required')
            # Recompute against raw samples, not a saved passed=true boolean.
            audit(observer['directory'],observer['max_gap_seconds'],observer['marks'])
        except (ValueError,KeyError,TypeError,OSError) as e:reasons.append('telemetry '+observer['id']+': '+str(e))
    metrics={}
    for name,m in s.get('metrics',{}).items():
        try:
            v=evidence(m['evidence'])
            for part in m['field'].split('.'):v=v[part]
            metrics[name]={'value':number(v),'unit':m['unit'],'direction':m['direction']}
            if m['direction'] not in ('lower','higher'):raise ValueError('metric direction required')
        except (ValueError,KeyError,TypeError,OSError) as e:reasons.append('metric '+name+': '+str(e))
    if not metrics:reasons.append('no measured metrics')
    return {'id':cell['id'],'arm':cell['arm_id'],'pool':cell['pool'],'metrics':metrics,
            'timings_seconds':{k:number(v) for k,v in s.get('timings_seconds',{}).items()},
            'accepted':not reasons,'reasons':reasons,'limitations':s.get('limitations',[]),'sample':ref}


def costs(path):
    if not path:return {'status':'not supplied; not zero cost'}
    s=load(path);rows=[];seen=set()
    for r in s['resources']:
        if r['id'] in seen:raise ValueError('one aggregate ownership interval per resource required')
        seen.add(r['id'])
        start=datetime.fromisoformat(r['created_at'].replace('Z','+00:00'))
        end=datetime.fromisoformat((r.get('deleted_at') or s['as_of_utc']).replace('Z','+00:00'))
        if start.tzinfo is None or end.tzinfo is None:raise ValueError('resource UTC offset required')
        seconds=number((end-start).total_seconds())
        rows.append({'id':r['id'],'seconds':seconds,'released':bool(r.get('deleted_at')),
                     'configuration_estimate':seconds/3600*number(r['hourly_rate']),
                     'currency':r['currency']})
    if len({r['currency'] for r in rows})>1:raise ValueError('report currencies separately')
    invoices=[]
    for item in s.get('bills',[]):
        v=evidence(item['evidence'])
        for part in item['amount_field'].split('.'):v=v[part]
        if type(v) not in (int,float) or not math.isfinite(v):raise ValueError('finite invoice amount required')
        invoices.append({'evidence':item['evidence'],'amount':v,'scope':item['scope']})
    return {'resources':rows,'configuration_estimate':sum(r['configuration_estimate'] for r in rows),
            'bill_evidence':invoices,'status':'configuration estimate; network, billing granularity and adjustments are separate',
            'all_resources_released':all(r['released'] for r in rows) if rows else False}


def paired_blocks(plan, samples):
    """Join frozen condition/repeat identities, irrespective of finish order."""
    by_id={s['id']:s for s in samples};pairs=[]
    for block in plan['blocks']:
        members=[by_id[c] for c in block['cells']]
        complete=all(s['accepted'] for s in members)
        comparisons=[]
        if complete:
            # Only aligned, named metrics within this actual paired block.
            baseline=next((s for s in members if s['arm']=='juicefs'),None)
            if baseline:
                for arm in members:
                    if arm==baseline:continue
                    for key,b in baseline['metrics'].items():
                        a=arm['metrics'].get(key)
                        if a and (a['unit'],a['direction'])==(b['unit'],b['direction']) and a['value']>0 and b['value']>0:
                            comparisons.append({'arm':arm['arm'],'metric':key,'baseline':'juicefs',
                                'advantage_ratio':b['value']/a['value'] if a['direction']=='lower' else a['value']/b['value']})
        pairs.append({'id':block['id'],'pool':block['pool'],'complete_accepted_block':complete,
                      'cells':block['cells'],'comparisons':comparisons})
        if parallel(plan['config']):
            pairs[-1].update(pools=block['pools'],cell_pools=block['cell_pools'],
                placement_mode='baseline-parallel',comparison_scope=(
                    'matched cross-pool operation block; fixed pool assignment does not separate method and cluster effects'
                    if len(block['pools'])>1 else 'matched operation block within one pool'))
    return pairs


def report(db_path, cost_path=None):
    with transaction(db_path) as db:
        plan=plan_of(db)
        states={r['id']:dict(r) for r in db.execute('SELECT * FROM cells')}
        assets=[dict(r) for r in db.execute('SELECT * FROM assets')]
        stages=[dict(r) for r in db.execute('SELECT * FROM stages')]
        qualifications={r['cell_id']:r['receipt'] for r in db.execute('SELECT * FROM qualifications')}
    samples=[]
    for cell in plan['cells']:
        state=states[cell['id']]
        try:
            if not state['result']:raise ValueError('cell not closed: '+state['status'])
            closing=evidence(json.loads(state['result']))
            from .experiment_step import verify
            retained=closing['retention']
            verify(retained['root'],retained['manifest']['path'],retained['manifest']['sha256'])
            from .archive_check import verify as verify_archive
            for archive in closing['archives']:
                if verify_archive(archive['path'],archive['sha256'])['cell_id']!=cell['id']:raise ValueError('archive identity differs')
            value=sample(closing['sample'],plan,cell)
            if state['status']!='complete':value['reasons'].append('cell closed as '+state['status']);value['accepted']=False
            if parallel(plan['config']):
                try:
                    q=evidence(json.loads(qualifications[cell['id']]))
                    for item in q['checks']:predicates(evidence(item['evidence']),item['equals'])
                except (ValueError,KeyError,TypeError,OSError) as e:
                    value['reasons'].append('qualification: '+str(e));value['accepted']=False
        except (ValueError,KeyError,TypeError,OSError) as e:
            value={'id':cell['id'],'arm':cell['arm_id'],'accepted':False,'reasons':[str(e)],'metrics':{}}
        value['pool']=cell['pool'];value['operation_status']=state['status'];samples.append(value)
    pairs=paired_blocks(plan,samples)
    preparation=[]
    for asset in assets:
        ref=json.loads(asset['receipt']);v=evidence(ref)
        for check in v['stages'].values():predicates(evidence(check['evidence']),check['equals'])
        preparation.append({'asset':asset['id'],'receipt':ref,'timings_seconds':v['timings_seconds']})
    return {'schema':'suite-report-v1','plan_sha256':plan['plan_sha256'],'samples':samples,'blocks':pairs,
            'accepted_blocks':sum(b['complete_accepted_block'] for b in pairs),'preparation_once_per_asset':preparation,
            'original_stages':stages,'costs':costs(cost_path),
            'policy':'never pool interrupted samples, partial blocks, native paths as pure ablations, or sum parallel wall intervals; retain raw phase distributions'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',required=True);p.add_argument('--costs');p.add_argument('--output',required=True)
    a=p.parse_args();write_new(a.output,report(a.db,a.costs))
