#!/usr/bin/env python3
"""New B-only 64 MiB same-pool scratch attempt; never writes the old campaign."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import shlex
import signal
import subprocess
import sys
import tarfile
import time
import uuid

HERE=Path(__file__).resolve().parent
EVIDENCE=HERE.parent/'evidence/preparation/parallel-control-01'
REPO=HERE.parents[3]
REGION='cn-hangzhou'
ZONE='cn-hangzhou-j'
OBJECT_HOST='i-bp1bhdngko7nq3c3jrhp'
SSH=['ssh.exe','-F','C:/Users/HXY/.cache/ax-pilot-20261111/pool-b/ssh.conf',
     '-o','ConnectTimeout=5','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2']
ROLES=('objects','metadata','client')
ORDERS={'juicefs-first':('juicefs','brewfs'),'brewfs-first':('brewfs','juicefs')}
LATEST={'brewfs':'ca07159f3da4b670e4aeb878faef928e69bc1634',
        'rustfs':'f4ed202508f2153afb7956bae6f7799b60eccd00'}
BRANCHES={
    'brewfs':('https://github.com/brewfs/brewfs.git','refs/heads/main'),
    'rustfs':('https://github.com/Ivanbeethoven/rustfs.git','refs/heads/feat/dynamic-block-size-rebased'),
}
SIZE=100
MAX_MINUTES=60
def need(ok,msg):
    if not ok:
        raise ValueError(msg)
def stamp():
    return datetime.now(timezone.utc).isoformat()
def digest(data):
    return hashlib.sha256(data).hexdigest()
def write(path,value):
    with path.open('x') as f:
        json.dump(value,f,indent=2)
        f.write('\n')
def read(path):
    return json.loads(path.read_bytes())
def exact(doc,container,member):
    rows=doc[container][member]
    need(len(rows)==doc['TotalCount'],'Incomplete cloud inventory')
    return rows
def refs():
    actual={}
    for name,(url,ref) in BRANCHES.items():
        row=subprocess.check_output(['git','ls-remote',url,ref],text=True,timeout=12,cwd='/tmp').split()
        need(len(row)==2 and row[1]==ref,'Missing actual upstream ref: '+name)
        actual[name]=row[0]
    need(actual==LATEST,'Cached experiment artifacts no longer match the branches')
    return actual
def tag_rows(rows,tag):
    for row in rows:
        pairs=row.get('Tags',{}).get('Tag',[])
        if {x['TagKey']:x['TagValue'] for x in pairs}.get('campaign')==tag:
            yield row

def arm_progress(status,previous):
    runtime=status['runtime']
    if runtime:
        identity=(runtime['invocation'],runtime['pid'],runtime['start_ticks'])
        need(previous in (None,identity),'Remote service invocation changed; refusing duplicate measurement')
        previous=identity
        live=status.get('InvocationID')
        need(live in (None,'','0'*32,runtime['invocation']),'Systemd invocation differs from first worker')
        if status.get('ActiveState')=='active':
            need(status.get('MainPID')==str(runtime['pid']),'Active systemd process differs from first worker')
    need(status.get('NRestarts') in (None,'','0'),'Owned arm unexpectedly restarted')
    terminal=status['terminal']
    if terminal is not None:
        need(runtime and terminal['runtime']==runtime and terminal['passed'] is True and
             re.fullmatch('[0-9a-f]{64}',terminal.get('result_sha256','')) and
             status.get('observed_result_sha256')==terminal['result_sha256'],
             'Arm terminal receipt is not a successful cold close')
        if status.get('ActiveState') in ('inactive','failed') or status.get('LoadState')=='not-found':
            need(status.get('ActiveState')!='failed' and status.get('ExecMainStatus','0') in ('','0'),
                 'Arm did not exit normally after terminal receipt')
            return previous, True
    elif status.get('ActiveState')=='failed':
        raise ValueError('Owned systemd arm failed without a cold-close receipt: '+str(status.get('Result')))
    return previous, False


def workload_contract():
    plan=read(EVIDENCE/'plan.json')
    cells={c['arm']['product']:c for c in plan['cells'] if c['paired_block']=='8e3d340ff23c3ece-r1'}
    need(set(cells)=={'brewfs','juicefs'} and cells['brewfs']['params']==cells['juicefs']['params'],
         'Pinned mixed-read/write contract changed')
    return dict(params=cells['brewfs']['params'],client_settings={k:v['client_settings'] for k,v in cells.items()},
                seed=9173,sha256=digest((EVIDENCE/'plan.json').read_bytes()))


def matched_fixture(prior,order,inv):
    plan,audit,contract=(read(prior/name) for name in ('plan.json','audit.json','contract.json'))
    need(plan['inventory']==inv and plan['latest']==LATEST and plan['size_gib']==SIZE and plan['disks']==4 and
         tuple(plan['phase_order'])==order[::-1] and contract==workload_contract(),
         'Prior fixture must be the same configuration, hardware and opposite arm order')
    run=plan['run']
    pins={audit['completed'][arm]['input_sha256'] for arm in order}
    need(audit['run']==run and audit['paired_diagnostic'] is True and audit['resources_closed'] is True and
         not audit['failed'] and re.fullmatch(r'ax-c1b-[0-9a-f]{10}',run) and len(pins)==1 and
         re.fullmatch(r'[0-9a-f]{64}',next(iter(pins))),
         'Prior fixture needs an independently audited closed pair with one input SHA')
    return dict(run=run,input_sha256=next(iter(pins)),prior_audit_sha256=digest((prior/'audit.json').read_bytes()))


class Attempt:
    def __init__(self,out,resume_blanks=None,order=ORDERS['juicefs-first'],match=None):
        self.out=out
        self.tag='ax-c1b-'+secrets.token_hex(5)
        self.start=time.monotonic()
        self.deadline=self.start+MAX_MINUTES*60
        self.log=out/'api';self.log.mkdir()
        self.inv=next(x['roles'] for x in read(EVIDENCE/'inventory.json')['pools'] if x['pool_id']=='pool-b')
        self.order=order
        self.match=matched_fixture(match,order,self.inv) if match else None
        self.contract=workload_contract()
        self.ids=[self.inv[r]['instance_id'] for r in ROLES]
        self.old={d for r in self.inv.values() for d in r['data_disk_ids']}
        self.created=[]
        self.staged=set()
        self.launched=False
        self.guard=None
        self.errors=[]
        if resume_blanks:
            prior, first=read(resume_blanks/'summary.json'),read(resume_blanks/'plan.json')
            need(first['inventory']==self.inv and first['size_gib']==SIZE and first['disks']==4 and
                 tuple(first['phase_order'])==self.order and first.get('matched_fixture')==self.match and
                 first['formal_historical_cell'] is False and prior['measured'] is False and
                 prior['passed'] is False and not list((resume_blanks/'api').glob('*-StartInstance.json')) and
                 len(prior['created'])==len(set(prior['created']))==4 and re.fullmatch(r'ax-c1b-[0-9a-f]{10}',first['run']),
                 'Only previously untouched, unstarted scratch disks may be reused')
            self.created=list(prior['created'])
            self.tag=first['run']
            age=time.time()-datetime.fromisoformat(first['created_at']).timestamp()
            need(0<=age<MAX_MINUTES*60-900,'Prior scratch allocation has no safe remaining window')
            self.deadline-=age
            write(out/'prior-provisioning.json',dict(path=str(resume_blanks),
                  original_plan_sha256=digest((resume_blanks/'plan.json').read_bytes()),
                  original_summary_sha256=digest((resume_blanks/'summary.json').read_bytes()),
                  disk_ids=self.created,original_deadline_seconds_remaining=MAX_MINUTES*60-age))
        self.remote_code='/var/tmp/'+self.tag+'-code'
        self.remote='/var/tmp/'+self.tag
    def api(self,action,**params):
        args=['aliyun.exe','ecs',action,'--RegionId',REGION]
        for k,v in params.items():
            args+=['--'+k,str(v).lower() if isinstance(v,bool) else str(v)]
        log=self.log/(str(time.time_ns())+'-'+action+'.json')
        write(log,dict(utc=stamp(),action=action,params=params))
        p=subprocess.run(args,capture_output=True,text=True,timeout=28)
        try:
            response=json.loads(p.stdout)
        except json.JSONDecodeError:
            response={'stdout':p.stdout}
        write(log.with_suffix('.response.json'),dict(exit=p.returncode,response=response,stderr=p.stderr))
        need(p.returncode==0 and response.get('RequestId'),'Cloud API failed: '+action)
        return response
    def disks(self,**params):
        return exact(self.api('DescribeDisks',PageSize=100,**params),'Disks','Disk')
    def states(self):
        states=exact(self.api('DescribeInstances',InstanceIds=json.dumps(self.ids),PageSize=100),
                     'Instances','Instance')
        need({x['InstanceId'] for x in states}==set(self.ids),'Cannot verify exact three B hosts')
        need(all(x['ZoneId']==ZONE for x in states),'B host zone changed')
        return {x['InstanceId']:(x['Status'],x.get('StoppedMode')) for x in states}
    def owned(self):
        rows=list(tag_rows(self.disks(DiskType='data'),self.tag))
        for row in rows:
            labels={x['TagKey']:x['TagValue'] for x in row['Tags']['Tag']}
            last=labels.pop('acs:ecs:lastAttachedInstance',OBJECT_HOST)
            need(last==OBJECT_HOST and row['DiskId'] not in self.old and re.fullmatch(r'd-[a-z0-9]+',row['DiskId']) and
                 (row['InstanceId'] in ('',OBJECT_HOST) or row['InstanceId'] is None) and
                 row['DiskName'] in [f'{self.tag}-scratch-{i}' for i in range(4)] and
                 row['Size']==SIZE and row['ZoneId']==ZONE and row['PerformanceLevel']=='PL1' and
                 row['Category']=='cloud_essd' and row['DiskChargeType']=='PostPaid' and
                 row.get('MultiAttach','Disabled')=='Disabled' and labels=={'campaign':self.tag,'role':'c1-b-scratch'},
                 'Unexpected scratch disk ownership/billing')
        return sorted(rows,key=lambda x:x['DiskName'])
    def ssh(self,role,*argv,timeout=40,**kw):
        host='ax-pilot-jump' if role=='jump' else 'pool-b-'+role
        return subprocess.run([*SSH,host,shlex.join(list(map(str,argv)))],timeout=timeout,check=True,**kw)
    def hold(self,stack):
        raw=(HERE/'guard-b-c1-scratch.py').read_bytes()
        cmd='python3 -c '+shlex.quote('import base64; exec(base64.b64decode('+repr(base64.b64encode(raw).decode())+'))')
        err=stack.enter_context((self.out/'guard.stderr').open('x'))
        self.guard=subprocess.Popen([*SSH,'ax-pilot-jump',cmd],stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,stderr=err,text=True)
        with ThreadPoolExecutor(max_workers=1) as exe:
            reply=exe.submit(self.guard.stdout.readline)
            try:
                ack=json.loads(reply.result(timeout=20))
            except Exception:
                self.guard.kill()
                raise
        need(ack.get('passed') is True and ack.get('roles')==self.inv,'Coordinator lock inventory changed')
        write(self.out/'lock.json',ack)
    def active(self):
        need(self.guard and self.guard.poll() is None,'B guard lost')
        need(time.monotonic()<self.deadline-300,'Protected cleanup window reached')
    def wait(self,probe,condition,seconds,label,closing=False):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if not closing:
                self.active()
            value=probe()
            if condition(value):
                return value
            time.sleep(2)
        raise TimeoutError(label)
    def confirm_initial(self):
        need(self.states()=={iid:('Stopped','StopCharging') for iid in self.ids},'B compute already in use')
        attached=self.disks(InstanceId=OBJECT_HOST)
        data=[x for x in attached if x['Type']=='data']
        need({d['DiskId'] for d in data}==set(self.inv['objects']['data_disk_ids']) and
             all(d['Size']==60 and d['PerformanceLevel']=='PL1' for d in data),'Original object disks changed')
        types=self.api('DescribeInstanceTypes',**{'InstanceTypes.1':'ecs.u1-c1m4.2xlarge'})
        [row]=types['InstanceTypes']['InstanceType']
        need(row['DiskQuantity']>=len(attached)+4,'Object host lacks four free disk slots')
        quote=self.api('DescribePrice',ZoneId=ZONE,ResourceType='disk',PriceUnit='Hour',Period=1,Amount=1,
                       **{'DataDisk.1.Category':'cloud_essd','DataDisk.1.PerformanceLevel':'PL1',
                          'DataDisk.1.Size':SIZE})['PriceInfo']['Price']
        need(quote['Currency']=='CNY' and 0<quote['TradePrice']<=.21,'Disk quote exceeds budget')
        own=self.owned()
        if self.created:
            need({d['DiskId'] for d in own}==set(self.created) and
                 all(d['Status']=='Available' and not d.get('InstanceId') for d in own),
                 'All four prior scratch IDs must be detached and available')
        else:
            need(not own,'Scratch campaign name is already present')
        self.active()
        write(self.out/'plan.json',dict(schema='b-c1-randrw-scratch-v1',created_at=stamp(),run=self.tag,
              inventory=self.inv,latest=refs(),size_gib=SIZE,disks=4,staging_disk_hourly_cny=quote['TradePrice']*4,
              compute_hourly_cny=3.60,max_minutes=MAX_MINUTES,formal_historical_cell=False,
              phase_order=self.order,matched_fixture=self.match,old_data_writable=False))
    def allocate(self):
        self.active()
        for i in range(4) if not self.created else ():
            r=self.api('CreateDisk',ZoneId=ZONE,Size=SIZE,DiskCategory='cloud_essd',PerformanceLevel='PL1',
                       DiskName=f'{self.tag}-scratch-{i}',ClientToken=str(uuid.uuid4()),
                       **{'Tag.1.Key':'campaign','Tag.1.Value':self.tag,
                          'Tag.2.Key':'role','Tag.2.Value':'c1-b-scratch'})
            disk=r['DiskId']
            need(re.fullmatch(r'd-[a-z0-9]+',disk) and disk not in self.old and disk not in self.created,
                 'CreateDisk did not return a new disk')
            self.created.append(disk)
            print(f'Created scratch disk {i+1}/4: {disk}',flush=True)
        self.wait(self.owned,lambda r:len(r)==4 and {x['DiskId'] for x in r}==set(self.created) and
                  all(x['Status']=='Available' for x in r),70,'Scratch disk creation')
        for disk in self.created:
            self.active()
            self.api('AttachDisk',InstanceId=OBJECT_HOST,DiskId=disk,DeleteWithInstance=False)
            self.wait(self.owned,lambda rows:any(x['DiskId']==disk and x['Status']=='In_use' and
                      x['InstanceId']==OBJECT_HOST for x in rows),75,'Scratch attachment settlement')
            time.sleep(5)
            confirmed=self.owned()
            need(any(x['DiskId']==disk and x['Status']=='In_use' and x['InstanceId']==OBJECT_HOST for x in confirmed),
                 'Scratch attachment failed second settled readback')
            print('Attached and settled '+disk,flush=True)
    def start_hosts(self):
        self.active()
        self.launched=True
        with ThreadPoolExecutor(max_workers=3) as exe:
            for r in exe.map(lambda x:self.api('StartInstance',InstanceId=x),self.ids):
                need(bool(r['RequestId']),'Start request not acknowledged')
        self.wait(self.states,lambda s:all(x[0]=='Running' for x in s.values()),120,'B nodes running')
        print('All three B nodes Running.',flush=True)
    def pack_code(self):
        result=io.BytesIO()
        with tarfile.open(fileobj=result,mode='w:gz') as archive:
            for file in [HERE/'worker-b-c1-scratch.py', REPO/'bench/fio_workload.py',REPO/'bench/experiment_io.py']:
                archive.add(file,arcname=file.name,recursive=False)
        return result.getvalue()
    def init_hosts(self):
        common=dict(inventory=self.inv,scratch_disk_ids=self.created,
                    access=secrets.token_hex(12),secret=secrets.token_hex(24),redis_secret=secrets.token_hex(24),
                    matched_fixture=self.match,params=self.contract['params'],client_settings=self.contract['client_settings'])
        write(self.out/'contract.json',self.contract)
        bundle=self.pack_code()
        def launch(role):
            self.active()
            timeout=105 if role=='objects' else 65
            self.wait(lambda:subprocess.run([*SSH,'pool-b-'+role,'true'],capture_output=True,timeout=8).returncode,
                      lambda code:code==0,60,'SSH '+role)
            self.ssh(role,'sh','-e','-c','umask 077; mkdir "$1"; tar -xzf - -C "$1"','unpack',self.remote_code,
                     input=bundle)
            self.staged.add(role)
            with (self.out/(role+'-init.log')).open('x') as f:
                self.ssh(role,'python3',self.remote_code+'/worker-b-c1-scratch.py','init',self.tag,
                         input=json.dumps(dict(common,role=role)).encode(),timeout=timeout+90,stdout=f,stderr=f)
        with ThreadPoolExecutor(max_workers=3) as exe:
            list(exe.map(launch,ROLES))
    def run_pair(self):
        for arm in self.order:
            self.active()
            print('Starting isolated '+arm+' arm.',flush=True)
            with (self.out/(arm+'-headroom.json')).open('x') as f:
                self.ssh('objects','python3',self.remote_code+'/worker-b-c1-scratch.py','headroom',self.tag,stdout=f)
            remaining=max(0,int(self.deadline-time.monotonic())-350)
            need(remaining>=240,'No safe remaining window for next arm')
            limit=min(remaining,1150)
            code=self.remote_code+'/worker-b-c1-scratch.py'
            self.arm_once(arm,code,limit)
            print('Closed isolated '+arm+' arm.',flush=True)

    def arm_once(self,arm,code,limit):
        with (self.out/(arm+'-client.log')).open('x') as f:
            try:
                self.ssh('client','python3',code,'launch-arm',self.tag,arm,limit,
                         timeout=20,stdout=f,stderr=f)
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
                # The remote launch may have succeeded. Never resend. Poll the
                # durable claim, unique service invocation and terminal receipt.
                write(self.out/(arm+'-launch-transport.json'),
                      dict(error_type=type(exc).__name__,retried=False))
        opened=time.monotonic()
        seen=None
        with (self.out/(arm+'-unit-status.ndjson')).open('x',buffering=1) as log:
            while time.monotonic()<opened+limit+30:
                self.active()
                try:
                    status=self.ssh('client','python3',code,'arm-status',self.tag,arm,
                                    timeout=10,capture_output=True,text=True)
                    value=json.loads(status.stdout)
                except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
                    log.write(json.dumps(dict(poll_error=type(exc).__name__,utc=stamp()))+'\n')
                    time.sleep(3)
                    continue
                log.write(json.dumps(value)+'\n')
                seen, done = arm_progress(value,seen)
                if done:
                    return
                if time.monotonic()>=opened+25 and not value['launch']:
                    raise ValueError('No durable launch claim after the single request')
                if (time.monotonic()>=opened+25 and value.get('LoadState')=='not-found' and
                        not value['terminal']):
                    raise ValueError('Unit disappeared without a terminal receipt')
                time.sleep(4)
        raise TimeoutError('Owned arm exceeded its observed closure window')
    def close(self):
        def collect(role):
            if role not in self.staged:
                return
            cmd=self.remote_code+'/worker-b-c1-scratch.py'
            try:
                self.ssh(role,'sh','-e','-c','if test -f "$1/private/spec.json"; then python3 "$2" close "$3"; fi',
                         'close',self.remote,cmd,self.tag,stdout=subprocess.DEVNULL,timeout=55)
            except Exception as e:
                self.errors.append('worker close '+role+': '+repr(e))
            try:
                with (self.out/(role+'-evidence.tar.gz')).open('xb') as f:
                    self.ssh(role,'tar','-czf','-','--exclude=private','--exclude=fixture','--exclude=redis',
                         '--exclude=rustlogs','--exclude=rustfs.log','--exclude=data[0-3]',
                         '--exclude=mount','-C',self.remote,'.',stdout=f,timeout=35)
                with tarfile.open(self.out/(role+'-evidence.tar.gz')) as archive:
                    members=archive.getmembers()
                    need(all(x.isdir() or x.isfile() for x in members),'Archive includes non-regular entries')
                    need(not any('private' in Path(x.name).parts for x in members),'Private path in archive')
            except Exception as e:
                self.errors.append('worker archive '+role+': '+repr(e))
        if self.launched:
            # Complete the client teardown before taking down the shared services.
            collect('client')
            with ThreadPoolExecutor(max_workers=2) as exe:
                list(exe.map(collect,('metadata','objects')))
            for iid,(status,mode) in self.states().items():
                if (status,mode)!=('Stopped','StopCharging'):
                    try:
                        self.api('StopInstance',InstanceId=iid,ForceStop=False,StoppedMode='StopCharging')
                    except Exception as e:
                        self.errors.append('stop '+iid+': '+repr(e))
            s=self.wait(self.states,lambda values:all(v==('Stopped','StopCharging') for v in values.values()),
                        130,'B StopCharging confirmation',closing=True)
            write(self.out/'stopped.json',s)
        rows=self.owned()
        for x in rows:
            if x['InstanceId']==OBJECT_HOST:
                self.api('DetachDisk',InstanceId=OBJECT_HOST,DiskId=x['DiskId'])
                self.wait(self.owned,lambda values:any(v['DiskId']==x['DiskId'] and v['Status']=='Available' and
                          not v.get('InstanceId') for v in values),75,'Scratch detach settlement',closing=True)
                time.sleep(5)
        if rows:
            self.wait(self.owned,lambda values:len(values)==len(rows) and all(v['Status']=='Available' for v in values),
                      80,'Scratch detach',closing=True)
            for row in self.owned():
                self.api('DeleteDisk',DiskId=row['DiskId'])
            self.wait(self.owned,lambda values:not values,70,'Scratch release',closing=True)
        write(self.out/'released.json',dict(disks=[x['DiskId'] for x in rows],available=[],confirmed_at=stamp()))
    def finish(self):
        self.guard.stdin.write('release\n');self.guard.stdin.flush()
        need(self.guard.stdout.readline().strip()=='released' and self.guard.wait(timeout=10)==0,
             'Guard closing verification failed')
def main():
    def on_signal(signum, _):
        raise InterruptedError('Operator interrupted run; enter cloud teardown: '+str(signum))
    signal.signal(signal.SIGTERM,on_signal)
    signal.signal(signal.SIGINT,on_signal)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--resume-blanks',type=Path)
    parser.add_argument('--order',choices=ORDERS,default='juicefs-first')
    parser.add_argument('--match-run',type=Path,help='Reuse an audited opposite-order pair’s exact input')
    a=parser.parse_args()
    a.out.mkdir(mode=0o700,parents=True,exist_ok=False)
    run=Attempt(a.out,a.resume_blanks,ORDERS[a.order],a.match_run)
    success=False
    begun=False
    with ExitStack() as stack:
        run.hold(stack)
        try:
            run.confirm_initial()
            if not a.apply:
                success=True
                print('Read-only preflight passed; no billable resources requested.',flush=True)
                return
            begun=True
            run.allocate()
            run.start_hosts()
            run.init_hosts()
            run.run_pair()
            success=True
        except Exception as e:
            run.errors.append(repr(e))
            raise
        finally:
            try:
                if begun:
                    run.close()
                run.finish()
            except Exception as e:
                run.errors.append('cleanup: '+repr(e))
                raise
            finally:
                write(a.out/'summary.json',dict(passed=success and not run.errors,measured=success and begun,
                      created=run.created,errors=run.errors,elapsed_seconds=time.monotonic()-run.start,
                      formal_historical_cell=False,performance_calibrated=False))
if __name__=='__main__':
    main()
