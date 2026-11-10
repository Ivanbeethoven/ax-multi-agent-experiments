"""The optional terminal-probe policy must retain byte validation and handles.

Run with FULL_FUSE_REPLAY_BIN set to a Linux build of the actual Rust executable.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name=='posix' and os.environ.get('FULL_FUSE_REPLAY_BIN'),'requires the Linux Rust executable')
class PortableReplay(unittest.TestCase):
    def replay(self, actual, expected, options=('--portable-terminal-probe',)):
        with tempfile.TemporaryDirectory(prefix='ax-portable-data-') as tmp:
            out=Path(tmp);root=out/'dedicated/root';root.mkdir(parents=True)
            (root/'file').write_bytes(actual);blobs=out/'blobs';blobs.mkdir()
            rows=[('init',[],None),('open',['/file',0],7),
                  ('read',['/file',len(expected),0,7],dict(len=len(expected),sha256=hashlib.sha256(expected).hexdigest())),
                  ('release',['/file',7],0),('destroy',[],None)]
            trace=out/'events.jsonl';trace.write_text(''.join(json.dumps(dict(seq=i+1,op=op,args=args,result=result,errno=0))+'\n'
                    for i,(op,args,result) in enumerate(rows)))
            process=subprocess.run([os.environ['FULL_FUSE_REPLAY_BIN'],str(trace),str(blobs),str(root),str(out/'result.json'),*options],
                    capture_output=True,text=True)
            report=json.loads((out/'result.json').read_text()) if (out/'result.json').stat().st_size else None
            return process,report

    def test_wrong_read_bytes_still_fail(self):
        process,report=self.replay(b'wrong',b'right')
        self.assertNotEqual(process.returncode,0)
        self.assertFalse(report['valid'])
        self.assertEqual(report['seq'],3)

    def test_valid_complete_reads_retain_exact_byte_checking(self):
        process,report=self.replay(b'right',b'right')
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertTrue(report['valid'])
        self.assertEqual(report['mode'],'strict-portable-terminal-probe')
        self.assertEqual(report['actual_read_bytes'],5)
        self.assertEqual(report['remaining_handles'],0)

    def test_default_strict_mode_stays_strict(self):
        process,report=self.replay(b'right',b'right',options=())
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertEqual(report['mode'],'strict')

    def test_directory_fd_cache_keeps_strict_byte_check(self):
        for actual,expected_code in [(b'right',0),(b'wrong',1)]:
            process,report=self.replay(actual,b'right',options=('--portable-terminal-probe','--cache-parent-fds'))
            self.assertEqual(process.returncode,expected_code,process.stderr)
            if not expected_code:
                self.assertEqual(report['path_resolution'],'parent-fd-cache')
                self.assertLessEqual(report['parent_fds']['peak'],report['parent_fds']['capacity'])


if __name__=='__main__':unittest.main()
