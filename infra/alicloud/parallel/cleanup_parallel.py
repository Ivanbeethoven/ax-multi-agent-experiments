#!/usr/bin/env python3
"""Exact cleanup for the three-node pilot, only after all three verified archives exist."""
import argparse,base64,hashlib,json,re,subprocess,tarfile,time
from pathlib import Path
from parallel_controller import ROOT,ORIGINAL,STATE,REGION,PLAN,ecs,cli,invoke,sha,save
TERMINAL={'ok','failed','interrupted','unsupported','not_run'}
def evidence(state):
 if set(state['workers'])!={'node-a','node-b','node-c'}:raise ValueError('wrong participant set')
 if not PLAN.is_file() or not re.fullmatch('[0-9a-f]{64}',state.get('plan_sha256','')) or sha(PLAN)!=state['plan_sha256']:
  raise ValueError('frozen plan missing or changed')
 plan=json.loads(PLAN.read_text());assignments=plan.get('assignments',[])
 if len(assignments)!=3 or {v.get('worker_id') for v in assignments}!=set(state['workers']):
  raise ValueError('worker assignments differ from frozen plan')
 expected={v['worker_id']:v.get('arms',[]) for v in assignments}
 if any(len(v)!=4 or len(set(v))!=4 for v in expected.values()):raise ValueError('four unique planned arms required')
 for name,item in state['workers'].items():
  receipt=item.get('receipt');archive=(ROOT/item.get('archive','missing')).resolve()
  if not archive.is_relative_to(ROOT.resolve()):raise ValueError('archive outside this batch')
  if not receipt or receipt.get('run_id')!=state['batch_id'] or receipt.get('worker_id')!=name:
   raise ValueError('wrong worker receipt')
  if receipt.get('known_credential_scan')!='passed':raise ValueError('worker credential scan not verified')
  if not archive.is_file() or sha(archive)!=receipt.get('sha256') or archive.stat().st_size!=receipt.get('bytes'):
   raise ValueError('worker archive unverified')
  with tarfile.open(archive) as tar:
   seen=set()
   for member in tar.getmembers():
    if member.name in seen or not member.isfile() or Path(member.name).is_absolute() or '..' in Path(member.name).parts or '\\' in member.name:
     raise ValueError('unsafe or duplicate result archive member')
    seen.add(member.name)
   record=tar.extractfile('results/terminal.json')
   if record is None:raise ValueError('missing worker terminal record')
   terminal=json.load(record)
  if terminal.get('worker_id')!=name or terminal.get('run_id')!=state['batch_id'] or terminal.get('terminal_reason') not in ('completed','failed'):
   raise ValueError('worker terminal mismatch')
  if terminal['terminal_reason']!=receipt.get('terminal_reason'):raise ValueError('receipt terminal reason differs')
  methods=terminal.get('methods',{})
  if not isinstance(methods,dict) or set(methods)!=set(expected[name]):raise ValueError('terminal methods differ from planned arms')
  if any(not isinstance(v,dict) or v.get('status') not in TERMINAL for v in methods.values()):raise ValueError('worker may still run')
  if terminal['terminal_reason']=='completed' and any(v['status']!='ok' for v in methods.values()):
   raise ValueError('completed worker includes unsuccessful arm')
 return True

