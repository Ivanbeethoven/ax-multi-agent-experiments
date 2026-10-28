#!/usr/bin/env python3
import json,hashlib,subprocess,os,time,urllib.request,urllib.error,tarfile,http.client,urllib.parse,shutil
from pathlib import Path
C=json.loads(Path('/root/ax-rustfs-build.private.json').read_text());OUT=Path('/data/rustfs-external-build');OUT.mkdir(parents=True,exist_ok=True);R='a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def write(name,value):
 p=OUT/name
 if p.is_file():
  try:
   old=json.loads(p.read_text());previous=old.get('build_attempt_id','legacy-attempt-before-token')
   if previous!=C['build_attempt_id']:
    archive=OUT/'attempts'/previous;archive.mkdir(parents=True,exist_ok=True);shutil.copy2(p,archive/name)
  except (ValueError,TypeError):pass
 t=p.with_suffix('.tmp');t.write_text(json.dumps(value,indent=2)+'\n');t.replace(p)
 archive=OUT/'attempts'/C['build_attempt_id'];archive.mkdir(parents=True,exist_ok=True);shutil.copy2(p,archive/name)
def put(p,url,typ):
 u=urllib.parse.urlsplit(url);c=http.client.HTTPSConnection(u.hostname,timeout=120);c.putrequest('PUT',u.path+'?'+u.query);c.putheader('Content-Type',typ);c.putheader('Content-Length',str(p.stat().st_size));c.endheaders()
 with p.open('rb') as f:
  for z in iter(lambda:f.read(1048576),b''):c.send(z)
 r=c.getresponse();r.read();assert r.status in (200,201),r.status;c.close()
def phase(name,**kw):
 write('status.json',{'stage':name,'instance':C['instance'],'source':R,'epoch':time.time(),'build_attempt_id':C['build_attempt_id'],**kw});put(OUT/'status.json',C['status_put'],'application/json')
def get(url,p,expected=None):
 if p.is_file() and expected and sha(p)==expected:return
 tmp=p.with_suffix('.part')
 with urllib.request.urlopen(url,timeout=120) as response,tmp.open('wb') as f:shutil.copyfileobj(response,f,1024*1024)
 if expected and sha(tmp)!=expected:raise ValueError('download hash mismatch '+p.name)
 tmp.replace(p)
def extract(p,dest,allowed=None):
 dest.mkdir(parents=True,exist_ok=True)
 with tarfile.open(p) as t:
  for m in t.getmembers():
   parts=Path(m.name).parts
   if Path(m.name).is_absolute() or '..' in parts:raise ValueError('unsafe archive member')
   if m.issym() or m.islnk():
    if Path(m.linkname).is_absolute() or '..' in Path(m.linkname).parts:raise ValueError('unsafe archive link')
   if allowed and not any(m.name==a or m.name.startswith(a+'/') for a in allowed):raise ValueError('cache contains unexpected path')
  t.extractall(dest,filter='data')
