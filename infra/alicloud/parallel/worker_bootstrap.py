#!/usr/bin/env python3
"""Prepare one owned participant with immutable assets; no compilation, no replay deadline."""
import hashlib,http.client,json,os,secrets,shutil,subprocess,sys,tarfile,time,urllib.parse,urllib.request
from pathlib import Path
C=json.loads(Path(sys.argv[1]).read_text())
if C.get('egress_proxy'):
 os.environ.update(http_proxy=C['egress_proxy'],https_proxy=C['egress_proxy'],HTTP_PROXY=C['egress_proxy'],HTTPS_PROXY=C['egress_proxy'],no_proxy='127.0.0.1,localhost,.aliyuncs.com,.aliyun.com',NO_PROXY='127.0.0.1,localhost,.aliyuncs.com,.aliyun.com')
BASE=Path('/data/parallel');OUT=BASE/'results';OUT.mkdir(parents=True,exist_ok=True)
def save(path,x):
 t=path.with_suffix(path.suffix+'.tmp');t.write_text(json.dumps(x,indent=2)+'\n');t.replace(path)
def call(a):return subprocess.check_output(a,text=True,stderr=subprocess.STDOUT).strip()
def run(a,**kw):return subprocess.run(a,check=True,**kw)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def stage(name,**kw):save(OUT/'worker-state.json',{'worker':C['worker_id'],'phase':name,'epoch':time.time(),**kw})
def download(info,p):
 p.parent.mkdir(parents=True,exist_ok=True)
 if p.is_file() and sha(p)==info['sha256']:return
 tmp=p.with_suffix(p.suffix+'.part')
 with urllib.request.urlopen(info['url'],timeout=120) as response,tmp.open('wb') as f:
  for block in iter(lambda:response.read(1048576),b''):f.write(block)
 if sha(tmp)!=info['sha256']:raise ValueError('download SHA mismatch: '+p.name)
 tmp.replace(p)
def safe_extract(archive,destination):
 destination.mkdir(parents=True,exist_ok=True)
 with tarfile.open(archive) as tar:
  for item in tar.getmembers():
   path=(destination/item.name).resolve()
   if not path.is_relative_to(destination.resolve()) or not item.isfile():raise ValueError('unsafe asset member')
   path.parent.mkdir(parents=True,exist_ok=True)
   with tar.extractfile(item) as f,path.open('wb') as out:shutil.copyfileobj(f,out)
   path.chmod(item.mode & 0o777)
def registry_request(method,path,data=None,headers=None):
 con=http.client.HTTPConnection('127.0.0.1',5000,timeout=120);con.request(method,path,body=data,headers=headers or {});r=con.getresponse();content=r.read();result=(r.status,dict(r.getheaders()),content);con.close();return result
def put_file(url,p,ctype):
 u=urllib.parse.urlsplit(url);con=http.client.HTTPSConnection(u.hostname,timeout=120)
 con.putrequest('PUT',u.path+'?'+u.query);con.putheader('Content-Type',ctype);con.putheader('Content-Length',str(p.stat().st_size));con.endheaders()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):con.send(b)
 r=con.getresponse();r.read();con.close()
 if r.status not in (200,201):raise RuntimeError('result upload HTTP '+str(r.status))
def launch_gate():
 with urllib.request.urlopen(C['launch_gate_url'],timeout=30) as response:gate=json.load(response)
 if not gate.get('active') or gate.get('batch_id')!=C['run_id'] or gate.get('runner_commit')!=C['runner_commit'] or gate.get('plan_sha256')!=C['plan_sha256']:raise ValueError('launch authorization is paused or differs')
 if len(C['arms'])!=4 or set(C['arms'])!={'native-kernel-default','overlay-kernel-default','packed-kernel-default','juicefs-kernel-default'}:raise ValueError('unexpected normal-cache matrix')
