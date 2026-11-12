"""A private launcher must not mask modes already captured at FUSE callbacks."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

QUALIFY=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/qualify-trace.py'


@unittest.skipUnless(os.name=='posix' and os.environ.get('FULL_FUSE_REPLAY_BIN'),
                     'requires the Linux Rust replay executable')
class TraceUmask(unittest.TestCase):
    def test_private_launcher_preserves_captured_directory_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            parent=Path(folder); case=parent/'case'; case.mkdir()
            (case/'initial').mkdir(); (case/'capture/blobs').mkdir(parents=True)
            rows=[('init',[],None),('mkdir',['/state',0o755],0),
                  ('getattr',['/state',None],{'st_mode':stat.S_IFDIR|0o755,'st_size':4096}),
                  ('destroy',[],None)]
            (case/'capture/events.jsonl').write_text(''.join(json.dumps(dict(
                seq=index+1,op=op,args=args,result=result,errno=0))+'\n'
                for index,(op,args,result) in enumerate(rows)))
            for name,value in {'initial-tree.json':{},
                    'final-tree.json':{'state':{'type':'directory','mode':0o755}},
                    'hardlink-check.json':{'groups':[]},
                    'metrics.json':{'callbacks':4,'actual_read_bytes':0,'actual_write_bytes':0,
                                    'repeated_read_ratio':0}}.items():
                (case/name).write_text(json.dumps(value))
            proc=subprocess.run([sys.executable,str(QUALIFY),'--case',str(case),
                '--binary',os.environ['FULL_FUSE_REPLAY_BIN'],'--root',str(parent/'replay'),
                '--out',str(parent/'out')],capture_output=True,text=True,umask=0o077,timeout=10)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertEqual(stat.S_IMODE((parent/'replay/state').stat().st_mode),0o755)
            self.assertEqual(stat.S_IMODE((parent/'out/command.json').stat().st_mode),0o600)
            self.assertEqual(json.loads((parent/'out/command.json').read_text())['replay_umask'],0)
