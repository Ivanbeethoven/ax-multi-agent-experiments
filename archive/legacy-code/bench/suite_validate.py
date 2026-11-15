"""Validate native work receipts against one frozen cell; does not rerun work."""
import argparse
import csv
from collections import Counter
from pathlib import Path

from .experiment_io import load, write_new
from .suite_state import evidence, predicates
from .suite_plan import check_plan, fingerprint
from .runtime.workloads import counts


def work(plan, cell_id, packet):
    check_plan(plan);cell=next(c for c in plan['cells'] if c['id']==cell_id)
    params=cell['params'];kind=cell['workload'];items=packet['results']
    expected=params['agents']*(params['workspaces'] if kind=='smallfiles' else 1)
    if kind=='fork':expected=1  # One burst receipt covers all native child commands.
    if kind=='handoff':expected=params['moved_agents']
    if len(items)!=expected or len({x['evidence']['path'] for x in items})!=len(items):
        raise ValueError('distinct complete per-agent/workspace results required')
    if kind=='smallfiles':
        identities=[(x['agent_index'],x['workspace_index']) for x in items]
        if any(type(a) is not int or type(w) is not int for a,w in identities) or set(identities)!={
                (a,w) for a in range(params['agents']) for w in range(params['workspaces'])}:
            raise ValueError('complete distinct agent/workspace identities required')
    totals=Counter()
    for item in items:
        v=evidence(item['evidence'])
        if kind=='smallfiles':
            predicates(v,counts(params))
            predicates(v,{k:params[k] for k in ('workers','hot_files','cow_files','read_ops','write_ops','mixed_ops')})
            predicates(v,{'seed':plan['config']['seed'],
                          'workspace':1+item['agent_index']*params['workspaces']+item['workspace_index']})
            phases={p['phase'] for p in v['phases']}
            if len(v['phases'])!=8 or phases!={'cold-read','lower-prewarm','hot-read','cow','upper-prewarm','hot-write','upper-rewarm','mixed'}:
                raise ValueError('small-file phase coverage differs')
            actual=Counter();per_phase={p:Counter() for p in phases}
            logs=item['raw_logs']
            if len(logs)!=params['workers'] or len({r['path'] for r in logs})!=len(logs):raise ValueError('all raw worker streams required')
            if {Path(r['path']).name for r in logs}!={f'worker-{n}.csv' for n in range(params['workers'])}:
                raise ValueError('raw worker identities differ')
            for ref in logs:
                # Raw CSVs are hashed before reading, just like JSON evidence.
                from .runtime.common import pinned
                with pinned(ref).open(newline='') as f:
                    seen=Counter()
                    for row in csv.DictReader(f):
                        phase=row['phase']
                        if phase not in phases or int(row['operation'])!=seen[phase]:raise ValueError('raw operation gap/duplicate')
                        seen[phase]+=1
                        rb,wb=int(row['read_bytes']),int(row['write_bytes'])
                        if (rb,wb) not in ((4096,0),(0,4096)):raise ValueError('raw byte count differs')
                        if phase in ('cow','hot-write') and rb or phase not in ('cow','hot-write','mixed') and wb:
                            raise ValueError('raw read/write phase differs')
                        if phase=='mixed' and bool(wb)!=(int(row['operation'])%4==3):
                            raise ValueError('raw mixed operation order differs')
                        actual['verified_reads']+=int(rb>0);actual['writes']+=int(wb>0)
                        actual['actual_read_bytes']+=rb;actual['actual_write_bytes']+=wb
                        per_phase[phase]['reads']+=int(rb>0);per_phase[phase]['writes']+=int(wb>0)
                        if not 0<=int(row['file'])<params['files'] or min(int(row['io_ns']),int(row['application_ns']))<0:
                            raise ValueError('invalid raw identity/timing')
                    if set(seen)!=phases:raise ValueError('missing worker phase')
                    expected_phase={'cold-read':params['read_ops'],'lower-prewarm':2*params['hot_files'],
                        'hot-read':params['read_ops'],'cow':params['cow_files'],
                        'upper-prewarm':2*params['cow_files'],'hot-write':params['write_ops'],
                        'upper-rewarm':2*params['cow_files'],'mixed':params['mixed_ops']}
                    if any(seen[p]!=n//params['workers'] for p,n in expected_phase.items()):
                        raise ValueError('raw fixed work per phase differs')
            for k,n in actual.items():
                if v[k]!=n:raise ValueError('raw operations differ from native summary')
            for phase in v['phases']:
                for k in ('reads','writes'):
                    if phase[k]!=per_phase[phase['phase']][k]:raise ValueError('raw phase differs')
            totals.update(actual)
        elif kind=='fio':
            predicates(v,{'completed':True,'passed':True,'phase':'measure','jobs':params['numjobs'],
                          'application_bytes':params['numjobs']*params['io_bytes_per_job'],
                          'params':params,'seed':plan['config']['seed'],'raw_sha256':item['raw']['sha256']})
            raw=evidence(item['raw'])
            from .fio_workload import summarize, validate_raw
            summarize(raw,params['numjobs'],v['application_bytes'],0,v['wrapper_wall_seconds'])
            validate_raw(raw,params,plan['config']['seed'],'measure')
            totals['application_bytes']+=v['application_bytes']
        elif kind=='trace':
            source=evidence(item['source_metrics'])
            predicates(v,{'completed':True,'mode':'performance','valid':False,'remaining_handles':0,
                          'operations':source['callbacks'],'actual_read_bytes':source['actual_read_bytes'],
                          'actual_write_bytes':source['actual_write_bytes']})
            for k in ('operations','actual_read_bytes','actual_write_bytes'):totals[k]+=v[k]
        elif kind=='fork':
            predicates(v,{'completed':True,'passed':True,'children':params['children']})
            if v.get('schema')=='c4-fork-work-v1':
                from .runtime.common import pinned
                from .runtime.fork_work import validate_result
                validate_result(v,load(pinned(item['clock'])),params)
                if v['clock']!=item['clock_source'] or v['clock']['sha256']!=item['clock']['sha256']:
                    raise ValueError('Original fork clock differs from its verified retained copy')
            elif len(v['results'])!=params['children'] or any(r.get('exit_code')!=0 for r in v['results']):raise ValueError('fork child completion differs')
            totals['children']+=v['children']
        else:
            predicates(v,{'passed':True,'mode':cell['arm']['handoff_mode']})
            if not v.get('session_validation') or v['first_completed_tool_ack_seconds']<=0:raise ValueError('native continuation evidence missing')
            totals['native_sessions']+=1
    return {'schema':'suite-work-check-v1','passed':True,'cell_id':cell_id,'workload':kind,
            'workload_signature':fingerprint(params),'results':items,'totals':dict(totals),
            'scope':'native work/byte coverage; independent correctness, isolation, remote readback and telemetry still required'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('plan','cell','packet','output'):p.add_argument('--'+key,required=True)
    a=p.parse_args();write_new(a.output,work(load(a.plan),a.cell,load(a.packet)))
