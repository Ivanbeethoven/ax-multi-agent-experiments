"""A real stop/HTTP race must not hide a live failure, reset or bad receipt."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from bench.runtime import observe


class MountLifecycle(unittest.TestCase):
    def fixture(self,root):
        directory=root/'mount';directory.mkdir()
        mount=dict(mount_id=123,point='/shared',filesystem='fuse.juicefs')
        process=dict(pid=12,boot_id='one-boot',start_ticks=100,state='R')
        ref=dict(path='/frozen/mount.json',sha256='a'*64);anchor=dict(volume='original')
        def write(name,value):(directory/name).write_text(json.dumps(value))
        write('ready.json',dict(process={**process,'state':'S'},spec=ref,anchor=anchor,mount=mount))
        write('process.json',dict(process=process,spec=ref))
        def stop(bad=False):
            write('stopped.json',dict(passed=True,mount_absent=True,exit_code=0,active_workers=[],
                anchor={'volume':'wrong'} if bad else anchor,original_mount=mount))
        target=dict(id='filesystem',mount_directory=str(directory),required_names=['operations_total'],counter_names=['operations_total'])
        settings=dict(cgroups=[],processes=[],data_paths=[],targets=[target])
        return target,settings,stop,mount

    def sample(self,root,settings,collect):
        with patch.object(observe,'HOST_FILES',[]),patch.object(observe,'alive',return_value=False),\
             patch.object(observe,'mounts',return_value=[]),patch.object(observe,'collect_target',side_effect=collect):
            return observe.snapshot(settings,root,0)

    def test_connection_closes_during_verified_original_stop_without_inventing_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target,settings,stop,_=self.fixture(root)
            def collect(t):stop();raise URLError('connection refused during normal stop')
            value=self.sample(root,settings,collect)
            self.assertTrue(value['passed']);self.assertEqual(value['counters'],{})
            self.assertEqual(value['target_states']['filesystem']['state'],'cleanly-stopped')
            self.assertTrue(value['target_states']['filesystem']['stopped_during_collection'])
            self.assertNotIn('filesystem.txt',value['files'])

    def test_live_connection_failure_remains_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,settings,_,_=self.fixture(root)
            def collect(t):raise URLError('live endpoint down')
            self.assertFalse(self.sample(root,settings,collect)['passed'])

    def test_http_error_or_counter_reset_cannot_be_hidden_by_later_stop(self):
        for error in [ValueError('counter reset'),HTTPError('http://localhost',500,'failed',None,None)]:
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);_,settings,stop,_=self.fixture(root)
                def collect(t):stop();raise error
                self.assertFalse(self.sample(root,settings,collect)['passed'])

    def test_wrong_anchor_or_live_original_process_rejects_stop_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target,_,stop,_=self.fixture(root);stop(bad=True)
            with self.assertRaises(ValueError):observe.mount_lifecycle(target)
            stop()
            with patch.object(observe,'alive',return_value=True),self.assertRaises(ValueError):
                observe.mount_lifecycle(target)

    def test_old_mount_can_be_stopped_after_kernel_reuses_its_id_and_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target,_,stop,mount=self.fixture(root);stop()
            with patch.object(observe,'alive',return_value=False),patch.object(observe,'mounts',return_value=[mount]):
                self.assertEqual(observe.mount_lifecycle(target)['state'],'cleanly-stopped')

    def test_stopping_drain_window_waits_for_verified_stop_receipt(self):
        import threading, time
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target,settings,stop,mount=self.fixture(root)
            mdir=Path(target['mount_directory'])
            (mdir/'stopping.json').write_text(json.dumps({'anchor':{'volume':'original'},'original_mount':mount}))
            def delayed_stop():
                time.sleep(.15)
                stop()
            writer=threading.Thread(target=delayed_stop);writer.start()
            try:
                def collect(t):raise URLError('metrics listener closed while draining TiKV/FUSE')
                value=self.sample(root,settings,collect)
            finally:
                writer.join(2)
            self.assertTrue(value['passed'])
            self.assertEqual(value['target_states']['filesystem']['state'],'cleanly-stopped')
            self.assertTrue(value['target_states']['filesystem']['stopped_during_collection'])

