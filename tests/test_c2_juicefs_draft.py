"""A copied dataset qualifies a future recipe only for its exact physical pool."""
from pathlib import Path
import runpy
import unittest

ROOT=Path(__file__).resolve().parents[1]
CODE=runpy.run_path(str(ROOT/'experiments/unified-suite/pilot-aliyun/scripts/draft-c2-juicefs.py'))


class TraceDraft(unittest.TestCase):
    def test_requires_actual_complete_copy_to_same_pool_and_case(self):
        cell=dict(pool='pool-b',params={'trace':'pytest-full-02'})
        copied=dict(passed=True,destination='pool-b-client',case='/case/pytest-full-02',manifest='/manifest.json',binary='/replay',
            manifest_sha256='a'*64,binary_sha256='b'*64,verified_bytes=914193836,manifest_files=67577,initial_entries=714)
        self.assertEqual(CODE['trace_source'](copied,cell)['manifest_files'],67577)
        for change in [dict(destination='pool-a-client'),dict(case='/case/other'),dict(passed=False),dict(verified_bytes=0),dict(binary='relative')]:
            with self.subTest(change=change),self.assertRaises(ValueError):CODE['trace_source'](dict(copied,**change),cell)


if __name__=='__main__':unittest.main()
