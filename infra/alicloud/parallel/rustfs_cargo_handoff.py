#!/usr/bin/python3
"""Only the future exact RustFS build is supplied by verified external node-b."""
import sys,os,json,time,hashlib,subprocess
from pathlib import Path
REAL='/data/toolchain/bin/cargo';args=sys.argv[1:]
def manifest(a):
 for i,v in enumerate(a):
  if v=='--manifest-path' and i+1<len(a):return a[i+1]
  if v.startswith('--manifest-path='):return v.split('=',1)[1]
 return None
if manifest(args)!='/data/src/rustfs/Cargo.toml':os.execv(REAL,[REAL,*args])
expected=['build','--release','--locked','--manifest-path','/data/src/rustfs/Cargo.toml','-p','rustfs','--bin','rustfs']
if args!=expected:raise SystemExit('external RustFS handoff refuses different build arguments')
R='a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
status=Path('/data/results/rustfs-external-wait.json');status.write_text(json.dumps({'stage':'waiting_verified_external_build','source':R,'started_epoch':time.time()})+'\n')
attempt=json.loads(Path('/data/results/rustfs-external-attempt.json').read_text())['build_attempt_id']
ready=Path('/data/results/rustfs-external-ready.json')
while not ready.exists():
 failure=Path('/data/results/rustfs-external-import-status.json')
 if failure.is_file() and json.loads(failure.read_text()).get('stage')=='failed' and json.loads(failure.read_text()).get('build_attempt_id')==attempt:raise SystemExit('verified external RustFS pipeline failed; preserve logs and do not mark source-builds-complete')
 time.sleep(10)
m=json.loads(ready.read_text());assert m['source']==R and m['builder_instance']=='i-bp11n3094igg6bfpwxn8' and m['build_attempt_id']==attempt
assert subprocess.check_output(['git','-C','/data/src/rustfs','rev-parse','HEAD'],text=True).strip()==R
for path,key in [('/data/src/rustfs/Cargo.lock','lock_sha256'),('/data/src/rustfs/Cargo.toml','cargo_toml_sha256'),('/data/toolchain/bin/rustc','rustc_sha256'),('/data/toolchain/bin/cargo','cargo_sha256'),('/data/build/rustfs-target/release/rustfs','binary_sha256')]:assert sha(path)==m[key],key
assert m['command']==expected and m['build_exit_code']==0 and m['package_versions_match'] is True
status.write_text(json.dumps({'stage':'external_build_verified_imported','source':R,'builder_instance':m['builder_instance'],'binary_sha256':m['binary_sha256'],'epoch':time.time()})+'\n')
