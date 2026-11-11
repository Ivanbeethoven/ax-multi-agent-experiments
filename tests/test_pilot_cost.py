"""Cost intervals must follow actual configuration/release evidence."""
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

FILE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/record-cost.py'
spec=importlib.util.spec_from_file_location('pilot_cost',FILE)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class CostIntervals(unittest.TestCase):
    def test_resize_then_release_stops_at_confirmed_time(self):
        start=datetime(2026,11,11,tzinfo=timezone.utc)
        changes=[dict(kind='rate-change',at=(start+timedelta(hours=2)).isoformat(),quoted_hourly_cny=5),
            dict(kind='release-confirmed',at=(start+timedelta(hours=3)).isoformat())]
        rows,rate,released,end=module.intervals(start,start+timedelta(hours=8),3,changes)
        self.assertEqual(sum(row['estimated_cny'] for row in rows),11)
        self.assertTrue(released);self.assertEqual(rate,0);self.assertEqual(end,start+timedelta(hours=3))

    def test_absent_release_proof_keeps_counting(self):
        start=datetime(2026,11,11,tzinfo=timezone.utc)
        rows,rate,released,end=module.intervals(start,start+timedelta(hours=8),3,[])
        self.assertEqual(sum(row['estimated_cny'] for row in rows),24)
        self.assertFalse(released);self.assertEqual(rate,3)
        with self.assertRaises(ValueError):module.timestamp('2026-11-11T00:00:00')
        with self.assertRaises(ValueError):module.intervals(start,start+timedelta(hours=1),float('nan'),[])

    def test_request_or_partial_disk_release_cannot_end_charging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proof=root/'proof.json';events=root/'events.json'
            at=datetime(2026,11,11,1,tzinfo=timezone.utc).isoformat()
            def write(value):
                proof.write_text(json.dumps(value))
                event=dict(kind='release-confirmed',resource_id='i-owned',at=at,evidence=module.ref(proof))
                events.write_text(json.dumps(dict(schema='ax-pilot-cost-events-v1',reviewed=True,events=[event])))
            value=dict(passed=True,resource_id='i-owned',at=at,released=False,all_owned_disks_absent=False)
            write(value)
            with self.assertRaisesRegex(ValueError,'Actual absence'):module.events(events,{'i-owned'},datetime(2026,11,11,2,tzinfo=timezone.utc))
            value.update(released=True);write(value)
            with self.assertRaisesRegex(ValueError,'every owned disk'):module.events(events,{'i-owned'},datetime(2026,11,11,2,tzinfo=timezone.utc))
            value.update(all_owned_disks_absent=True);write(value)
            self.assertEqual(len(module.events(events,{'i-owned'},datetime(2026,11,11,2,tzinfo=timezone.utc))['i-owned']),1)
            proof.write_text('{}')
            with self.assertRaisesRegex(ValueError,'evidence changed'):module.events(events,{'i-owned'},datetime(2026,11,11,2,tzinfo=timezone.utc))


if __name__=='__main__':unittest.main()
