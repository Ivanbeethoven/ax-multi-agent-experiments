from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
import subprocess,time

root=Path(__file__).resolve().parent
previous=root/'cloud-release-01.json'
prior=json.loads(previous.read_text());assert prior['after_instance']['Instances']['Instance']==[]
instance='i-bp1dqgriutdy4yqies4k';disk='d-bp1dqgriutdy4yqiq8vn';group='sg-bp172x8c1ontwri3vcrw'
out=root/'cloud-release-02.json'
r={'started_at':datetime.now(timezone.utc).isoformat(),'previous_receipt':str(previous),'previous_sha256':hashlib.sha256(previous.read_bytes()).hexdigest(),'reason':'Instance deletion succeeded; dedicated security group initially still had an asynchronously retiring instance dependency. Retry only that same group; never delete another resource to clear its dependency.','attempts':[],'passed':False}
def api(op,**kw):
 c=['aliyun','ecs',op,'--RegionId','cn-hangzhou']
 for k,v in kw.items():c.extend(['--'+k,str(v)])
 p=subprocess.run(c,text=True,capture_output=True,timeout=60)
 return p.returncode,json.loads(p.stdout if p.returncode==0 else p.stderr)
def query(op,**kw):
 code,data=api(op,**kw);assert code==0,data
 return data
try:
 r['after_instance']=query('DescribeInstances',InstanceIds=json.dumps([instance]));assert not r['after_instance']['Instances']['Instance']
 r['after_disk']=query('DescribeDisks',DiskIds=json.dumps([disk]));assert not r['after_disk']['Disks']['Disk']
 for i in range(10):
  code,data=api('DeleteSecurityGroup',SecurityGroupId=group)
  r['attempts'].append({'exit_code':code,'response':data})
  if code==0:break
  assert data.get('error_code')=='DependencyViolation',data
  time.sleep(3)
 assert code==0,data
 r['after_security_group']=query('DescribeSecurityGroups',SecurityGroupIds=json.dumps([group]))
 assert not r['after_security_group']['SecurityGroups']['SecurityGroup']
 code='''from pathlib import Path
import json
p=Path('/home/dev/ax-packed-build');rows=[]
for name in ('cloud-key','cloud-key.pub'):
 f=p/name
 assert f.parent.resolve()==p.resolve()
 existed=f.exists() or f.is_symlink()
 if existed:f.unlink()
 rows.append({'path':str(f),'previously_existed':existed,'absent_after':not f.exists() and not f.is_symlink()})
print(json.dumps({'only_owned_copies_removed':rows,'provider_keypair':'ax-c1-20261106 retained'}))
'''
 p=subprocess.run(['ssh','rbm','python3','-'],input=code,text=True,capture_output=True,timeout=60)
 assert p.returncode==0,p.stderr
 r['owned_key_copies']=json.loads(p.stdout)
 r.update(passed=True,finished_at=datetime.now(timezone.utc).isoformat())
except Exception as e:
 r['error']=repr(e);raise
finally:
 out.write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps({'passed':True,'all_three_resources_absent':True,'receipt':str(out),'sha256':hashlib.sha256(out.read_bytes()).hexdigest()}))
