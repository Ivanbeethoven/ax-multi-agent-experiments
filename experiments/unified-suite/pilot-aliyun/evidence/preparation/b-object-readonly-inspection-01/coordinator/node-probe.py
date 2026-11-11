import pathlib,json,os,subprocess,hashlib,socket,datetime
assert socket.gethostname()=='ax-pilot-20261111-b-objects'
def pin(p):return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hostname':socket.gethostname(),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'disks':[],'observers':{}}
for i in range(4):
 p=pathlib.Path('/srv/ax-objects-'+str(i));s=os.statvfs(p);r['disks'].append({'path':str(p),'device':p.stat().st_dev,'total_bytes':s.f_blocks*s.f_frsize,'available_bytes':s.f_bavail*s.f_frsize,'available_inodes':s.f_favail,'total_inodes':s.f_files})
r['lsblk']=json.loads(subprocess.check_output(['lsblk','--json','--bytes','--output','NAME,TYPE,SERIAL,SIZE,MAJ:MIN,MOUNTPOINTS'],text=True))
r['containers']=json.loads(subprocess.check_output(['docker','ps','-a','--format','json'],text=True).replace('\n','\n')) if False else [json.loads(x) for x in subprocess.check_output(['docker','ps','-a','--format','json'],text=True).splitlines()]
for tag in ['cal-pool-b-j-mx-mxb01-on1','cal-pool-b-j-mx-mxb01-on2']:
 d=pathlib.Path('/srv/ax-objects-0/o')/tag;a={'directory':str(d),'files':{},'sample_count':0}
 for name in ['ready.json','finished.json','state.json','observer.json','marks.jsonl']:
  p=d/name
  if p.is_file() and p.stat().st_size<200000:a['files'][name]={'pin':pin(p),'value':json.loads(p.read_bytes()) if p.suffix=='.json' else p.read_text()}
 samples=sorted(d.glob('sample-*/sample.json'));a['sample_count']=len(samples)
 for label,p in ([('first',samples[0]),('last',samples[-1])] if samples else []):a[label]={'pin':pin(p),'value':json.loads(p.read_bytes())}
 r['observers'][tag]=a
print(json.dumps(r))
