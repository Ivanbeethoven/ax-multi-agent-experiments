#!/usr/bin/env python3
"""Provision one C4 observer-only ESSD on the owned A object host."""
import argparse
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import uuid

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_evidence', HERE/'release-evidence.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
cloud_module = release.cloud_module
ROLE = 'c4-observer-scratch'
SIZE = 100

# The original guard implementation selects physical resource IDs from the
# canonical plan. Check all original state DBs while holding its A-pool locks.
GUARD = '''
import json, pathlib, subprocess, sys
root=pathlib.Path('/srv/ax-pilot-control')
sys.path.insert(0,str(root/'code-19'))
from bench.runtime.pilot_pools import active_conflicts, resource_guard
from bench.suite_plan import check_plan
control=root/'parallel-control-01'
plan=check_plan(json.loads((control/'suite/plan.json').read_bytes()))
cell=next(c for c in plan['cells'] if c['pool']=='pool-a')
with resource_guard(control,cell,plan):
    for state in sorted(root.glob('*/state.sqlite')):
        if active_conflicts(state.parent,cell,plan):
            raise ValueError('Pool A has a conflicting lease: '+str(state))
    unit=subprocess.check_output(['systemctl','show','ax-pilot-pa-c4-flat-reuse-01.service',
                                 '-p','MainPID','-p','InvocationID'],text=True)
    if 'MainPID=0' not in unit.splitlines() or 'InvocationID=d944eeaa8da34cc495ad8a993adade7f' not in unit.splitlines():
        raise ValueError('Original A C4 unit no longer matches terminal invocation')
    print('held',flush=True)
    if sys.stdin.readline()!='release\\n':
        raise RuntimeError('Guard control connection ended')
'''


def read(path):
    return json.loads(Path(path).read_bytes())


def exact(response, container, member):
    rows = response[container][member]
    if len(rows) != response['TotalCount']:
        raise ValueError('Incomplete ECS API results')
    return rows


def scratch_disk(row, disk_id, instance_id, campaign, zone, attached):
    if not re.fullmatch(r'd-[a-z0-9]+', disk_id):
        raise ValueError('Expected an actual ECS disk ID')
    expected = dict(DiskId=disk_id, Type='data', Size=SIZE, Category='cloud_essd',
                    PerformanceLevel='PL0', DiskChargeType='PostPaid', ZoneId=zone,
                    Status='In_use' if attached else 'Available')
    if (any(row.get(k)!=v for k,v in expected.items()) or
            row.get('MultiAttach','Disabled')!='Disabled' or
            release.tags(row)!={'campaign':campaign,'role':ROLE} or
            (row.get('InstanceId') or '')!=(instance_id if attached else '') or
            (attached and row.get('DeleteWithInstance') is not True)):
        raise ValueError('Scratch disk identity, billing, attachment or ownership differs')
    return row


@contextmanager
def guarded(a):
    if not (a.ssh and a.ssh_config):
        raise ValueError('Pinned coordinator SSH configuration required')
    config = release.pinned(dict(path=str(a.ssh_config), sha256=a.ssh_config_sha256))
    command = 'python3 -B -c '+shlex.quote(GUARD)
    with (a.out/'guard.stderr').open('xb') as err:
        proc = subprocess.Popen([a.ssh,'-o','BatchMode=yes','-o','ConnectTimeout=8',
                                 '-F',a.ssh_config_native or str(config),
                                 'ax-pilot-jump',command],
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True)
        try:
            if proc.stdout.readline().strip()!='held' or proc.poll() is not None:
                raise RuntimeError('Canonical A guard unavailable; see guard.stderr')
            cloud_module.save_new(a.out/'guard-held.json',dict(at=cloud_module.now(),held=True,
                ssh_config=release.ref(config)))
            yield
        finally:
            if proc.poll() is None:
                proc.stdin.write('release\n')
                proc.stdin.flush()
            proc.stdin.close()
            status=proc.wait(timeout=15)
            proc.stdout.close()
            cloud_module.save_new(a.out/'guard-exit.json',dict(at=cloud_module.now(),exit_code=status))
            if status:
                raise RuntimeError('A guard ended unsuccessfully; check actual ECS state')


