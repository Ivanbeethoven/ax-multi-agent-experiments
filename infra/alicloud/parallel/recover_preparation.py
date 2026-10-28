#!/usr/bin/env python3
"""Archive a preparation-only failure and prepare one explicit retry on the same three VMs."""
import argparse,base64,json,re,shutil,time,tarfile
from pathlib import Path
import cleanup_parallel as cleanup
from parallel_controller import ROOT,ORIGINAL,STATE,sha,save,invoke,authorize_launch,ecs

def snapshot_attempt(state):
 cleanup.evidence(state)
 if any(any(v.get('status')!='not_run' for v in item['terminal']['methods'].values()) for item in state['workers'].values()):raise ValueError('this recovery is only for an attempt where no planned cell ran')
 old=state.get('attempt_id','initial');history=ROOT/'attempt-history'/old
 records=[]
 for name,item in state['workers'].items():
  p=(ROOT/item['archive']).resolve();r=item['receipt']
  if not p.is_relative_to(ROOT.resolve()) or sha(p)!=r['sha256'] or p.stat().st_size!=r['bytes']:raise ValueError('old archive must be verified before retention')
  with tarfile.open(p) as archive:terminal=json.load(archive.extractfile('results/terminal.json'))
  if terminal!=item['terminal'] or any(v.get('status')!='not_run' for v in terminal['methods'].values()):raise ValueError('only archived all-not-run attempts can be recovered')
  records.append((name,p,r))
 history.mkdir(parents=True,exist_ok=True)
 if not (history/'parallel-state.local.json').exists():save(history/'parallel-state.local.json',state)
 for name,p,receipt in records:
  target=history/p.name
  if not target.exists():shutil.copy2(p,target)
  if sha(target)!=receipt['sha256'] or target.stat().st_size!=receipt['bytes']:raise ValueError('retained archive differs')
  receipt_path=history/(name+'-receipt.json')
  if not receipt_path.exists():save(receipt_path,receipt)
  if json.loads(receipt_path.read_text())!=receipt:raise ValueError('retained receipt differs')
 state_path=history/'parallel-state.local.json';previous=history/'retention-receipt.json';prior=json.loads(previous.read_text()) if previous.exists() else {}
 for filename,expected in prior.get('evidence_files',{}).items():
  retained=history/filename
  if sha(retained)!=expected['sha256'] or retained.stat().st_size!=expected['bytes']:raise ValueError('retained evidence file was changed')
 state_saved=json.loads(state_path.read_text())
 if state_saved['batch_id']!=state['batch_id'] or {n:v['instance_id'] for n,v in state_saved['workers'].items()}!={n:v['instance_id'] for n,v in state['workers'].items()}:raise ValueError('retained state identity differs')
 evidence_files=[state_path]+[history/(n+'-receipt.json') for n,p,r in records]
 save(previous,{'attempt_id':old,'at_epoch':time.time(),'archives':{n:{'sha256':r['sha256'],'bytes':r['bytes']} for n,p,r in records},'evidence_files':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in evidence_files},'state_preserved':True})
 return history

