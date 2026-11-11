#!/usr/bin/env python3
"""Read-only ECS inspection and quote for growing the four owned object disks.

Produces exact future ResizeDisk requests, but never sends one or touches a
worker. The capacity decision and idle-boundary checks remain separate.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import shutil
import uuid

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_evidence', HERE / 'release-evidence.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
cloud_module = release.cloud_module


def growth_rows(original, node, scope, instances, disks, desired_gib):
    owned = release.owned_set(original, node, scope, instances, disks)
    if (owned['role'] != 'objects' or len(node['data_disks']) != 4 or
            type(desired_gib) is not int or not 60 < desired_gib <= 1024):
        raise ValueError('Only four original object disks and an explicit pilot size are supported')
    instance = instances[0]
    if (instance['Status'] != 'Running' or instance['ZoneId'] != scope['zone'] or
            instance['InstanceType'] != cloud_module.ROLES['objects']['type'] or
            instance['InstanceChargeType'] != 'PostPaid'):
        raise ValueError('Original running on-demand object host in the exact zone required')
    actual = {d['DiskId']: d for d in disks}
    result = []
    for expected in node['data_disks']:
        disk = actual[expected['id']]
        if (disk['Type'] != 'data' or disk['Status'] != 'In_use' or
                disk['Category'] != 'cloud_essd' or disk['PerformanceLevel'] != 'PL1' or
                disk['ZoneId'] != scope['zone'] or disk['DiskChargeType'] != 'PostPaid' or
                disk.get('MultiAttach', 'Disabled') != 'Disabled' or
                not expected['serial'] or disk['DiskId'] != 'd-' + expected['serial'] or
                disk['Size'] >= desired_gib):
            raise ValueError('Wrong data disk, layout, charge type or non-growing size')
        result.append(dict(disk_id=disk['DiskId'], serial=expected['serial'], mount=expected['mount'],
            before_gib=disk['Size'], desired_gib=desired_gib, delete_with_instance=True,
            future_request=dict(DiskId=disk['DiskId'], NewSize=desired_gib,
                Type='online', ClientToken=str(uuid.uuid4()))))
    if len({x['disk_id'] for x in result}) != 4 or len({x['before_gib'] for x in result}) != 1:
        raise ValueError('Four distinct equally sized original disks required')
    return result


def quoted_price(cloud, sizes):
    options = dict(cloud_module.disk_options('objects'))
    options = {k: v for k, v in options.items() if not k.endswith('DeleteWithInstance')}
    for index, size in enumerate(sizes, 1):
        options[f'DataDisk.{index}.Size'] = size
    response = cloud.api('DescribePrice', ZoneId=cloud.scope['zone'], ResourceType='instance',
        InstanceType=cloud_module.ROLES['objects']['type'], PriceUnit='Hour', Period=1, Amount=1,
        InstanceNetworkType='vpc', IoOptimized='optimized', **options)
    price = response['PriceInfo']['Price']
    rate = price['TradePrice']
    if price['Currency'] != 'CNY' or type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError('Actual positive hourly CNY quote required')
    return dict(hourly_trade_price_cny=rate, hourly_standard_price_cny=price['StandardPrice'],
        disk_options=options, price_unit='Hour', amount=1,
        scope='Current whole-instance quote for this disk configuration; not a resize bill')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release-set', type=Path, required=True)
    p.add_argument('--release-set-sha256', required=True)
    p.add_argument('--desired-gib', type=int, required=True)
    p.add_argument('--aliyun', default=shutil.which('aliyun') or shutil.which('aliyun.exe'))
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if not a.aliyun or not a.out.is_absolute():
        p.error('Configured Aliyun CLI and a new absolute output directory required')
    frozen = json.loads(release.pinned(dict(path=str(a.release_set), sha256=a.release_set_sha256)).read_text())
    if frozen.get('schema') != 'ax-pilot-owned-release-set-v1' or frozen.get('passed') is not True or frozen['role'] != 'objects':
        raise ValueError('Original verified object-node release set required')
    creation = json.loads(release.pinned(frozen['creation']).read_text())
    node = json.loads(release.pinned(frozen['original_node_spec']).read_text())
    for proof in frozen['api_evidence']:
        release.pinned(proof)
    a.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    cloud = cloud_module.Cloud(a.aliyun, a.out)
    response = cloud.api('DescribeInstances', InstanceIds=json.dumps([frozen['resource_id']]), PageSize=100)
    instances = response['Instances']['Instance']
    if response['TotalCount'] != len(instances):
        raise ValueError('Incomplete instance response')
    response = cloud.api('DescribeDisks', InstanceId=frozen['resource_id'], PageSize=100)
    disks = response['Disks']['Disk']
    if response['TotalCount'] != len(disks):
        raise ValueError('Incomplete disk response')
    rows = growth_rows(creation, node, cloud.scope, instances, disks, a.desired_gib)
    if set(frozen['owned_disk_ids']) != {d['DiskId'] for d in disks}:
        raise ValueError('Original owned disk membership changed')
    before = quoted_price(cloud, [r['before_gib'] for r in rows])
    after = quoted_price(cloud, [r['desired_gib'] for r in rows])
    increment = after['hourly_trade_price_cny'] - before['hourly_trade_price_cny']
    if increment <= 0:
        raise ValueError('Expected positive quoted cost for added capacity')
    value = dict(schema='ax-pilot-object-growth-plan-v1', passed=True,
        at=datetime.now(timezone.utc).isoformat(), applied=False,
        release_set=release.ref(a.release_set), instance_id=frozen['resource_id'], disks=rows,
        current_object_host_quote=before, desired_object_host_quote=after,
        quoted_incremental_hourly_cny=increment,
        source=release.ref(Path(__file__).resolve()),
        local_source_dependencies=[release.ref(HERE / name) for name in ['release-evidence.py', 'cloud.py']],
        api_evidence=[release.ref(path) for path in sorted(a.out.glob('*-Describe*.json'))],
        prerequisites_before_application=[
            'All original C1/C2 paired measurements and calibrations closed and independently retained',
            'No unfinished invocation, lease, occupied agent slot or active observer/workload',
            'Measure one complete million-file base and decide capacity with 20 percent reserve',
            'Refresh this exact cloud ownership check and native block serial/ext4 UUID/mount inspection',
            'Only grow whole-device ext4 on these four original data disks; never format or touch the system disk',
            'After API expansion grow ext4, verify all four devices and filesystem sizes, preserve data and UUIDs',
            'Record actual new capacity/rate evidence and create fresh C3 inventory/qualifications'],
        scope='Read-only capacity option and live quote. No disk mutation, worker command, resource creation, cost-rate event or C3 admission.')
    cloud_module.save_new(a.out / 'growth-plan.json', value)
    print(json.dumps(dict(passed=True, applied=False, before_gib=rows[0]['before_gib'],
        desired_gib=a.desired_gib, disks=4, quoted_incremental_hourly_cny=increment), indent=2))


if __name__ == '__main__':
    main()
