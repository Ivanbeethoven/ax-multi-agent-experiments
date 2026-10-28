#!/usr/bin/env python3
import json,time,hashlib,subprocess,urllib.request,urllib.error,shutil,os
from pathlib import Path
C=json.loads(Path('/root/ax-rustfs-import.private.json').read_text());R='a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed';OUT=Path('/data/results')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def save(p,m):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(m,indent=2)+'\n');t.replace(p)
def candidate_state(provenance,status,builder,attempt):
 if status and status.get('build_attempt_id')==attempt:
  if status.get('instance')!=builder or status.get('source')!=R:raise ValueError('builder status identity differs')
  if status.get('stage')=='failed':return 'failed'
 if provenance and provenance.get('build_attempt_id')==attempt:
  if provenance.get('builder_instance')!=builder or provenance.get('source')!=R:raise ValueError('build provenance identity differs')
  return 'ready'
 return 'pending'
def run():
 p=OUT/'rustfs-external-import-status.json';save(p,{'stage':'waiting_node_b_provenance','builder_instance':C['builder_instance'],'epoch':time.time(),'build_attempt_id':C['build_attempt_id']})
 while True:
  m=None;status=None
  try:
   with urllib.request.urlopen(C['provenance_get'],timeout=60) as response:m=json.load(response)
  except urllib.error.HTTPError as e:
   if e.code!=404:raise
  try:
   with urllib.request.urlopen(C['builder_status_get'],timeout=30) as response:status=json.load(response)
  except urllib.error.HTTPError as e:
   if e.code!=404:raise
  decision=candidate_state(m,status,C['builder_instance'],C['build_attempt_id'])
  if decision=='failed':raise RuntimeError('fixed external builder reported failure')
  if decision=='ready':break
  time.sleep(15)
 assert m['build_exit_code']==0,'external compilation failed'
 assert m['source']==R and m['builder_instance']==C['builder_instance'] and m['package_versions_match'] is True
 assert m['command']==['build','--release','--locked','--manifest-path','/data/src/rustfs/Cargo.toml','-p','rustfs','--bin','rustfs']
 assert subprocess.check_output(['git','-C','/data/src/rustfs','rev-parse','HEAD'],text=True).strip()==R
 for path,key in [('/data/src/rustfs/Cargo.lock','lock_sha256'),('/data/src/rustfs/Cargo.toml','cargo_toml_sha256'),('/data/toolchain/bin/rustc','rustc_sha256'),('/data/toolchain/bin/cargo','cargo_sha256')]:assert sha(path)==m[key],key
 assert subprocess.check_output(['dpkg-query','-W','gcc','g++','libc6-dev','clang','libssl-dev','cmake','protobuf-compiler'],text=True).strip()==m['packages']
 dest=Path('/data/build/rustfs-target/release/rustfs');dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix('.external.part')
 with urllib.request.urlopen(C['binary_get'],timeout=120) as response,tmp.open('wb') as f:shutil.copyfileobj(response,f,1024*1024)
 assert sha(tmp)==m['binary_sha256'] and tmp.stat().st_size==m['binary_bytes']
 tmp.chmod(0o755);tmp.replace(dest);m['imported_source_instance']=C['source_instance'];m['imported_epoch']=time.time();m['transfer_path']='same-region private OSS internal endpoint; no local artifact relay';save(OUT/'rustfs-external-ready.json',m);save(p,{'stage':'verified_binary_imported','builder_instance':C['builder_instance'],'binary_sha256':m['binary_sha256'],'epoch':time.time(),'build_attempt_id':C['build_attempt_id']})
try:run()
except Exception as e:
 save(OUT/'rustfs-external-import-status.json',{'stage':'failed','error_type':type(e).__name__,'epoch':time.time(),'build_attempt_id':C['build_attempt_id']});raise SystemExit(1)
