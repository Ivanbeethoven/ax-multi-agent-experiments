#!/usr/bin/env python3
"""Check only live-touched Packed C3 calibration; never certify a formal cold proof."""
import argparse
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.observe import overhead

def ref(p):return dict(path=str(p),sha256=digest(p))
def checked_path(pin,root=None):
    path=Path(pin['path'])
    if root is not None and str(path).startswith('/srv/ax-client/'):
        path=root/'raw/client'/path.relative_to('/srv/ax-client')
    if digest(path)!=pin['sha256']:raise ValueError('Original calibration proof changed')
    return path
def original(pin,root=None):return load(checked_path(pin,root))

def native(pin,root):
    result=original(pin,root)
    if result.get('passed') is not True or len(result.get('samples',[]))!=2:raise ValueError('Two actual native samples required')
    samples=[original(p,root) for p in result['samples']]
    if samples[1]['monotonic_ns']-samples[0]['monotonic_ns']<1000000000:raise ValueError('Native samples too close')
    for sample in samples:
        raw=original(sample['native_status'],root)
        if raw['descriptor']['state']!='Quiescent' or raw['descriptor']['active_lease'] is not None:raise ValueError('Original namespace not quiescent')
        if any(type(raw['debt'].get(k)) is not int or raw['debt'][k]!=0 for k in ('bytes','objects')):raise ValueError('Nonzero or missing native upload debt')
        if sample['native_authority']!=samples[0]['native_authority']:raise ValueError('Unstable native identity')
    return samples[0]['native_authority']

def trial(root,mode,metric,workload,reused):
    finished=load(root/'finished.json')
    if finished.get('passed') is not True or finished.get('verification_mode')!='live-touched-v1' or finished.get('full_namespace_cold') is not False:
        raise ValueError('Explicit live-only completed calibration required')
    if any(key in finished for key in ('verify_exit','parent_verify','cold_child')):raise ValueError('A live trial must not claim its own cold reader')
    if finished['full_cold_qualification']!=reused:raise ValueError('Different full qualification reused')
    full=original(reused)
    if full['passed'] is not True:raise ValueError('Original full cloud qualification failed')
    params=load(workload)['params']
    for name in ('cold_child','cold_parent'):
        proof=original(full['proofs'][name])
        fields=('passed','expected_tree_equal','expected_hardlinks_equal','namespace_unchanged','authority_unchanged','volume_rows_unchanged')
        if not all(proof.get(k) is True for k in fields) or proof['files']!=params['files'] or proof['logical_file_bytes']!=params['files']*4096:
            raise ValueError('Incomplete retained full cold qualification')
    verify=original(finished['live_verification']);live=original(verify['live_check'])
    if verify.get('full_namespace_cold') is not False or live.get('full_namespace_cold') is not False or live['checked_files']!=params['cow_files'] or live['checked_bytes']!=params['cow_files']*4096:
        raise ValueError('Every touched byte must be checked live')
    command=original(ref(Path(verify['live_check']['path']).parent/'command.json'))
    if command['binary'] not in load(workload)['binary_pins'] or command['argv'][1]!='verify-touched':raise ValueError('Actual original touched checker binary required')
    checked=original(live['bytes_check'],root);exited=original(live['normal_check_exit'],root)
    props=original(live['properties'],root)
    if len(props)!=params['cow_files'] or len({p['index'] for p in props})!=len(props) or any(p['properties']['bytes']!=4096 or p['properties']!=p['source_properties'] for p in props):raise ValueError('Every touched file property check required')
    state=checked_path(live['state'],root).read_text().splitlines()
    if not state or state[0]!=f"smallfiles-v1\t{params['files']}\t1" or len(state)!=len(props)+1:raise ValueError('Wrong original touched state')
    rows=[tuple(map(int,line.split('\t'))) for line in state[1:]]
    if len(set(rows))!=len(rows) or set(rows)!={(p['index'],p['version']) for p in props} or command['state']!=live['state']:raise ValueError('Property and original byte-check state differ')
    if checked.get('passed') is not True or checked.get('completed') is not True or checked['checked_files']!=params['cow_files'] or exited['exit_code']!=0:raise ValueError('Original touched check did not finish normally')
    before=original(verify['parent_before']);after=native(verify['parent_after'],root);child=native(verify['child_status'],root)
    if before['native_authority']!=after or after['lower']!=child['lower']:raise ValueError('Parent identity or lower binding changed')
    if child['namespace_id']!=verify['namespace'] or child['namespace_id']==after['namespace_id']:raise ValueError('Fresh original child required')
    stopped=original(finished['normal_exit'])
    if stopped['passed'] is not True or stopped['exit_code']!=0 or not stopped['mount_absent'] or stopped['anchor']['workspace']!=verify['namespace']:raise ValueError('Wrong original normal stop')
    closed=load(root/'closure.json')
    if not all(closed[k] is True for k in ('passed','all_exited','all_unmounted')) or closed['pending_upload_bytes']!=0 or closed['pending_upload_operations']!=0:raise ValueError('Original owned processes or uploads remain')
    if original(finished['work_check'])['passed'] is not True:raise ValueError('Original eight-phase CSV check failed')
    for archive in finished['archives']:pinned(archive)
    cohort=load(root/'cohort.json');result=original(finished['cohort'])
    if any(w['node_id']!='pool-a-client' for w in cohort['workers']):raise ValueError('Wrong calibration pool')
    if cohort['metric_contract']['sha256']!=digest(metric) or cohort['workload_contract']['sha256']!=digest(workload):raise ValueError('Fixed measurement contracts changed')
    expected={'pool-a-'+r for r in ('client','metadata','objects')} if mode=='on' else set()
    if {o['id'] for o in cohort['observers']}!=expected:raise ValueError('Wrong observer roles/mode')
    if mode=='on':
        for role in ('client','metadata','objects'):
            if load(root/'observer-stop'/('pool-a-'+role)/'raw-audit.json')['passed'] is not True:raise ValueError('Missing original role audit')
    return dict(mode=mode,root=str(root),finished=ref(root/'finished.json'),namespace=verify['namespace'],volume=verify['volume'],
                makespan_seconds=result['coordinator_makespan_seconds'],task_seconds=[j['seconds'] for j in result['jobs']],
                verification_mode='live-touched-v1',full_namespace_cold=False)

