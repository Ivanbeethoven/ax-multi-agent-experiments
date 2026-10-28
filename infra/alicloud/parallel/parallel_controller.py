#!/usr/bin/env python3
"""Three-worker pilot controller. Exact assets+runner+plan gate precedes all purchases."""
import argparse,base64,hashlib,hmac,io,json,os,subprocess,sys,tarfile,time,urllib.parse,urllib.request,uuid
from pathlib import Path
CODE=Path(__file__).resolve().parent;ROOT=Path(os.environ.get('AX_PARALLEL_RUN_DIR',str(CODE)));ORIGINAL=ROOT.parent/'aliyun-main-20261028';AX=ROOT.parents[1]
RUNNER='8e815bd91060bae90a2699668cc5d87e32b2e4c1';BASE_COMMIT='25ea3171ac021d573bb6d45b092b9f95f6b02c8c'
REGION='cn-hangzhou';MAX_EXTRA=2;MAX_PRICE=1.7
PLAN=ORIGINAL/'parallel-plan-kernel-default.json';STATE=ROOT/'parallel-state.local.json'
def save(path,value):
 path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def cli(args,check=True):
 p=subprocess.run(['aliyun',*map(str,args)],capture_output=True,text=True,encoding='utf-8',timeout=180)
 if check and p.returncode:
  (ROOT/'cli-error.local.txt').write_text(p.stderr);raise RuntimeError('Aliyun operation failed; private error retained')
 return p
def ecs(action,**kw):
 args=['ecs',action,'--RegionId',REGION]
 for k,v in kw.items():args+=['--'+k,str(v).lower() if isinstance(v,bool) else str(v)]
 return json.loads(cli(args).stdout)
def upload(path,key,bucket):
 cli(['oss','cp',path,'oss://'+bucket+'/'+key,'--endpoint','https://oss-'+REGION+'.aliyuncs.com','-f'])
def get_json(bucket,key):
 p=cli(['oss','cat','oss://'+bucket+'/'+key,'--endpoint','https://oss-'+REGION+'.aliyuncs.com'],False)
 return json.loads(p.stdout) if p.returncode==0 else None
def signed(bucket,key,method='GET',ctype=''):
 config=json.loads((Path.home()/'.aliyun/config.json').read_text());profile=next(v for v in config['profiles'] if v['name']==config.get('current','default'))
 expiry=str(int(time.time())+86400);message=f'{method}\n\n{ctype}\n{expiry}\n/{bucket}/{key}'
 signature=base64.b64encode(hmac.new(profile['access_key_secret'].encode(),message.encode(),hashlib.sha1).digest()).decode()
 return 'https://'+bucket+'.oss-'+REGION+'-internal.aliyuncs.com/'+key+'?'+urllib.parse.urlencode({'OSSAccessKeyId':profile['access_key_id'],'Expires':expiry,'Signature':signature})
NORMAL_ARMS={'native-kernel-default','overlay-kernel-default','packed-kernel-default','juicefs-kernel-default'}
def frozen_contract():
 path=ROOT/'runner-ready.json'
 if not path.is_file():raise ValueError('frozen normal-cache runner contract is not ready')
 value=json.loads(path.read_text());commit=value.get('runner_commit','')
 if len(commit)!=40 or any(c not in '0123456789abcdef' for c in commit):raise ValueError('full runner SHA required')
 if set(value.get('arms',[]))!=NORMAL_ARMS or len(value['arms'])!=4:raise ValueError('main cache semantics differ')
 return value
