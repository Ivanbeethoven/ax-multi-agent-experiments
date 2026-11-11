"""A successor cannot silently adopt an active predecessor or another collector."""
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = runpy.run_path(str(ROOT/'experiments/unified-suite/pilot-aliyun/scripts/continue-c2-juicefs.py'))['require_clean_predecessor']


class C2Handoff(unittest.TestCase):
    def setUp(self):
        self.recipe = dict(pool='pool-b')
        self.collector = ('ax-pilot-c4-b-jfs-on3-collector', '/srv/ax-objects-0/q/c4-b-jfs-on3-collector')
        self.handoff = dict(pool='pool-b', safe_for_handoff=True, collector_name=self.collector[0],
                            collector_out=self.collector[1])
        self.closure = dict(passed=True, pool='pool-b')

    def test_current_pool_and_exact_collector_with_clean_closure(self):
        GUARD(self.handoff, self.recipe, self.closure, self.collector)

    def test_cannot_treat_expected_or_stale_collector_as_handoff(self):
        for changes in (dict(safe_for_handoff=False), dict(pool='pool-a'), dict(collector_out='/guess')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GUARD(dict(self.handoff, **changes), self.recipe, self.closure, self.collector)

    def test_unpassed_or_other_pool_closure_rejected(self):
        for changes in (dict(passed=False), dict(pool='pool-a')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GUARD(self.handoff, self.recipe, dict(self.closure, **changes), self.collector)


if __name__ == '__main__':
    unittest.main()