PROOF=r"""python3 - <<'PY'
import pathlib,subprocess,json,time,re
out={'epoch':time.time(),'clients':[],'units':{}}
for p in pathlib.Path('/proc').iterdir():
 if p.name.isdigit():
  try:
   name=(p/'comm').read_text().strip()
   if name in ('cargo','rustc','full-fuse-repla','ax-fs-probe','brewfs','packed_v3_snaps','juicefs','mount.juicefs'):out['clients'].append({'pid':int(p.name),'name':name})
  except (FileNotFoundError,PermissionError,ProcessLookupError):pass
for unit in UNIT_NAMES:
 out['units'][unit]=subprocess.check_output(['systemctl','show',unit,'-p','SubState','--value'],text=True).strip()
out['fuse_mounts']=[v for v in subprocess.check_output(['findmnt','-rn','-o','FSTYPE'],text=True).splitlines() if re.match(r'^fuse($|[.]|blk$)',v)]
assert not out['clients'] and not out['fuse_mounts'] and all(v in ('dead','failed','exited') for v in out['units'].values()),'client/prepare may still be active'
print(json.dumps(out))
PY"""
RESET=r"""python3 - <<'PY'
import json,pathlib,subprocess,base64,time
c=json.loads(base64.b64decode('CONFIG'));base=pathlib.Path('/data/parallel');history=base/'attempt-history'/c['old_attempt'];history.mkdir(parents=True,exist_ok=True)
# Keep the failed config and all old results/workspaces/data/secrets in place.
for name in ('worker-config'+c['old_tag']+'.json','backend-pin'+c['old_tag']+'.json'):
 p=base/name;t=history/name
 if p.is_file() and not t.exists():t.write_bytes(p.read_bytes());t.chmod(0o600)
r=subprocess.run(['docker','inspect','ax-parallel-rustfs'],capture_output=True,text=True);proof={'epoch':time.time()}
if r.returncode==0:
 d=json.loads(r.stdout)[0];assert d['Image']==c['image_id'];mounts=[m for m in d.get('Mounts',[]) if m.get('Destination')=='/data'];assert len(mounts)==1 and mounts[0]['Source']==str(base/('rustfs'+c['old_tag'])),'unexpected runtime volume'
 proof['old_backend']={'Id':d['Id'],'Image':d['Image'],'data_source':mounts[0]['Source'],'was_running':d['State']['Running']}
 if d['State']['Running']:subprocess.run(['docker','stop','ax-parallel-rustfs'],check=True,stdout=subprocess.DEVNULL)
 archived='ax-parallel-rustfs-'+c['old_attempt'];existing=subprocess.run(['docker','inspect',archived],capture_output=True,text=True)
 assert existing.returncode!=0,'archived container name already occupied'
 subprocess.run(['docker','rename','ax-parallel-rustfs',archived],check=True);proof['archived_backend_name']=archived
elif 'No such object' not in r.stderr and 'No such container' not in r.stderr:raise ValueError('cannot determine owned backend state')
substate=subprocess.check_output(['systemctl','show','ax-parallel-redis','-p','SubState','--value'],text=True).strip()
if substate=='running':
 command=subprocess.check_output(['systemctl','show','ax-parallel-redis','-p','ExecStart','--value'],text=True);assert '--port 6380' in command and str(base/('redis'+c['old_tag'])) in command,'unexpected Redis owner'
 subprocess.run(['systemctl','stop','ax-parallel-redis'],check=True);proof['old_redis_stopped']=True
if c['source_node']:
 registry_state=subprocess.check_output(['systemctl','show','ax-main-registry','-p','SubState','--value'],text=True).strip()
 if registry_state=='running':
  command=subprocess.check_output(['systemctl','show','ax-main-registry','-p','ExecStart','--value'],text=True);config=pathlib.Path('/data/registry/config.yml');assert '/usr/bin/docker-registry serve /data/registry/config.yml' in command and not config.is_symlink() and config.resolve()==config,'unexpected source registry owner'
  text=config.read_text();assert '/data/registry/files' in text and '127.0.0.1:5000' in text,'unexpected source registry binding'
  subprocess.run(['systemctl','stop','ax-main-registry'],check=True);proof['source_registry_stopped']='ax-main-registry; data preserved at /data/registry/files'
p=history/'runtime-before-repair.json'
if not p.exists():p.write_text(json.dumps(proof,indent=2)+'\n');p.chmod(0o600)
print(json.dumps({'reset_owned_auxiliary_only':True,'history':str(history)}))
PY"""

def execute(state):
 history=snapshot_attempt(state);old=state.get('attempt_id','initial');pending=state.get('repair_pending') or {'old_attempt':old,'new_attempt':'prep-'+time.strftime('%Y%m%d-%H%M%S',time.gmtime()),'reset_workers':[]};state['repair_pending']=pending;save(STATE,state)
 ids=[v['instance_id'] for v in state['workers'].values()];rows=ecs('DescribeInstances',InstanceIds=json.dumps(ids))['Instances']['Instance']
 if {v['InstanceId'] for v in rows}!=set(ids) or any(v['Status']!='Running' for v in rows):raise ValueError('the exact three existing instances must still be running')
 fresh={}
 for name,item in state['workers'].items():
  units=[item.get('prepare_unit','ax-parallel-prepare'),item.get('matrix_unit','ax-parallel-matrix')]
  fresh[name]=json.loads(invoke(item['instance_id'],PROOF.replace('UNIT_NAMES',repr(units)),'ax-repair-fresh-'+name))
 save(history/'fresh-no-workload-proof.local.json',fresh);authorize_launch(state,False);state['launch_authorized']=False;state['phase']='repair_hold';save(STATE,state);(ROOT/'PAUSED-FOR-CACHE-PLAN').write_text('Preparation recovery reviewed separately; no measured cell started in old attempt.\n')
 for name,item in state['workers'].items():
  if name in pending['reset_workers']:continue
  cfg=base64.b64encode(json.dumps({'old_attempt':old,'old_tag':'' if old=='initial' else '-'+old,'image_id':state['asset_manifest']['image_id'],'source_node':item.get('source_node',False)}).encode()).decode()
  invoke(item['instance_id'],RESET.replace('CONFIG',cfg),'ax-repair-only-owned-idle-aux-'+name);pending['reset_workers'].append(name);save(STATE,state)
 state.setdefault('attempt_history',[]).append({'attempt_id':old,'retained_directory':str(history),'next_attempt':pending['new_attempt']});state['attempt_id']=pending['new_attempt'];state.pop('repair_pending',None)
 for item in state['workers'].values():
  for key in ('receipt','terminal','archive','deployed','prepare_unit','matrix_unit'):item.pop(key,None)
  item['phase']='awaiting_prepare'
 state['phase']='waiting_assets';save(STATE,state);save(history/'recovery-complete.local.json',{'next_attempt':state['attempt_id'],'at_epoch':time.time(),'same_instance_ids':ids,'no_data_or_cache_deleted':True});return state

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');args=parser.parse_args();state=json.loads(STATE.read_text());history=snapshot_attempt(state)
 if not args.execute:print(json.dumps({'snapshot_verified':str(history),'cloud_mutation':False,'would_reuse_instances':{n:v['instance_id'] for n,v in state['workers'].items()}}));return
 result=execute(state);print(json.dumps({'new_attempt':result['attempt_id'],'same_three_instances':True,'temporary_review_pause':True,'old_evidence_retained':str(history)}))
if __name__=='__main__':main()
