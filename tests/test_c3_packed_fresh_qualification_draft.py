import hashlib
from pathlib import Path
import runpy
import tempfile
import unittest


PILOT = Path(__file__).resolve().parents[1] / 'experiments/unified-suite/pilot-aliyun'
MOD = runpy.run_path(str(PILOT / 'scripts/draft-c3-packed-fresh-qualification.py'))
TAG = 'c3-reboot-20261112t120000z'


class PackedFreshDraftTest(unittest.TestCase):
    def test_resolves_original_bytes_and_distinct_fresh_paths(self):
        result = MOD['draft'](TAG)
        self.assertFalse(result['reviewed'])
        self.assertFalse(result['execution_enabled'])
        self.assertEqual(len(set(result['unused_worker_paths'])), 6)
        self.assertTrue(all(TAG in path for path in result['unused_worker_paths']))
        parent = dict(zip(result['parent_argv'][2::2], result['parent_argv'][3::2], strict=True))
        child = dict(zip(result['child_argv'][3::2], result['child_argv'][4::2], strict=True))
        pins = result['worker_sha256_prechecks']
        self.assertEqual(parent['--database'], MOD['DB'])
        self.assertEqual(parent['--asset'], child['--base-asset'])
        self.assertEqual(child['--cell'], MOD['CELL'])
        self.assertEqual(parent['--build-root'] + '/provenance/build-manifest.json', child['--manifest'])
        for key in ('prepare_tool', 'entity_tool'):
            path = PILOT / 'scripts' / Path(pins[key]['path']).name
            self.assertEqual(pins[key]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(parent['--inventory'], pins['inventory']['path'])
        self.assertNotIn('runtime_control', result)
        with self.assertRaisesRegex(ValueError, 'Use c3-reboot'):
            MOD['draft']('../unsafe')

    def test_does_not_draft_from_modified_plan(self):
        names = MOD['draft'](TAG)['historical_source_hashes']
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp)
            for name in names:
                path = copied / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((MOD['PREP'] / name).read_bytes())
            plan = copied / 'parallel-control-01/plan.json'
            plan.write_bytes(plan.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'Retained bytes differ'):
                MOD['draft'](TAG, copied)


if __name__ == '__main__':
    unittest.main()