def run():
 phase('preparing_disk')
 serial=C['data_disk_id'].removeprefix('d-');matches=[p.parent.parent for p in Path('/sys/class/block').glob('*/device/serial') if p.read_text().strip()==serial];assert len(matches)==1
 dev='/dev/'+matches[0].name;assert int(subprocess.check_output(['blockdev','--getsize64',dev]))==500*1024**3
 assert subprocess.check_output(['blkid','-o','value','-s','TYPE',dev],text=True).strip()=='xfs'
 assert subprocess.check_output(['findmnt','-n','-o','SOURCE','-M','/data'],text=True).strip()==dev
 assert '/data/ax-build.swap' in subprocess.check_output(['swapon','--noheadings','--show=NAME'],text=True).splitlines()
 OUT.mkdir(parents=True,exist_ok=True)
 phase('downloading_toolchain_source')
 inp=Path('/data/input');inp.mkdir(exist_ok=True)
 for name,m in C['downloads'].items():get(m['url'],inp/name,m['sha256'])
 tool=Path('/data/toolchain')
 if not (tool/'bin/rustc').is_file():extract(inp/'rust-toolchain-1.99.tar.gz',tool)
 source=Path('/data/src/rustfs')
 if not (source/'.git').is_dir():source.parent.mkdir(exist_ok=True,parents=True);subprocess.run(['git','clone',str(inp/'rustfs.bundle'),str(source)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 subprocess.run(['git','-C',str(source),'checkout','--detach',R],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 assert subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()==R
 phase('waiting_verified_public_cache')
 mpath=OUT/'cache-manifest.json'
 while True:
  try:get(C['manifest_get'],mpath);break
  except urllib.error.HTTPError as e:
   if e.code!=404:raise
   time.sleep(15)
 meta=json.loads(mpath.read_text());assert meta['source']==R
 for p,key in [(source/'Cargo.lock','lock_sha256'),(source/'Cargo.toml','cargo_toml_sha256'),(tool/'bin/rustc','rustc_sha256'),(tool/'bin/cargo','cargo_sha256')]:assert sha(p)==meta[key],key
 versions=subprocess.check_output(['dpkg-query','-W','gcc','g++','libc6-dev','clang','libssl-dev','cmake','protobuf-compiler'],text=True).strip();assert versions==meta['packages'],'compiler/system package versions differ'
 cache=OUT/'rustfs-public-cache.tar.gz';get(C['cache_get'],cache,meta['cache_sha256']);extract(cache,Path('/data/build'),meta['cache_roots'])
 # Reconstruct only verified public registry source mappings, excluding every other Cargo config field.
 source_map=C['public_cargo_sources'];allowed_hosts={'mirrors.aliyun.com','rsproxy.cn','index.crates.io','github.com','mirrors.ustc.edu.cn','mirrors.tuna.tsinghua.edu.cn'}
 lines=[]
 for name,item in source_map.items():
  assert set(item)<={'replace-with','registry'};lines.append('[source.'+json.dumps(name)+']')
  for key,value in item.items():
   if key=='registry':
    u=urllib.parse.urlsplit(value.removeprefix('sparse+'));assert u.scheme=='https' and u.hostname in allowed_hosts and not u.username and not u.password and not u.query
   lines.append(key+' = '+json.dumps(value))
 Path('/data/build/cargo/config.toml').write_text('\n'.join(lines)+'\n')
 # Cache copied from fixed source in exactly the same absolute paths; no Cargo config or credentials copied.
 command=['/data/toolchain/bin/cargo',*meta['command']];env={**os.environ,'PATH':'/data/toolchain/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','CARGO_HOME':'/data/build/cargo','CARGO_TARGET_DIR':'/data/build/rustfs-target','CARGO_BUILD_JOBS':'1','CARGO_NET_GIT_FETCH_WITH_CLI':'true','CARGO_NET_OFFLINE':'true'}
 start=time.time();phase('compiling',command=meta['command'],offline=True)
 with (OUT/'attempts'/C['build_attempt_id']/'build.log').open('ab') as f:p=subprocess.run(command,cwd=source,env=env,stdout=f,stderr=subprocess.STDOUT)
 binary=Path('/data/build/rustfs-target/release/rustfs');provenance={k:meta[k] for k in ('source','lock_sha256','cargo_toml_sha256','rustc_sha256','cargo_sha256','rustc_version','profile','command','target_dir','cargo_home','packages')};provenance.update(builder_instance=C['instance'],build_attempt_id=C['build_attempt_id'],public_cargo_sources_sha256=hashlib.sha256(json.dumps(C['public_cargo_sources'],sort_keys=True).encode()).hexdigest(),build_exit_code=p.returncode,package_versions_match=True,started_epoch=start,finished_epoch=time.time(),memory_max_bytes=26*1024**3,memory_swap_max_bytes=8*1024**3,build_jobs=1,cache_sha256=meta['cache_sha256'])
 if p.returncode:write('provenance.json',provenance);put(OUT/'provenance.json',C['provenance_put'],'application/json');phase('failed',exit_code=p.returncode);return p.returncode
 assert binary.is_file();provenance.update(binary_sha256=sha(binary),binary_bytes=binary.stat().st_size,ldd=subprocess.check_output(['ldd',str(binary)],text=True).strip());write('provenance.json',provenance);put(binary,C['binary_put'],'application/octet-stream');put(OUT/'provenance.json',C['provenance_put'],'application/json');phase('completed',binary_sha256=provenance['binary_sha256']);return 0
try:raise SystemExit(run())
except Exception as e:
 # Exceptions contain only safe stage/field descriptions; URL-bearing HTTP errors are not printed.
 try:phase('failed',error_type=type(e).__name__)
 except Exception:pass
 raise SystemExit(1)
