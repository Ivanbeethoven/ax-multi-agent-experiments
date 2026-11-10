#!/usr/bin/env python3
"""Explicit Aliyun pilot resource steps. Never starts workloads or retries jobs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
ROLES = {
    'objects': {'type': 'ecs.u1-c1m4.2xlarge', 'data_gib': [60, 60, 60, 60]},
    'client': {'type': 'ecs.u1-c1m4.xlarge', 'data_gib': [100]},
    'target': {'type': 'ecs.u1-c1m4.xlarge', 'data_gib': [100]},
    'metadata': {'type': 'ecs.u1-c1m4.xlarge', 'data_gib': [60]},
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save_new(path, obj):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, indent=2)
        stream.write('\n')


def disk_options(role):
    result = {'SystemDisk.Category': 'cloud_essd', 'SystemDisk.PerformanceLevel': 'PL0',
              'SystemDisk.Size': 40}
    for i, size in enumerate(ROLES[role]['data_gib'], 1):
        result.update({f'DataDisk.{i}.Category': 'cloud_essd', f'DataDisk.{i}.Size': size,
                       f'DataDisk.{i}.PerformanceLevel': 'PL1', f'DataDisk.{i}.DeleteWithInstance': True})
    return result


class Cloud:
    def __init__(self, cli, out):
        self.cli, self.out = cli, out
        self.scope = json.loads((ROOT / 'scope.json').read_text())

    def api(self, action, **params):
        args = [self.cli, 'ecs', action, '--RegionId', self.scope['region']]
        for name, value in params.items():
            args += ['--' + name, str(value).lower() if isinstance(value, bool) else str(value)]
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        call = self.out / (stamp + '-' + action + '.json')
        # Aliyun CLI may return exit 0 for invalid argument/help JSON. Require API RequestId.
        child = subprocess.run(args, capture_output=True, text=True)
        try:
            value = json.loads(child.stdout)
        except json.JSONDecodeError:
            value = {'unparsed_stdout': child.stdout}
        save_new(call, dict(at=now(), action=action, params=params, exit_code=child.returncode,
                            response=value, stderr=child.stderr))
        if child.returncode or not value.get('RequestId') or value.get('Code'):
            raise RuntimeError(f'{action} failed; inspect {call}')
        return value

    def inventory(self):
        result = dict(at=now(), instances=[], disks=[])
        for action, container, name, target in [('DescribeInstances', 'Instances', 'Instance', 'instances'),
                                               ('DescribeDisks', 'Disks', 'Disk', 'disks')]:
            page = 1
            while True:
                value = self.api(action, PageSize=100, PageNumber=page)
                result[target] += value[container][name]
                if len(result[target]) >= value['TotalCount']:
                    break
                page += 1
        save_new(self.out / 'inventory.json', result)
        return result

    def quote(self, roles):
        quotes = []
        for role in roles:
            price_options = {k: v for k, v in disk_options(role).items() if not k.endswith('DeleteWithInstance')}
            available = self.api('DescribeAvailableResource', ZoneId=self.scope['zone'],
                                 DestinationResource='InstanceType', InstanceChargeType='PostPaid',
                                 IoOptimized='optimized', InstanceType=ROLES[role]['type'])
            raw = self.api('DescribePrice', ZoneId=self.scope['zone'], ResourceType='instance',
                           InstanceType=ROLES[role]['type'], PriceUnit='Hour', Period=1, Amount=1,
                           InstanceNetworkType='vpc', IoOptimized='optimized', **price_options)
            p = raw['PriceInfo']['Price']
            quotes.append(dict(role=role, instance_type=ROLES[role]['type'], disk_options=price_options,
                               currency=p['Currency'], hourly_trade_price=p['TradePrice'],
                               hourly_standard_price=p['StandardPrice'], availability=available))
        result = dict(at=now(), roles=quotes, new_nodes_per_hour=sum(q['hourly_trade_price'] for q in quotes),
                      excludes=['existing jump compute/disk/EIP', 'OSS storage/requests/egress', 'tax/billing adjustments'],
                      note='Live quote, not final bill. No hard budget cap. Start target only for C4.')
        save_new(self.out / 'quote.json', result)
        return result

    def verify_gate(self, gate):
        g = json.loads(gate.read_text())
        if not g.get('ready_for_cloud') or sha(g['build_manifest_path']) != g['build_manifest_sha256']:
            raise ValueError('Verified local assets gate required before creating instances')
        for condition in ('binaries_verified', 'rustfs_image_verified', 'local_routes_checked', 'dependencies_prepared'):
            if g.get(condition) is not True:
                raise ValueError('Incomplete local preparation: ' + condition)
        manifest = json.loads(Path(g['build_manifest_path']).read_text())
        expected = {'brewfs': ('https://github.com/brewfs/brewfs.git', 'main'),
                    'rustfs': ('https://github.com/Ivanbeethoven/rustfs.git', 'feat/dynamic-block-size-rebased')}
        sources = manifest.get('sources', [])
        if len(sources) != 2 or {s['component'] for s in sources} != set(expected):
            raise ValueError('Both component versions must be present')
        for source in sources:
            url, branch = expected[source['component']]
            if (source['repository'], source['branch']) != (url, branch):
                raise ValueError('Unexpected source branch')
            current = subprocess.check_output(['git', 'ls-remote', url, 'refs/heads/'+branch], text=True).split()
            if len(current) != 2 or current[0] != source['source_commit']:
                raise ValueError(source['component']+' branch advanced or unavailable; rebuild before procurement')
        self.verify_local_receipts(g)
        save_new(self.out/'branch-latest.json', dict(checked_at=now(), sources=sources))
        return manifest

    def verify_local_receipts(self, gate):
        if not gate.get('proofs') or not gate.get('binaries') or not gate.get('deployment_bundle'):
            raise ValueError('Actual local receipt, binary and bundle references required')
        for name, ref in gate['proofs'].items():
            path=Path(ref['path'])
            if sha(path)!=ref['sha256']:
                raise ValueError('Local preparation evidence changed: '+name)
            data=json.loads(path.read_text())
            for key,value in ref['equals'].items():
                if type(data.get(key)) is not type(value) or data[key]!=value:
                    raise ValueError('Local preparation evidence differs: '+name+'/'+key)
        for ref in gate['binaries']:
            if sha(ref['path'])!=ref['sha256']:
                raise ValueError('Local prepared binary changed')
        bundle=gate['deployment_bundle']
        if sha(bundle['receipt'])!=bundle['receipt_sha256'] or sha(bundle['archive'])!=bundle['sha256']:
            raise ValueError('Local deployment bundle changed')

    def create(self, role, ledger, campaign, image, gate):
        if role not in ROLES or not re.fullmatch(r'ax-pilot-[0-9]{8}(-[a-z0-9]+)?', campaign):
            raise ValueError('Known role and dedicated pilot campaign required')
        self.verify_gate(gate)
        ledger.mkdir(parents=True, exist_ok=True)
        marker = ledger / (role + '.request.json')
        # An interrupted request remains reviewable. Never auto-retry a purchase.
        if marker.exists() or (ledger / (role + '.created.json')).exists():
            raise FileExistsError(f'{role} already requested; inspect ledger and live tags before proceeding')
        existing = self.api('DescribeInstances', **{'Tag.1.Key': 'campaign', 'Tag.1.Value': campaign,
                                                   'Tag.2.Key': 'role', 'Tag.2.Value': role, 'PageSize': 100})
        if existing['TotalCount']:
            raise ValueError('Existing matching instance; do not duplicate it')
        request = dict(ZoneId=self.scope['zone'], InstanceType=ROLES[role]['type'], ImageId=image,
                       InstanceChargeType='PostPaid', SpotStrategy='NoSpot', Amount=1,
                       VSwitchId=self.scope['vswitch_id'], SecurityGroupId=self.scope['security_group_id'],
                       KeyPairName=self.scope['key_pair_name'], InternetMaxBandwidthOut=0,
                       InstanceName=campaign+'-'+role, HostName=campaign+'-'+role,
                       IoOptimized='optimized', ClientToken=str(uuid.uuid4()),
                       **{'Tag.1.Key':'campaign', 'Tag.1.Value':campaign, 'Tag.2.Key':'role',
                          'Tag.2.Value':role, **disk_options(role)})
        save_new(marker, dict(at=now(), campaign=campaign, role=role, preexisting=False,
                              local_gate_sha256=sha(gate), request=request))
        response = self.api('RunInstances', **request)
        ids = response['InstanceIdSets']['InstanceIdSet']
        if len(ids) != 1 or ids[0] == self.scope['reuse_jump_instance']:
            raise ValueError('Unexpected resource identity; inspect API receipt')
        receipt = dict(at=now(), role=role, campaign=campaign, instance_id=ids[0], preexisting=False,
                       request_sha256=sha(marker), request_id=response['RequestId'])
        save_new(ledger/(role+'.created.json'), receipt)
        return receipt

    def control_disk(self, action, ledger, campaign, gate=None, closure=None):
        if not re.fullmatch(r'ax-pilot-[0-9]{8}(-[a-z0-9]+)?', campaign):
            raise ValueError('Dedicated pilot campaign required')
        ledger.mkdir(parents=True, exist_ok=True)
        record=ledger/'coordinator-disk.created.json'
        if action=='create-control-disk':
            self.verify_gate(gate)
            marker=ledger/'coordinator-disk.request.json'
            if marker.exists() or record.exists():
                raise FileExistsError('Control disk already requested; inspect, never duplicate automatically')
            prior=self.api('DescribeDisks', **{'Tag.1.Key':'campaign','Tag.1.Value':campaign,
                'Tag.2.Key':'role','Tag.2.Value':'coordinator-data','PageSize':100})
            if prior['TotalCount']:raise ValueError('Existing campaign control disk')
            request=dict(ZoneId=self.scope['zone'],Size=100,DiskCategory='cloud_essd',PerformanceLevel='PL0',
                DiskName=campaign+'-control',ClientToken=str(uuid.uuid4()),
                **{'Tag.1.Key':'campaign','Tag.1.Value':campaign,'Tag.2.Key':'role','Tag.2.Value':'coordinator-data'})
            save_new(marker,dict(at=now(),campaign=campaign,preexisting=False,request=request))
            response=self.api('CreateDisk',**request)
            result=dict(at=now(),disk_id=response['DiskId'],campaign=campaign,preexisting=False,
                instance_id=self.scope['reuse_jump_instance'],request_id=response['RequestId'])
            save_new(record,result)
            return result
        owned=json.loads(record.read_text())
        if owned['preexisting'] or owned['campaign']!=campaign or owned['instance_id']!=self.scope['reuse_jump_instance']:
            raise ValueError('Not this campaign newly created coordinator disk')
        disk_id=owned['disk_id']
        response=self.api('DescribeDisks',DiskIds=json.dumps([disk_id]))
        rows=response['Disks']['Disk']
        if len(rows)!=1:raise ValueError('Expected the exact owned control disk')
        disk=rows[0];tags={t['TagKey']:t['TagValue'] for t in disk.get('Tags',{}).get('Tag',[])}
        if tags.get('campaign')!=campaign or tags.get('role')!='coordinator-data' or disk['Type']!='data' or disk['Size']!=100:
            raise ValueError('Control disk ownership/type/size changed')
        if action=='attach-control-disk':
            if disk['Status']!='Available' or disk.get('InstanceId'):
                raise ValueError('Control disk must be detached and available')
            result=self.api('AttachDisk',InstanceId=self.scope['reuse_jump_instance'],DiskId=disk_id,DeleteWithInstance=False)
        else:
            if not closure:raise ValueError('Actual control disk archive and unmount closure required')
            closed=json.loads(closure.read_text())
            if not all(closed.get(k) is True for k in ('results_archived','workloads_stopped','control_disk_unmounted')):
                raise ValueError('Control disk still has results/writers or is mounted')
            if action=='detach-control-disk':
                if disk['Status']!='In_use' or disk.get('InstanceId')!=self.scope['reuse_jump_instance']:
                    raise ValueError('Control disk not attached to expected jump')
                result=self.api('DetachDisk',InstanceId=self.scope['reuse_jump_instance'],DiskId=disk_id)
            elif action=='delete-control-disk':
                if disk['Status']!='Available' or disk.get('InstanceId'):
                    raise ValueError('Control disk must be detached before deletion')
                result=self.api('DeleteDisk',DiskId=disk_id)
            else:raise ValueError('Unknown control disk action')
        save_new(ledger/(action+'.requested.json'),dict(at=now(),disk_id=disk_id,response=result,
            note='Query actual disk state to confirm completion'))
        return result

    def release(self, ledger, role, closure):
        done = json.loads(closure.read_text())
        if not all(done.get(k) is True for k in ('results_archived', 'workloads_stopped', 'mounts_absent')):
            raise ValueError('Archive and clean workload/mount closure required')
        row = json.loads((ledger/(role+'.created.json')).read_text())
        instance = row['instance_id']
        if row['preexisting'] or instance == self.scope['reuse_jump_instance']:
            raise ValueError('Never release the preexisting jump host')
        raw = self.api('DescribeInstances', InstanceIds=json.dumps([instance]))
        nodes = raw['Instances']['Instance']
        if len(nodes) != 1:
            raise ValueError('Expected this exact owned instance')
        tags = {t['TagKey']:t['TagValue'] for t in nodes[0].get('Tags',{}).get('Tag',[])}
        if tags.get('campaign') != row['campaign'] or tags.get('role') != role:
            raise ValueError('Ownership tags changed; preserve the resource')
        response = self.api('DeleteInstance', InstanceId=instance, Force=True)
        save_new(ledger/(role+'.release-requested.json'), dict(at=now(), instance_id=instance, response=response,
                                                              note='Query DescribeInstances/DescribeDisks to confirm release'))
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect','quote','create','release','start-jump','stop-jump',
        'create-control-disk','attach-control-disk','detach-control-disk','delete-control-disk'])
    parser.add_argument('--aliyun', default=shutil.which('aliyun') or shutil.which('aliyun.exe'))
    parser.add_argument('--out', type=Path, required=True, help='New directory for API evidence')
    parser.add_argument('--roles', nargs='+', choices=list(ROLES), default=['objects','client','metadata'])
    parser.add_argument('--role', choices=list(ROLES))
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--campaign')
    parser.add_argument('--image')
    parser.add_argument('--gate', type=Path)
    parser.add_argument('--closure', type=Path)
    args = parser.parse_args()
    if not args.aliyun:
        parser.error('Provide the configured Aliyun CLI executable')
    args.out.mkdir(parents=True, exist_ok=False)
    os.chmod(args.out, 0o700)
    cloud = Cloud(args.aliyun, args.out)
    if args.action == 'inspect':
        result = cloud.inventory()
    elif args.action == 'quote':
        result = cloud.quote(args.roles)
    elif args.action == 'create':
        if not all((args.role, args.ledger, args.campaign, args.image, args.gate)):
            parser.error('create requires role, ledger, campaign, image and gate')
        result = cloud.create(args.role, args.ledger, args.campaign, args.image, args.gate)
    elif args.action == 'release':
        if not all((args.role, args.ledger, args.closure)):
            parser.error('release requires role, ledger and closure')
        result = cloud.release(args.ledger, args.role, args.closure)
    elif args.action.endswith('control-disk'):
        if not args.ledger or not args.campaign or (args.action=='create-control-disk' and not args.gate):
            parser.error('control disk steps require ledger/campaign, and create requires the preparation gate')
        result=cloud.control_disk(args.action,args.ledger,args.campaign,args.gate,args.closure)
    else:
        # Existing jump is reused, never destroyed. No expiry/deadline is installed.
        options = dict(InstanceId=cloud.scope['reuse_jump_instance'])
        if args.action == 'stop-jump':
            if not args.closure or not json.loads(args.closure.read_text()).get('workloads_stopped'):
                parser.error('stop-jump requires an actual stopped-workload closure')
            options['StoppedMode'] = 'StopCharging'
        else:
            if not args.gate:parser.error('start-jump requires completed local preparation gate')
            cloud.verify_gate(args.gate)
        result = cloud.api('StartInstance' if args.action=='start-jump' else 'StopInstance', **options)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
