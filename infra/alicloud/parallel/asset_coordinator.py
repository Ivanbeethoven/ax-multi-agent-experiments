#!/usr/bin/env python3
"""Wait for exact source builds, package once, export an immutable reusable asset bundle."""
import hashlib,http.client,json,os,shutil,subprocess,tarfile,time,urllib.parse,urllib.request,uuid
from pathlib import Path
B='350dc108a3bffcb5e7e27cc4279dfb5729febe60'; R='a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed'
OUT=Path('/data/parallel-assets');RESULT=Path('/data/results');OUT.mkdir(parents=True,exist_ok=True)

def save(p,x):
 t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(x,indent=2)+'\n');t.replace(p)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def command(a):return subprocess.check_output(a,text=True).strip()
def phase(stage,**kw):save(RESULT/'parallel-assets-status.json',{'stage':stage,'epoch':time.time(),**kw})
def upload(path,url,ctype):
 u=urllib.parse.urlsplit(url);con=http.client.HTTPSConnection(u.hostname,timeout=120)
 con.putrequest('PUT',u.path+'?'+u.query);con.putheader('Content-Type',ctype);con.putheader('Content-Length',str(path.stat().st_size));con.endheaders()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):con.send(block)
 response=con.getresponse();response.read()
 if response.status not in (200,201):raise RuntimeError('asset upload HTTP '+str(response.status))
 con.close()