def gate(manifest,plan,runner_commit):
 if (ROOT/'PAUSED-FOR-CACHE-PLAN').exists():raise ValueError('matrix paused pending cache semantics review')
 contract=frozen_contract()
 if not manifest or manifest.get('brewfs_source')!='350dc108a3bffcb5e7e27cc4279dfb5729febe60' or manifest.get('rustfs_source')!='a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed':raise ValueError('assets not ready or wrong source')
 if 'registry_image' not in manifest or not manifest.get('credential_scan'):raise ValueError('target-image export/scan missing')
 if set(manifest.get('binaries',{}))!={'brewfs','packed_v3_snapshot_fixture','full-fuse-replay','ax-fs-probe','juicefs'}:raise ValueError('binary set incomplete')
 workers=['node-a','node-b','node-c'];assignments=plan.get('assignments',[])
 if plan.get('workers')!=workers or plan.get('planned_cells')!=12 or len(assignments)!=3 or {a.get('worker_id') for a in assignments}!=set(workers):raise ValueError('wrong experiment participants')
 by_worker={a['worker_id']:a['arms'] for a in assignments}
 if any(len(v)!=4 or set(v)!=NORMAL_ARMS for v in by_worker.values()):raise ValueError('normal-cache blocks incomplete')
 phases=plan.get('phases',[])
 if len(phases)!=4 or any(p.get('phase')!=i+1 or p.get('workers')!={w:by_worker[w][i] for w in workers} for i,p in enumerate(phases)):raise ValueError('rotation differs from assignments')
 if plan.get('schema_version')!=2 or set(plan.get('arm_controls',{}))!=NORMAL_ARMS or any(v.get('group')!='kernel-default' or v.get('requested_kernel_metadata_ttl_ms') is not None or v.get('requested_open_keep_cache') is not None for v in plan['arm_controls'].values()):raise ValueError('normal-cache controls differ')
 if runner_commit!=contract['runner_commit'] or sha(PLAN)!=contract['plan_sha256']:raise ValueError('frozen runner/plan differs')
 return True
def revalidate(state):
 if sha(PLAN)!=state['plan_sha256']:raise ValueError('plan changed after gate started')
 return gate(state.get('asset_manifest'),json.loads(PLAN.read_text()),state['runner_commit'])
def prepare_runner_bundle(state,source):
 revalidate(state);commit=state['runner_commit']
 subprocess.run(['git','cat-file','-e',commit+'^{commit}'],cwd=AX,check=True)
 subprocess.run(['git','merge-base','--is-ancestor',BASE_COMMIT,commit],cwd=AX,check=True)
 if state.get('runner_bundle'):
  if state['runner_bundle']['commit']!=commit or sha(ROOT/state['runner_bundle']['local_file'])!=state['runner_bundle']['sha256']:raise ValueError('frozen source bundle changed')
  return
 ref='refs/ax-parallel/'+commit
 existing=subprocess.run(['git','rev-parse','--verify',ref],cwd=AX,capture_output=True,text=True)
 if existing.returncode==0 and existing.stdout.strip()!=commit:raise ValueError('private bundle ref differs')
 if existing.returncode:subprocess.run(['git','update-ref',ref,commit,'0'*40],cwd=AX,check=True)
 bundle=ROOT/('runner-'+commit[:12]+'.bundle')
 try:subprocess.run(['git','bundle','create',str(bundle),ref,'^'+BASE_COMMIT],cwd=AX,check=True)
 finally:subprocess.run(['git','update-ref','-d',ref,commit],cwd=AX,check=True)
 key='parallel-assets/'+bundle.name
 upload(bundle,key,source['created_resources']['bucket'])
 state['runner_bundle']={'commit':commit,'ref':ref,'sha256':sha(bundle),'key':key,'local_file':bundle.name};save(STATE,state)
def authorize_launch(state,active):
 value={'batch_id':state['batch_id'],'active':active,'plan_sha256':state['plan_sha256'],'runner_commit':state['runner_commit'],'epoch':time.time()}
 path=ROOT/'launch-authorization.local.json';save(path,value);upload(path,'parallel-assets/launch-authorization.json',state['bucket'])