def check(pairs,metric,workload):
    if len(pairs)!=3:raise ValueError('Exactly three fixed pairs required')
    seen=set();namespaces=set();volumes=set();details=[];schedule=[];reused=None
    for pair in pairs:
        row={}
        for mode in ('off','on'):
            root=Path(pair[mode]).resolve(strict=True)
            if root in seen:raise ValueError('No calibration trial reuse')
            seen.add(root);finished=load(root/'finished.json')
            if reused is None:reused=finished['full_cold_qualification']
            value=trial(root,mode,metric,workload,reused)
            if value['namespace'] in namespaces or value['volume'] in volumes:raise ValueError('Six fresh namespaces and volumes required')
            namespaces.add(value['namespace']);volumes.add(value['volume']);details.append(value);row[mode]=ref(root/'cohort/result.json')
        schedule.append(row)
    return dict(schema='experiment-observer-overhead-plan-v1',reviewed=True,metric_contract_sha256=digest(metric),
                workload_contract_sha256=digest(workload),max_relative_change=.05,pairs=schedule),details,reused

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('pairs','metric-contract','workload-contract','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();plan,details,reused=check(load(a.pairs),a.metric_contract,a.workload_contract)
    a.out.mkdir(parents=True,exist_ok=False);write_new(a.out/'plan.json',plan);result=overhead(ref(a.out/'plan.json'))
    result.update(trial_details=details,pair_selection=ref(a.pairs),verification_mode='live-touched-v1',full_namespace_cold=False,
        reused_full_cold_qualification=reused,limitation='Calibration only: unchanged complete eight phases, all touched bytes and original native closure. This trial has no complete namespace cold proof; initial qualification and formal cells retain their own complete cold checks.')
    write_new(a.out/'result.json',result);print(json.dumps(dict(passed=result['passed'],relative_changes=result['relative_changes'])))
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
