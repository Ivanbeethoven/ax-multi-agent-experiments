import json,re,sys
from pathlib import Path
def prom(p):
 return {line.split()[0]:float(line.split()[1]) for line in p.read_text().splitlines() if line and not line.startswith('#')}
def stat(p):
 return {key:int(value) for key,value in (line.split() for line in p.read_text().splitlines())}
def summarize(root):
 result=[]
 for p in sorted(root.rglob('summary.json')):
  d=p.parent
  before,after=[prom(d/f'cold-server-{phase}.prom') for phase in ('before','after')]
  stages=[]
  for key,val in after.items():
   if not key.startswith('rustfs_io_get_object_stage_duration_seconds_sum'):continue
   labels=dict(re.findall(r'(\w+)="([^"]*)"',key))
   if labels.get('path')!='legacy_duplex':continue
   count=key.replace('_sum','_count')
   n=after.get(count,0)-before.get(count,0)
   total=val-before.get(key,0)
   if n<0 or total<0:raise ValueError('Server counters decreased')
   if n:stages.append(dict(stage=labels['stage'],count=n,mean_ms=1000*total/n))
  cpu={}
  for label,prefix in [('client',''),('rustfs','rustfs-cgroup-')]:
   a,b=[stat(d/f'cold-{phase}-{prefix}cpu.stat') for phase in ('before','after')]
   cpu[label]=dict(cpu_seconds=(b['usage_usec']-a['usage_usec'])/1e6,throttled_seconds=(b.get('throttled_usec',0)-a.get('throttled_usec',0))/1e6)
  def io(phase):
   return {k:int(v) for k,v in (line.split(':') for line in (d/f'cold-{phase}-rustfs-io').read_text().splitlines())}
  a,b=[io(phase) for phase in ('before','after')]
  data=json.loads(p.read_text())
  result.append(dict(raw_directory=str(d),mode=data['volume_format'],verified=data['verified'],cold=data['passes']['cold'],cpu=cpu,rustfs_disk_read_bytes=b['read_bytes']-a['read_bytes'],server_stages=stages))
 return result
if __name__=='__main__':
 root=Path(sys.argv[1]);data=summarize(root)
 (root/'monitoring-summary.json').write_text(json.dumps(data,indent=2))
 print('Saved',len(data),'cases')
