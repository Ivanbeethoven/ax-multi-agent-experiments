"""Check current C4 dispatch and its command-only timing contract."""
from pathlib import Path
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from bench import fork_source
from bench.experiment_io import digest, load, write_new
from bench.fork_burst import NATIVE, SOURCE_BOUND, native_argv, run
from bench.runtime.prepare import anchor
from scripts.full_trace_common import tree


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


@unittest.skipUnless(os.name=='posix','uses a local Linux executable stand-in')
class SourceBoundBurstTest(unittest.TestCase):
    """Use actual source gate and subprocess dispatch; simulate only FUSE observations."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.out = self.root/'out'
        self.point = self.root/'mount'
        fixture, source, parent = self.root/'fixture', self.point/'source', self.point/'children'
        for folder in (fixture, source, parent):
            folder.mkdir(parents=True, exist_ok=True)
        for folder in (fixture, source):
            (folder/'task').write_bytes(b'actual reviewed bytes\n')
        binary = self.root/'juicefs'
        binary.write_text('#!/bin/sh\nprintf "native clone ran\\n"\n')
        binary.chmod(0o700)
        config = dict(metadata_url='redis://127.0.0.1:6379/5', cache_dir='memory')
        config_ref = self.record('mount-config.json', config)
        volume = anchor('juicefs', config)

        def mount(name, point, index):
            argv = [str(binary), 'mount', config['metadata_url'], str(point)]
            step = self.record(f'{name}/command.json', dict(schema='experiment-step-v1', reviewed=True,
                               argv=argv, executable_sha256=digest(binary), inputs=[config_ref]))
            spec = self.record(f'{name}/mount.json', dict(schema='experiment-mount-v1', reviewed=True,
                               filesystem='juicefs', mount_point=str(point), anchor=volume, config=config_ref,
                               command=step, allowed_fstypes=['fuse.juicefs']))
            identity = dict(mount_id=index, point=str(point), filesystem='fuse.juicefs',
                            source='fixture-volume', options='rw')
            snapshot = dict(passed=True, anchor=volume, mount=identity,
                            process=dict(pid=index+100, boot_id='test-boot', start_ticks=index))
            ready = self.record(f'{name}/ready.json', {**snapshot, 'spec': spec})
            stop = self.record(f'{name}/stopped.json', dict(passed=True, exit_code=0, mount_absent=True,
                               anchor=volume, original_mount=identity, active_workers=[])) if name != 'measure' else None
            return dict(mount_spec=spec, ready=ready), snapshot, stop

        writer, _, writer_stop = mount('writer', self.root/'writer-mount', 10)
        readback, self.current, readback_stop = mount('readback', self.point, 11)
        measurement, self.measured, _ = mount('measure', self.point, 12)

        def observed(point):
            mounted = self.current['mount']
            return [mounted] if str(point) == mounted['point'] else []

        def status(directory, action):
            self.assertEqual(action, 'status')
            self.assertEqual(directory.name, 'readback' if self.current['mount']['mount_id'] == 11 else 'measure')
            return self.current

        for target, side_effect in (
            ('bench.fork_source.mounts', observed), ('bench.fork_burst.mounts', observed),
            ('bench.fork_source.node.control', status),
            ('bench.fork_source.alive', lambda process: process == self.current['process']),
        ):
            probe = patch(target, side_effect=side_effect)
            probe.start()
            self.addCleanup(probe.stop)

        native = dict(binary=pin(binary), mount=str(self.point), source=str(source),
                      destination_parent=str(parent), threads=4)
        input_tree = self.record('fixture-tree.json', tree(fixture))
        self.burst = dict(schema=SOURCE_BOUND, reviewed=True, cell_id='pilot-c4-source-bound',
                          method='juicefs-clone', parallelism=1, native=native, input_tree=input_tree)
        request = {**readback, **{k: self.burst[k] for k in ('cell_id', 'method', 'native', 'input_tree')},
                   'schema': fork_source.REQUEST, 'reviewed': True,
                   'writer': dict(ready=writer['ready'], stopped=writer_stop)}
        qualification = self.root/'qualification.json'
        self.qualified = fork_source.qualify(self.record('source.json', request), qualification)
        self.current = self.measured
        self.proof = dict(receipt=pin(qualification), probe_stopped=readback_stop, measurement=measurement)

    def record(self, name, value):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_new(path, value)
        return pin(path)

    def request(self, proof):
        step = self.record('native-step.json', dict(schema='experiment-step-v1', reviewed=True,
                           cell_id=self.burst['cell_id'], stage='fork-child-01', cwd=str(self.root),
                           argv=native_argv(self.burst, 'child-01'), inputs=[self.burst['input_tree']],
                           executable_sha256=self.burst['native']['binary']['sha256']))
        return self.record('burst.json', dict(self.burst, children=[dict(id='child-01', step=step)],
                           source_qualification=proof))

    def test_failed_actual_preflight_prevents_output_and_dispatch(self):
        request = self.request({**self.proof, 'probe_stopped': None})
        with patch('bench.fork_burst.subprocess.Popen') as start:
            with self.assertRaisesRegex(ValueError, 'normally closed qualification'):
                run(request, self.out)
        start.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_success_persists_source_binding_and_release_before_real_local_dispatch(self):
        result = run(self.request(self.proof), self.out)
        self.assertEqual(result['schema'], 'suite-fork-burst-native-result-v3')
        self.assertTrue(result['passed'])
        self.assertFalse(result['performance_accepted'])
        self.assertEqual(result['source_binding'], {**self.qualified, 'measurement_mount': {
            k: self.measured[k] for k in ('anchor', 'mount', 'process')}})
        self.assertEqual(load(self.out/'result.json'), result)
        self.assertLessEqual(result['release_ns'], result['results'][0]['launch_ns'])
        self.assertEqual((self.out/'child-01/stdout.log').read_text(), 'native clone ran\n')

    def test_mount_swap_after_workers_are_ready_prevents_dispatch(self):
        request = self.request(self.proof)
        test = self

        class SwapAfterReady(threading.Barrier):
            def wait(self, timeout=None):
                value = super().wait(timeout)
                if threading.current_thread() is threading.main_thread():
                    test.current = {**test.measured, 'mount': {**test.measured['mount'], 'mount_id': 19}}
                return value

        with (patch('bench.fork_burst.threading.Barrier', SwapAfterReady),
              patch('bench.fork_burst.subprocess.Popen') as start):
            with self.assertRaisesRegex(ValueError, 'JuiceFS measurement mount changed'):
                run(request, self.out)
        start.assert_not_called()
        self.assertTrue((self.out/'native-intent.json').exists())
        self.assertFalse((self.out/'child-01/process.json').exists())
        self.assertFalse((self.out/'result.json').exists())
