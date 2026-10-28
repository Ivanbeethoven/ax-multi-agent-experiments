#!/usr/bin/env python3
"""Create exactly one useful build-only node; never launches the paused matrix."""
import base64,hashlib,ipaddress,json,os,subprocess,time,urllib.request,uuid
from pathlib import Path
from parallel_controller import ROOT,ORIGINAL,STATE,ecs,quote,save
SOURCE='a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed'
def main():
 original=json.loads((ORIGINAL/'state.local.json').read_text());spec=original['approved_spec']
 if not (ROOT/'PAUSED-FOR-CACHE-PLAN').exists():raise ValueError('matrix pause must remain in effect for build-only purchase')
 if STATE.exists():state=json.loads(STATE.read_text())
 else:
  state={'batch_id':'ax-parallel-'+time.strftime('%Y%m%d-%H%M%S',time.gmtime()),'phase':'build_only','build_only_authorization':{'extra_nodes':1,'source':SOURCE,'maximum_hourly_cny':1.7,'purpose':'compile fixed RustFS, later reusable as node-b; no replay'},'bucket':original['created_resources']['bucket'],'workers':{'node-a':{'source_node':True,'instance_id':original['created_resources']['instances'][0],'disks':original['created_resources']['disks'],'data_disk_id':next(d['DiskId'] for d in original['disks'] if d['Type']=='data'),'public_ip':original['instance_snapshot'][0]['PublicIpAddress']['IpAddress'][0]}},'created_epoch':time.time()};save(STATE,state)
 if set(state['workers'])-{'node-a','node-b'}:raise ValueError('unexpected additional participant')
 key=ROOT/'ssh-key.local';pub=ROOT/'ssh-key.local.pub'
 if not key.is_file():subprocess.run(['ssh-keygen','-q','-t','rsa','-b','3072','-N','','-f',str(key)],check=True)
 if os.name=='nt':
  account=subprocess.check_output(['whoami'],text=True).strip();subprocess.run(['icacls',str(key),'/inheritance:r','/grant:r',account+':F','SYSTEM:F'],check=True,stdout=subprocess.DEVNULL)
 if 'ssh_key_name' not in state:state['ssh_key_name']=state['batch_id']+'-key';state['ssh_key_path']=str(key);save(STATE,state)
 if not state.get('ssh_key_imported'):
  rows=ecs('DescribeKeyPairs',KeyPairName=state['ssh_key_name'])['KeyPairs']['KeyPair']
  expected=hashlib.md5(base64.b64decode(pub.read_text().split()[1])).hexdigest();expected=':'.join(expected[i:i+2] for i in range(0,len(expected),2))
  if rows:
   if len(rows)!=1 or rows[0]['KeyPairFingerPrint'].removeprefix('MD5:').lower()!=expected:raise ValueError('SSH key identity differs')
   r=rows[0]
  else:r=ecs('ImportKeyPair',KeyPairName=state['ssh_key_name'],PublicKeyBody=pub.read_text().strip())
  state.update(ssh_key_imported=True,ssh_key_fingerprint=r['KeyPairFingerPrint'],ssh_public_sha256=hashlib.sha256(pub.read_bytes()).hexdigest());save(STATE,state)
 if 'ssh_source' not in state:
  ip=urllib.request.urlopen('https://api.ipify.org',timeout=20).read().decode().strip()
  if ipaddress.ip_address(ip).version!=4:raise ValueError('IPv4 source required')
  state['ssh_source']=ip+'/32';save(STATE,state)
 if 'worker_security_group' not in state:
  rows=[r for r in ecs('DescribeSecurityGroups',VpcId=spec['vpc_id'],SecurityGroupName=state['batch_id'])['SecurityGroups']['SecurityGroup'] if r['SecurityGroupName']==state['batch_id']]
  if len(rows)>1:raise ValueError('ambiguous security group')
  if rows:
   r=rows[0];tags={v['TagKey']:v['TagValue'] for v in r.get('Tags',{}).get('Tag',[])}
   if tags.get('ax-batch')!=state['batch_id']:raise ValueError('wrong security group owner')
  else:r=ecs('CreateSecurityGroup',VpcId=spec['vpc_id'],SecurityGroupName=state['batch_id'],**{'Tag.1.Key':'ax-batch','Tag.1.Value':state['batch_id']})
  state['worker_security_group']=r['SecurityGroupId'];save(STATE,state)
 if not state.get('ssh_rule_created'):
  ecs('AuthorizeSecurityGroup',SecurityGroupId=state['worker_security_group'],IpProtocol='tcp',PortRange='22/22',SourceCidrIp=state['ssh_source'],NicType='intranet',Policy='accept');state['ssh_rule_created']=True;save(STATE,state)
 item=state['workers'].setdefault('node-b',{'source_node':False,'role':'build_only','token':str(uuid.uuid4())});save(STATE,state)
 if not item.get('instance_id'):
  params,price=quote(spec);params={k:v for k,v in params.items() if k not in ('ResourceType','PriceUnit','Period','InstanceNetworkType')}
  userdata='#!/bin/bash\nset -eu\nmkdir -p /var/log/ax-parallel\napt-get update -qq >/var/log/ax-parallel/apt.log 2>&1\nDEBIAN_FRONTEND=noninteractive apt-get install -y -qq xfsprogs curl git build-essential pkg-config libssl-dev clang cmake protobuf-compiler fuse3 >/var/log/ax-parallel/install.log 2>&1\n'
  params.update(VSwitchId=spec['vswitch_id'],InstanceChargeType='PostPaid',Amount=1,InstanceName=state['batch_id']+'-node-b',HostName='ax-node-b',ClientToken=item['token'],KeyPairName=state['ssh_key_name'],UserData=base64.b64encode(userdata.encode()).decode())
  params.update({'SecurityGroupIds.1':state['worker_security_group'],'DataDisk.1.DeleteWithInstance':True,'Tag.1.Key':'ax-batch','Tag.1.Value':state['batch_id']})
  item.update(create_parameters=params,price_cny_per_hour=price,phase='creating');save(STATE,state)
  r=ecs('RunInstances',**params);ids=r['InstanceIdSets']['InstanceIdSet']
  if len(ids)!=1:raise ValueError('unexpected purchase count')
  item['instance_id']=ids[0];item['phase']='created_for_build';save(STATE,state)
 print(json.dumps({'instance_id':item['instance_id'],'role':'build_only','quoted_hourly_cny':item['price_cny_per_hour'],'source':SOURCE,'matrix_paused':True,'other_new_instances':0}))
if __name__=='__main__':main()
