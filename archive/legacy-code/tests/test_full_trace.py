"""Linux integration checks using a recorded no-model preflight fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_full_fuse_trace import analyze


@unittest.skipUnless(os.environ.get('FULL_TRACE_PROBE'), 'set FULL_TRACE_PROBE to the recorded preflight')
class FullTraceTests(unittest.TestCase):
    def setUp(self):
        self.source=Path(os.environ['FULL_TRACE_PROBE'])
        self.binary=Path('/home/hxy/ax-full-trace-build/release/full-fuse-replay')
        self.temp=tempfile.TemporaryDirectory(prefix='full-trace-test-')
        self.root=Path(self.temp.name)
        shutil.copytree(self.source/'initial',self.root/'workspace')
        self.events=[json.loads(l) for l in (self.source/'capture/events.jsonl').read_text().splitlines()]
    def tearDown(self):self.temp.cleanup()
    def run_trace(self,events):
        trace=self.root/'events.jsonl'
        trace.write_text(''.join(json.dumps(e)+'\n' for e in events))
        return subprocess.run([str(self.binary),str(trace),str(self.source/'capture/blobs'),str(self.root/'workspace'),str(self.root/'result.json')],capture_output=True,text=True)
    def test_preflight_round_trip(self):
        self.assertEqual(self.run_trace(self.events).returncode,0)
    def test_missing_normal_unmount_rejected(self):
        self.assertNotEqual(self.run_trace(self.events[:-1]).returncode,0)
    def test_sequence_gap_rejected(self):
        self.events[2]['seq']+=1
        self.assertNotEqual(self.run_trace(self.events).returncode,0)
    def test_changed_read_content_rejected(self):
        next(e for e in self.events if e['op']=='read' and e['errno']==0)['result']['sha256']='0'*64
        self.assertNotEqual(self.run_trace(self.events).returncode,0)
    def test_missing_write_payload_rejected(self):
        next(e for e in self.events if e['op']=='write')['args'][1]['blob']='0'*64
        self.assertNotEqual(self.run_trace(self.events).returncode,0)
    def test_counters_match_rust(self):
        self.assertEqual(self.run_trace(self.events).returncode,0)
        metrics=analyze(self.source/'capture/events.jsonl')
        replay=json.loads((self.root/'result.json').read_text())
        self.assertEqual(metrics['actual_read_bytes'],replay['actual_read_bytes'])
        self.assertEqual(metrics['actual_write_bytes'],replay['actual_write_bytes'])
        self.assertEqual(metrics['callbacks'],replay['operations'])


if __name__=='__main__':unittest.main()
