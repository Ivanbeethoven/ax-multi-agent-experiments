#!/usr/bin/env python3
"""One C4 base, six fresh calibration children and one admitted formal child.

The original campaign's physical-resource guards cover this entire operation;
the new C4 SQLite database never supplies a second independent lock namespace.
An explicitly reviewed pretrial reuse can retain the original closed flat base
after a capacity-only failure; the trial and formal paths remain identical.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import load,pinned,digest,write_new
from bench.runtime import c4_verify,pilot_context
from bench.runtime.pilot_pools import active_conflicts,resource_guard,alias,cell_resources,fleet_node
from bench import suite_state
from bench.suite_plan import check_plan

REUSE_CELL='c5e97774bcffa69a-r1-native'
ROLES=('client','metadata','objects')
MANAGER_FIELDS=('LoadState','InvocationID','ActiveState','SubState','MainPID','ControlPID','Result','ExecMainStatus')
SCRIPT_DIR=Path('experiments/unified-suite/pilot-aliyun/scripts')


def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))

def require(ok,message):
    if not ok:raise ValueError(message)

def local_document(pin,expected=None):
    path=pinned(pin)
    require(path.resolve(strict=True)==path and path.is_file() and
            (expected is None or path==expected),'Changed or misplaced reuse input: '+str(path))
    return load(path)

def absent(path):
    require(not path.exists() and not path.is_symlink() and path.resolve()==path,
            'Already used or unsafe pretrial path: '+str(path))

def evidence_time(value):
    t=datetime.fromisoformat(value)
    require(t.tzinfo is not None,'Timezone required in reuse evidence')
    return t

def recent(value,clock):
    stamp=evidence_time(value)
    require(0 <= (clock-stamp).total_seconds() <= 900,'Missing fresh pretrial evidence')
    return stamp

def remote_ref(value,expected=None):
    result={k:value[k] for k in ('path','sha256')}
    path=Path(result['path'])
    require(path.is_absolute() and '..' not in path.parts and
            (expected is None or path==expected) and re.fullmatch('[a-f0-9]{64}',result['sha256']),
            'Wrong remote preparation evidence identity')
    return result

def load_reuse(pin,request,s):
    r=local_document(pin)
    require(set(r)=={'schema','reviewed','new_batch','original_batch','original_outer','quiescence',
                    'capacity','capacity_producer','recovery_proof','runner'} and
            r['schema']=='c4-pretrial-reuse-v1' and r['reviewed'] is True and r['new_batch']==request and
            pinned(r['runner'])==Path(__file__).resolve(),'Reviewed, runner-bound C4 pretrial reuse request required')
    old=local_document(r['original_batch'])
    original=Path(old['out']);out=Path(s['out'])
    require(s==dict(old,out=str(out)) and out!=original and
            original.parent==out.parent==Path(s['control']) and s['initialize_state'] is False and
            s['cell_id']==REUSE_CELL and s['calibration_verification']==c4_verify.TOUCHED,
            'Reuse may change only the output of the original A 100k flat batch')
    absent(out)
    return r,original

def baseline_context(s,baseline,reuse):
    request=pinned(s['preparation_request'])
    args=SimpleNamespace(request=request,sha256=s['preparation_request']['sha256'],runtime_repo=REPO)
    if reuse is None:return baseline.validate_request(args)
    original=load(request)
    # The frozen validator requires an unused output even though validation
    # never runs the worker. Change only its unused output in a scratch copy.
    with TemporaryDirectory(prefix='ax-c4-pretrial-check-') as scratch:
        directory=Path(scratch).resolve();shadow=directory/'request.json'
        write_new(shadow,dict(original,out=str(directory/'unused-output')))
        args.request=shadow;args.sha256=digest(shadow)
        args.runtime_repo=Path(s['tools']['baseline']['path']).parents[4]
        checked=baseline.validate_request(args)
    checked.update(s=original,out=Path(original['out']),request_path=request)
    return checked


def original_stop(s,r,old,validated,control):
    outer=local_document(r['original_outer']);outer_out=Path(outer['out'])
    original_repo=Path(s['tools']['run']['path']).parents[4]
    runner=original_repo/SCRIPT_DIR/'run-c4-batch.py'
    manifest=load(pinned(outer['manifest']))
    pinned(dict(path=str(runner),sha256=manifest[str(SCRIPT_DIR/'run-c4-batch.py')]))
    require(outer['schema']=='c4-native-flat-continuation-v1' and outer['reviewed'] is True and
            outer['database']==validated['w']['database'] and
            r['original_batch']==ref(outer_out/'batch.request.json') and
            s['preparation_request']==ref(outer_out/'base.request.json'),
            'Original failed outer invocation does not own this C4 batch and preparation')
    outer_intent=load(outer_out/'batch/intent.json')
    require(outer_intent['argv'][1:]==[str(runner),'--request',r['original_batch']['path'],'--sha256',
                                      r['original_batch']['sha256']] and
            load(outer_out/'batch/exit.json')=={'exit_code':1} and
            load(outer_out/'failed.json')==dict(passed=False,error='Preserve original failure: '+str(outer_out/'batch'),
                                               automatic_retry=False),'Outer C4 did not terminate at its original batch')
    absent(outer_out/'result.json')
    locks=load(old/'locks-held.json')
    require(load(old/'request.json')==load(pinned(r['original_batch'])) and
            locks['request']==r['original_batch'] and locks['canonical_control']==str(validated['control']) and
            locks['new_state']==str(control/'state.sqlite') and locks['pool']=='pool-a' and
            locks['resource_ids']==sorted(validated['wanted']),
            'Original batch did not run with the same plan and physical ownership')
    before,after=ref(old/'capacity-before.json'),ref(old/'capacity-after.json')
    capacity=ref(old/'capacity.json')
    intent=load(old/'capacity-budget/intent.json')
    failure=load(old/'failed.json')
    require(load(old/'capacity-before/exit.json')==load(old/'capacity-after/exit.json')=={'exit_code':0} and
            load(old/'capacity-budget/exit.json')=={'exit_code':2} and
            intent==dict(argv=['python3',s['tools']['capacity']['path'],'budget','--before',before['path'],
                              '--after',after['path'],'--extra-full-copies','7','--reserve','.2',
                              '--out',capacity['path']],automatic_retry=False) and
            failure['error']=='Inspect original failed C4 substep '+str(old/'capacity-budget') and
            failure['passed'] is False and failure['automatic_retry'] is False,
            'Original run must have stopped only at its unmodified capacity budget exit 2')
    for stage in ('qualification','overhead','admit','formal',*(f'trial-{i}' for i in range(1,7))):
        absent(old/stage);absent(old/(stage+'-request.json'))
    for name in ('route.json','trials.json','result.json'):
        absent(old/name)
    for path in (Path(s['qualification']),*(Path(t['out']) for t in s['trials']),
                 control/'cells'/s['cell_id'],control/'cells'/(s['cell_id']+'.close.json'),
                 control/'cells'/(s['cell_id']+'.retention.json')):
        absent(path)
    return before,after,capacity


def validate_prepared(s,validated,after):
    w=validated['w'];directory=validated['out']
    worker=load(directory/'worker-result.json')
    prepared=load(directory/'result.json')
    locked=load(directory/'locks-held.json')
    remote=after['nodes']['client']['preparation']
    prep=remote_ref(remote,Path(w['out'])/'result.json')
    require(prepared['passed'] is True and prepared['cell_id']==s['cell_id'] and prepared['pool']=='pool-a' and
            prepared['formal_admission'] is False and prepared['performance_accepted'] is False and
            prepared['remote_evidence']==w['out'] and prepared['result']==ref(directory/'worker-result.json') and
            prepared['lock']==ref(directory/'locks-held.json') and load(directory/'request.json')==validated['s'] and
            locked['request']==s['preparation_request'] and locked['canonical_control']==str(validated['control']) and
            locked['pool']=='pool-a' and locked['resource_ids']==sorted(validated['wanted']) and
            load(directory/'exit.json')=={'exit_code':0} and remote['value']==worker,
            'Original coordinator preparation did not complete the same worker request')
    require(worker['passed'] is True and worker['purpose']=='asset-preparation' and
            worker['pool']=='pool-a' and worker['product']=='brewfs' and worker['cell_id']==s['cell_id'] and
            worker['files']==100000 and worker['bytes']==409600000 and worker['work_tag']=='child-01' and
            worker['formal_admission'] is False and worker['performance_accepted'] is False and
            worker['source_tree']==w['source_tree'] and worker['fixture']==w['fixture'],
            'Original full C4 flat base and fixture do not match')
    remote_ref(worker['asset'],Path(s['asset'])/'asset.json')
    remote_ref(worker['cold_base_check'],Path(w['out'])/'cold-base-check.json')
    normal=worker['normal_exits']
    require(len(normal)==len(remote['normal_exits'])==2,'Both original base mount exits are required')
    for i,(pin,receipt) in enumerate(zip(normal,remote['normal_exits']),1):
        value=receipt['value']
        require(pin==remote_ref(receipt,Path(w['out'])/f'normal-stop-{i}.json') and value['passed'] is True and
                value['mount_absent'] is True and value['exit_code']==0,'Original base mount did not close normally')
    return prep,worker


def validate_quiescence(r,old,s,validated,controls,after_pin,prep,worker,clock):
    q=local_document(r['quiescence']);stamp=recent(q['observed_at'],clock)
    require(q['schema']=='c123-pool-a-retention-quiescence-v1' and q['passed'] is True and q['reviewed'] is True and
            q['pool']=='pool-a' and set(q['resource_ids'])==set(validated['wanted']) and
            all(q[k] is True for k in ('no_active_pool_lease','agent_slot_empty','all_workload_processes_exited','all_mounts_absent')) and
            all(type(q[k]) is int and q[k]==0 for k in ('pending_upload_bytes','pending_upload_operations')),
            'Fresh original three-role quiescence with zero debt required')
    directory=Path(r['quiescence']['path']).parent
    expected={old/'failed.json',Path(after_pin['path']),directory/'boundary.json'}
    pins={Path(v['path']):v for v in q['source_pins']}
    require(len(pins)==len(q['source_pins'])==3 and set(pins)==expected and pins[Path(after_pin['path'])]==after_pin,
            'Quiescence must bind the exact original stop, capacity and fresh boundary')
    for pin in pins.values():pinned(pin)
    b=local_document(pins[directory/'boundary.json'])
    require(b['source_batch']==r['original_batch'] and b['source_outer']==r['original_outer'] and
            b['runtime']==s['runtime_control'] and set(map(Path,b['controls']))==set(controls) and
            set(b['live'])==set(ROLES),'Boundary belongs to another batch, runtime, lease inventory or role set')
    for role,row in b['live'].items():
        v=row['result']
        require(row['role']==role and re.fullmatch('[a-f0-9]{64}',row['script_sha256']) and
                v['safe_for_release'] is True and not any(v[k] for k in
                    ('live','unknown_startup_directories','slot','mounts','original_processes')),
                'Original trial processes or mounts are not proven absent: '+role)
    closed=b['closed_base'];v=closed['result']
    prep_dir=Path(prep['path']).parent
    require(closed['role']=='client' and re.fullmatch('[a-f0-9]{64}',closed['script_sha256']) and
            v['passed'] is True and v['sources'][:4]==[prep,worker['cold_base_check'],*worker['normal_exits']] and
            [remote_ref(pin)['path'] for pin in v['sources'][4:]]==[
                str(prep_dir/f'start-{i}'/'stdout') for i in (1,2)],
            'Original cold check and both preparation process closures required')
    remote_ref(v['debt'],prep_dir/'import-debt/result.json')
    require(len(v['samples'])==len(v['values'])==2,'Two native original debt samples required')
    for pin,value in zip(v['samples'],v['values']):
        remote_ref(pin)
        require(value['passed'] is True and value['cell_id']==s['cell_id'] and
                all(type(value[k]) is int and value[k]==0 for k in ('pending_upload_bytes','pending_upload_operations')) and
                bool(value['native_gauges']) and all(type(x) in (int,float) and x==0 for x in value['native_gauges'].values()),
                'Original closed base retained upload debt')
    require(v['values'][1]['monotonic_ns']-v['values'][0]['monotonic_ns']>=1_000_000_000,
            'Original zero-debt samples are not spaced by one second')
    unit=q['unit'];manager=q['manager'];invocation=manager['InvocationID']
    require(re.fullmatch(r'ax-pilot-pa-[a-z0-9-]+\.service',unit) and
            re.fullmatch('[a-f0-9]{32}',invocation) and int(invocation,16)!=0 and
            manager==b['unit']['show']==dict(LoadState='loaded',InvocationID=invocation,ActiveState='failed',
                SubState='failed',MainPID='0',ControlPID='0',Result='exit-code',ExecMainStatus='1'),
            'Exact original failed manager invocation required')
    start,end=(b['unit'][k] for k in ('start','end'))
    require(all(event['UNIT']==unit and event['INVOCATION_ID']==invocation and event['__CURSOR'] for event in (start,end)) and
            start['_BOOT_ID']==end['_BOOT_ID'] and start['__CURSOR']!=end['__CURSOR'] and
            int(start['__REALTIME_TIMESTAMP']) < int(end['__REALTIME_TIMESTAMP']) <= stamp.timestamp()*1_000_000 and
            all(r['original_outer'][k] in start['MESSAGE'] for k in ('path','sha256')) and
            end['MESSAGE']==unit+": Failed with result 'exit-code'.",'Original outer journal identity differs')
    current=subprocess.check_output(['systemctl','show',unit,*['-p'+k for k in MANAGER_FIELDS]],text=True)
    require(dict(line.split('=',1) for line in current.splitlines())==manager,'Original failed manager invocation changed')
    return q


def integer(value):
    return type(value) is int and value>=0

def capacity_rows(snapshot):
    nodes=snapshot['nodes']
    require(set(nodes)==set(ROLES),'Three-role capacity snapshots required')
    result={}
    for role,n in nodes.items():
        require(len(n['disks'])==(4 if role=='objects' else 1),'Capacity disk membership differs')
        for disk in n['disks']:
            for quantity in ('bytes','inodes'):
                key=(role,disk['path'],quantity);used=disk['used_'+quantity];total=disk['total_'+quantity]
                require(key not in result and integer(used) and integer(total) and 0<=used<=total and total>0,
                        'Invalid native capacity sample')
                result[key]=(used,total)
    redis=nodes['metadata']['redis']
    memory,aof,total=(redis[k] for k in ('used_memory','aof_current_size','maxmemory'))
    require(all(map(integer,(memory,aof,total))) and 0<=memory<=total and total>0,'Invalid Redis capacity sample')
    result['metadata',None,'redis-memory-bytes']=(memory,total)
    result['metadata',None,'redis-aof-bytes']=(aof,None)
    return result

def capacity_identity(snapshot):
    out={}
    for role,n in snapshot['nodes'].items():
        out[role]=[n['boot_id'],[(d['path'],d['device'],d['serial'],d['device_bytes'],d['total_bytes'],d['total_inodes'])
                                for d in n['disks']]]
    obj=snapshot['nodes']['objects'];redis=snapshot['nodes']['metadata']['redis']
    return (out,tuple(obj[k] for k in ('container_id','image','pid')),redis['process_id'],redis['maxmemory'])

def row_map(doc):
    rows=doc['rows']
    keyed={(r['role'],r.get('path'),r['quantity']):r for r in rows}
    require(len(rows)==len(keyed),'Duplicate capacity row')
    return keyed

def validate_capacity(r,s,runtime,pins,before,after,prep,clock):
    before_pin,after_pin,old_pin=pins
    old=local_document(old_pin);cap=local_document(r['capacity'])
    require(old['passed'] is False and old['before']==before_pin and old['after']==after_pin and
            old['extra_full_copies']==7 and old['reserve_fraction']==.2,
            'Original 7-copy/20-percent refusal must remain unchanged')
    require(cap['schema']=='c4-aof-reconciled-capacity-v1' and cap['passed'] is True and
            cap['cell_id']==s['cell_id'] and cap['pool']=='pool-a' and cap['preparation']==prep and
            cap['before']==before_pin and cap['after']==after_pin and cap['original_capacity']==old_pin and
            cap['producer']==r['capacity_producer'] and cap['recovery_proof']==r['recovery_proof'] and
            cap['extra_full_copies']==7 and cap['reserve_fraction']==.2,
            'Independent corrected 7-copy/20-percent capacity for this exact C4 base required')
    needed=[*pins,cap['producer'],cap['recovery_proof'],cap['fresh']]
    require(all(p in cap['source_pins'] for p in needed) and
            len({p['path'] for p in needed})==len(needed) and r['capacity']['path'] not in {p['path'] for p in needed},
            'Capacity receipt does not bind distinct native, original, producer and forensic sources')
    for pin in cap['source_pins']:pinned(pin)
    recovery=local_document(cap['recovery_proof'])
    require(recovery['schema']=='c4-aof-recovery-proof-v1' and recovery['passed'] is True and
            recovery['cell_id']==s['cell_id'] and recovery['pool']=='pool-a' and
            recovery['before']==before_pin and recovery['after']==after_pin and recovery['original_capacity']==old_pin and
            all(integer(recovery[k]) and recovery[k]>0 for k in
                ('retirement_bytes','retirement_inodes','retired_aof_logical_bytes')),
            'Verified retirement bounds for the exact original capacity snapshots required')
    for pin in (recovery['journal_evidence'],recovery['verifier'],*recovery.get('source_pins',[])):pinned(pin)
    fresh=local_document(cap['fresh'])
    for snap in (before,after,fresh):
        for pin in snap['source_pins']:pinned(pin)
    require(before['cell_id']==s['cell_id'] and before['files']==100000 and
            ref(Path(s['control'])/'suite/plan.json') in before['source_pins'] and
            all(snap['inventory']==ref(runtime/'fleet.json') and snap['pool']=='pool-a' for snap in (before,after,fresh)) and
            all(before_pin in snap['source_pins'] and s['tools']['capacity'] in snap['source_pins'] for snap in (after,fresh)) and
            capacity_identity(before)==capacity_identity(after)==capacity_identity(fresh) and
            fresh['nodes']['client']['preparation']==after['nodes']['client']['preparation'],
            'Fresh capacity snapshot changed the original runtime, disk, service or preparation')
    fresh_at=recent(fresh['at'],clock);old_at=evidence_time(after['at'])
    require(evidence_time(before['at']) < old_at < fresh_at <= recent(cap['at'],clock) and
            all(old_at < recent(v['sampled_at'],clock)<=fresh_at for v in fresh['nodes'].values()),
            'Fresh native observations must follow the original completed import')
    native=capacity_rows(fresh);baseline=capacity_rows(before);original=capacity_rows(after)
    old_rows,new_rows=row_map(old),row_map(cap)
    require(set(native)==set(baseline)==set(original)==set(old_rows)==set(new_rows),'All native capacity rows are required')
    meta_disk=next(k for k in native if k[0]=='metadata' and k[2]=='bytes')
    meta_inodes=(meta_disk[0],meta_disk[1],'inodes')
    aof=('metadata',None,'redis-aof-bytes')
    require({k for k,v in old_rows.items() if v['passed'] is not True}=={meta_disk,aof},
            'Only the proven metadata-disk/AOF capacity refusal is eligible for reuse')
    for key,row in new_rows.items():
        used,total=native[key];copies=0 if key[0]=='client' else 7
        used=max(used,original[key][0],baseline[key][0])
        increment=row['measured_base_increment']
        minimum=max(0,original[key][0]-baseline[key][0],old_rows[key]['measured_base_increment'])
        if key in (meta_disk,meta_inodes,aof):
            field={meta_disk:'retirement_bytes',meta_inodes:'retirement_inodes',aof:'retired_aof_logical_bytes'}[key]
            corrected=max(0,original[key][0]-baseline[key][0]+recovery[field])
            minimum=max(minimum,corrected+baseline[key][0])
        require(row['passed'] is True and type(row['extra_full_copies']) is int and row['extra_full_copies']==copies and
                integer(increment) and increment>=minimum,
                'Unsafe corrected capacity growth or row status: '+str(key))
        if key==aof:
            projected=row['projected_aof_bytes']
            require(integer(projected) and projected>=used+increment*copies and
                    row['included_in_metadata_disk_forecast'] is True and projected<=new_rows[meta_disk]['projected_used'],
                    'Corrected AOF forecast is not covered by metadata disk capacity')
        else:
            projected=row['projected_used']
            headroom=0
            minimum_headroom=0
            if key==meta_disk:
                headroom=row['rewrite_headroom_bytes']
                minimum_headroom=((new_rows[aof]['projected_aof_bytes']+4095)//4096)*4096+4096*16
                require(headroom==cap['rewrite_headroom_bytes'],'Metadata disk rewrite headroom does not match the receipt')
            if key==meta_inodes:
                headroom=row['rewrite_headroom_inodes'];minimum_headroom=16
            require(integer(headroom) and headroom>=minimum_headroom and
                    integer(projected) and row['total']==total and used+increment*copies+headroom<=projected and
                    5*projected<=4*total,
                    'Corrected native capacity violates the 20-percent reserve: '+str(key))
    return pinned(r['capacity'])


def pretrial_reuse(s,r,old,validated,control,runtime,controls,clock):
    pins=original_stop(s,r,old,validated,control)
    before,after=map(local_document,pins[:2])
    preparation,worker=validate_prepared(s,validated,after)
    validate_quiescence(r,old,s,validated,controls,pins[1],preparation,worker,clock)
    capacity=validate_capacity(r,s,runtime,pins,before,after,preparation,clock)
    return preparation,capacity

def module(path):
    loader=importlib.util.spec_from_file_location('c4_batch_'+Path(path).stem.replace('-','_'),path)
    value=importlib.util.module_from_spec(loader);loader.loader.exec_module(value);return value


def trial_order(trials,formal_attempt):
    if len(trials)!=6 or [t['mode'] for t in trials]!=['off','on','on','off','off','on']:
        raise ValueError('Six predeclared counterbalanced trials required')
    names=[t['attempt'] for t in trials]+[formal_attempt]
    if len(set(names))!=7 or len({t['out'] for t in trials})!=6:
        raise ValueError('Seven unique child paths and six unique trial outputs required')


def trial_verification(policy,index,reference):
    if policy==c4_verify.FULL:return {}
    if policy!=c4_verify.TOUCHED:raise ValueError('Unknown C4 calibration verification policy')
    if index==0:return dict(verification_policy=c4_verify.REFERENCE)
    if not reference:raise ValueError('Original first full C4 trial must pass before touched trials')
    return dict(verification_policy=c4_verify.TOUCHED,full_reference=reference)


def ensure_conflicts(controls,cell,plan):
    for control in controls:
        if active_conflicts(control,cell,plan):
            raise ValueError('A lease still owns this pool or an intersecting resource: '+str(control))


def failure_release_code(root,repo,role,asset,attempts):
    """Read only original identities; absence of a startup receipt is unknown."""
    values=dict(ROOT=root,REPO=repo,ROLE=role,ASSET=asset,ATTEMPTS=attempts)
    return '\n'.join(k+'='+repr(v) for k,v in values.items())+'''
import pathlib,sys,json
root=pathlib.Path(ROOT);sys.path.insert(0,REPO)
from bench.runtime.common import alive,load,mounts
identities=[];unknown=[]
for tag in ATTEMPTS:
 observer=root/'o'/tag
 if observer.exists():
  if (observer/'ready.json').is_file():identities.append(load(observer/'ready.json')['process'])
  else:unknown.append(str(observer))
 if ROLE=='client':
  for suffix in ('','-v'):
   state=root/'m'/(tag+suffix)
   if state.exists():
    if (state/'process.json').is_file():
     p=load(state/'process.json');identities.extend([p['process'],p['supervisor']])
    else:unknown.append(str(state))
  worker=root/'j'/tag
  if worker.exists():
   if (worker/'ready.json').is_file():identities.append(load(worker/'ready.json')['process'])
   else:unknown.append(str(worker))
   for path in worker.glob('job-*/process.json'):identities.append(load(path)['process'])
live=[p for p in identities if p is None or alive(p)]
slot=pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip() if ROLE=='client' else ''
mounted=mounts(str(pathlib.Path(ASSET)/'mnt')) if ROLE=='client' else []
safe=not (live or unknown or slot or mounted)
print(json.dumps(dict(safe_for_release=safe,original_processes=identities,live=live,unknown_startup_directories=unknown,slot=slot,mounts=mounted)))
raise SystemExit(0 if safe else 2)
'''


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--reuse-request');p.add_argument('--reuse-sha256')
    a=p.parse_args();request=dict(path=a.request,sha256=a.sha256);s=load(pinned(request))
    if bool(a.reuse_request)!=bool(a.reuse_sha256):p.error('Both --reuse-request and --reuse-sha256 are required together')
    if s.get('schema')!='c4-fork-batch-v1' or s.get('reviewed') is not True:
        raise ValueError('Reviewed explicit C4 batch required')
    reuse_pin=dict(path=a.reuse_request,sha256=a.reuse_sha256) if a.reuse_request else None
    reuse=load_reuse(reuse_pin,request,s) if reuse_pin else None
    for tool in s['tools'].values():pinned(tool)
    if set(s['tools'])!={'baseline','capacity','prepare','calibrate','check','admit','run'}:
        raise ValueError('Freeze all seven C4 coordinator tools')
    baseline=module(pinned(s['tools']['baseline']))
    validated=baseline_context(s,baseline,reuse)
    canonical=validated['control'];plan=validated['plan'];cell=validated['cell'];pool=cell['pool']
    control=Path(s['control']).resolve(strict=True)
    if (control==canonical or check_plan(load(control/'suite/plan.json'))!=plan or
            digest(control/'provenance/inventory.json')!=validated['s']['inventory']['sha256']):
        raise ValueError('New C4 phase control must bind the actual fork plan and same inventory')
    if s['cell_id']!=cell['id'] or s['asset']!=validated['w']['asset'] or validated['w'].get('mode')!='prepare':
        raise ValueError('One matching once-imported base in preparation mode required')
    if set(cell_resources(plan,cell))!=set(validated['wanted']):raise ValueError('Actual physical resources differ')
    runtime=pilot_context.select(control,s.get('runtime_control'))
    trial_order(s['trials'],s['formal_attempt'])
    verification=s.get('calibration_verification',c4_verify.FULL)
    trial_verification(verification,0,None)
    out=Path(s['out']);formal=control/'cells'/cell['id']
    if out.exists() or formal.exists() or Path(s['qualification']).exists():raise ValueError('Fresh batch/formal/qualification paths required')
    controls=list(dict.fromkeys([*validated['controls'],control]))
    with resource_guard(canonical,cell,plan):
        # The canonical flock also excludes preparation/calibration in the same
        # pool. Other physical pools remain free, regardless of phase/database.
        if reuse and set(controls)!={p.parent for p in canonical.parent.glob('*/state.sqlite')}:
            raise ValueError('Pretrial reuse must check every actual phase lease database')
        if not (control/'state.sqlite').exists():
            if s.get('initialize_state') is not True:raise ValueError('Explicit new phase state creation required')
            suite_state.initialize(control/'state.sqlite',plan)
        ensure_conflicts(controls,cell,plan)
        with suite_state.transaction(control/'state.sqlite') as db:
            status=db.execute('SELECT status FROM cells WHERE id=?',(cell['id'],)).fetchone()
            started=db.execute("SELECT 1 FROM stages WHERE cell_id=? AND status!='pending'",(cell['id'],)).fetchone()
            if reuse:
                state=db.execute('SELECT status,result FROM cells WHERE id=?',(cell['id'],)).fetchone()
                has_stage=db.execute('SELECT 1 FROM stages WHERE cell_id=?',(cell['id'],)).fetchone()
                qualification=db.execute('SELECT 1 FROM qualifications WHERE cell_id=?',(cell['id'],)).fetchone()
                # Frozen SQLite inserts stage rows only after a formal stage starts.
                require(state is not None and tuple(state)==('pending',None) and not has_stage and not qualification,
                        'Original formal C4 must have no stage or qualification records')
        if not status or status[0]!='pending' or started:raise ValueError('Original cell was already started; no automatic retry')
        if reuse:
            r,old=reuse
            preparation,corrected=pretrial_reuse(s,r,old,validated,control,runtime,controls,datetime.now(timezone.utc))
            absent(out)
            raw=corrected.read_bytes()
            require(hashlib.sha256(raw).hexdigest()==r['capacity']['sha256'],'Corrected capacity changed before use')
        out.mkdir(parents=True);write_new(out/'request.json',s)
        write_new(out/'locks-held.json',dict(canonical_control=str(canonical),new_state=str(control/'state.sqlite'),
            pool=pool,resource_ids=sorted(validated['wanted']),at=datetime.now(timezone.utc).isoformat(),request=request))
        if reuse:
            with (out/'capacity.json').open('xb') as stream:stream.write(raw)
            write_new(out/'pretrial-reuse.json',dict(passed=True,reviewed_request=reuse_pin,original_batch=r['original_batch'],
                quiescence=r['quiescence'],preparation=preparation,capacity_source=r['capacity'],
                capacity=ref(out/'capacity.json'),base_reimported=False,automatic_retry=False))
        def run(name,argv):
            directory=out/name;directory.mkdir();argv=list(map(str,argv))
            write_new(directory/'intent.json',dict(argv=argv,automatic_retry=False))
            with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
                code=subprocess.run(argv,stdout=stdout,stderr=stderr).returncode
            write_new(directory/'exit.json',dict(exit_code=code))
            if code:raise RuntimeError('Inspect original failed C4 substep '+str(directory))
        def invoke(name,tool,body):
            path=out/(name+'-request.json');write_new(path,dict(body,reviewed=True))
            run(name,['python3',pinned(s['tools'][tool]),'--request',path,'--sha256',digest(path)])
        cap=['python3',pinned(s['tools']['capacity']),'snapshot','--control',control,'--pool',pool,
             '--objects-container',s['objects_container']]
        if s.get('runtime_control'):cap+=['--runtime-control',s['runtime_control']['path'],
                                         '--runtime-control-sha256',s['runtime_control']['sha256']]
        try:
            if not reuse:
                # Read-only before snapshot precedes the first import. Binding
                # that observation to the frozen cell adds identity, not numbers.
                run('capacity-before',cap+['--out',out/'capacity-before-raw.json'])
                before=load(out/'capacity-before-raw.json');before.update(cell_id=cell['id'],files=cell['params']['files'])
                before['source_pins'] += [ref(out/'capacity-before-raw.json'),ref(control/'suite/plan.json')]
                write_new(out/'capacity-before.json',before)
                baseline.run_remote_locked(validated)
                remote=str(Path(validated['w']['out'])/'result.json')
                actual=subprocess.check_output(['ssh','-F',str(runtime/'ssh.conf'),alias(pool,'client'),
                    shlex.join(['sha256sum',remote])],text=True).split()[0]
                preparation=dict(path=remote,sha256=actual)
                run('capacity-after',cap+['--before',out/'capacity-before.json','--preparation-result',remote,
                                         '--out',out/'capacity-after.json'])
                run('capacity-budget',['python3',pinned(s['tools']['capacity']),'budget','--before',out/'capacity-before.json',
                    '--after',out/'capacity-after.json','--extra-full-copies','7','--reserve','.2','--out',out/'capacity.json'])
            base={k:s[k] for k in ('control','cell_id','asset','qualification','node_tool','observer_tool_name',
                                   'observer_tool_sha256','collector_name','collector_out')}
            if s.get('runtime_control'):base['runtime_control']=s['runtime_control']
            base['fork']=dict(tool=s['fork_tool'],preparation=preparation)
            invoke('qualification','prepare',dict(base,shared_qualification=s['shared_qualification'],hardware=s['hardware'],
                preparation_coordinator_result=ref(validated['out']/'result.json')))
            current=dict(collector_name=s['collector_name'],collector_out=s['collector_out'])
            full_reference=None
            for i,t in enumerate(s['trials']):
                expected={k:t[k] for k in ('collector_name','collector_out')}
                if expected!=current:raise ValueError('Predeclared collector chain differs from original completed trial')
                body=dict(base,**current,purpose='observer-calibration-'+t['mode'],attempt=t['attempt'],out=t['out'])
                body.update(trial_verification(verification,i,full_reference))
                for key in ('new_collector_name','new_collector_out'):
                    if key in t:body[key]=t[key]
                invoke('trial-'+str(i+1),'calibrate',body)
                finished=load(Path(t['out'])/'finished.json')
                current={k:finished[k] for k in ('collector_name','collector_out')}
                if i==0 and verification==c4_verify.TOUCHED:
                    original=load(pinned(finished['client_verify']))
                    if original.get('child_name')!=t['attempt'] or original['binding']['asset']['path']!=s['asset']+'/asset.json':
                        raise ValueError('First full proof did not use the original prepared C4 asset and attempt')
                    expected_binding=dict(cell_id=cell['id'],arm=cell['arm_id'],files=cell['params']['files'],
                        work_tag='child-01',preparation=preparation,asset=original['binding']['asset'],
                        source_tree=original['binding']['source_tree'],expected_child=original['expected_child'])
                    c4_verify.reference_identity(original,expected_binding,s['formal_attempt'])
                    full_reference=dict(path='/srv/ax-client/q/'+t['attempt']+'/verify/result.json',
                                        sha256=finished['client_verify']['sha256'])
            write_new(out/'trials.json',[t['out'] for t in s['trials']])
            q=Path(s['qualification'])/cell['arm_id']
            run('overhead',['python3',pinned(s['tools']['check']),'--trials',out/'trials.json','--metric',q/'metrics.json',
                '--workload',q/'workload.json','--out',out/'overhead','--verification-policy',verification])
            # This qualification comes from actual clone/copy+full cold parent
            # and child checks. No preparation-only receipt is called formal.
            trial=Path(s['trials'][0]['out']);end=load(trial/'finished.json')
            write_new(out/'route.json',dict(schema='c4-fork-route-v1',passed=True,cell_id=cell['id'],pool=pool,
                work=end['work'],client_verify=end['client_verify'],parent_verify=end['parent_verify'],
                native_exit=end['native_exit'],verify_exit=end['verify_exit'],closure=end['closure'],
                trial=ref(trial/'finished.json'),scope='Actual completed calibration child; formal sample is separate.'))
            body=dict(base,**current,out=str(formal),formal_attempt=s['formal_attempt'],
                overhead=ref(out/'overhead/result.json'),capacity=ref(out/'capacity.json'),route=ref(out/'route.json'))
            if s.get('cross_pool_qualification'):body['cross_pool_qualification']=s['cross_pool_qualification']
            invoke('admit','admit',body)
            run('formal',['python3',pinned(s['tools']['run']),'--context',formal/'context.json'])
            write_new(out/'result.json',dict(passed=True,cell_id=cell['id'],pool=pool,preparation=preparation,
                closing=ref(control/'cells'/(cell['id']+'.close.json')),sample=ref(formal/'sample.json'),
                automatic_retry=False,next_cell=False))
        except Exception as error:
            write_new(out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False,
                state='Preserve original stages and evidence. No workload retries or cleanup are automatic.'))
            # A transport/child exception cannot release physical ownership while
            # an original mount or slot process may still exist. Only read probes
            # repeat; they never stop processes, change mounts or replay work.
            i=0
            while True:
                i+=1;directory=out/('failure-release-probe-'+str(i));directory.mkdir()
                safe=True;fleet=load(runtime/'fleet.json')
                for role,root in dict(client='/srv/ax-client',metadata='/srv/ax-metadata',objects='/srv/ax-objects-0').items():
                    probe=failure_release_code(root,fleet_node(fleet,pool,role)['repo'],role,s['asset'],
                                               [t['attempt'] for t in s['trials']]+[s['formal_attempt']])
                    argv=['ssh','-F',str(runtime/'ssh.conf'),alias(pool,role),shlex.join(['python3','-c',probe])]
                    result=subprocess.run(argv,capture_output=True)
                    (directory/(role+'-stdout')).write_bytes(result.stdout);(directory/(role+'-stderr')).write_bytes(result.stderr)
                    write_new(directory/(role+'-exit.json'),dict(argv=argv,exit_code=result.returncode))
                    safe=safe and result.returncode==0
                if safe:break
                time.sleep(5)
            raise


if __name__=='__main__':main()
