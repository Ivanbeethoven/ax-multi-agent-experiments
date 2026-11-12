import importlib.util
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO/'experiments/unified-suite/pilot-aliyun/scripts/plan-c4-observer-budget.py'


def load_module():
    spec = importlib.util.spec_from_file_location('plan_c4_observer_budget', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_module()


class Prediction(unittest.TestCase):
    def test_reproduces_the_measured_manifest(self):
        # 132,944 entries came out at 20,430,785 bytes in the real window.
        per = module.KNOWN['manifest_bytes']/module.KNOWN['archive_members']
        self.assertAlmostEqual(per, 153.68, places=1)
        self.assertEqual(int(module.KNOWN['archive_members']*per), module.KNOWN['manifest_bytes'])

    def test_five_second_five_hour_series_overflows(self):
        row = module.predict(18000, 5)
        self.assertGreater(row['manifest_bytes'], module.MANIFEST_BOUND)
        self.assertGreater(row['samples'], 3600)
        self.assertGreater(row['members'], 100000)

    def test_short_series_fits(self):
        row = module.predict(600, 5)
        self.assertLess(row['manifest_bytes'], module.MANIFEST_BOUND)

    def test_members_scale_with_samples(self):
        one = module.predict(1800, 5)['members']
        two = module.predict(3600, 5)['members']
        self.assertGreater(two, one)
        self.assertLess(two, 3*one)

    def test_extra_members_are_counted(self):
        base = module.predict(600, 5)['members']
        more = module.predict(600, 5, extra=module.KNOWN['extra_members']+7)['members']
        self.assertEqual(more, base+7)

    def test_bad_input_is_refused(self):
        for seconds, interval in ((0, 5), (-1, 5), (600, 0), (600, -2)):
            with self.subTest(seconds=seconds, interval=interval):
                with self.assertRaises(ValueError):
                    module.predict(seconds, interval)


class Fit(unittest.TestCase):
    def test_coarsest_interval_makes_the_series_fit(self):
        seconds = 18000
        interval = module.fits(seconds)
        self.assertIsNotNone(interval)
        self.assertGreater(interval, 5)
        row = module.predict(seconds, interval)
        self.assertLessEqual(row['manifest_bytes'], module.MANIFEST_BOUND)
        tighter = module.predict(seconds, interval-0.5)
        self.assertGreater(tighter['manifest_bytes'], module.MANIFEST_BOUND)

    def test_impossible_series_reports_none(self):
        self.assertIsNone(module.fits(10, members_per_sample=10**6))


if __name__ == '__main__':
    unittest.main()