def main():
 launch_gate()
 if call(['findmnt','-n','-o','FSTYPE','-M','/data'])!='xfs':raise ValueError('owned XFS data disk must be mounted')
 disk=call(['findmnt','-n','-o','SOURCE','-M','/data'])
 serial=Path('/sys/class/block/'+Path(disk).name+'/device/serial').read_text().strip()
 if serial!=C['data_disk_id'].removeprefix('d-'):raise ValueError('wrong data disk ID')
 if subprocess.run(['pgrep','-x','rustc'],stdout=subprocess.DEVNULL).returncode==0:raise ValueError('compiler is still running; not a measurement host')
 swap=BASE/'worker.swap'
 if not C.get('source_node') and not swap.exists():
  run(['fallocate','-l','16G',str(swap)]);swap.chmod(0o600);run(['mkswap',str(swap)],stdout=subprocess.DEVNULL);run(['swapon',str(swap)])
 run(['sysctl','-w','vm.swappiness=10'],stdout=subprocess.DEVNULL)
 stage('installing_packages')
 with (OUT/'apt.log').open('a') as f:
  env={**os.environ,'DEBIAN_FRONTEND':'noninteractive'}
  run(['apt-get','update','-qq'],stdout=f,stderr=subprocess.STDOUT,env=env)
  run(['apt-get','install','-y','-qq','curl','git','python3','python3-boto3','redis-server','docker.io','docker-registry','fuse3','libfuse3-3','xfsprogs'],stdout=f,stderr=subprocess.STDOUT,env=env)
 run(['systemctl','start','docker'])
 stage('downloading_immutable_assets')
 asset_tar=BASE/'input/compiled-assets.tar.gz';download(C['bundle'],asset_tar);assets=BASE/'assets';safe_extract(asset_tar,assets)
 manifest=C['asset_manifest']
 for item in manifest['binaries'].values():
  if sha(assets/item['path'])!=item['sha256']:raise ValueError('binary SHA mismatch')
 for item in manifest['dependencies'].values():
  if sha(assets/item['path'])!=item['sha256']:raise ValueError('library SHA mismatch')
 for name in ('brewfs','packed_v3_snapshot_fixture','full-fuse-replay','ax-fs-probe','juicefs'):
  result=subprocess.run(['ldd',str(assets/'bin'/name)],capture_output=True,text=True)
  (OUT/('ldd-'+name+'.txt')).write_text(result.stdout+result.stderr)
  if 'not found' in result.stdout:raise ValueError('missing runtime dependency: '+name)
 stage('restoring_exact_image')
 try: registry_ready=registry_request('GET','/v2/')[0]==200
 except OSError: registry_ready=False
 if not registry_ready:
  directory=BASE/'registry';directory.mkdir(exist_ok=True)
  (directory/'config.yml').write_text('version: 0.1\nlog:\n  level: warn\nstorage:\n  filesystem:\n    rootdirectory: /data/parallel/registry/files\nhttp:\n  addr: 127.0.0.1:5000\n')
  run(['systemd-run','--unit=ax-parallel-registry','--property=RemainAfterExit=yes','--property=RuntimeMaxSec=infinity','/usr/bin/docker-registry','serve',str(directory/'config.yml')])
  for _ in range(30):
   try:
    if registry_request('GET','/v2/')[0]==200:break
   except OSError:pass
   time.sleep(1)
 for blob in manifest['registry_image']['blobs']:
  p=assets/blob['path'];digest=blob['digest']
  if 'sha256:'+sha(p)!=digest:raise ValueError('image blob SHA mismatch')
  if registry_request('HEAD','/v2/ax/rustfs/blobs/'+digest)[0]==200:continue
  status,headers,_=registry_request('POST','/v2/ax/rustfs/blobs/uploads/')
  if status!=202:raise ValueError('cannot begin registry import')
  location=urllib.parse.urlsplit(headers.get('Location',headers.get('location')))
  path=location.path+'?'+location.query+('&' if location.query else '')+'digest='+digest
  with p.open('rb') as f:
   status,_,_=registry_request('PUT',path,f,{'Content-Type':'application/octet-stream','Content-Length':str(p.stat().st_size)})
  if status!=201:raise ValueError('registry blob import failed')
 raw=(assets/manifest['registry_image']['manifest_path']).read_bytes()
 if 'sha256:'+hashlib.sha256(raw).hexdigest()!=manifest['image_digest']:raise ValueError('manifest changed')
 if registry_request('PUT','/v2/ax/rustfs/manifests/'+manifest['image_digest'],raw,{'Content-Type':manifest['registry_image']['media_type']})[0]!=201:raise ValueError('manifest import failed')
 image='127.0.0.1:5000/ax/rustfs@'+manifest['image_digest'];run(['docker','pull',image],stdout=(OUT/'image-pull.log').open('w'),stderr=subprocess.STDOUT)
 identity=json.loads(call(['docker','image','inspect',image]))[0]
 if identity['Id']!=manifest['image_id'] or identity['Config']['Labels'].get('org.opencontainers.image.revision')!=manifest['rustfs_source']:raise ValueError('runtime image source mismatch')
 stage('preparing_fixed_sources_and_trace')
 base_bundle=BASE/'input/experiment-base.bundle';delta=BASE/'input/experiment-delta.bundle'
 download(C['base_bundle'],base_bundle);download(C['runner_bundle'],delta)
 repo=BASE/'repo'
 if not repo.exists():run(['git','clone','--quiet',str(base_bundle),str(repo)])
 run(['git','-C',str(repo),'fetch','--quiet',str(delta),C['runner_bundle']['ref']])
 run(['git','-C',str(repo),'checkout','--quiet','--detach',C['runner_commit']])
 if call(['git','-C',str(repo),'rev-parse','HEAD'])!=C['runner_commit'] or call(['git','-C',str(repo),'status','--porcelain']):raise ValueError('worker repo is not the clean frozen commit')
 trace_tar=BASE/'input/trace.tar.gz';download(C['trace_archive'],trace_tar)
 case_parent=BASE/'input/case';case_parent.mkdir(exist_ok=True)
 case=case_parent/'pytest-full-02'
 if not case.exists():run(['tar','--no-same-owner','-xzf',str(trace_tar),'-C',str(case_parent)])
 sys.path.insert(0,str(repo/'scripts'));from full_trace_common import tree
 if not (case/'initial-tree.json').is_file():save(case/'initial-tree.json',tree(case/'initial'))
 inventory={}
 for p in [case/'metrics.json',case/'initial-tree.json',case/'capture/events.jsonl',*[p for r in (case/'initial',case/'capture/blobs') for p in r.rglob('*') if p.is_file() and not p.is_symlink()]]:inventory[p.relative_to(case).as_posix()]=sha(p)
 trace_manifest=BASE/'input/trace-manifest.json';save(trace_manifest,inventory)
 stage('configuring_equal_resources')
 subprocess.run(['systemctl','stop','redis-server'],check=True)
 secret_dir=BASE/'secrets';secret_dir.mkdir(mode=0o700,exist_ok=True);access=secrets.token_hex(12);secret=secrets.token_hex(24)
 client_env=secret_dir/'client.env';client_env.write_text(f'AWS_ACCESS_KEY_ID={access}\nAWS_SECRET_ACCESS_KEY={secret}\n');client_env.chmod(0o600)
 if C.get('egress_proxy'):
  with client_env.open('a') as f:
   for key in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','no_proxy','NO_PROXY'):f.write(key+'='+os.environ[key]+'\n')
 server_env=secret_dir/'rustfs.env';server_env.write_text(f'RUSTFS_ACCESS_KEY={access}\nRUSTFS_SECRET_KEY={secret}\nRUSTFS_VOLUMES=/data\nRUSTFS_ADDRESS=127.0.0.1:9000\nRUSTFS_CONSOLE_ENABLE=false\n');server_env.chmod(0o600)
 # On the original builder, the verified packaging probe is the only prior S3 service.
 if C.get('source_node'):
  previous=subprocess.run(['docker','inspect','--format','{{.Image}}','ax-rustfs'],capture_output=True,text=True)
  if previous.returncode==0:
   if previous.stdout.strip()!=manifest['image_id']:raise ValueError('unexpected old server identity')
   run(['docker','stop','ax-rustfs'],stdout=subprocess.DEVNULL)
 data=BASE/'rustfs';data.mkdir(exist_ok=True)
 run(['docker','run','-d','--name','ax-parallel-rustfs','--network','host','--cpus','2','--memory','16g','--memory-swap','16g','--env-file',str(server_env),'-v',str(data)+':/data',image],stdout=(OUT/'container-id.txt').open('w'))
 redis_dir=BASE/'redis';redis_dir.mkdir(exist_ok=True)
 run(['systemd-run','--unit=ax-parallel-redis','--property=RemainAfterExit=yes','--property=RuntimeMaxSec=infinity','--property=CPUQuota=100%','--property=MemoryMax=2G','--property=MemorySwapMax=0','/usr/bin/redis-server','--bind','127.0.0.1','--port','6380','--dir',str(redis_dir),'--appendonly','yes','--appendfsync','always','--maxmemory','1536mb','--maxmemory-policy','noeviction'])
 for _ in range(120):
  try:
   with urllib.request.urlopen('http://127.0.0.1:9000/health',timeout=3) as r:
    if r.status==200:break
  except OSError:pass
  time.sleep(1)
 host=json.loads(call(['docker','inspect','--format','{{json .HostConfig}}','ax-parallel-rustfs']))
 if host['NanoCpus']!=2000000000 or host['Memory']!=16*1024**3 or host['MemorySwap']!=16*1024**3:raise ValueError('RustFS resources differ')
 redis_props={k:call(['systemctl','show','ax-parallel-redis','-p',k,'--value']) for k in ('CPUQuotaPerSecUSec','MemoryMax','MemorySwapMax')}
 if redis_props['MemoryMax']!=str(2*1024**3) or redis_props['MemorySwapMax']!='0':raise ValueError('Redis memory resources differ')
 group=call(['systemctl','show','ax-parallel-redis','-p','ControlGroup','--value'])
 actual=(Path('/sys/fs/cgroup')/group.lstrip('/')/'cpu.max').read_text().split()
 if actual[0]=='max' or int(actual[0])!=int(actual[1]):raise ValueError('Redis CPU quota differs')
 pin=json.loads((assets/'metadata/backend-pin.json').read_text());pin['image_reference']=image
 pin_path=BASE/'backend-pin.json';save(pin_path,pin)
 binary=lambda name:{'path':str(assets/'bin'/name),'sha256':manifest['binaries'][name]['sha256']}
 brewfs={**binary('brewfs'),'source_sha':manifest['brewfs_source']}
 config={'run_id':C['run_id'],'worker_id':C['worker_id'],'output':str(BASE/'cell-results'),'work_base':str(BASE/'workspaces'),'environment_file':str(client_env),'arms':C['arms'],'worker_qualification_file':str(OUT/'qualification.json'),'client_memory_bytes':8*1024**3,'client_cpu_percent':400,'fixture':{**binary('packed_v3_snapshot_fixture'),'source_sha':manifest['brewfs_source']},'base_spec':{'method':'native','backend_pin':str(pin_path),'rustfs_container':'ax-parallel-rustfs','endpoint':'http://127.0.0.1:9000','region':'us-east-1','bucket':'unused-overridden-per-cell','redis_url':'redis://127.0.0.1:6380/0','data_mount':'/data','brewfs':brewfs,'juicefs':binary('juicefs'),'probe':binary('ax-fs-probe'),'replayer':binary('full-fuse-replay'),'trace_case':str(case),'trace_manifest':str(trace_manifest),'trace_manifest_sha256':sha(trace_manifest),'observe':True}}
 save(BASE/'worker-config.json',config);save(OUT/'qualification.json',{'worker':C['worker_id'],'runner_commit':C['runner_commit'],'runner_dirty':False,'asset_manifest_sha256':C['asset_manifest_sha256'],'image_id':identity['Id'],'image_digest':manifest['image_digest'],'rustfs_resources':{k:host[k] for k in ('NanoCpus','Memory','MemorySwap')},'redis_resources':redis_props,'trace_archive_sha256':sha(trace_tar),'prepared':True})
 launch_gate()
 stage('ready_to_run')
 archive=BASE/'results.tar.gz'
 subprocess.run(['systemd-run','--unit=ax-parallel-matrix','--wait','--property=RuntimeMaxSec=infinity','--property=TimeoutStartSec=infinity','--property=EnvironmentFile='+str(client_env),'/usr/bin/python3',str(repo/'harness/run_trace_matrix.py'),'run','--config',str(BASE/'worker-config.json'),'--archive',str(archive)],check=False)
 if not archive.is_file():raise RuntimeError('matrix ended without archive; retain all resources')
 # Record the actual host libraries; archived dependencies are retained as
 # provenance, not silently substituted for the worker OS loader's libraries.
 actual=OUT/'actual-runtime-packages.txt'
 actual.write_text(call(['dpkg-query','-W','-f=${Package}=${Version}\\n','libc6','libfuse3-3','libzstd1','redis-server','docker.io']))
 preparation=BASE/'cell-results/worker-preparation';preparation.mkdir(exist_ok=True)
 for p in OUT.glob('*'):
  if p.is_file() and not p.is_symlink():shutil.copy2(p,preparation/p.name)
 for line in client_env.read_text().splitlines():
  if '=' in line:
   key,value=line.split('=',1);os.environ[key]=value
 sys.path.insert(0,str(repo/'harness'));from run_trace_matrix import archive_results
 terminal=json.loads((BASE/'cell-results/terminal.json').read_text())
 archive=BASE/'results-with-preparation.tar.gz';archive_results(BASE/'cell-results',archive,terminal)
 receipt=json.loads(archive.with_name(archive.name+'.receipt.json').read_text())
 if receipt['sha256']!=sha(archive) or receipt['worker_id']!=C['worker_id']:raise ValueError('matrix receipt mismatch')
 for attempt in range(5):
  try:
   put_file(C['result_upload_url'],archive,'application/gzip');put_file(C['receipt_upload_url'],archive.with_name(archive.name+'.receipt.json'),'application/json');break
  except OSError:
   if attempt==4:raise
   time.sleep(15)
 stage('results_uploaded',receipt=receipt)
