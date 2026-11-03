"""Small preparation/evidence helpers. No allocation, workload replay or cleanup."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from .experiment_io import digest, load, write_new
from .experiment_step import prepare
from .suite_plan import check_plan, parallel
from .suite_state import reference, evidence, predicates


def manifest(files):
    if not files or len(files)!=len(set(files)):raise ValueError('explicit unique files required')
    result=[]
    for name in files:
        p=Path(name)
        if not p.is_file() or p.is_symlink():raise ValueError('regular input required')
        if any(word in p.name.lower() for word in ('secret','credential','.pem','id_ed25519','id_rsa','.env')):
            raise ValueError('keep credentials outside shareable manifests')
        result.append(dict(reference(p),bytes=p.stat().st_size))
    return {'schema':'suite-input-manifest-v1','files':result}


def drafts(plan, output):
    check_plan(plan);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    write_new(out/'queues.json',{'plan_sha256':plan['plan_sha256'],
        'placement_mode':'baseline-parallel' if parallel(plan['config']) else 'paired-pool',
        'pools':[{'pool':p['id'],'cells':[c['id'] for c in plan['cells'] if c['pool']==p['id']],
                  'assets':[a['id'] for a in plan['assets'] if a['pool']==p['id']]} for p in plan['config']['clusters']],
        'instruction':'queue is a preview; claim the original shared controller database before executing a cell'})
    for cell in plan['cells']:
        pool=next(p for p in plan['config']['clusters'] if p['id']==cell['pool'])
        folder=out/cell['id'];folder.mkdir()
        write_new(folder/'cell.json',cell)
        write_new(folder/'qualification.draft.json',{'schema':'suite-qualification-v1',
            'plan_sha256':plan['plan_sha256'],'cell_id':cell['id'],
            'checks':[{'gate':g,'evidence':None,'equals':None} for g in cell['required_gates']]})
        write_new(folder/'sample.draft.json',{'schema':'suite-sample-v1','plan_sha256':plan['plan_sha256'],
            'cell_id':cell['id'],'uninterrupted':False,'recovered':False,
            'checks':{k:{'evidence':None,'equals':None} for k in
                ('work','correctness','isolation','remote_readback','drain','real_exit','cache_policy','observer_overhead')},
            'required_observer_ids':pool['observer_ids'], 'required_marks':cell['observation_marks'],
            'observers':[], 'metrics':{},'timings_seconds':{},'limitations':[],
            'instruction':'bind actual receipts; uninterrupted=false until original complete sequence is independently verified'})
        write_new(folder/'stages.draft.json',{'cell_id':cell['id'],
            'stages':{s:{'schema':'experiment-step-v1','reviewed':False,'cell_id':cell['id'],
                          'stage':s,'argv':[],'cwd':None,'executable_sha256':None,
                          'inputs':[],'inherit_environment':[]} for s in cell['stages']}})
        params=cell['params'];kind=cell['workload'];items=[]
        agents=1 if kind=='fork' else params['moved_agents'] if kind=='handoff' else params['agents']
        for agent in range(agents):
            for workspace in range(params['workspaces'] if kind=='smallfiles' else 1):
                item={'agent_index':agent,'evidence':{'path':None,'sha256':None}}
                if kind=='smallfiles':
                    item.update(workspace_index=workspace,raw_logs=[
                        {'path':None,'sha256':None,'expected_name':f'worker-{n}.csv'} for n in range(params['workers'])])
                if kind=='fio':item['raw']={'path':None,'sha256':None}
                if kind=='trace':item['source_metrics']={'path':None,'sha256':None}
                items.append(item)
        write_new(folder/'work-packet.draft.json',{'cell_id':cell['id'],'results':items})
    for asset in plan['assets']:
        write_new(out/('asset-'+asset['id']+'.draft.json'),{'schema':'suite-asset-v1',
            'plan_sha256':plan['plan_sha256'],'asset_id':asset['id'],'native_binding':None,
            'timings_seconds':{},'stages':{s:{'evidence':None,'equals':None} for s in asset['required_stages']}})


def review(path, reviewer, note):
    """Explicit AI/operator review receipt; refresh nested pins BEFORE this call."""
    if not reviewer.strip() or len(note.strip())<12:raise ValueError('reviewer and concrete review note required')
    s=load(path)
    if s.get('reviewed') is not False:raise ValueError('review an unreviewed draft into a NEW file')
    s['reviewed']=True
    s['review']={'reviewer':reviewer,'note':note,'utc':datetime.now(timezone.utc).isoformat(),
                 'draft':reference(path)}
    # The CLI writes a new spec, never mutates the draft or its parents' pins.
    return s


def closure(request):
    from .runtime.common import alive, mounts
    import uuid
    s=load(request)
    if s.get('schema')!='suite-closure-request-v1' or not s.get('cell_id'):
        raise ValueError('cell-bound closure request required')
    if not s.get('processes') or not s.get('mount_points') or len(s.get('debt_samples',[]))<2:
        raise ValueError('explicit original process identities, mount list and two native debt samples required')
    for p in s['processes']:
        if type(p.get('pid')) is not int or p['pid']<=0 or type(p.get('start_ticks')) is not int or p['start_ticks']<=0 or not uuid.UUID(p['boot_id']).int:
            raise ValueError('actual original PID/boot/start-tick identity required')
    processes=[dict(p,alive=alive(p)) for p in s['processes']]
    points=[]
    for point in s['mount_points']:
        if not Path(point).is_absolute() or point=='/':raise ValueError('dedicated absolute mount point required')
        points.append({'point':point,'mounts':mounts(point)})
    times=[]
    for sample in s['debt_samples']:
        value=evidence(sample['evidence'])
        # Native field names differ. Bind exact counters and their target ID in
        # the reviewed request, not a generic HTTP-200 health response.
        predicates(value,sample['equals'])
        for field in (sample['pending_bytes_field'],sample['pending_operations_field']):
            predicates(value,{field:0})
        observed=datetime.fromisoformat(sample['observed_at'].replace('Z','+00:00'))
        if observed.tzinfo is None:raise ValueError('timestamp timezone required')
        times.append(observed)
    if any((b-a).total_seconds()<1 for a,b in zip(times,times[1:])):raise ValueError('distinct ordered zero-debt observations required')
    return {'schema':'suite-closure-v1','cell_id':s['cell_id'],'utc':datetime.now(timezone.utc).isoformat(),
            'all_exited':not any(p['alive'] for p in processes),'processes':processes,
            'all_unmounted':not any(p['mounts'] for p in points),'mount_points':points,
            'pending_upload_bytes':0,'pending_upload_operations':0,'debt_samples':s['debt_samples'],
            'request':reference(request),'scope':'listed identities only; reviewer must enumerate all owned descendants and mounts'}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('pin');a.add_argument('--file',required=True)
    a=sub.add_parser('manifest');a.add_argument('--files',nargs='+',required=True);a.add_argument('--output',required=True)
    a=sub.add_parser('drafts');a.add_argument('--plan',required=True);a.add_argument('--output',required=True)
    a=sub.add_parser('review');a.add_argument('--spec',required=True);a.add_argument('--reviewer',required=True);a.add_argument('--note',required=True);a.add_argument('--output',required=True)
    a=sub.add_parser('closure');a.add_argument('--request',required=True);a.add_argument('--output',required=True)
    a=p.parse_args()
    if a.action=='pin':
        import json
        print(json.dumps(reference(a.file)));return
    if a.action=='drafts':drafts(load(a.plan),a.output);return
    result=manifest(a.files) if a.action=='manifest' else review(a.spec,a.reviewer,a.note) if a.action=='review' else closure(a.request)
    write_new(a.output,result)
    if a.action=='review' and result['schema']=='experiment-step-v1':prepare(a.output,digest(a.output))


if __name__=='__main__':main()
