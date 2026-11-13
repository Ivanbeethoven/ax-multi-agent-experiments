import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'cloud-release-01.json'
INSTANCE='i-bp1dqgriutdy4yqies4k'
DISK='d-bp1dqgriutdy4yqiq8vn'
GROUP='sg-bp172x8c1ontwri3vcrw'
REGION='cn-hangzhou'
record={'started_at':datetime.now(timezone.utc).isoformat(),'instance':INSTANCE,'disk':DISK,'security_group':GROUP,'scope':'Only this task-owned temporary instance, its attached DeleteWithInstance system disk, and its dedicated security group. Original provider keypair, VPC, vSwitch, RBM and other resources are preserved. Old full backend volumes are not backed up; targeted failed-byte evidence, logs, exact binaries and profile raw have been verified on RBM.','passed':False}

def save():
    OUT.write_text(json.dumps(record,indent=2)+'\n')

def api(operation,**args):
    command=['aliyun','ecs',operation,'--RegionId',REGION]
    for k,v in args.items():command.extend(['--'+k,str(v)])
    r=subprocess.run(command,text=True,capture_output=True,timeout=60)
    if r.returncode:raise RuntimeError(operation+': '+r.stderr)
    return json.loads(r.stdout)

def rbm(code):
    r=subprocess.run(['ssh','rbm','python3','-'],input=code,text=True,capture_output=True,timeout=60)
    if r.returncode:raise RuntimeError(r.stderr)
    return json.loads(r.stdout)

try:
    record['preservation_check']=rbm('''import hashlib,json,tarfile,pathlib,subprocess,base64
p=pathlib.Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-11.tar.gz')
assert hashlib.sha256(p.read_bytes()).hexdigest()=='bef783c9277a6d4a8c232e0b999e5e14882ac4ad456eecd8a54216f14be124ef'
with tarfile.open(p) as t:
 m=json.load(t.extractfile('manifest.json'))
 for x in m['files']:
  b=t.extractfile(x['path']).read();assert len(b)==x['bytes'] and hashlib.sha256(b).hexdigest()==x['sha256']
 n=next(n for n in t.getnames() if n.endswith('final-runtime-stopped-04/receipt.json'))
 r=json.load(t.extractfile(n))
 assert r['runtime_stopped'] and r['heavy_lock_acquired']
 assert r['process_check']['exit_code']==1 and not r['active_containers']['stdout'].strip()
 assert all(not x['state']['Running'] for x in r['retained_stopped_owned_containers'])
q="import subprocess,json; p=subprocess.run(['pgrep','-x','brewfs|cargo|rustc|fio|perf|smallfiles'],capture_output=True,text=True); c=subprocess.run(['docker','ps','-q'],capture_output=True,text=True); assert p.returncode==1 and c.returncode==0 and not c.stdout.strip(); print(json.dumps({'no_workload_processes':True,'no_running_containers':True}))"
cmd='python3 -c "import base64;exec(base64.b64decode(\\\''+base64.b64encode(q.encode()).decode()+'\\\'))"'
# Use shell quoting for the remote Python command; no secret contents are printed.
import shlex
cmd='python3 -c '+shlex.quote(q)
v=subprocess.run(['/home/dev/ax-packed-build/cloud',cmd],capture_output=True,text=True,timeout=30)
assert v.returncode==0,v.stderr
print(json.dumps({'archive_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'payloads_checked':len(m['files']),'stopped_receipt_path':n,'actual_final_probe':json.loads(v.stdout),'old_backend_volume_scope':'Targeted sealed-byte/log/source evidence preserved; complete temporary backend volumes not backed up.'}))
''')
    before=api('DescribeInstances',InstanceIds=json.dumps([INSTANCE]))
    items=before['Instances']['Instance'];assert len(items)==1
    x=items[0]
    assert x['InstanceId']==INSTANCE and x['InstanceName']=='ax-packed-prefetch-20261112'
    assert x['CreationTime']=='2026-11-12T08:55Z'
    assert x['SecurityGroupIds']['SecurityGroupId']==[GROUP]
    disks=api('DescribeDisks',DiskIds=json.dumps([DISK]))
    items=disks['Disks']['Disk'];assert len(items)==1
    d=items[0];assert d['DiskId']==DISK and d['InstanceId']==INSTANCE and d['Size']==512 and d['DeleteWithInstance'] is True
    groups=api('DescribeSecurityGroups',SecurityGroupIds=json.dumps([GROUP]))
    assert [x['SecurityGroupId'] for x in groups['SecurityGroups']['SecurityGroup']]==[GROUP]
    record.update(before_instance=before,before_disk=disks,before_security_group=groups,phase='verified-before-delete');save()
    record['delete_instance']=api('DeleteInstance',InstanceId=INSTANCE,Force='true',ForceStop='false');save()
    for _ in range(12):
        record['after_instance']=api('DescribeInstances',InstanceIds=json.dumps([INSTANCE]))
        if not record['after_instance']['Instances']['Instance']:break
        time.sleep(3)
    assert not record['after_instance']['Instances']['Instance']
    record['delete_security_group']=api('DeleteSecurityGroup',SecurityGroupId=GROUP);save()
    record['after_security_group']=api('DescribeSecurityGroups',SecurityGroupIds=json.dumps([GROUP]))
    assert not record['after_security_group']['SecurityGroups']['SecurityGroup']
    for _ in range(10):
        record['after_disk']=api('DescribeDisks',DiskIds=json.dumps([DISK]))
        if not record['after_disk']['Disks']['Disk']:break
        time.sleep(3)
    assert not record['after_disk']['Disks']['Disk']
    record['owned_key_copies']=rbm('''from pathlib import Path
import json
parent=Path('/home/dev/ax-packed-build');rows=[]
for name in ('cloud-key','cloud-key.pub'):
 p=parent/name
 assert p.parent.resolve()==parent.resolve()
 existed=p.exists() or p.is_symlink()
 if existed:p.unlink()
 rows.append({'path':str(p),'previously_existed':existed,'absent_after':not p.exists() and not p.is_symlink()})
print(json.dumps({'only_owned_copies_removed':rows,'original_provider_keypair':'ax-c1-20261106 retained'}))
''')
    record.update(passed=True,phase='released',finished_at=datetime.now(timezone.utc).isoformat())
except Exception as e:
    record['error']=repr(e)
    raise
finally:
    save()
print(json.dumps({'passed':record['passed'],'phase':record['phase'],'receipt':str(OUT),'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest()}))
