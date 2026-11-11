#!/usr/bin/env python3
"""Build Pool B storage-only rate events from saved ECS receipts; never call ECS."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path


ALLOWLIST = Path(__file__).resolve().parent.parent / 'evidence/preparation/release-pool-b-freeze-01/result.json'
ALLOWLIST_SHA = '0a7c6e3751f14f3b4d0a45a30d4352bc0e232fc2e39954fec78535ce770f38cf'
CAMPAIGN = 'ax-pilot-20261111-b'
ROLES = ('client', 'metadata', 'objects')


def need(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    path = path.resolve(strict=True)
    raw = path.read_bytes()
    return json.loads(raw, parse_float=Decimal), {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def timestamp(value):
    at = datetime.fromisoformat(value)
    need(at.utcoffset() is not None and at.utcoffset().total_seconds() == 0, 'Explicit UTC time required')
    return at


def positive_rate(value):
    need(type(value) in (int, Decimal) and Decimal(value).is_finite() and value > 0,
         'A finite positive hourly disk rate is required')
    return Decimal(value)


def api_receipts(directory, action):
    indexed = {}
    for path in sorted(directory.glob(f'*-{action}.json')):
        doc, pin = read(path)
        response = doc['response']
        request = response.get('RequestId')
        need(doc['action'] == action and type(doc['exit_code']) is int and doc['exit_code'] == 0 and
             not response.get('Code') and isinstance(request, str) and request and request not in indexed,
             'Failed or duplicate API receipt: ' + path.name)
        indexed[request] = (doc, pin)
    return indexed


def disk_snapshot(api, role):
    instance = role['instance']
    resource = instance['id']
    frozen = {d['id']: d for d in role['disks']}
    response = api['response']
    disks = response['Disks']['Disk']
    need(api['params'].get('InstanceId') == resource and response['TotalCount'] == len(disks) == len(frozen) and
         not response.get('NextToken') and {d['DiskId'] for d in disks} == set(frozen),
         'Exact complete disk set not returned for ' + resource)
    keys = {}
    for disk in disks:
        did = disk['DiskId']
        expected = frozen[did]
        need(disk['InstanceId'] == resource and disk['Type'] == expected['type'] and
             type(disk['Size']) is int and disk['Size'] == expected['gib'] and
             disk['PerformanceLevel'] == expected['performance_level'] and disk['Category'] == 'cloud_essd' and
             disk['ZoneId'] == instance['zone'] and disk['DiskChargeType'] == 'PostPaid' and
             disk['Status'] == 'In_use' and disk['DeleteWithInstance'] is True,
             'Original attached PostPaid disk configuration differs: ' + did)
        keys[did] = (disk['ZoneId'], disk['Category'], disk['PerformanceLevel'], disk['Size'])
    return keys


def validate_quote(allowlist, quote):
    frozen, allow_pin = read(allowlist)
    need(allow_pin['sha256'] == ALLOWLIST_SHA and frozen['campaign'] == CAMPAIGN and frozen['passed'] is True and
         frozen['owned_instance_count'] == 3 and frozen['owned_disk_count'] == 9 and set(frozen['roles']) == set(ROLES),
         'The frozen three-instance, nine-disk Pool B allowlist is required')
    roles = frozen['roles']
    normalized, quote_pin = read(quote)
    quote_at = timestamp(normalized['at'])
    need(normalized['currency'] == 'CNY' and normalized['cloud_changed'] is False and
         quote_at >= timestamp(frozen['recorded_at']), 'Invalid disk quote currency, scope, or time')

    directory = Path(quote_pin['path']).parent
    disks = api_receipts(directory, 'DescribeDisks')
    expected_hosts = {row['instance']['id'] for row in roles.values()}
    by_host = {api['params']['InstanceId']: (api, pin) for api, pin in disks.values()}
    need(len(disks) == len(by_host) == len(expected_hosts) == 3 and set(by_host) == expected_hosts,
         'Three original quote-time DescribeDisks receipts for exact Pool B instances required')
    pricing = api_receipts(directory, 'DescribePrice')
    prices = {}
    for api, pin in pricing.values():
        params = api['params']
        key = (params['ZoneId'], params['DataDisk.1.Category'], params['DataDisk.1.PerformanceLevel'],
               params['DataDisk.1.Size'])
        price = api['response']['PriceInfo']['Price']
        need(params['ResourceType'] == 'disk' and params['PriceUnit'] == 'Hour' and params['Period'] == 1 and
             params['Amount'] == 1 and price['Currency'] == 'CNY' and key not in prices and
             timestamp(frozen['recorded_at']) <= timestamp(api['at']) <= quote_at,
             'Invalid or repeated one-disk hourly price receipt')
        prices[key] = positive_rate(price['TradePrice']), pin

    quote_rows = {row['disk_id']: row for row in normalized['disks']}
    need(len(normalized['disks']) == len(quote_rows) == 9 and
         set(quote_rows) == {d['id'] for role in roles.values() for d in role['disks']},
         'Normalized quote must contain each frozen disk exactly once')
    checked = {}
    used_prices = set()
    for role, row in roles.items():
        api, disk_pin = by_host[row['instance']['id']]
        need(timestamp(row['freeze_api_observed_at']) <= timestamp(api['at']) <= quote_at,
             'Disk quote inventory timestamp is out of order: ' + role)
        bindings = disk_snapshot(api, row)
        checked_rows = []
        for did, key in sorted(bindings.items()):
            quoted = quote_rows[did]
            rate = positive_rate(quoted['quoted_hourly_cny'])
            need(key in prices and (quoted['role'], quoted['category'], quoted['performance_level'], quoted['gib'], rate)
                 == (role, key[1], key[2], key[3], prices[key][0]), 'Disk quote differs from raw API price: ' + did)
            used_prices.add(key)
            checked_rows.append({**quoted, 'quoted_hourly_cny': float(rate), 'price_receipt': prices[key][1]})
        checked[role] = (sum(positive_rate(quote_rows[did]['quoted_hourly_cny']) for did in bindings),
                         checked_rows, disk_pin)
    need(used_prices == set(prices) and sum(v[0] for v in checked.values()) ==
         positive_rate(normalized['total_disk_hourly_cny']), 'Quoted disk rates or total do not match all source prices')
    return roles, checked, allow_pin, quote_pin, quote_at


def instance_snapshot(api, role, frozen, state):
    original = frozen['instance']
    host = original['id']
    response = api['response']
    instances = response['Instances']['Instance']
    need(json.loads(api['params']['InstanceIds']) == [host] and response['TotalCount'] == len(instances) == 1 and
         not response.get('NextToken'), 'Expected one exact instance API readback: ' + role)
    instance = instances[0]
    tags = {x['TagKey']: x['TagValue'] for x in instance['Tags']['Tag']}
    need(instance['InstanceId'] == host and instance['Status'] == state['status'] and
         instance.get('StoppedMode') == state['stopped_mode'] and instance['InstanceChargeType'] == 'PostPaid' and
         instance['ZoneId'] == original['zone'] and instance['InstanceType'] == original['type'] and
         instance['InternetMaxBandwidthOut'] == 0 and tags.get('campaign') == CAMPAIGN and tags.get('role') == role,
         'Instance readback does not prove original ownership, state and zero public bandwidth: ' + role)


def generate(allowlist, quote, stop_dir, out, now=None):
    now = now or datetime.now(timezone.utc)
    if out is not None:
        need(not out.exists() and not out.is_symlink(), 'Output must be a fresh directory')
        out = out.resolve()
    roles, checked, allow_pin, quote_pin, quote_at = validate_quote(allowlist, quote)
    done, done_pin = read(stop_dir / 'result.json')
    need(not (stop_dir / 'failed.json').exists() and done['passed'] is True and done['pool'] == 'pool-b' and
         done['preserved_disks'] is True and done['released'] is False and done['stopped'] == list(ROLES) and
         done['automatic_retry'] is False, 'Completed guarded StopCharging apply required')
    instances = api_receipts(stop_dir, 'DescribeInstances')
    disks = api_receipts(stop_dir, 'DescribeDisks')
    stops = api_receipts(stop_dir, 'StopInstance')
    need(len(stops) == len(ROLES), 'Exactly three original StopInstance API receipts required')
    proofs = {}
    previous = quote_at
    for role in ROLES:
        frozen = roles[role]
        host = frozen['instance']['id']
        expected_ids = sorted(d['id'] for d in frozen['disks'])
        raw, raw_pin = read(stop_dir / f'{role}-rate-change-raw.json')
        last = raw['last_running']
        need(raw['schema'] == 'ax-pilot-stopcharging-rate-change-raw-v1' and raw['passed'] is True and
             raw['pool'] == 'pool-b' and raw['role'] == role and raw['status'] == 'Stopped' and
             raw['stopped_mode'] == 'StopCharging' and raw['preserved_disks'] is True and raw['released'] is False and
             raw['quote_required_for_cost_event'] is True and last['status'] == 'Running' and
             all(s['resource_id'] == host and s['preserved_disk_ids'] == expected_ids for s in (last, raw)),
             'Raw preserved-disk StopCharging confirmation differs: ' + role)

        sources = {}
        snapshot_times = []
        for label, state in (('last_running', last), ('confirmed_stopped', raw)):
            instance_api, instance_pin = instances[state['instance_readback_request_id']]
            disk_api, disk_pin = disks[state['disk_readback_request_id']]
            instance_snapshot(instance_api, role, frozen, state)
            disk_snapshot(disk_api, frozen)
            snapshot_times.append((timestamp(instance_api['at']), timestamp(disk_api['at']), timestamp(state['at'])))
            sources[label] = {'instance': instance_pin, 'disks': disk_pin}
        stop_api, stop_pin = stops[raw['stop_request_id']]
        req = stop_api['params']
        need(req['InstanceId'] == host and req['ForceStop'] is False and req['StoppedMode'] == 'StopCharging',
             'Original API request was not a graceful StopCharging of ' + role)
        (before_i, before_d, before), (after_i, after_d, confirmed) = snapshot_times
        need(previous <= before_i <= before_d <= before <= timestamp(raw['stop_request_at']) <=
             timestamp(stop_api['at']) <= after_i <= after_d <= confirmed <= now,
             'StopCharging request/readback timestamps are out of order: ' + role)
        previous = confirmed
        rate, retained, quote_disks = checked[role]
        proofs[role] = dict(schema='ax-pilot-stopcharging-cost-rate-evidence-v1', passed=True, pool='pool-b', role=role,
                            configuration_verified=True, resource_id=host, at=raw['at'], status='Stopped',
                            stopped_mode='StopCharging', released=False, preserved_disks=True,
                            preserved_disk_ids=expected_ids, quoted_hourly_cny=float(rate), quote=quote_pin,
                            quote_scope='attached-system-and-data-disks-only', retained_disks=retained,
                            api_request_ids={k: raw[k] for k in ('stop_request_id', 'instance_readback_request_id',
                                                                 'disk_readback_request_id')},
                            sources=dict(allowlist=allow_pin, apply_result=done_pin, raw=raw_pin,
                                         quote_time_disks=quote_disks, stop_api=stop_pin, **sources))

    total = float(sum(v[0] for v in checked.values()))
    if out is None:
        return dict(passed=True, validate_only=True, total_disk_hourly_cny=total,
                    roles={role: {k: proof[k] for k in ('at', 'resource_id', 'quoted_hourly_cny', 'preserved_disk_ids')}
                           for role, proof in proofs.items()})

    # Write only after every role passes. The events index is written last.
    out.mkdir(mode=0o700)
    events = []
    for role, proof in proofs.items():
        proof_path = out / f'{role}-rate-change.json'
        write_new(proof_path, proof)
        _, proof_pin = read(proof_path)
        events.append(dict(resource_id=proof['resource_id'], at=proof['at'], kind='rate-change',
                           quoted_hourly_cny=proof['quoted_hourly_cny'], evidence=proof_pin))
    result = dict(schema='ax-pilot-cost-events-v1', reviewed=True, pool='pool-b', currency='CNY',
                  total_disk_hourly_cny=total, events=events)
    write_new(out / 'events.json', result)
    return result


def write_new(path, obj):
    with path.open('x', encoding='utf-8') as output:
        json.dump(obj, output, indent=2, allow_nan=False)
        output.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allowlist', type=Path, default=ALLOWLIST)
    parser.add_argument('--quote', type=Path, required=True, help='Disk-only quote alongside its original API JSON files')
    parser.add_argument('--stop-dir', type=Path, required=True, help='Successful stop-idle-pool.py --apply output directory')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--out', type=Path, help='New evidence directory (writes events.json last)')
    mode.add_argument('--validate-only', action='store_true', help='Verify local evidence and print rates without writing')
    args = parser.parse_args()
    try:
        result = generate(args.allowlist, args.quote, args.stop_dir, args.out)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'StopCharging cost events rejected: {exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