def run(a):
    frozen = read(release.pinned(dict(path=str(a.release_set),sha256=a.release_set_sha256)))
    if (frozen.get('schema')!='ax-pilot-owned-release-set-v1' or frozen.get('passed') is not True or
            frozen['campaign']!='ax-pilot-20261111' or frozen['role']!='objects'):
        raise ValueError('Expected original A object host release set')
    creation = read(release.pinned(frozen['creation']))
    node = read(release.pinned(frozen['original_node_spec']))
    iid, campaign = frozen['resource_id'], frozen['campaign']
    a.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    if a.action=='create':
        a.ledger.mkdir(parents=True,exist_ok=True,mode=0o700)
    cloud = cloud_module.Cloud(a.aliyun,a.out)
    zone = cloud.scope['zone']
    ledger = a.ledger

    def host(extra=None):
        i=exact(cloud.api('DescribeInstances',InstanceIds=json.dumps([iid])), 'Instances','Instance')
        d=exact(cloud.api('DescribeDisks',InstanceId=iid,PageSize=100), 'Disks','Disk')
        own=[x for x in d if x['DiskId']!=extra]
        v=release.owned_set(creation,node,cloud.scope,i,own)
        if (v['resource_id']!=iid or v['owned_disk_ids']!=sorted(frozen['owned_disk_ids']) or
                i[0]['Status']!='Running' or i[0]['ZoneId']!=zone or
                i[0]['InstanceType']!=cloud_module.ROLES['objects']['type'] or
                i[0]['InstanceChargeType']!='PostPaid' or
                len(node['data_disks'])!=4 or
                any(x['Size']!=60 or x['PerformanceLevel']!='PL1' for x in own if x['Type']=='data') or
                (extra and sum(x['DiskId']==extra for x in d)!=1)):
            raise ValueError('Original host or four original 60GiB PL1 disks changed')

    def source():
        return [release.ref(f) for f in sorted(a.out.glob('*-Describe*.json'))]

    def find():
        return exact(cloud.api('DescribeDisks',PageSize=100,**{
            'Tag.1.Key':'campaign','Tag.1.Value':campaign,'Tag.2.Key':'role','Tag.2.Value':ROLE}),
            'Disks','Disk')

    if a.action=='plan':
        host()
        if find(): raise ValueError('An owned scratch disk already exists')
        req=cloud.api('DescribePrice',ZoneId=zone,ResourceType='disk',PriceUnit='Hour',Amount=1,
            **{'DataDisk.1.Category':'cloud_essd','DataDisk.1.PerformanceLevel':'PL0','DataDisk.1.Size':SIZE})
        p=req['PriceInfo']['Price']
        if p['Currency']!='CNY' or not 0<p['TradePrice']<=0.105:
            raise ValueError('New quote exceeds this reviewed option')
        result=dict(schema='c4-observer-scratch-plan-v1',passed=True,applied=False,
            at=cloud_module.now(),instance_id=iid,campaign=campaign,zone=zone,size_gib=SIZE,
            original_disk_ids=frozen['owned_disk_ids'],hourly_cny=p['TradePrice'],source_pins=source(),
            release_set=release.ref(a.release_set))
        cloud_module.save_new(a.out/'plan.json',result)
        return result
    if a.action in ('create','attach'):
        plan=read(release.pinned(dict(path=str(a.plan),sha256=a.plan_sha256)))
        if (plan.get('schema')!='c4-observer-scratch-plan-v1' or plan['passed'] is not True or
                plan['instance_id']!=iid or plan['campaign']!=campaign or plan['size_gib']!=SIZE or
                plan['zone']!=zone or plan['release_set']!=release.ref(a.release_set) or
                plan['original_disk_ids']!=frozen['owned_disk_ids'] or not plan['source_pins'] or
                not 0<plan['hourly_cny']<=0.105):
            raise ValueError('Exact reviewed scratch quote required')
        for pin in plan['source_pins']: release.pinned(pin)
        with guarded(a):
            host()
            if a.action=='create':
                if (ledger/'created.json').exists() or find():
                    raise ValueError('Scratch disk already exists')
                args=dict(ZoneId=zone,Size=SIZE,DiskCategory='cloud_essd',PerformanceLevel='PL0',
                    DiskName=campaign+'-observer-scratch',ClientToken=str(uuid.uuid4()),
                    **{'Tag.1.Key':'campaign','Tag.1.Value':campaign,'Tag.2.Key':'role','Tag.2.Value':ROLE})
                cloud_module.save_new(ledger/'create-intent.json',dict(at=cloud_module.now(),args=args))
                created=cloud.api('CreateDisk',**args)
                disk=created['DiskId']
                if disk in frozen['owned_disk_ids'] or not re.fullmatch(r'd-[a-z0-9]+',disk):
                    raise ValueError('Unexpected CreateDisk response')
                result=dict(at=cloud_module.now(),disk_id=disk,instance_id=iid,
                            request_id=created['RequestId'],plan=release.ref(a.plan),
                            hourly_cny=plan['hourly_cny'])
                cloud_module.save_new(ledger/'created.json',result)
                return result
            created=read(ledger/'created.json')
            if created['instance_id']!=iid or created['plan']!=release.ref(a.plan):
                raise ValueError('Created disk was not bound to this quote/host')
            disk=created['disk_id']
            rows=find()
            if len(rows)!=1 or disk in frozen['owned_disk_ids']: raise ValueError('Exact single new scratch required')
            scratch_disk(rows[0],disk,iid,campaign,zone,False)
            args=dict(InstanceId=iid,DiskId=disk,DeleteWithInstance=True)
            cloud_module.save_new(ledger/'attach-intent.json',dict(at=cloud_module.now(),args=args))
            attached=cloud.api('AttachDisk',**args)
            result=dict(at=cloud_module.now(),disk_id=disk,instance_id=iid,request_id=attached['RequestId'])
            cloud_module.save_new(ledger/'attach-requested.json',result)
            return result
    created=read(ledger/'created.json')
    attached=read(ledger/'attach-requested.json')
    disk=created['disk_id']
    if (disk in frozen['owned_disk_ids'] or (attached['instance_id'],attached['disk_id'])!=(iid,disk) or
            created['instance_id']!=iid):
        raise ValueError('Exact create/attach receipts required')
    host(extra=disk)
    rows=find()
    if len(rows)!=1: raise ValueError('Expected one tagged scratch disk')
    scratch_disk(rows[0],disk,iid,campaign,zone,True)
    result=dict(schema='c4-observer-scratch-ownership-v1',passed=True,applied=True,pool='pool-a',
        role='objects',instance_id=iid,disk_id=disk,serial=disk[2:],size_gib=SIZE,category='cloud_essd',
        performance_level='PL0',zone=zone,status='In_use',delete_with_instance=True,
        at=cloud_module.now(),original_disk_ids=frozen['owned_disk_ids'],
        hourly_cny=created['hourly_cny'],source_pins=[*source(),
            release.ref(ledger/'created.json'),release.ref(ledger/'attach-requested.json')])
    cloud_module.save_new(a.out/'ownership.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','create','attach','verify'])
    for name in ('release-set','out','ledger'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('release-set-sha256','aliyun'):
        p.add_argument('--'+name,required=True)
    for name in ('plan','ssh-config'):
        p.add_argument('--'+name,type=Path)
    for name in ('plan-sha256','ssh','ssh-config-native','ssh-config-sha256'):
        p.add_argument('--'+name)
    print(json.dumps(run(p.parse_args()),indent=2))