def quote(spec,private=False):
 params={'ZoneId':spec['zone'],'ResourceType':'instance','InstanceType':spec['instance_type'],'InstanceNetworkType':'vpc','IoOptimized':'optimized','ImageId':spec['image_id'],'PriceUnit':'Hour','Period':1,'SpotStrategy':'SpotAsPriceGo','SpotDuration':1,'SystemDisk.Category':'cloud_essd','SystemDisk.Size':40,'SystemDisk.PerformanceLevel':'PL0','DataDisk.1.Category':'cloud_essd','DataDisk.1.Size':500,'DataDisk.1.PerformanceLevel':'PL1','InternetChargeType':'PayByTraffic','InternetMaxBandwidthOut':5}
 if private:params['InternetMaxBandwidthOut']=0
 data=ecs('DescribePrice',**params);price=data['PriceInfo']['Price']
 stock=ecs('DescribeAvailableResource',ZoneId=spec['zone'],DestinationResource='InstanceType',InstanceType=spec['instance_type'],InstanceChargeType='PostPaid',SpotStrategy='SpotAsPriceGo',NetworkCategory='vpc')
 available=[x for z in stock['AvailableZones']['AvailableZone'] for a in z['AvailableResources']['AvailableResource'] for x in a['SupportedResources']['SupportedResource'] if x['Value']==spec['instance_type'] and x['StatusCategory']=='WithStock']
 if price['Currency']!='CNY' or price['TradePrice']>MAX_PRICE or not available:raise ValueError('price/stock outside approved limit')
 return params,price['TradePrice']
def invoke(iid,script,name):
 r=ecs('RunCommand',**{'InstanceId.1':iid,'Type':'RunShellScript','ContentEncoding':'Base64','CommandContent':base64.b64encode(script.encode()).decode(),'Timeout':180,'Name':name,'KeepCommand':False})
 for _ in range(45):
  time.sleep(2);rows=ecs('DescribeInvocationResults',InvokeId=r['InvokeId'],ContentEncoding='PlainText')['Invocation']['InvocationResults']['InvocationResult']
  if rows and rows[0]['InvocationStatus'] not in ('Pending','Running'):
   if rows[0]['InvocationStatus']!='Success' or rows[0].get('ExitCode')!=0:raise RuntimeError('remote setup command failed')
   return rows[0].get('Output','')
 raise RuntimeError('remote setup command did not return; do not resubmit blindly')
def ssh_sources(state):
 ip=urllib.request.urlopen('https://api.ipify.org',timeout=20).read().decode().strip()
 import ipaddress
 if ipaddress.ip_address(ip).version!=4:raise ValueError('IPv4 SSH source required')
 state['ssh_source']=ip+'/32';save(STATE,state)
