import pathlib,os,json,subprocess,socket,stat,datetime
assert socket.gethostname()=='ax-pilot-20261111-b-objects'
rows=json.loads(subprocess.check_output(['lsblk','--json','--bytes','--output','NAME,TYPE,SERIAL,SIZE,MOUNTPOINTS'],text=True))['blockdevices']
b=next(x for x in rows if x.get('serial')=='bp1bhdngko7nq3c2i340');assert b['name']=='vdb' and b['size']==60*1024**3 and not any(b['mountpoints'])
assert not subprocess.check_output(['docker','ps','-q'],text=True).strip()
root=pathlib.Path('/mnt/ax-inspect-2235-01/0');subprocess.run(['mount','-t','ext4','-o','ro,noload','/dev/vdb',str(root)],check=True)
result={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'serial':b['serial'],'device':root.stat().st_dev,'scope':'Read-only allocation of explicit historical observer candidates. Not an archive-content or deletion approval.','groups':[]}
tags=['cal-pool-b-j-tr-c201-on1','cal-pool-b-j-tr-c201-on2','cal-pool-b-j-tr-c201-on3','c2-fa630431ea12-05357b5089cd679c-r1-juicefs','cal-pool-b-j-sf-on1','cal-pool-b-j-sf-on2','cal-pool-b-j-sf-on3','c3-d909e5330618-16f4ac389d2a2da6-r1-juicefs','c4-b-jfs-on1','c4-b-jfs-on2','c4-b-jfs-on3','c4-b-jfs-formal-01']
seen=set()
try:
 for tag in tags:
  paths=[root/'o'/tag,root/'evidence'/tag/'o'/tag,*root.glob('retained-calibrations/*/'+tag+'/raw/o/'+tag)]
  for p in paths:
   if not p.exists():continue
   if not(p.resolve()==p and p.is_dir() and not p.is_symlink()):
    result['groups'].append({'tag':tag,'relative_path':str(p.relative_to(root)),'allocated_bytes':0,'excluded':'noncanonical-or-not-directory'});continue
   allocated=files=dirs=0
   for d,ds,fs in os.walk(p,followlinks=False):
    for x in [pathlib.Path(d),*(pathlib.Path(d)/n for n in fs)]:
     s=x.lstat();assert s.st_dev==root.stat().st_dev
     if not(stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode)):continue
     if stat.S_ISREG(s.st_mode):assert s.st_nlink==1;files+=1
     else:dirs+=1
     identity=(s.st_dev,s.st_ino)
     assert identity not in seen;seen.add(identity);allocated+=s.st_blocks*512
   result['groups'].append({'tag':tag,'relative_path':str(p.relative_to(root)),'allocated_bytes':allocated,'files':files,'directories':dirs})
 result['allocated_bytes']=sum(x['allocated_bytes'] for x in result['groups'])
finally:
 subprocess.run(['umount',str(root)],check=True);result['all_unmounted']=not root.is_mount()
result['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();print(json.dumps(result))
