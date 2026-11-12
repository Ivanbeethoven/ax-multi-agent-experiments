"""Keep the native/proxy qualification gate strict in both cache modes."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest

from bench.warm_lower import KEYS

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('packed_native_probe', ROOT /
    'experiments/unified-suite/pilot-aliyun/scripts/packed_native_probe.py')
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def native(ranges=0, full=0, heads=0, body=0, logical=0):
    return dict(range_requests=ranges, full_requests=full, head_requests=heads,
                received_bytes=body, logical_bytes=logical, failures=0)


def proxy(ranges=0, full=0, heads=0, body=0):
    return dict(gets=ranges + full, ranges=ranges, heads=heads, bytes=body,
                errors=0, active=0, duplicate_gets=0)


def fixture(mode):
    mount = proxy(ranges=2, body=43)  # Deliberately unlike the real manifest.
    if mode == 'on':
        cold = native(full=3, heads=3, body=120, logical=16)
        last = native(full=3, heads=3, body=120, logical=32)
    else:
        cold = native(ranges=2, body=64, logical=16)
        last = native(ranges=4, body=128, logical=32)
    snapshots = {}
    for label, fields in [('before', native()), ('cold-drained', cold), ('warm-end', last)]:
        observed = proxy(ranges=mount['ranges'] + fields['range_requests'],
                         full=fields['full_requests'], heads=fields['head_requests'],
                         body=mount['bytes'] + fields['received_bytes'])
        snapshots[label] = dict(fields=fields, proxy_before=observed.copy(), proxy_after=observed.copy())
    return snapshots, mount, snapshots['warm-end']['proxy_after'].copy(), proxy()


class PackedNativeProbeTest(unittest.TestCase):
    def test_full_download_then_quiet_warm_pass_matches_proxy_without_mount(self):
        rows, mount, end, unmount = fixture('on')
        result = PROBE.evaluate(rows, 16, 'on', mount, end, unmount)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['native_runtime'], dict(full_requests=3, range_requests=0,
                                                       head_requests=3, received_bytes=120))
        self.assertTrue(all(row['matched'] for row in result['phase_reconciliation']))

    def test_off_arm_expects_backend_warm_reads_without_failing_correctness(self):
        rows, mount, end, unmount = fixture('off')
        result = PROBE.evaluate(rows, 16, 'off', mount, end, unmount)
        self.assertTrue(result['passed'], result)
        self.assertFalse(result['warm_lower_assessment']['passed'])
        self.assertEqual(result['warm_lower_assessment']['reasons'],
                         ['second pass still reads the Packed lower backend'])

    def test_off_arm_cannot_hide_bad_logical_bytes_or_failure_counters(self):
        for field, value in [('logical_bytes', 0), ('failures', 1)]:
            rows, mount, end, unmount = fixture('off')
            rows['cold-drained']['fields'][field] = value
            self.assertFalse(PROBE.evaluate(rows, 16, 'off', mount, end, unmount)['passed'])

    def test_counter_mismatch_retry_and_post_snapshot_work_are_rejected(self):
        for fault in ('mismatch', 'retry', 'unmount', 'busy-boundary'):
            with self.subTest(fault=fault):
                rows, mount, end, unmount = fixture('on')
                if fault == 'mismatch':
                    end['bytes'] += 1
                elif fault == 'retry':
                    end['errors'] = 1
                elif fault == 'unmount':
                    unmount['heads'] = 1
                else:
                    rows['warm-end']['proxy_before']['active'] = 1
                self.assertFalse(PROBE.evaluate(rows, 16, 'on', mount, end, unmount)['passed'])

    @unittest.skipUnless(hasattr(os, 'O_NOFOLLOW'), 'Linux stats reader required')
    def test_raw_sample_requires_every_native_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            point = Path(tmp) / 'mount'
            work = Path(tmp) / 'raw'
            point.mkdir(); work.mkdir()
            fields = native()
            raw = ''.join(f'{metric} {fields[key]}\n' for key, metric in KEYS.items()
                          if key != 'head_requests').encode()
            (point / '.stats').write_bytes(raw)
            sample = PROBE.NativeProbe(point, work, 'on-0', lambda: proxy(), 16,
                                       time.monotonic() + 5)
            with self.assertRaisesRegex(ValueError, 'Missing'):
                sample.sample('before')
            self.assertEqual((work / 'native-on-0-before.stats').read_bytes(), raw)

    def test_private_and_retained_visits_do_not_claim_cold_to_hot_qualification(self):
        for side in ('on-private', 'on-private-cold', 'on-0-remount', 'on-parent-isolation'):
            sample = PROBE.NativeProbe('/unused', '/unused', side, lambda: proxy(), 16,
                                       time.monotonic() + 5)
            sample.sample('before')
            self.assertFalse(sample.finish(proxy(), proxy(), proxy())['required'])


if __name__ == '__main__':
    unittest.main()