def instances(ids):return ecs('DescribeInstances',InstanceIds=json.dumps(ids))['Instances']['Instance']
def execute(state,original):
 evidence(state)
 expected_ids={v['instance_id'] for v in state['workers'].values()}
 if len(expected_ids)!=3 or original['created_resources']['instances'][0] not in expected_ids:raise ValueError('wrong exact instance set')
 for name,item in state['workers'].items():
  for instance in instances([item['instance_id']]):
   tags={t['TagKey']:t['TagValue'] for t in instance.get('Tags',{}).get('Tag',[])}
   expected_name=original['run_id'] if item['source_node'] else state['batch_id']+'-'+name
   expected_sg=original['created_resources']['security_group'] if item['source_node'] else state['worker_security_group']
   if instance['InstanceName']!=expected_name or instance['SecurityGroupIds']['SecurityGroupId']!=[expected_sg]:raise ValueError('instance identity differs')
   if item['source_node'] and tags.get('ax-run')!=original['run_id']:raise ValueError('source tag differs')
   if not item['source_node'] and tags.get('ax-batch')!=state['batch_id']:raise ValueError('worker tag differs')
   disks=ecs('DescribeDisks',InstanceId=item['instance_id'])['Disks']['Disk']
   if {d['DiskId'] for d in disks}!=set(item['disks']) or not all(d['DeleteWithInstance'] for d in disks):raise ValueError('disk inventory differs')
   # This gate never signals a workload; auxiliary services stop only after it passes.
   script="""set -eu
processes=$(ps -eo comm=) || exit 10
if printf '%s\n' "$processes" | grep -Ex 'full-fuse-repla|ax-fs-probe|cargo|rustc|brewfs|packed_v3_snaps|juicefs|mount.juicefs' >/dev/null; then exit 7; fi
units=$(systemctl list-units 'ax-cell-*' 'ax-parallel-matrix*' 'ax-parallel-prepare*' 'ax-main-rebuild*' --all --plain --no-legend) || exit 12
for unit in $(printf '%s\n' "$units" | awk '{print $1}'); do state=$(systemctl show "$unit" -p SubState --value) || exit 13; case "$state" in exited|dead|failed) ;; *) exit 8;; esac; done
mounts=$(findmnt -rn -o FSTYPE) || exit 11
if printf '%s\n' "$mounts" | grep -E '^fuse' >/dev/null; then exit 9; fi
systemctl stop ax-parallel-redis ax-parallel-registry ax-main-registry ax-main-checkpoint-recovery 2>/dev/null || true
docker stop ax-parallel-rustfs ax-rustfs >/dev/null 2>&1 || true
"""
   invoke(item['instance_id'],script,'ax-parallel-cleanup-gate')
   ecs('DeleteInstance',InstanceId=item['instance_id'],Force=True)
 for _ in range(40):
  if not instances(list(expected_ids)):break
  time.sleep(3)
 if instances(list(expected_ids)):raise RuntimeError('instance deletion pending')
 disk_ids=[x for v in state['workers'].values() for x in v['disks']]
 if ecs('DescribeDisks',DiskIds=json.dumps(disk_ids))['Disks']['Disk']:raise RuntimeError('owned disks still exist; preserve state and retry')
 groups={original['created_resources']['security_group']:original['run_id'],state['worker_security_group']:state['batch_id']}
 for group,name in groups.items():
  rows=ecs('DescribeSecurityGroups',SecurityGroupIds=json.dumps([group]))['SecurityGroups']['SecurityGroup']
  if rows:
   if len(rows)!=1 or rows[0]['SecurityGroupName']!=name:raise ValueError('security group identity differs')
   ecs('DeleteSecurityGroup',SecurityGroupId=group)
  if ecs('DescribeSecurityGroups',SecurityGroupIds=json.dumps([group]))['SecurityGroups']['SecurityGroup']:raise RuntimeError('security group deletion unconfirmed')
 pairs=ecs('DescribeKeyPairs',KeyPairName=state['ssh_key_name'])['KeyPairs']['KeyPair']
 if pairs:
  if len(pairs)!=1 or pairs[0]['KeyPairFingerPrint']!=state['ssh_key_fingerprint']:raise ValueError('key pair identity differs')
  ecs('DeleteKeyPairs',KeyPairNames=json.dumps([state['ssh_key_name']]))
 if ecs('DescribeKeyPairs',KeyPairName=state['ssh_key_name'])['KeyPairs']['KeyPair']:raise RuntimeError('key pair deletion unconfirmed')
 bucket=state['bucket']
 if bucket!=original['created_resources']['bucket'] or bucket!=original['run_id']+'-tmp':raise ValueError('wrong temporary bucket')
 endpoint='https://oss-'+REGION+'.aliyuncs.com';uri='oss://'+bucket
 stat=cli(['oss','stat',uri,'--endpoint',endpoint],False)
 if stat.returncode==0:
  cli(['oss','rm',uri,'--recursive','--all-type','--all-versions','--force','--yes','--endpoint',endpoint])
  cli(['oss','rm',uri,'--bucket','--force','--yes','--endpoint',endpoint])
 elif 'NoSuchBucket' not in stat.stdout+stat.stderr:raise RuntimeError('unknown bucket state')
 stat=cli(['oss','stat',uri,'--endpoint',endpoint],False)
 if not stat.returncode or 'NoSuchBucket' not in stat.stdout+stat.stderr:raise RuntimeError('temporary bucket deletion unconfirmed')
 switch=json.loads((ORIGINAL/'vswitch.local.json').read_text())['VSwitchId']
 if switch!=original['approved_spec']['vswitch_id']:raise ValueError('wrong switch')
 args=['vpc','DescribeVSwitchAttributes','--RegionId',REGION,'--VSwitchId',switch];r=cli(args,False)
 if r.returncode==0 and json.loads(r.stdout).get('VSwitchId'):
  detail=json.loads(r.stdout)
  if detail['VSwitchName']!='ax-main-test-20261028' or detail['VpcId']!=original['approved_spec']['vpc_id']:raise ValueError('switch identity differs')
  cli(['vpc','DeleteVSwitch','--RegionId',REGION,'--VSwitchId',switch])
 elif r.returncode and 'InvalidVSwitchId.NotFound' not in r.stdout+r.stderr:raise RuntimeError('cannot determine switch state')
 for _ in range(20):
  r=cli(args,False)
  if (r.returncode==0 and json.loads(r.stdout).get('VSwitchId')=='') or ('InvalidVSwitchId.NotFound' in r.stdout+r.stderr):break
  time.sleep(3)
 else:raise RuntimeError('switch deletion unconfirmed')
 receipt={'complete':True,'batch_id':state['batch_id'],'instances':sorted(expected_ids),'disks':disk_ids,'security_groups':list(groups),'key_pair':state['ssh_key_name'],'bucket':bucket,'vswitch':switch,'preexisting_vpc_preserved':True,'confirmed_epoch':time.time()}
 save(ROOT/'cleanup.local.json',receipt);save(ORIGINAL/'cleanup.local.json',receipt)
 (ORIGINAL/'stop-collect').write_text('parallel batch complete');(ORIGINAL/'stop-watch').write_text('parallel batch complete')
 for p in (ROOT/'ssh-key.local',ROOT/'ssh-key.local.pub'):
  if p.is_file():p.unlink()
 print('All exact batch resources verified released')
def main():
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');a=p.parse_args();state=json.loads(STATE.read_text());original=json.loads((ORIGINAL/'state.local.json').read_text())
 if not a.execute:
  print(json.dumps({'dry_run':True,'batch_id':state['batch_id'],'instance_ids':[i['instance_id'] for i in state['workers'].values()],'eligible':state.get('phase')=='collected_all'}));return
 execute(state,original)
if __name__=='__main__':main()
