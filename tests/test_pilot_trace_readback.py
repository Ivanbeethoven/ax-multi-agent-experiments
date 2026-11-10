"""Exercise the real Rust performance replay plus independent cold-tree checks."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'experiments/unified-suite/pilot-aliyun/scripts'


@unittest.skipUnless(os.name=='posix' and os.environ.get('FULL_FUSE_REPLAY_BIN'),'requires Linux Rust executable')
class TraceReadback(unittest.TestCase):
    def run_check(self,mutate=None,option=True):
        with tempfile.TemporaryDirectory(prefix='ax-trace-readback-') as tmp:
            out=Path(tmp);case=out/'case';case.mkdir();capture=case/'capture';capture.mkdir();(capture/'blobs').mkdir()
            root=out/'root';root.mkdir();(root/'file').write_bytes(b'right')
            expected={'file':dict(type='file',bytes=5,sha256=hashlib.sha256(b'right').hexdigest(),mode=0o644)}
            (root/'file').chmod(0o644)
            for name,value in [('initial-tree.json',expected),('final-tree.json',expected),('hardlink-check.json',{'groups':[]}),
                ('metrics.json',dict(callbacks=5,actual_read_bytes=5,actual_write_bytes=0,repeated_read_ratio=0))]:
                (case/name).write_text(json.dumps(value))
            rows=[('init',[],None),('open',['/file',0],7),
                ('read',['/file',5,0,7],dict(len=5,sha256=hashlib.sha256(b'right').hexdigest())),
                ('release',['/file',7],0),('destroy',[],None)]
            events=capture/'events.jsonl';events.write_text(''.join(json.dumps(dict(seq=i+1,op=op,args=args,result=result,errno=0))+'\n'
                    for i,(op,args,result) in enumerate(rows)))
            report=out/'replay.json'
            subprocess.run([os.environ['FULL_FUSE_REPLAY_BIN'],str(events),str(capture/'blobs'),str(root),str(report),'--performance'],check=True,capture_output=True)
            if mutate:mutate(root,report)
            argv=[sys.executable,str(SCRIPTS/'qualify-trace.py'),'--case',str(case),'--binary',os.environ['FULL_FUSE_REPLAY_BIN'],
                '--root',str(root),'--out',str(out/'check'),'--verify-only','--replay-result',str(report)]
            if option:argv.append('--performance-readback')
            result=subprocess.run(argv,capture_output=True,text=True)
            proof=out/'check/verification.json'
            return result,json.loads(proof.read_text()) if proof.exists() else None

    def test_actual_performance_replay_then_final_content_check(self):
        result,proof=self.run_check()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(proof['passed']);self.assertTrue(proof['final_byte_validation'])
        self.assertFalse(proof['byte_validation']);self.assertFalse(proof['replayed'])
        self.assertEqual(proof['replay_mode'],'performance')

    def test_corrupt_cold_file_is_rejected_after_successful_replay(self):
        result,proof=self.run_check(lambda root,report:(root/'file').write_bytes(b'wrong'))
        self.assertNotEqual(result.returncode,0);self.assertIsNone(proof)
        self.assertIn('Final tree differs',result.stderr)

    def test_performance_report_cannot_be_called_strict(self):
        result,proof=self.run_check(option=False)
        self.assertNotEqual(result.returncode,0);self.assertIsNone(proof)
        self.assertIn('validation status',result.stderr)

    def test_wrong_read_count_is_rejected(self):
        def change(root,path):
            value=json.loads(path.read_text());value['actual_read_bytes']+=1;path.write_text(json.dumps(value))
        result,proof=self.run_check(change)
        self.assertNotEqual(result.returncode,0);self.assertIsNone(proof)
        self.assertIn('Replay work differs',result.stderr)


@unittest.skipUnless(os.name=='posix','requires directory fsync and symlinks')
class ImportDurability(unittest.TestCase):
    def test_trace_link_is_persisted_through_parent_without_opening_target(self):
        spec=importlib.util.spec_from_file_location('pilot_io',SCRIPTS/'pilot_io.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'root';root.mkdir();(root/'file').write_bytes(b'payload')
            (root/'dangling').symlink_to('/does-not-exist-ax-test')
            self.assertEqual(module.fsync_import(root,allow_symlinks=True),1)
            with self.assertRaisesRegex(ValueError,'no symlinks'):module.fsync_import(root)


if __name__=='__main__':unittest.main()
