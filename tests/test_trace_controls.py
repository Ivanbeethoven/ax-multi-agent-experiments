"""Full trace verification must skip JuiceFS control streams without hiding user files."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[1]
QUAL=REPO/'experiments/unified-suite/pilot-aliyun/scripts/qualify-trace.py'


@unittest.skipUnless(os.name=='posix' and os.environ.get('FULL_FUSE_REPLAY_BIN'),'requires Linux Rust executable')
class ControlStreams(unittest.TestCase):
    def check(self, corrupt=False):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp); case=out/'case'; case.mkdir()
            initial=case/'initial'; initial.mkdir(); root=out/'root'; root.mkdir()
            digest=hashlib.sha256(b'right').hexdigest()
            expected={}
            for folder in (initial,root):
                (folder/'file').write_bytes(b'right'); (folder/'file').chmod(0o644)
                os.link(folder/'file',folder/'alias')
                (folder/'sub').mkdir(); (folder/'sub').chmod(0o755)
                (folder/'sub/.accesslog').write_bytes(b'right'); (folder/'sub/.accesslog').chmod(0o644)
            for name in ('file','alias','sub/.accesslog'):
                expected[name]={'type':'file','bytes':5,'sha256':digest,'mode':0o644}
            expected['sub']={'type':'directory','mode':0o755}
            os.mkfifo(root/'.accesslog'); (root/'.trash').mkdir(); os.mkfifo(root/'.trash/stream')
            (case/'capture/blobs').mkdir(parents=True)
            for name,value in [('initial-tree.json',expected),('final-tree.json',expected),
                ('hardlink-check.json',{'groups':[['alias','file']]}),
                ('metrics.json',{'callbacks':5,'actual_read_bytes':5,'actual_write_bytes':0,'repeated_read_ratio':0})]:
                (case/name).write_text(json.dumps(value))
            rows=[('init',[],None),('open',['/file',0],7),
                  ('read',['/file',5,0,7],{'len':5,'sha256':digest}),
                  ('release',['/file',7],0),('destroy',[],None)]
            (case/'capture/events.jsonl').write_text(''.join(json.dumps({'seq':i+1,'op':op,'args':args,'result':value,'errno':0})+'\n'
                for i,(op,args,value) in enumerate(rows)))
            common=[sys.executable,str(QUAL),'--case',str(case),'--binary',os.environ['FULL_FUSE_REPLAY_BIN'],
                    '--root',str(root),'--juicefs-controls']
            first=subprocess.run(common+['--out',str(out/'strict'),'--prepared-root'],capture_output=True,text=True,timeout=10)
            self.assertEqual(first.returncode,0,first.stderr)
            if corrupt: (root/'sub/.accesslog').write_bytes(b'wrong')
            cold=subprocess.run(common+['--out',str(out/'cold'),'--verify-only','--replay-result',str(out/'strict/replay.json')],
                                capture_output=True,text=True,timeout=10)
            return cold

    def test_control_fifo_does_not_block_strict_or_cold_checks(self):
        result=self.check(); self.assertEqual(result.returncode,0,result.stderr)

    def test_same_named_user_file_is_still_checked(self):
        result=self.check(corrupt=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Final tree differs',result.stderr)
