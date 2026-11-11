"""Compare all original formal stage inputs against the packing-only overlay."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bench.experiment_io import load
from bench.suite_plan import STAGES

SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/(name+'.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


overlay = module('run-c4-formal-closed-observer')
original = module('run-c4')


def doc(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')
    return original.ref(path)


class FormalC4(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        out = self.root/'formal'
        control = self.root/'control'
        control.mkdir()
        self.c = SimpleNamespace(id='frozen-c4',pool='pool-a',tag='c4-a-formal',purpose='experiment',
            verification_policy=original.c4_verify.FULL,out=out,control=control,check_sources=Mock(),
            cell={'stages':STAGES[:]},plan={'plan':'original'})
        self.context = out/'context.json'
        self.context_pin = doc(self.context,{'original':'context','attempt':self.c.tag})
        self.lease = {'already_claimed':False,'cell':self.c.cell}
        doc(out/'lease.json',self.lease)
        doc(out/'sample.json',{'original':'sample'})
        self.s = dict(schema='c4-formal-archive-overlay-v1',reviewed=True,
            wrapper=original.ref(Path(overlay.__file__)),original_runner=original.ref(Path(original.__file__)),
            calibration_overlay=original.ref(SCRIPTS/'calibrate-c4-closed-observer.py'),
            helper=original.ref(SCRIPTS/'archive-c4-closed-observer.py'),worker_code={},
            context=self.context_pin,cell_id=self.c.id,pool=self.c.pool,attempt=self.c.tag)
        self.request = doc(self.root/'formal-overlay.request.json',self.s)

    def record_run(self, use_overlay):
        captured = []
        def run_stage(db,lease,stage,pin,out):
            self.assertEqual((db,lease,out),(self.c.control/'state.sqlite',self.lease,self.c.out/'stages'/stage))
            self.assertEqual(original.ref(Path(pin['path'])),pin)
            captured.append(load(pin['path']))
            return 0
        with patch.object(original,'Fork',return_value=self.c), \
                patch.object(original.suite_state,'run_stage',side_effect=run_stage), \
                patch.object(original.suite_report,'sample',return_value={'accepted':True}) as accept, \
                patch.object(original.common,'close_cell') as close:
            if use_overlay:
                with patch.object(overlay,'overlay_inputs',return_value=(None,'')),patch.object(overlay,'module',return_value=original):
                    overlay.run(self.request)
            else:
                with patch.object(sys,'argv',['run-c4.py','--context',str(self.context)]):
                    original.main()
            accept.assert_called_once_with(original.ref(self.c.out/'sample.json'),self.c.plan,self.c.cell)
            close.assert_called_once_with(self.c,'complete')
        for path in (self.c.out/'steps').iterdir():
            path.unlink()
        (self.c.out/'acceptance.json').unlink()
        return captured

    def test_only_archive_argv_and_pins_change(self):
        old = self.record_run(False)
        new = self.record_run(True)
        self.assertEqual([s['stage'] for s in old],STAGES)
        self.assertEqual(old[:-1],new[:-1])
        self.assertEqual({k:v for k,v in old[-1].items() if k not in ('argv','inputs')},
                         {k:v for k,v in new[-1].items() if k not in ('argv','inputs')})
        self.assertEqual(new[-1]['inputs'],old[-1]['inputs']+[self.s['wrapper'],self.s['calibration_overlay'],
                                                             self.s['helper'],self.request])
        self.assertEqual(new[-1]['argv'],[sys.executable,self.s['wrapper']['path'],'--request',
                                         self.request['path'],'--sha256',self.request['sha256'],'--archive-stage'])
        self.c.check_sources.assert_has_calls([unittest.mock.call(),unittest.mock.call()])
        self.assertEqual(load(self.context),{'original':'context','attempt':self.c.tag})

    def test_archive_child_needs_real_started_step_and_executor_parent(self):
        spec = overlay.stage_spec(original,self.context,self.c.id,'archive',self.request,self.s)
        pin = doc(self.c.out/'steps/archive.json',spec)
        output = self.c.out/'stages/archive'
        doc(output/'started.json',dict(cell_id=self.c.id,stage='archive',spec_sha256=pin['sha256'],
                                      wrapper_pid=os.getppid(),automatic_retry=False))
        with sqlite3.connect(self.c.control/'state.sqlite') as db:
            db.execute('CREATE TABLE stages(cell_id,name,status,spec_sha,output)')
            db.executemany('INSERT INTO stages VALUES (?,?,?,?,?)',[
                (self.c.id,n,'complete','p','x') for n in STAGES[:-1]])
            db.execute('INSERT INTO stages VALUES (?,?,?,?,?)',
                       (self.c.id,'archive','started',pin['sha256'],str(output)))
        with patch.object(original.suite_state,'admitted') as admitted:
            overlay.archive_authorized(original,self.c,self.lease,spec)
            admitted.assert_called_once()
            with patch.object(overlay.os,'getppid',return_value=0):
                with self.assertRaisesRegex(ValueError,'one-step executor'):
                    overlay.archive_authorized(original,self.c,self.lease,spec)
            with sqlite3.connect(self.c.control/'state.sqlite') as db:
                db.execute("UPDATE stages SET status='failed' WHERE name='verify'")
            with self.assertRaisesRegex(ValueError,'original verification'):
                overlay.archive_authorized(original,self.c,self.lease,spec)


if __name__=='__main__':
    unittest.main()