if __name__=='__main__':
 try:main()
 except Exception as e:
  stage('failed',error_type=type(e).__name__)
  state=subprocess.run(['systemctl','show','ax-parallel-matrix','-p','SubState','--value'],capture_output=True,text=True).stdout.strip()
  if state in ('running','start','start-pre','start-post','stop','stop-sigterm','stop-sigkill'):
   raise
  prior=BASE/'cell-results/trace-arms.json'
  methods=json.loads(prior.read_text()).get('arms',{}) if prior.is_file() else {label:{'status':'not_run'} for label in C['arms']}
  if any(v.get('status') in ('running','unknown') for v in methods.values()):raise
  terminal={'run_id':C['run_id'],'worker_id':C['worker_id'],'terminal_reason':'failed','methods':methods,'failure_class':type(e).__name__,'strict_correctness':False}
  save(OUT/'terminal.json',terminal);archive=BASE/'preparation-failed.tar.gz'
  patterns=[b'OSSAccessKeyId=',b'Signature=',b'X-Amz-Signature=',b'X-Amz-Credential=']
  env=BASE/'secrets/client.env'
  if env.is_file():patterns += [v.split('=',1)[1].encode() for v in env.read_text().splitlines() if v.startswith(('AWS_ACCESS_KEY_ID=','AWS_SECRET_ACCESS_KEY='))]
  for f in OUT.glob('*'):
   if not f.is_file():continue
   tail=b''
   with f.open('rb') as stream:
    for chunk in iter(lambda:stream.read(1048576),b''):
     data=tail+chunk
     if any(value and value in data for value in patterns):raise ValueError('preparation log credential scan failed')
     tail=data[-1024:]
  with tarfile.open(archive,'w:gz') as tar:
   for f in OUT.glob('*'):
    if f.is_file() and not f.is_symlink():tar.add(f,arcname='results/'+f.name,recursive=False)
  receipt={'run_id':C['run_id'],'worker_id':C['worker_id'],'bytes':archive.stat().st_size,'sha256':sha(archive),'terminal_reason':'failed','known_credential_scan':'passed'}
  rp=BASE/'preparation-failed.receipt.json';save(rp,receipt)
  put_file(C['result_upload_url'],archive,'application/gzip');put_file(C['receipt_upload_url'],rp,'application/json')
  raise
