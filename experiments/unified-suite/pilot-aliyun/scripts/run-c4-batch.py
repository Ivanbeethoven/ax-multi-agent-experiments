#!/usr/bin/env python3
"""One C4 base, six fresh calibration children and one admitted formal child.

The original campaign's physical-resource guards cover this entire operation;
the new C4 SQLite database never supplies a second independent lock namespace.
"""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import load,pinned,digest,write_new
from bench.runtime import c4_verify,pilot_context
from bench.runtime.pilot_pools import active_conflicts,resource_guard,alias,cell_resources,fleet_node
from bench import suite_state
from bench.suite_plan import check_plan


def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


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
    a=p.parse_args();request=dict(path=a.request,sha256=a.sha256);s=load(pinned(request))
    if s.get('schema')!='c4-fork-batch-v1' or s.get('reviewed') is not True:
        raise ValueError('Reviewed explicit C4 batch required')
    for tool in s['tools'].values():pinned(tool)
    if set(s['tools'])!={'baseline','capacity','prepare','calibrate','check','admit','run'}:
        raise ValueError('Freeze all seven C4 coordinator tools')
    baseline=module(pinned(s['tools']['baseline']))
    validated=baseline.validate_request(SimpleNamespace(request=pinned(s['preparation_request']),
        sha256=s['preparation_request']['sha256'],runtime_repo=REPO))
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
        if not (control/'state.sqlite').exists():
            if s.get('initialize_state') is not True:raise ValueError('Explicit new phase state creation required')
            suite_state.initialize(control/'state.sqlite',plan)
        ensure_conflicts(controls,cell,plan)
        with suite_state.transaction(control/'state.sqlite') as db:
            status=db.execute('SELECT status FROM cells WHERE id=?',(cell['id'],)).fetchone()
            started=db.execute("SELECT 1 FROM stages WHERE cell_id=? AND status!='pending'",(cell['id'],)).fetchone()
        if not status or status[0]!='pending' or started:raise ValueError('Original cell was already started; no automatic retry')
        out.mkdir(parents=True);write_new(out/'request.json',s)
        write_new(out/'locks-held.json',dict(canonical_control=str(canonical),new_state=str(control/'state.sqlite'),
            pool=pool,resource_ids=sorted(validated['wanted']),at=datetime.now(timezone.utc).isoformat(),request=request))
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
            # Read-only before snapshot precedes the first import. Binding the
            # original observation to a frozen cell adds identity, not numbers.
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