def provision(state,source):
 revalidate(state)
 if not state.get('runner_bundle'):raise ValueError('runner must be bundled before any purchase')
 if len([v for v in state['workers'].values() if not v.get('source_node')])>MAX_EXTRA:raise ValueError('instance count limit exceeded')
 spec=source['approved_spec']
 private=ROOT/'ssh-key.local';public=ROOT/'ssh-key.local.pub'
 if not state.get('ssh_key_imported'):
  if not private.exists():subprocess.run(['ssh-keygen','-q','-t','rsa','-b','3072','-N','','-f',str(private)],check=True)
  if os.name=='nt':
   account=subprocess.check_output(['whoami'],text=True).strip()
   subprocess.run(['icacls',str(private),'/inheritance:r','/grant:r',account+':F','SYSTEM:F'],check=True,stdout=subprocess.DEVNULL)
  state['ssh_key_path']=str(private);state['ssh_public_sha256']=sha(public);state['ssh_key_name']=state['batch_id']+'-key';save(STATE,state)
  rows=ecs('DescribeKeyPairs',KeyPairName=state['ssh_key_name'])['KeyPairs']['KeyPair']
  expected=hashlib.md5(base64.b64decode(public.read_text().split()[1])).hexdigest();expected=':'.join(expected[i:i+2] for i in range(0,len(expected),2))
  if rows:
   if len(rows)!=1 or rows[0]['KeyPairFingerPrint'].removeprefix('MD5:').lower()!=expected:raise ValueError('existing key pair fingerprint differs')
   key=rows[0]
  else:key=ecs('ImportKeyPair',KeyPairName=state['ssh_key_name'],PublicKeyBody=public.read_text().strip())
  state['ssh_key_fingerprint']=key['KeyPairFingerPrint'];state['ssh_key_imported']=True;save(STATE,state)
 if 'ssh_source' not in state:ssh_sources(state)
 if not state.get('source_ssh_rule_created'):
  source_group=source['created_resources']['security_group']
  ecs('AuthorizeSecurityGroup',SecurityGroupId=source_group,IpProtocol='tcp',PortRange='22/22',SourceCidrIp=state['ssh_source'],NicType='intranet',Policy='accept')
  payload=base64.b64encode(public.read_bytes()).decode()
  script="python3 - <<'AXKEY'\nfrom pathlib import Path\nimport base64\np=Path('/root/.ssh/authorized_keys');p.parent.mkdir(mode=0o700,exist_ok=True);p.touch(exist_ok=True);key=base64.b64decode('"+payload+"').decode().strip();text=p.read_text();p.write_text(text.rstrip()+'\\n'+key+'\\n') if key not in text.splitlines() else None;p.chmod(0o600)\nAXKEY\n"
  invoke(source['created_resources']['instances'][0],script,'ax-parallel-source-ssh')
  state['source_ssh_rule_created']=True;state['source_ssh_security_group']=source_group;save(STATE,state)
 if 'worker_security_group' not in state:
  matches=[g for g in ecs('DescribeSecurityGroups',VpcId=spec['vpc_id'],SecurityGroupName=state['batch_id'])['SecurityGroups']['SecurityGroup'] if g['SecurityGroupName']==state['batch_id']]
  if len(matches)>1:raise ValueError('ambiguous existing worker security group')
  if matches:
   r=matches[0];tags={v['TagKey']:v['TagValue'] for v in r.get('Tags',{}).get('Tag',[])}
   if tags.get('ax-batch')!=state['batch_id']:raise ValueError('existing security group is not this batch')
  else:r=ecs('CreateSecurityGroup',VpcId=spec['vpc_id'],SecurityGroupName=state['batch_id'],**{'Tag.1.Key':'ax-batch','Tag.1.Value':state['batch_id']})
  state['worker_security_group']=r['SecurityGroupId'];save(STATE,state)
 if not state.get('ssh_rule_created'):
  ecs('AuthorizeSecurityGroup',SecurityGroupId=state['worker_security_group'],IpProtocol='tcp',PortRange='22/22',SourceCidrIp=state['ssh_source'],NicType='intranet',Policy='accept')
  state['ssh_rule_created']=True;save(STATE,state)
 for worker in ('node-b','node-c'):
  item=state['workers'].setdefault(worker,{'source_node':False,'token':str(uuid.uuid4())});save(STATE,state)
  if item.get('instance_id'):continue
  params,price=quote(spec,private=(worker=='node-c'));params={k:v for k,v in params.items() if k not in ('ResourceType','PriceUnit','Period','InstanceNetworkType')}
  params.update(VSwitchId=spec['vswitch_id'],InstanceChargeType='PostPaid',Amount=1,InstanceName=state['batch_id']+'-'+worker,HostName='ax-'+worker,ClientToken=item['token'],KeyPairName=state['ssh_key_name'])
  params.update({'SecurityGroupIds.1':state['worker_security_group'],'DataDisk.1.DeleteWithInstance':True,'Tag.1.Key':'ax-batch','Tag.1.Value':state['batch_id']})
  if worker=='node-c':item['private_only']=True
  item.update(price_cny_per_hour=price,create_parameters=params,phase='creating');save(STATE,state)
  r=ecs('RunInstances',**params);ids=r['InstanceIdSets']['InstanceIdSet']
  if len(ids)!=1:raise ValueError('unexpected number of instances')
  item['instance_id']=ids[0];item['phase']='created';save(STATE,state)
 for worker,item in state['workers'].items():
  if item.get('data_disk_id'):continue
  while True:
   nodes=ecs('DescribeInstances',InstanceIds=json.dumps([item['instance_id']]))['Instances']['Instance']
   if nodes and nodes[0]['Status']=='Running':break
   time.sleep(5)
  disks=ecs('DescribeDisks',InstanceId=item['instance_id'])['Disks']['Disk']
  if len(disks)!=2 or sorted(d['Size'] for d in disks)!=[40,500] or not all(d['DeleteWithInstance'] for d in disks):raise ValueError('unexpected disposable disk inventory')
  item['disks']=[d['DiskId'] for d in disks];item['data_disk_id']=next(d['DiskId'] for d in disks if d['Type']=='data');item['public_ip']=next(iter(nodes[0]['PublicIpAddress']['IpAddress']),None);item['private_ip']=nodes[0]['VpcAttributes']['PrivateIpAddress']['IpAddress'][0];save(STATE,state)
