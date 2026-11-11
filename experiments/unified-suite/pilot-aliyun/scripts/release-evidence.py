#!/usr/bin/env python3
"""Freeze owned instance/disk IDs, or confirm actual release. Read-only ECS APIs.

No deletion, stop, detach, retry, OSS request or automatic billing cut-off.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('pilot_cloud', HERE/'cloud.py')
cloud_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(cloud_module)


def ref(path):
    path = Path(path)
    with path.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(path=str(path), sha256=checksum)


def pinned(entry):
    path = Path(entry['path'])
    if not path.is_absolute() or path.is_symlink() or ref(path) != entry:
        raise ValueError('Release evidence reference changed')
    return path


def tags(row):
    return {t['TagKey']:t['TagValue'] for t in row.get('Tags',{}).get('Tag',[])}


def owned_set(creation, node, scope, instances, disks):
    """Require exact original boot/data identities, including all attached disks."""
    if creation.get('preexisting') is not False:
        raise ValueError('Only newly created pilot resources may enter the release set')
    campaign = creation['campaign']; jump = scope['reuse_jump_instance']
    if creation.get('disk_id'):
        resource = creation['disk_id']
        if instances or len(disks)!=1 or disks[0]['DiskId']!=resource:
            raise ValueError('Exact newly created coordinator disk required')
        disk = disks[0]
        if (creation['instance_id']!=jump or disk['Type']!='data' or disk.get('InstanceId')!=jump or
                disk['Size']!=100 or disk.get('DeleteWithInstance') is not False or
                tags(disk).get('campaign')!=campaign or tags(disk).get('role')!='coordinator-data'):
            raise ValueError('Never include an original jump/system disk in the release set')
        return dict(resource_id=resource, role='coordinator-disk', campaign=campaign, owned_disk_ids=[resource])
    resource = creation['instance_id']; role = creation['role']
    if resource==jump or node['instance_id']!=resource or node['campaign']!=campaign or node['role']!=role:
        raise ValueError('Preexisting jump or mismatched owned instance')
    if len(instances)!=1 or instances[0]['InstanceId']!=resource:
        raise ValueError('Exact original instance required')
    if tags(instances[0]).get('campaign')!=campaign or tags(instances[0]).get('role')!=role:
        raise ValueError('Original instance ownership tags changed')
    boot = 'd-'+node['system_disk_serial']
    data = {d['id']:d for d in node['data_disks']}
    expected = {boot, *data}
    if len(expected)!=len(data)+1 or {d['DiskId'] for d in disks}!=expected or len(disks)!=len(expected):
        raise ValueError('Attached disks differ from original owned boot/data devices')
    for disk in disks:
        is_boot = disk['DiskId']==boot
        minimum = 40 if is_boot else data[disk['DiskId']]['gib']
        if (disk.get('InstanceId')!=resource or disk['Type']!=('system' if is_boot else 'data') or
                disk.get('DeleteWithInstance') is not True or disk['Size']<minimum):
            raise ValueError('Owned disk identity/type/size/delete policy differs')
    return dict(resource_id=resource, role=role, campaign=campaign, owned_disk_ids=sorted(expected))


def absence(original, instances, disks, jump):
    resource = original['resource_id']; known = set(original['owned_disk_ids'])
    if not known or len(known)!=len(original['owned_disk_ids']) or not all(x.startswith('d-') for x in known):
        raise ValueError('Complete unique owned disk IDs required')
    if resource.startswith('i-'):
        if resource==jump or original['role']=='coordinator-disk':
            raise ValueError('Original jump must be preserved')
        if len(instances)>1 or any(x['InstanceId']!=resource for x in instances):
            raise ValueError('Instance API filter returned another resource')
        gone = not instances
    else:
        if original['role']!='coordinator-disk' or known!={resource} or instances:
            raise ValueError('Exact dedicated coordinator data disk required')
        gone = not disks
    if len(disks)!=len({d['DiskId'] for d in disks}) or any(d['DiskId'] not in known for d in disks):
        raise ValueError('Disk API filter returned unknown/duplicate resources')
    return dict(passed=gone and not disks, resource_id=resource, released=gone,
        all_owned_disks_absent=not disks, owned_disk_ids=sorted(known),
        remaining_instances=[dict(id=d['InstanceId'],status=d['Status']) for d in instances],
        remaining_disks=[dict(id=d['DiskId'],status=d['Status']) for d in disks])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['freeze','check'])
    p.add_argument('--aliyun', default=shutil.which('aliyun') or shutil.which('aliyun.exe'))
    p.add_argument('--ledger', type=Path)
    p.add_argument('--role', choices=['client','metadata','objects','target','coordinator-disk'])
    p.add_argument('--node-spec', type=Path)
    p.add_argument('--release-set', type=Path)
    p.add_argument('--release-set-sha256')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if not a.aliyun or not a.out.is_absolute():
        p.error('Configured Aliyun CLI and new absolute output required')
    a.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    cloud = cloud_module.Cloud(a.aliyun, a.out); scope = cloud.scope
    if a.action=='freeze':
        if not a.ledger or not a.role or (a.role!='coordinator-disk' and not a.node_spec):
            p.error('freeze requires ledger, exact role and its original provision spec')
        creation_file = a.ledger/(a.role+'.created.json')
        creation = json.loads(creation_file.read_text()); node = None
        if creation.get('preexisting') is not False or creation.get('instance_id')==scope['reuse_jump_instance'] and a.role!='coordinator-disk':
            raise ValueError('Cannot freeze a preexisting instance')
        if a.role=='coordinator-disk':
            instances=[]
            response=cloud.api('DescribeDisks', DiskIds=json.dumps([creation['disk_id']]), PageSize=100)
        else:
            node=json.loads(a.node_spec.read_text())
            request=a.ledger/(a.role+'.request.json')
            if ref(request)['sha256']!=creation['request_sha256']:
                raise ValueError('Original create request changed')
            response=cloud.api('DescribeInstances', InstanceIds=json.dumps([creation['instance_id']]), PageSize=100)
            instances=response['Instances']['Instance']
            if response['TotalCount']!=len(instances):
                raise ValueError('Incomplete instance membership response')
            response=cloud.api('DescribeDisks', InstanceId=creation['instance_id'], PageSize=100)
        disks=response['Disks']['Disk']
        if response['TotalCount']!=len(disks):
            raise ValueError('Incomplete disk membership response')
        value=owned_set(creation,node,scope,instances,disks)
        value.update(schema='ax-pilot-owned-release-set-v1', passed=True,
            at=datetime.now(timezone.utc).isoformat(), creation=ref(creation_file),
            original_node_spec=ref(a.node_spec) if node else None,
            api_evidence=[ref(f) for f in sorted(a.out.glob('*Describe*.json'))],
            preserve_jump_instance=scope['reuse_jump_instance'],
            scope='Original owned instance and all attached boot/data disks; no resource has been released')
        cloud_module.save_new(a.out/'release-set.json', value)
    else:
        if not a.release_set or not a.release_set_sha256:
            p.error('check requires the pinned original owned release set')
        entry=dict(path=str(a.release_set),sha256=a.release_set_sha256)
        original=json.loads(pinned(entry).read_text())
        if original.get('schema')!='ax-pilot-owned-release-set-v1' or original.get('passed') is not True:
            raise ValueError('Successful original owned release set required')
        for pin in [original['creation'], original.get('original_node_spec'), *original['api_evidence']]:
            if pin:pinned(pin)
        resource=original['resource_id']; instances=[]
        if resource.startswith('i-'):
            response=cloud.api('DescribeInstances', InstanceIds=json.dumps([resource]), PageSize=100)
            instances=response['Instances']['Instance']
            if response['TotalCount']!=len(instances):
                raise ValueError('Incomplete instance absence response')
        response=cloud.api('DescribeDisks', DiskIds=json.dumps(original['owned_disk_ids']), PageSize=100)
        disks=response['Disks']['Disk']
        if response['TotalCount']!=len(disks):
            raise ValueError('Incomplete disk absence response')
        value=absence(original,instances,disks,scope['reuse_jump_instance'])
        value.update(at=datetime.now(timezone.utc).isoformat(), original_release_set=entry,
            api_evidence=[ref(f) for f in sorted(a.out.glob('*Describe*.json'))],
            automatic_retry=False, scope='Actual API absence at this timestamp; delete-request success and StopCharging alone do not prove release')
        result_file=a.out/'result.json'; cloud_module.save_new(result_file,value)
        if value['passed']:
            cloud_module.save_new(a.out/'cost-event.json',dict(schema='ax-pilot-cost-events-v1',reviewed=True,
                events=[dict(kind='release-confirmed',resource_id=resource,at=value['at'],evidence=ref(result_file))]))
    print(json.dumps(value))
    return 0 if value['passed'] else 2


if __name__=='__main__':
    raise SystemExit(main())
