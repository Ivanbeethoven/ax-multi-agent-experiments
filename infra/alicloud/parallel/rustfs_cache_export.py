#!/usr/bin/env python3
import os,json,hashlib,subprocess,tarfile,time,http.client,urllib.parse
from pathlib import Path
C=json.loads(Path('/root/ax-rustfs-export.private.json').read_text());OUT=Path('/data/parallel-build');OUT.mkdir(exist_ok=True)
R='a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def put(p,u,typ):
 u=urllib.parse.urlsplit(u);c=http.client.HTTPSConnection(u.hostname,timeout=120);c.putrequest('PUT',u.path+'?'+u.query);c.putheader('Content-Type',typ);c.putheader('Content-Length',str(p.stat().st_size));c.endheaders()
 with p.open('rb') as f:
  for z in iter(lambda:f.read(1048576),b''):c.send(z)
 r=c.getresponse();r.read();assert r.status in (200,201),r.status;c.close()
assert Path('/data/results/rebuild-current-stage.txt').read_text().strip()=='brewfs'
assert subprocess.check_output(['git','-C','/data/src/rustfs','rev-parse','HEAD'],text=True).strip()==R
for d in Path('/proc').iterdir():
 if d.name.isdigit():
  try:
   argv=(d/'cmdline').read_bytes().split(b'\0')
   assert not (argv and argv[0].endswith(b'/rustc') and b'rustfs' in argv), 'active rustfs compiler'
  except (FileNotFoundError,PermissionError,ProcessLookupError):pass
roots=['rustfs-target','cargo/registry/cache','cargo/registry/src','cargo/registry/index','cargo/git/db','cargo/git/checkouts']
root=Path('/data/build');members=[]
for rel in roots:
 p=root/rel
 if p.exists():
  for q in [p,*p.rglob('*')]:
   if q.is_symlink():
    target=q.resolve(strict=True);assert target.is_file() and target.is_relative_to((root/rel).resolve()),'symlink escapes its public cache root'
   if q.is_file() and q.name in ('.credentials','credentials','credentials.toml'):
    # AWS SDK contains public fake-credential fixtures. Require byte identity to its committed Git blob.
    tail=q.relative_to(root/'cargo/git/checkouts').parts;repo=root/'cargo/git/checkouts'/tail[0]/tail[1]
    tracked=subprocess.check_output(['git','-C',str(repo),'show','HEAD:'+str(q.relative_to(repo))]);assert hashlib.sha256(tracked).hexdigest()==sha(q),'modified credential fixture'
  members.append(rel)
# Every bare cache remote must be a public GitHub HTTPS URL with no embedded credentials.
import configparser
for cfg in (root/'cargo/git/db').glob('*/config'):
 text=subprocess.run(['git','config','--file',str(cfg),'--get-regexp','^remote\\..*\\.url$'],text=True,capture_output=True).stdout.strip()
 for line in text.splitlines():
  u=urllib.parse.urlsplit(line.split(None,1)[1]);assert not u.username and not u.password and not u.query and ((u.scheme=='https' and u.hostname=='github.com') or (u.scheme=='file' and u.path.startswith('/data/'))),'non-public cached remote'
known=[]
secret_file=Path('/data/secrets/client.env')
if secret_file.is_file():known=[line.split('=',1)[1].encode() for line in secret_file.read_text().splitlines() if line.startswith(('AWS_ACCESS_KEY_ID=','AWS_SECRET_ACCESS_KEY=')) and len(line.split('=',1)[1])>=8]
for rel in members:
 for p in (root/rel).rglob('*'):
  if not p.is_file():continue
  tail=b''
  with p.open('rb') as f:
   for block in iter(lambda:f.read(1048576),b''):
    merged=tail+block
    if any(v in merged for v in known):raise ValueError('actual task credential found in cache')
    tail=merged[-1024:]
out=OUT/'rustfs-public-cache.tar.gz'
with tarfile.open(out,'w:gz',compresslevel=1,dereference=True) as t:
 for rel in members:t.add(root/rel,arcname=rel,recursive=True)
meta={'source':R,'source_instance':C['source_instance'],'created_epoch':time.time(),'cache_sha256':sha(out),'cache_bytes':out.stat().st_size,'cache_roots':members,'lock_sha256':sha('/data/src/rustfs/Cargo.lock'),'cargo_toml_sha256':sha('/data/src/rustfs/Cargo.toml'),'rustc_sha256':sha('/data/toolchain/bin/rustc'),'cargo_sha256':sha('/data/toolchain/bin/cargo'),'rustc_version':subprocess.check_output(['/data/toolchain/bin/rustc','-Vv'],text=True).strip(),'packages':subprocess.check_output(['dpkg-query','-W','gcc','g++','libc6-dev','clang','libssl-dev','cmake','protobuf-compiler'],text=True).strip(),'profile':'release; opt3 thinLTO codegen-units1 debug0 strip=symbols; no environment overrides','command':['build','--release','--locked','--manifest-path','/data/src/rustfs/Cargo.toml','-p','rustfs','--bin','rustfs'],'target_dir':'/data/build/rustfs-target','cargo_home':'/data/build/cargo','symlink_policy':'only regular-file targets resolved within the same public root; archive contains dereferenced bytes, no symlinks','credential_exclusion':'six exact public roots; top-level Cargo private config and HOME excluded; fake SDK fixtures match committed blobs; known actual task credentials scanned absent'}
p=OUT/'cache-manifest.json';p.write_text(json.dumps(meta,indent=2)+'\n');put(out,C['cache_put'],'application/gzip');put(p,C['manifest_put'],'application/json');Path('/data/results/rustfs-external-cache-ready.json').write_text(json.dumps(meta,indent=2)+'\n')