def private_egress(state,source):
 if 'node-c' not in state['workers']:return
 a=state['workers']['node-a'];c=state['workers']['node-c']
 if not c.get('private_only'):raise ValueError('new node-c must be private only')
 if not a.get('private_ip'):
  rows=ecs('DescribeInstances',InstanceIds=json.dumps([a['instance_id']]))['Instances']['Instance'];a['private_ip']=rows[0]['VpcAttributes']['PrivateIpAddress']['IpAddress'][0];save(STATE,state)
 if state.get('private_egress_proxy_ready'):return
 group=source['created_resources']['security_group'];cidr=c['private_ip']+'/32'
 if not state.get('private_egress_rule'):
  ecs('AuthorizeSecurityGroup',SecurityGroupId=group,IpProtocol='tcp',PortRange='3128/3128',SourceCidrIp=cidr,NicType='intranet',Policy='accept');state['private_egress_rule']={'group':group,'source':cidr,'port':'3128/3128'};save(STATE,state)
 body=base64.b64encode((CODE/'private_egress_proxy.py').read_bytes()).decode();config=base64.b64encode(json.dumps({'bind_ip':a['private_ip'],'clients':[c['private_ip']]}).encode()).decode()
 script='set -eu\numask 077\nprintf %s '+body+' | base64 -d > /root/ax-private-egress-proxy.py\nprintf %s '+config+' | base64 -d > /root/ax-private-egress-proxy.json\nsystemd-run --unit=ax-private-egress --property=RuntimeMaxSec=infinity --property=TimeoutStartSec=infinity --property=CPUQuota=5% --property=MemoryMax=128M /usr/bin/python3 /root/ax-private-egress-proxy.py /root/ax-private-egress-proxy.json\n'
 invoke(a['instance_id'],script,'ax-private-dependency-egress');state['private_egress_proxy_ready']=True;c['egress_proxy']='http://'+a['private_ip']+':3128';save(STATE,state)
def ssh_config(state):
 path=ROOT/'ssh-client.local.conf';key=Path(state['ssh_key_path']).as_posix();known=(ROOT/'known_hosts.local').as_posix();a=state['workers']['node-a']
 lines=['Host ax-bastion',' HostName '+a['public_ip'],' User root',' IdentityFile "'+key+'"',' UserKnownHostsFile "'+known+'"',' StrictHostKeyChecking yes',' BatchMode yes',' ConnectTimeout 15']
 for name,item in state['workers'].items():
  address=item.get('public_ip') or item.get('private_ip')
  lines+=['Host ax-'+name,' HostName '+address,' User root',' IdentityFile "'+key+'"',' UserKnownHostsFile "'+known+'"',' StrictHostKeyChecking yes',' BatchMode yes',' ConnectTimeout 15',' Port '+str(item.get('ssh_port',22))]
  if item.get('private_only'):lines+=[' ProxyJump ax-bastion']
 path.write_text('\n'.join(lines)+'\n');return path
