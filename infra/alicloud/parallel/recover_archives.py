#!/usr/bin/env python3
"""Preserve completed cell evidence in a separate scanned mirror; never rerun a cell."""
import argparse,hashlib,http.client,json,os,re,shutil,subprocess,sys,time,urllib.parse
from pathlib import Path
RUNNER='8e815bd91060bae90a2699668cc5d87e32b2e4c1'
MARKERS=(b'OSSAccessKeyId=',b'Signature=',b'X-Amz-Signature=',b'X-Amz-Credential=')
def digest_bytes(value):return hashlib.sha256(value).hexdigest()
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def sanitized_mirror(source,destination,patterns,allowed_hits):
 source=Path(source);destination=Path(destination)
 if destination.exists() or not source.is_dir():raise ValueError('new mirror of an existing result directory required')
 inventory={};changed=[]
 # Validate the complete input before creating any public mirror.
 files=[]
 for p in sorted(source.rglob('*')):
  if p.is_symlink():raise ValueError('symlink in private result tree')
  if not p.is_file() or p.name=='archive-manifest.json':continue
  relative=p.relative_to(source).as_posix();raw=p.read_bytes()
  hits={key for key,value in patterns.items() if value and value in raw}
  if any(marker in raw for marker in MARKERS):raise ValueError('unclassified signed-URL marker; retain private results')
  if hits and (relative not in allowed_hits or hits-set(allowed_hits[relative])):raise ValueError('unreviewed credential location: '+relative)
  clean=raw;counts={}
  for key in sorted(hits,key=lambda k:len(patterns[k]),reverse=True):
   counts[key]=clean.count(patterns[key]);clean=clean.replace(patterns[key],b'[REDACTED_'+key.encode()+b']')
  if any(value and value in clean for value in patterns.values()):raise ValueError('mirror still contains known credentials')
  inventory[relative]={'original_sha256':digest_bytes(raw),'mirror_sha256':digest_bytes(clean),'bytes':len(raw)}
  if hits:changed.append({'path':relative,'pattern_labels':sorted(hits),'replacement_counts':counts})
  files.append((relative,clean,p.stat().st_mode & 0o777))
 destination.mkdir(parents=True)
 for relative,clean,mode in files:
  p=destination/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(clean);p.chmod(mode)
 return {'private_originals_retained':True,'files':inventory,'redactions':changed,'measurement_values_modified':False}
def quiescent(attempt):
 names=subprocess.check_output(['ps','-eo','comm='],text=True).splitlines()
 if any(n.strip() in {'full-fuse-repla','brewfs','juicefs','mount.juicefs','cargo','rustc','packed_v3_snaps'} for n in names):raise ValueError('a workload still runs; no archive recovery')
 for prefix in ('ax-parallel-prepare-','ax-parallel-matrix-'):
  p=subprocess.run(['systemctl','show',prefix+attempt,'-p','LoadState','-p','SubState'],capture_output=True,text=True)
  v=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
  if p.returncode or v.get('LoadState')!='loaded' or v.get('SubState') not in ('failed','exited','dead'):raise ValueError('worker service is not confirmed terminal')
 units=subprocess.check_output(['systemctl','list-units','ax-cell-*','ax-parallel-*-retry*','--all','--plain','--no-legend'],text=True)
 for line in units.splitlines():
  unit=line.split()[0];state=subprocess.check_output(['systemctl','show',unit,'-p','SubState','--value'],text=True).strip()
  if state not in ('failed','exited','dead'):raise ValueError('cell or retry unit is still active')
def put(url,path,ctype):
 u=urllib.parse.urlsplit(url)
 if u.scheme!='https' or not u.hostname or not u.hostname.endswith('.oss-cn-hangzhou-internal.aliyuncs.com'):raise ValueError('same-region private result destination required')
 c=http.client.HTTPSConnection(u.hostname,timeout=120);c.putrequest('PUT',u.path+'?'+u.query);c.putheader('Content-Type',ctype);c.putheader('Content-Length',str(path.stat().st_size));c.endheaders()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):c.send(chunk)
 r=c.getresponse();r.read();c.close()
 if r.status not in (200,201):raise RuntimeError('result upload failed HTTP '+str(r.status))
