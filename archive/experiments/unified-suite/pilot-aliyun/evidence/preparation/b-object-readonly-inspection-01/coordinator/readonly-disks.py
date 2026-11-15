import pathlib,json,os,subprocess,hashlib,socket,datetime,re
assert socket.gethostname()=='ax-pilot-20261111-b-objects'
base=pathlib.Path('/mnt/ax-inspect-2235-01');base.mkdir(exist_ok=False)
block=json.loads(subprocess.check_output(['lsblk','--json','--bytes','--output','NAME,TYPE,SERIAL,SIZE,MAJ:MIN,MOUNTPOINTS'],text=True))['blockdevices']
serials=['bp1bhdngko7nq3c2i340','bp1bhdngko7nq3c2i341','bp1bhdngko7nq3c2i342','bp1bhdngko7nq3c2i343']
assert not subprocess.check_output(['docker','ps','-q'],text=True).strip()
def sh(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def space(p):
 s=os.statvfs(p);return {'device':p.stat().st_dev,'total_bytes':s.f_blocks*s.f_frsize,'available_bytes':s.f_bavail*s.f_frsize,'total_inodes':s.f_files,'available_inodes':s.f_favail}
result={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'mount_mode':'ro,noload','disks':[],'observers':{},'all_unmounted':False}
mounted=[]
try:
 for i,serial in enumerate(serials):
  b=next(x for x in block if x.get('serial')==serial);assert b['type']=='disk' and b['size']==60*1024**3 and not any(b['mountpoints']) and 'children' not in b
  device='/dev/'+b['name'];assert subprocess.check_output(['blkid','-s','TYPE','-o','value',device],text=True).strip()=='ext4'
  dest=base/str(i);dest.mkdir();subprocess.run(['mount','-t','ext4','-o','ro,noload',device,str(dest)],check=True);mounted.append(dest)
  assert dest.stat().st_dev==os.stat(device).st_rdev
  opts=subprocess.check_output(['findmnt','-n','-o','OPTIONS','--target',str(dest)],text=True).strip();assert 'ro' in opts.split(',')
  result['disks'].append({'serial':serial,'source':device,'diagnostic_mount':str(dest),'mount_options':opts,**space(dest)})
 for tag in ['cal-pool-b-j-mx-mxb01-on1','cal-pool-b-j-mx-mxb01-on2']:
  d=base/'0/o'/tag;rows=[];a={'source_path':'/srv/ax-objects-0/o/'+tag,'rows':rows,'lifecycle':{}}
  for n in ['ready.json','finished.json']:
   p=d/n
   if p.is_file():
    v=json.loads(p.read_bytes());a['lifecycle'][n]={'sha256':sh(p),'bytes':p.stat().st_size,'keys':list(v),'passed':v.get('passed'),'samples':v.get('samples')}
  files=sorted(d.glob('sample-*/sample.json'))
  a['sample_count']=len(files)
  for p in files:
   v=json.loads(p.read_bytes());row={'sample':p.parent.name,'sha256':sh(p),'index':v.get('index'),'utc_start_ns':v.get('utc_start_ns'),'monotonic_start_ns':v.get('monotonic_start_ns'),'keys':list(v) if len(rows)==0 else None}
   for k in ['data_paths','capacity','reason','mark','marks','passed','source_ids']:
    if k in v:row[k]=v[k]
   rows.append(row)
  for label,p in ([('first',files[0]),('last',files[-1])] if files else []):
   v=json.loads(p.read_bytes());data={}
   for name,e in v.get('files',{}).items():
    if name not in ['rustfs.txt','pid-4744-io.txt','host-net-netstat.txt']:continue
    src=p.parent/name;assert sh(src)==e['sha256']
    text=src.read_text();lines=text.splitlines()
    if name=='rustfs.txt':lines=[l for l in lines if not l.startswith('#') and (l.startswith('rustfs_http_server_request_body_bytes_total') or (l.startswith('rustfs_http_server_request_body_size_sum') and 'method="PUT"' in l) or ('s3_requests_total' in l and ('api="PutObject"' in l or 'api="DeleteObject"' in l)))]
    data[name]={'sha256':e['sha256'],'bytes':e['bytes'],'selected_lines':lines}
   a[label]={'sample':p.parent.name,'sample_json':v,'source_sha256':sh(p),'selected_raw':data}
  result['observers'][tag]=a
finally:
 for dest in reversed(mounted):subprocess.run(['umount',str(dest)],check=True)
 result['all_unmounted']=all(not p.is_mount() for p in mounted)
 result['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
print(json.dumps(result))