def deploy(state,source,assets,plan):
 bucket=source['created_resources']['bucket'];base_name='experiment-25ea317.bundle'
 private_egress(state,source)
 revalidate(state)
 if not state.get('runner_bundle'):raise ValueError('runner source not ready')
 authorize_launch(state,True)
 bootstrap=(CODE/'worker_bootstrap.py').read_text()
 for assignment in plan['assignments']:
  name=assignment['worker_id'];item=state['workers'][name]
  revalidate(state)
  if item.get('deployed'):continue
  config={'run_id':state['batch_id'],'worker_id':name,'egress_proxy':item.get('egress_proxy'),'source_node':item['source_node'],'data_disk_id':item['data_disk_id'],'arms':assignment['arms'],'runner_commit':state['runner_commit'],'plan_sha256':state['plan_sha256'],'launch_gate_url':signed(bucket,'parallel-assets/launch-authorization.json'),'asset_manifest':assets,'asset_manifest_sha256':hashlib.sha256(json.dumps(assets,sort_keys=True).encode()).hexdigest(),'bundle':{**assets['bundle'],'url':signed(bucket,'parallel-assets/'+assets['bundle']['key'])},'base_bundle':{'url':signed(bucket,base_name),'sha256':source['uploaded_files'][base_name]['sha256']},'runner_bundle':{'url':signed(bucket,state['runner_bundle']['key']),'sha256':state['runner_bundle']['sha256'],'ref':state['runner_bundle']['ref']},'trace_archive':{'url':signed('brewfs-agent-traces-20261023-4ccdae73','full-fuse/20261023/pytest-full-02.tar.gz'),'sha256':assets['trace_archive_sha256']},'result_upload_url':signed(bucket,'parallel-results/'+name+'/results.tar.gz','PUT','application/gzip'),'receipt_upload_url':signed(bucket,'parallel-results/'+name+'/receipt.json','PUT','application/json')}
  payload=base64.b64encode(json.dumps(config).encode()).decode();code=base64.b64encode(bootstrap.encode()).decode()
  mount="""python3 - <<'PY'
import json,pathlib,subprocess
C=json.loads(pathlib.Path('/root/ax-parallel-config.json').read_text());wanted=C['data_disk_id'].removeprefix('d-');matches=[p.parent.parent for p in pathlib.Path('/sys/class/block').glob('*/device/serial') if p.read_text().strip()==wanted]
if len(matches)!=1:raise ValueError('disk serial not unique')
dev='/dev/'+matches[0].name
if int(subprocess.check_output(['blockdev','--getsize64',dev]))!=500*1024**3:raise ValueError('wrong disk size')
probe=subprocess.run(['blkid','-o','value','-s','TYPE',dev],capture_output=True,text=True)
if probe.returncode!=0:
 if C['source_node']:raise ValueError('source disk lost filesystem')
 row=json.loads(subprocess.check_output(['lsblk','-J','-o','NAME,MOUNTPOINTS',dev],text=True))['blockdevices'][0]
 if row.get('children') or any(row.get('mountpoints') or []):raise ValueError('refuse nonempty/mounted new disk')
 subprocess.run(['mkfs.xfs','-f',dev],check=True,stdout=subprocess.DEVNULL)
elif probe.stdout.strip()!='xfs':raise ValueError('unexpected filesystem')
pathlib.Path('/data').mkdir(exist_ok=True)
if subprocess.run(['findmnt','-M','/data'],stdout=subprocess.DEVNULL).returncode:subprocess.run(['mount',dev,'/data'],check=True)
elif subprocess.check_output(['findmnt','-n','-o','SOURCE','-M','/data'],text=True).strip()!=dev:raise ValueError('wrong mounted disk')
PY"""
  shell='set -eu\ncommand -v mkfs.xfs >/dev/null || { apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xfsprogs; }\n'+mount+'\nexec /usr/bin/python3 /root/ax-parallel-worker.py /root/ax-parallel-config.json\n'
  launch=base64.b64encode(shell.encode()).decode()
  script='set -eu\numask 077\nprintf %s '+payload+' | base64 -d > /root/ax-parallel-config.json\nprintf %s '+code+' | base64 -d > /root/ax-parallel-worker.py\nprintf %s '+launch+' | base64 -d > /root/ax-parallel-launch.sh\nsystemd-run --unit=ax-parallel-prepare --property=RuntimeMaxSec=infinity --property=TimeoutStartSec=infinity --property=RemainAfterExit=yes --property=StandardOutput=append:/var/log/ax-parallel-prepare.log --property=StandardError=append:/var/log/ax-parallel-prepare.log /bin/bash /root/ax-parallel-launch.sh\n'
  for _ in range(60):
   rows=ecs('DescribeCloudAssistantStatus',**{'InstanceId.1':item['instance_id']})['InstanceCloudAssistantStatusSet']['InstanceCloudAssistantStatus']
   if any(str(v.get('CloudAssistantStatus')).lower()=='true' for v in rows):break
   time.sleep(5)
  else:raise RuntimeError('worker control channel not ready; no replay started')
  hostkey=invoke(item['instance_id'],'cat /etc/ssh/ssh_host_ed25519_key.pub','ax-parallel-ssh-host-key').strip()
  if not hostkey.startswith('ssh-ed25519 '):raise ValueError('unexpected SSH host key')
  known=ROOT/'known_hosts.local'
  address=item.get('public_ip') or item['private_ip']
  with known.open('a') as f:f.write(address+' '+hostkey+'\n')
  if item.get('private_only') and not state.get('private_bastion_rule'):
   arows=ecs('DescribeInstances',InstanceIds=json.dumps([state['workers']['node-a']['instance_id']]))['Instances']['Instance'];private=arows[0]['VpcAttributes']['PrivateIpAddress']['IpAddress'][0]+'/32'
   ecs('AuthorizeSecurityGroup',SecurityGroupId=state['worker_security_group'],IpProtocol='tcp',PortRange='22/22',SourceCidrIp=private,NicType='intranet',Policy='accept');state['private_bastion_rule']={'source_instance':state['workers']['node-a']['instance_id'],'source':private,'port':'22/22'};save(STATE,state)
  invoke(item['instance_id'],'systemctl start ssh','ax-parallel-ssh-enable')
  check=subprocess.run(['ssh','-F',str(ssh_config(state)),'ax-'+name,'true'],capture_output=True,text=True)
  if check.returncode:raise RuntimeError('backup SSH connection failed; no replay started')
  item['backup_ssh_verified']=True;save(STATE,state)
  result=invoke(item['instance_id'],script,'ax-parallel-deploy-'+name);item['deployed']=True;item['phase']='preparing';save(STATE,state)