def run():
 while True:
  state=command(['systemctl','show','ax-main-rebuild-sequential','-p','SubState','--value'])
  rc=command(['systemctl','show','ax-main-rebuild-sequential','-p','ExecMainStatus','--value'])
  if state=='exited' and rc=='0':break
  if state in ('failed','dead','exited'):raise RuntimeError('source build ended '+state+' '+rc)
  phase('waiting_build');time.sleep(30)
 if command(['git','-C','/data/src/brewfs','rev-parse','HEAD'])!=B or command(['git','-C','/data/src/rustfs','rev-parse','HEAD'])!=R:raise RuntimeError('wrong source revision')
 for marker in ('source-builds-complete','brewfs-350-ready'):
  if not (RESULT/marker).is_file():raise RuntimeError('missing build marker '+marker)
 phase('packaging')
 if not (RESULT/'backend-ready').exists():
  if subprocess.run(['systemctl','is-active','--quiet','ax-main-registry']).returncode:
   subprocess.run(['systemd-run','--unit=ax-main-registry','--property=RuntimeMaxSec=infinity','--property=TimeoutStartSec=infinity','--property=RemainAfterExit=yes','--property=StandardOutput=append:/var/log/ax-main/registry.log','--property=StandardError=append:/var/log/ax-main/registry.log','/usr/bin/docker-registry','serve','/data/registry/config.yml'],check=True)
  for _ in range(30):
   if subprocess.run(['curl','-fsS','http://127.0.0.1:5000/v2/'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
   time.sleep(1)
  with (OUT/'packaging.log').open('a') as f:subprocess.run(['bash','/root/ax-main-package-rustfs.sh'],stdout=f,stderr=subprocess.STDOUT,check=True)
 pin=json.loads((RESULT/'backend-pin.json').read_text())
 if pin.get('blocked_reason') or pin['frozen_commit']!=R or pin['brewfs_client_sha']!=B:raise RuntimeError('pin is unqualified')
 image=pin['image_reference'];identity=json.loads(command(['docker','image','inspect',image]))[0]
 if identity['Config']['Labels'].get('org.opencontainers.image.revision')!=R:raise RuntimeError('image revision mismatch')
 if not any(v.endswith('@'+pin['image_digest']) for v in identity.get('RepoDigests',[])):raise RuntimeError('image digest mismatch')
 tools={'brewfs':Path('/data/build/brewfs-target/release/brewfs'),'packed_v3_snapshot_fixture':Path('/data/build/brewfs-target/release/packed_v3_snapshot_fixture'),'full-fuse-replay':Path('/data/input/full-fuse-replay'),'ax-fs-probe':Path('/data/build/probe-target/release/ax-fs-probe'),'juicefs':Path('/data/input/juicefs')}
 if not tools['ax-fs-probe'].is_file():
  env={**os.environ,'PATH':'/data/toolchain/bin:'+os.environ['PATH'],'CARGO_HOME':'/data/build/cargo','CARGO_TARGET_DIR':'/data/build/probe-target','CARGO_BUILD_JOBS':'1'}
  with (OUT/'probe-build.log').open('a') as f:subprocess.run(['/data/toolchain/bin/cargo','build','--release','--locked','--manifest-path','/data/src/experiment/tools/fs-probe/Cargo.toml'],env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
 if sha(tools['juicefs'])!='6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc':raise RuntimeError('JuiceFS binary mismatch')
 stage=OUT/('stage-'+uuid.uuid4().hex);(stage/'bin').mkdir(parents=True,exist_ok=True);(stage/'metadata').mkdir(exist_ok=True)
 metadata={'schema_version':1,'brewfs_source':B,'rustfs_repository':'https://github.com/Ivanbeethoven/rustfs','rustfs_branch':'feat/dynamic-block-size-rebased','rustfs_source':R,'image_reference':image,'image_digest':pin['image_digest'],'image_id':identity['Id'],'image_revision':R,'binaries':{},'dependencies':{},'trace_archive_sha256':'9953d90517eaf520814150f80abccb11f9d74b7ac84ecc56fa394a5f8495aa3d','scope':'compiled assets only; no qualification of measured layout or distributed performance'}
 for name,p in tools.items():
  if not p.is_file():raise RuntimeError('missing binary '+name)
  shutil.copy2(p,stage/'bin'/name)
  metadata['binaries'][name]={'path':'bin/'+name,'sha256':sha(p),'bytes':p.stat().st_size}
  result=subprocess.run(['ldd',str(p)],capture_output=True,text=True)
  (stage/'metadata'/('ldd-'+name+'.txt')).write_text(result.stdout+result.stderr)
  for word in result.stdout.split():
   if word.startswith('/') and Path(word).is_file():
    library=Path(word);key=str(library)
    if key not in metadata['dependencies']:
     dst=stage/'dependencies'/library.relative_to('/');dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(library,dst)
     metadata['dependencies'][key]={'path':str(dst.relative_to(stage)),'sha256':sha(library)}
 metadata['rustfs_build_provenance']='metadata/rustfs-external-ready.json' if (RESULT/'rustfs-external-ready.json').is_file() else 'local serial source build'
 metadata['juicefs_version']=command([str(tools['juicefs']),'--version'])
 shutil.copy2(RESULT/'backend-pin.json',stage/'metadata/backend-pin.json')
 for name in ('brewfs-binary-sha256-350.txt','rustfs-binary-sha256-parallel.txt','rustfs-ldd.txt','parallel-plan-switch.json','rustfs-external-ready.json','rustfs-external-import-status.json'):
  if (RESULT/name).is_file():shutil.copy2(RESULT/name,stage/'metadata'/name)
 image_root=stage/'registry-image';blob_root=image_root/'blobs/sha256';blob_root.mkdir(parents=True,exist_ok=True)
 base='http://127.0.0.1:5000/v2/ax/rustfs/'
 req=urllib.request.Request(base+'manifests/'+pin['image_digest'],headers={'Accept':'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'})
 with urllib.request.urlopen(req,timeout=30) as response: raw=response.read();media=response.headers.get('Content-Type','').split(';')[0]
 if 'sha256:'+hashlib.sha256(raw).hexdigest()!=pin['image_digest']:raise RuntimeError('image manifest hash mismatch')
 m=json.loads(raw);(image_root/'manifest.json').write_bytes(raw);blobs=[]
 for entry in [m['config'],*m['layers']]:
  d=entry['digest']
  if not d.startswith('sha256:') or len(d)!=71:raise RuntimeError('unsupported blob hash')
  target=blob_root/d.split(':')[1]
  with urllib.request.urlopen(base+'blobs/'+d,timeout=120) as response,target.open('wb') as f:
   for chunk in iter(lambda:response.read(1048576),b''):f.write(chunk)
  if 'sha256:'+sha(target)!=d or target.stat().st_size!=entry['size']:raise RuntimeError('blob hash mismatch')
  blobs.append({'digest':d,'path':str(target.relative_to(stage)),'bytes':target.stat().st_size})
 config=json.loads((blob_root/m['config']['digest'].split(':')[1]).read_text())
 if any(any(k in v.upper() for k in ('ACCESS_KEY','SECRET_KEY','TOKEN=','PASSWORD=')) for v in config.get('config',{}).get('Env',[])):raise RuntimeError('credential-like image environment')
 metadata['registry_image']={'manifest_digest':pin['image_digest'],'manifest_path':'registry-image/manifest.json','media_type':media,'blobs':blobs}
 values=[v.split('=',1)[1].encode() for v in Path('/data/secrets/client.env').read_text().splitlines() if v.startswith(('AWS_ACCESS_KEY_ID=','AWS_SECRET_ACCESS_KEY='))]
 for selected in stage.rglob('*'):
  if selected.is_symlink():raise RuntimeError('asset symlink refused')
  if not selected.is_file():continue
  tail=b''
  with selected.open('rb') as f:
   for chunk in iter(lambda:f.read(1048576),b''):
    block=tail+chunk
    if any(v and v in block for v in values):raise RuntimeError('credential scan failed')
    tail=block[-512:]
 metadata['credential_scan']='known ephemeral credentials absent from whitelisted assets'
 allowed={v['path'] for v in metadata['binaries'].values()} | {v['path'] for v in metadata['dependencies'].values()} | {v['path'] for v in metadata['registry_image']['blobs']}
 allowed |= {'registry-image/manifest.json','metadata/backend-pin.json','metadata/asset-manifest.json'}
 allowed |= {'metadata/ldd-'+name+'.txt' for name in tools}
 allowed |= {'metadata/'+name for name in ('brewfs-binary-sha256-350.txt','rustfs-binary-sha256-parallel.txt','rustfs-ldd.txt','parallel-plan-switch.json','rustfs-external-ready.json','rustfs-external-import-status.json') if (stage/'metadata'/name).is_file()}
 metadata['file_whitelist']=sorted(allowed)
 save(stage/'metadata/asset-manifest.json',metadata)
 actual={p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
 if actual!=allowed or any(p.is_symlink() for p in stage.rglob('*')):raise RuntimeError('asset stage differs from exact whitelist')
 archive=OUT/'compiled-assets.tar.gz'
 with tarfile.open(archive,'w:gz') as t:
  for p in sorted(stage.rglob('*')):
   if p.is_file():t.add(p,arcname=p.relative_to(stage).as_posix(),recursive=False)
 metadata['bundle']={'key':'compiled-assets.tar.gz','sha256':sha(archive),'bytes':archive.stat().st_size}
 save(OUT/'asset-manifest.json',metadata)
 urls=json.loads(Path('/root/ax-main-parallel-assets-transfer.json').read_text())
 phase('uploading',archive_bytes=archive.stat().st_size)
 for attempt in range(5):
  try:
   upload(archive,urls['compiled-assets.tar.gz'],'application/gzip');upload(OUT/'asset-manifest.json',urls['asset-manifest.json'],'application/json');break
  except Exception:
   if attempt==4:raise
   time.sleep(15)
 save(RESULT/'parallel-assets-manifest.json',metadata);phase('ready',manifest_sha256=sha(OUT/'asset-manifest.json'))
if __name__=='__main__':
 try:run()
 except Exception as e:
  phase('failed',error_type=type(e).__name__,error=str(e) if 'http' not in str(e).lower() else 'HTTP operation failed')
  raise
