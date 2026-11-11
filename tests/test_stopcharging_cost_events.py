import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'experiments/unified-suite/pilot-aliyun/scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('build-stopcharging-cost-events')
cost = load('record-cost')
START = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
RATES = {40: 0.042, 60: 0.126, 100: 0.21}


def at(seconds):
    return (START + timedelta(seconds=seconds)).isoformat()


def save(path, obj):
    path.write_text(json.dumps(obj), encoding='utf-8')


def api(folder, sec, action, params, **response):
    rid = f'request-{sec}-{action}'
    path = folder / f'{sec}-{action}.json'
    save(path, dict(at=at(sec), action=action, params=params, exit_code=0, response={'RequestId': rid, **response}))
    return rid


def fixture(root):
    frozen = json.loads(builder.ALLOWLIST.read_bytes())['roles']
    quote_dir, stop = root / 'quote', root / 'stop'
    quote_dir.mkdir()
    stop.mkdir()
    quote = quote_dir / 'quote.json'
    quoted = []
    for i, (role, row) in enumerate(frozen.items()):
        orig = row['instance']
        host, zone = orig['id'], orig['zone']
        disks = [dict(DiskId=d['id'], InstanceId=host, Type=d['type'], Size=d['gib'],
                      PerformanceLevel=d['performance_level'], Category='cloud_essd', ZoneId=zone,
                      DiskChargeType='PostPaid', Status='In_use', DeleteWithInstance=True) for d in row['disks']]
        for d in disks:
            quoted.append(dict(disk_id=d['DiskId'], role=role, gib=d['Size'], category='cloud_essd',
                               performance_level=d['PerformanceLevel'], quoted_hourly_cny=RATES[d['Size']]))

        def describe_disks(folder, sec):
            return api(folder, sec, 'DescribeDisks', {'InstanceId': host}, TotalCount=len(disks), Disks={'Disk': disks})

        def snapshot(sec, status, mode):
            instance = dict(InstanceId=host, Status=status, StoppedMode=mode, InstanceChargeType='PostPaid',
                            ZoneId=zone, InstanceType=orig['type'], InternetMaxBandwidthOut=0,
                            Tags={'Tag': [{'TagKey': 'campaign', 'TagValue': builder.CAMPAIGN},
                                          {'TagKey': 'role', 'TagValue': role}]})
            return dict(at=at(sec+2), resource_id=host, status=status, stopped_mode=mode,
                        preserved_disk_ids=sorted(d['DiskId'] for d in disks),
                        instance_readback_request_id=api(stop, sec, 'DescribeInstances',
                                                         {'InstanceIds': json.dumps([host])}, TotalCount=1,
                                                         Instances={'Instance': [instance]}),
                        disk_readback_request_id=describe_disks(stop, sec+1))

        describe_disks(quote_dir, i)
        base = 20 + 10*i
        last = snapshot(base, 'Running', None)
        request = api(stop, base+4, 'StopInstance', dict(InstanceId=host, ForceStop=False, StoppedMode='StopCharging'))
        save(stop / f'{role}-rate-change-raw.json', dict(
            schema='ax-pilot-stopcharging-rate-change-raw-v1', passed=True, pool='pool-b', role=role,
            **snapshot(base+5, 'Stopped', 'StopCharging'), preserved_disks=True, released=False, last_running=last,
            stop_request_at=at(base+3), stop_request_id=request, quote_required_for_cost_event=True))
    for i, (gib, rate) in enumerate(RATES.items(), 5):
        api(quote_dir, i, 'DescribePrice', dict(ZoneId='cn-hangzhou-j', ResourceType='disk', PriceUnit='Hour',
                                               Period=1, Amount=1, **{'DataDisk.1.Category': 'cloud_essd',
                                               'DataDisk.1.PerformanceLevel': 'PL0' if gib == 40 else 'PL1',
                                               'DataDisk.1.Size': gib}),
            PriceInfo={'Price': {'Currency': 'CNY', 'TradePrice': rate}})
    save(quote, dict(at=at(8), currency='CNY', cloud_changed=False, disks=quoted, total_disk_hourly_cny=0.966))
    save(stop / 'result.json', dict(passed=True, pool='pool-b', preserved_disks=True, released=False,
                                     stopped=list(builder.ROLES), automatic_retry=False))
    return quote, stop


class StopChargingCostEventsTest(unittest.TestCase):
    def test_preserved_disks_keep_cost_after_each_confirmed_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            quote, stop = fixture(root)
            now = START + timedelta(minutes=5)
            validated = builder.generate(builder.ALLOWLIST, quote, stop, None, now)
            self.assertEqual(0.966, validated['total_disk_hourly_cny'])
            self.assertEqual({'quote', 'stop'}, {p.name for p in root.iterdir()})
            out = root / 'new-cost-events'
            result = builder.generate(builder.ALLOWLIST, quote, stop, out, now)
            known = {x['resource_id'] for x in result['events']}
            changes = cost.events(out / 'events.json', known, now)
            for event, (sec, expected_rate) in zip(result['events'], ((27, .252), (37, .168), (47, .546))):
                self.assertEqual(at(sec), event['at'])
                segments, active, released, through = cost.intervals(START, now, 1.0, changes[event['resource_id']])
                self.assertEqual([1.0, expected_rate], [s['quoted_hourly_cny'] for s in segments])
                self.assertEqual(expected_rate, active)
                self.assertFalse(released)
                self.assertEqual(now, through)

    def test_rejects_wrong_preservation_or_disk_price_before_any_output(self):
        for case in ('unconfirmed_disk', 'wrong_price'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                quote, stop = fixture(root)
                out = root / 'uncreated'
                if case == 'unconfirmed_disk':
                    path = stop / 'objects-rate-change-raw.json'
                    doc = json.loads(path.read_text())
                    doc['preserved_disk_ids'].pop()
                    error = 'Raw preserved-disk StopCharging confirmation differs'
                else:
                    path = quote
                    doc = json.loads(path.read_text())
                    doc['disks'][0]['quoted_hourly_cny'] = 0.01
                    error = 'Disk quote differs from raw API price'
                save(path, doc)
                with self.assertRaisesRegex(ValueError, error):
                    builder.generate(builder.ALLOWLIST, quote, stop, out, START + timedelta(minutes=5))
                self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