def collect(state,source):
 bucket=source['created_resources']['bucket']
 complete=True
 for name,item in state['workers'].items():
  if item.get('receipt'):continue
  receipt=get_json(bucket,'parallel-results/'+name+'/receipt.json')
  if not receipt:complete=False;continue
  if receipt.get('known_credential_scan')!='passed' or receipt.get('run_id')!=state['batch_id'] or receipt.get('worker_id')!=name or receipt.get('terminal_reason') not in ('completed','failed'):raise ValueError('wrong terminal receipt')
  archive=ROOT/(name+'-results.tar.gz');cli(['oss','cp','oss://'+bucket+'/parallel-results/'+name+'/results.tar.gz',archive,'--endpoint','https://oss-'+REGION+'.aliyuncs.com','-f'])
  if sha(archive)!=receipt['sha256'] or archive.stat().st_size!=receipt['bytes']:raise ValueError('result archive mismatch')
  with tarfile.open(archive) as tar:
   if len([m for m in tar.getmembers() if m.name=='results/terminal.json'])!=1:raise ValueError('unique terminal record required')
   for m in tar.getmembers():
    if not m.isfile() or '..' in Path(m.name).parts or Path(m.name).is_absolute():raise ValueError('unsafe result archive')
   terminal=json.load(tar.extractfile('results/terminal.json'))
  plan=json.loads(PLAN.read_text())
  if sha(PLAN)!=state['plan_sha256']:raise ValueError('frozen plan changed before collection')
  expected=next(a['arms'] for a in plan['assignments'] if a['worker_id']==name)
  methods=terminal.get('methods',{})
  if terminal.get('worker_id')!=name or terminal.get('run_id')!=state['batch_id'] or set(methods)!=set(expected) or any(v.get('status') not in ('ok','failed','interrupted','not_run','unsupported') for v in methods.values()):raise ValueError('nonterminal, incomplete or wrong worker archive')
  if terminal.get('terminal_reason')!=receipt['terminal_reason'] or (receipt['terminal_reason']=='completed' and any(v['status']!='ok' for v in methods.values())):raise ValueError('terminal reason differs from method outcomes')
  item['receipt']=receipt;item['terminal']=terminal;item['archive']=archive.name;item['phase']='collected';save(STATE,state)
 return complete and all(v.get('receipt') for v in state['workers'].values())
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');p.add_argument('--once',action='store_true');a=p.parse_args()
 source=json.loads((ORIGINAL/'state.local.json').read_text());bucket=source['created_resources']['bucket'];plan=json.loads(PLAN.read_text())
 if not a.execute:
  print(json.dumps({'dry_run':True,'extra_instance_limit':MAX_EXTRA,'maximum_hourly_cny_per_extra':MAX_PRICE,'runner_commit':RUNNER,'plan_cells':plan.get('planned_cells'),'gates':['complete immutable assets','frozen runner','complete rotated plan'],'new_resources':False}));return
 if STATE.exists():
  state=json.loads(STATE.read_text())
  if state.get('runner_commit')!=frozen_contract()['runner_commit'] or state.get('plan_sha256')!=sha(PLAN):raise ValueError('existing state needs explicit reviewed build-only promotion')
 else:
  state={'batch_id':'ax-main-parallel-'+time.strftime('%Y%m%d-%H%M%S',time.gmtime()),'runner_commit':frozen_contract()['runner_commit'],'plan_sha256':sha(PLAN),'bucket':bucket,'workers':{'node-a':{'source_node':True,'instance_id':source['created_resources']['instances'][0],'disks':source['created_resources']['disks'],'data_disk_id':next(d['DiskId'] for d in source['disks'] if d['Type']=='data'),'public_ip':source['instance_snapshot'][0]['PublicIpAddress']['IpAddress'][0]}},'phase':'waiting_assets','created_epoch':time.time()};save(STATE,state)
 while True:
  if (ROOT/'PAUSED-FOR-CACHE-PLAN').exists():
   save(ROOT/'gate-status.local.json',{'phase':'waiting_pause_review','epoch':time.time(),'new_resources':False})
   if a.once:return
   time.sleep(30);continue
  if sha(PLAN)!=state['plan_sha256']:raise ValueError('plan changed after gate started')
  assets=get_json(bucket,'parallel-assets/asset-manifest.json')
  if not assets:
   save(ROOT/'gate-status.local.json',{'phase':'waiting_immutable_assets','epoch':time.time(),'new_resources':False})
   if a.once:return
   time.sleep(60);continue
  gate(assets,plan,state['runner_commit']);state['asset_manifest']=assets;state['phase']='assets_ready';save(STATE,state)
  prepare_runner_bundle(state,source)
  # Existing workers must have an independently verified recovery channel before buying node-c.
  missing=[name for name,item in state['workers'].items() if not item.get('source_node') and not item.get('backup_ssh_verified')]
  if missing:
   save(ROOT/'gate-status.local.json',{'phase':'waiting_backup_channel','workers':missing,'epoch':time.time(),'new_resources':False})
   if a.once:return
   time.sleep(60);state=json.loads(STATE.read_text());continue
  provision(state,source);deploy(state,source,assets,plan);state['phase']='running';save(STATE,state)
  if collect(state,source):state['phase']='collected_all';save(STATE,state);print('All three archives verified; cleanup is now eligible');subprocess.run([sys.executable,str(CODE/'cleanup_parallel.py'),'--execute'],check=True);return
  if a.once:return
  time.sleep(90)
if __name__=='__main__':main()
