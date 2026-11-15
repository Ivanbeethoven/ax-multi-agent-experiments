"""Ensure successful operation counters cannot hide corrupt final bytes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(REPO/'bench/reference'),str(REPO/'bench'),str(REPO/'scripts')]
import aliyun_suite as suite
from full_trace_common import tree


class TraceFinalState(unittest.TestCase):
    def exercise(self, base, corruption=None, include_final=True):
        case=base/'case'; case.mkdir(); (case/'initial').mkdir(); (case/'capture/blobs').mkdir(parents=True)
        (case/'capture/events.jsonl').write_text('{}\n')
        suite.save(case/'initial-tree.json', {})
        metrics={'callbacks':1,'actual_read_bytes':3,'actual_write_bytes':0}
        suite.save(case/'metrics.json', metrics)
        mp=base/'mount'; root=mp/'trace-root'; root.mkdir(parents=True); (root/'a').write_bytes(b'abc')
        suite.save(case/'final-tree.json', tree(root))
        names=['capture/events.jsonl','initial-tree.json','metrics.json']
        if include_final:names.append('final-tree.json')
        manifest=base/'manifest.json'; suite.save(manifest,{n:suite.digest(case/n) for n in names})
        out=base/'out'; out.mkdir()
        class Session:
            def __init__(self):
                self.mp=mp;self.out=out;self.mounts=0
                self.s={'replayer':{},'trace_case':str(case),'trace_manifest':str(manifest),
                        'trace_manifest_sha256':suite.digest(manifest),'verify_final_tree':True,'redis_url':None}
            def mount(self):
                self.mounts+=1
                if self.mounts==2 and corruption=='after':(root/'a').write_bytes(b'bad')
            def unmount(self):pass
            def command(self,*args):
                if corruption=='before':(root/'a').write_bytes(b'bad')
                suite.save(out/'replay.json',{'completed':True,'remaining_handles':0,'operations':1,
                    'actual_read_bytes':3,'actual_write_bytes':0})
        session=Session()
        with patch.object(suite,'check_binary',return_value='unused'),patch.object(suite,'prepare_trace_root',return_value=root):
            suite.trace(session)
        return suite.load(out/'trace-result.json'), session.mounts

    def test_content_survives_remount(self):
        with tempfile.TemporaryDirectory() as d:
            result,mounts=self.exercise(Path(d))
            self.assertTrue(result['final_tree_verified_before_and_after_remount']);self.assertEqual(mounts,2)
            self.assertFalse(result['strict_correctness'])

    def test_counter_success_does_not_admit_corrupt_content(self):
        for phase in ('before','after'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as d:
                with self.assertRaisesRegex(ValueError,'final tree mismatch'):
                    self.exercise(Path(d),phase)
                self.assertFalse((Path(d)/'out/trace-result.json').exists())

    def test_original_final_tree_is_required(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'incomplete trace inventory'):
                self.exercise(Path(d),include_final=False)