def case_identity(c,jfs_retry,no_upload,native_retry=False):
 if jfs_retry and native_retry:raise ValueError('only one supplemental case may be selected')
 if native_retry:
  if c['worker_id']!='node-c' or not no_upload:raise ValueError('native supplement is local-retained only on node-c')
  return c['attempt_id']+'-native-retry01',c['run_id']+'-c-native-retry01',{'native-kernel-default'}
 if jfs_retry and (c['worker_id']!='node-a' or not no_upload):raise ValueError('single-cell supplement is local-retained only on node-a')
 return c['attempt_id']+('-jfs-retry01' if jfs_retry else ''),c['run_id']+('-a-jfs-retry01' if jfs_retry else ''),({'juicefs-kernel-default'} if jfs_retry else set(c['arms']))
def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--jfs-retry',action='store_true');p.add_argument('--native-retry',action='store_true');p.add_argument('--no-upload',action='store_true');a=p.parse_args();c=json.loads(a.config.read_text());attempt=c['attempt_id'];base=Path('/data/parallel')
 if not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',attempt):raise ValueError('invalid attempt')
 quiescent(attempt)
 repo=base/'repo'
 if subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()!=RUNNER or subprocess.check_output(['git','status','--porcelain','--untracked-files=all','--','harness'],cwd=repo,text=True).strip():raise ValueError('frozen archiver differs or harness is dirty')
 tag,expected_run,expected_arms=case_identity(c,a.jfs_retry,a.no_upload,a.native_retry)
 source=base/('cell-results-'+tag);terminal=json.loads((source/'terminal.json').read_text())
 if terminal.get('run_id')!=expected_run or terminal.get('worker_id')!=c['worker_id'] or set(terminal.get('methods',{}))!=expected_arms or any(v.get('status') in ('running','unknown') for v in terminal['methods'].values()):raise ValueError('wrong or nonterminal result')
 patterns={}
 for line in (base/('secrets-'+attempt)/'client.env').read_text().splitlines():
  if '=' in line:
   key,value=line.split('=',1);os.environ[key]=value
   if key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','REDISCLI_AUTH') and value:patterns[key]=value.encode()
 mirror=base/('sanitized-cell-results-'+tag+'-v1');archive=base/('sanitized-cell-results-'+tag+'-v1.tar.gz')
 if mirror.exists() or archive.exists():raise ValueError('recovery mirror already exists; inspect its receipt instead of replacing')
 record=sanitized_mirror(source,mirror,patterns,{'juicefs-kernel-default/0001-format.stderr':{'AWS_ACCESS_KEY_ID'}})
 prep=base/('results-'+attempt);prep_dest=mirror/'archival-worker-preparation';prep_dest.mkdir()
 for f in prep.glob('*'):
  if f.is_symlink():raise ValueError('preparation symlink')
  if f.is_file():
   raw=f.read_bytes()
   if any(value in raw for value in patterns.values()) or any(marker in raw for marker in MARKERS):raise ValueError('preparation evidence contains unclassified credential')
   shutil.copy2(f,prep_dest/f.name)
 terminal['attempt_id']=attempt;save(mirror/'terminal.json',terminal)
 record['files']['terminal.json']['mirror_sha256']=digest_bytes((mirror/'terminal.json').read_bytes());record['terminal_metadata_added']=['attempt_id']
 save(mirror/'archival-recovery.json',{'epoch':time.time(),'attempt_id':attempt,'worker_id':c['worker_id'],'frozen_archiver_commit':RUNNER,**record})
 sys.path.insert(0,str(repo/'harness'));from run_trace_matrix import archive_results
 receipt=archive_results(mirror,archive,terminal);receipt['attempt_id']=attempt;save(archive.with_name(archive.name+'.receipt.json'),receipt)
 if not a.no_upload:put(c['result_upload_url'],archive,'application/gzip');put(c['receipt_upload_url'],archive.with_name(archive.name+'.receipt.json'),'application/json')
 print(json.dumps({'worker_id':c['worker_id'],'attempt_id':attempt,'archive':str(archive),'receipt':receipt,'redactions':record['redactions']}))
if __name__=='__main__':main()
