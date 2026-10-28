#!/usr/bin/env python3
import json,time,subprocess,hashlib,http.client,urllib.parse,tarfile
from pathlib import Path
C=json.loads(Path('/root/ax-rustfs-checkpoint.private.json').read_text());OUT=Path('/data/rustfs-external-build')
def put(p,url,typ):
 u=urllib.parse.urlsplit(url);c=http.client.HTTPSConnection(u.hostname,timeout=120);c.putrequest('PUT',u.path+'?'+u.query);c.putheader('Content-Type',typ);c.putheader('Content-Length',str(p.stat().st_size));c.endheaders()
 with p.open('rb') as f:
  for z in iter(lambda:f.read(1048576),b''):c.send(z)
 r=c.getresponse();r.read();assert r.status in (200,201);c.close()
def write(p,m):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(m,indent=2)+'\n');t.replace(p)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
while True:
 try:
  status=json.loads((OUT/'status.json').read_text());assert status['build_attempt_id']==C['build_attempt_id']
  procs=[]
  for d in Path('/proc').iterdir():
   if not d.name.isdigit():continue
   try:
    name=(d/'comm').read_text().strip()
    if name not in ('cargo','rustc','cc','ld','lld','clang'):continue
    argv=(d/'cmdline').read_bytes().decode(errors='replace').split('\0');st=(d/'stat').read_text().split();memory={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in (d/'status').read_text().splitlines() if x.startswith(('VmRSS:','VmSwap:'))}
    procs.append({'pid':int(d.name),'name':name,'cpu_jiffies':int(st[13])+int(st[14]),'memory':memory,'crate_name':argv[argv.index('--crate-name')+1] if '--crate-name' in argv else None,'emit':next((a.split('=',1)[1] for a in argv if a.startswith('--emit=')),None)})
   except (FileNotFoundError,PermissionError,ProcessLookupError,IndexError):continue
  service=subprocess.check_output(['systemctl','show','ax-rustfs-build-only','-p','ActiveState','-p','SubState','-p','ExecMainStatus','-p','MemoryCurrent','-p','MemoryPeak','-p','CPUUsageNSec'],text=True).strip();m={'epoch':time.time(),'instance':C['instance'],'build_attempt_id':C['build_attempt_id'],'status':status,'service':service,'processes':procs};write(OUT/'checkpoint.json',m);put(OUT/'checkpoint.json',C['checkpoint_put'],'application/json')
  if status['stage'] in ('failed','completed') and not procs:
   members=[]
   for p in [OUT/'status.json',OUT/'provenance.json',OUT/'cache-manifest.json',OUT/'build.log',*(OUT/'attempts').rglob('*')]:
    if not p.is_file():continue
    assert not p.is_symlink();data=p.read_bytes();assert not any(v in data for v in (b'OSSAccessKeyId=',b'Signature=',b'AWS_SECRET_ACCESS_KEY=')),'secret marker in build evidence';members.append(p)
   archive=OUT/'build-evidence.tar.gz'
   with tarfile.open(archive,'w:gz',compresslevel=1) as t:
    for p in sorted(set(members)):t.add(p,arcname='build-evidence/'+str(p.relative_to(OUT)),recursive=False)
   put(archive,C['archive_put'],'application/gzip');receipt={'instance':C['instance'],'build_attempt_id':C['build_attempt_id'],'stage':status['stage'],'sha256':sha(archive),'bytes':archive.stat().st_size,'scan':'passed exact allowlist and signed-url marker scan'};write(OUT/'build-evidence-receipt.json',receipt);put(OUT/'build-evidence-receipt.json',C['receipt_put'],'application/json');break
 except Exception:
  # Control/OSS transient failures must not alter or interrupt the build.
  pass
 time.sleep(30)
