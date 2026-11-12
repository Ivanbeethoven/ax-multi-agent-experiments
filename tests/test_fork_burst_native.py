"""Check current C4 dispatch and its command-only timing contract."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from bench.experiment_io import digest, write_new
from bench.fork_burst import NATIVE, native_argv, run


def pin(path):
    return dict(path=str(path),sha256=digest(path))


@unittest.skipUnless(os.name=='posix','uses a local Linux executable stand-in')
class NativeBurstTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        binary=self.root/'brewfs'
        binary.write_text('#!/bin/sh\nprintf "native child called\\n"\n')
        binary.chmod(0o700)
        tree, data=(self.root/name for name in ('tree.json','data.json'))
        write_new(tree,{'files':100000})
        write_new(data,{'data':{'backend':'s3'}})
        self.burst=dict(schema=NATIVE,reviewed=True,cell_id='pilot-c4-fork',method='brewfs-fork',
            parallelism=1,input_tree=pin(tree),native=dict(binary=pin(binary),redis_url='redis://10.2.0.10:6379/0',
            namespace='test-native',data_config=pin(data),revision='00000000-0000-0000-0000-000000000001:1:'+'1'*64))

    def step(self, argv):
        step=self.root/'step.json'
        write_new(step,dict(schema='experiment-step-v1',reviewed=True,cell_id=self.burst['cell_id'],
            stage='fork-child-01',cwd=str(self.root),argv=argv,
            inputs=[self.burst['input_tree'],self.burst['native']['data_config']],
            executable_sha256=self.burst['native']['binary']['sha256']))
        spec=self.root/'burst.json'
        write_new(spec,dict(self.burst,children=[dict(id='child-01',step=pin(step))]))
        return pin(spec)

    def test_uses_a_single_native_packed_fork_and_retains_command_clock(self):
        argv=native_argv(self.burst,'child-01')
        self.assertIn('--require-packed-carrier',argv)
        self.assertEqual(argv[argv.index('--count')+1],'1')
        out=self.root/'out'
        result=run(self.step(argv),out)
        self.assertTrue(result['passed'])
        self.assertFalse(result['performance_accepted'])
        self.assertEqual(result['method'],'brewfs-fork')
        row=result['results'][0]
        self.assertLessEqual(result['release_ns'],row['launch_ns'])
        self.assertEqual(result['wall_seconds'],(row['exit_ns']-result['release_ns'])/1e9)
        self.assertEqual((out/'child-01/stdout.log').read_text(),'native child called\n')

    def test_generic_or_batched_fork_is_rejected_before_any_dispatch(self):
        argv=native_argv(self.burst,'child-01')
        argv.remove('--require-packed-carrier')
        argv[argv.index('--count')+1]='8'
        out=self.root/'out'
        with self.assertRaisesRegex(ValueError,'argv'):
            run(self.step(argv),out)
        self.assertFalse(out.exists())

    def test_clone_requires_juicefs_mount_and_an_unused_destination(self):
        mount=self.root/'jfs'; mount.mkdir()
        src,dst=(mount/name for name in ('baseline','children'))
        src.mkdir();dst.mkdir()
        binary=self.root/'juicefs';binary.touch()
        s=dict(method='juicefs-clone',native=dict(binary=pin(binary),mount=str(mount),source=str(src),
               destination_parent=str(dst),threads=4))
        with patch('bench.fork_burst.mounts',return_value=[dict(filesystem='fuse.juicefs')]):
            self.assertEqual(native_argv(s,'child-01'),[str(binary),'clone','-p','--threads','4',str(src),str(dst/'child-01')])
            (dst/'child-01').mkdir()
            with self.assertRaisesRegex(ValueError,'exists'):
                native_argv(s,'child-01')
