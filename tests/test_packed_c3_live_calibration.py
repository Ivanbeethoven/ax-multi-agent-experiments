"""Small calibration-interface tests; no FUSE or full input generation."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[1];TOOLS=REPO/'experiments/unified-suite/pilot-aliyun/scripts'
NODE=runpy.run_path(str(TOOLS/'packed-c3-calibration-node.py'))
CONTROLLER=runpy.run_path(str(TOOLS/'packed-c3-controller.py'))
CHECKER=runpy.run_path(str(TOOLS/'check-packed-c3-live-calibration.py'))

class LiveCalibration(unittest.TestCase):
    def test_all_touched_rows_are_required_without_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state';p.write_text('smallfiles-v1\t100000\t1\n4\t2\n17\t5\n')
            self.assertEqual(NODE['touched'](p,100000,1,2),[(4,2),(17,5)])
            with self.assertRaisesRegex(ValueError,'All declared'):NODE['touched'](p,100000,1,3)
            p.write_text('smallfiles-v1\t100000\t1\n4\t2\n4\t3\n')
            with self.assertRaisesRegex(ValueError,'duplicate'):NODE['touched'](p,100000,1,2)

    def test_properties_require_a_regular_complete_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'file';p.write_bytes(b'x'*4096)
            self.assertEqual(NODE['properties'](p)['bytes'],4096)
            p.write_bytes(b'x')
            with self.assertRaises(ValueError):NODE['properties'](p)

    def controller(self,purpose):
        c=CONTROLLER['PackedMethods'].__new__(CONTROLLER['PackedMethods'])
        c.purpose=purpose;c.s=dict(calibration_verification='live-touched-v1');c.id='c';c.base='/q/t';c.asset='/a/t';c.main='/m/t';c.tag='t'
        c.binding=dict(base_asset='/base',plan_remote='/plan',manifest_remote='/manifest',source='/source',source_proof_remote='/proof',private_object_env='/private',
                       tools={n:dict(path='/'+n,sha256='a'*64) for n in ('binary','probe','reader','verifier','oracle')})
        calls=[];c.entity=lambda action,**kwargs:calls.append(('entity',action));c.calibration_node=lambda action,**kwargs:calls.append(('calibration',action))
        c.helper=lambda action,**kwargs:calls.append(('helper',action));c.mark=lambda *args:None;c.remote=lambda *args:'a'*64+' /proof'
        return c,calls

    def test_live_calibration_uses_no_cold_reader(self):
        c,calls=self.controller('observer-calibration-on')
        for stage in ('create-w01','drain-w01','unmount-w01','verify-w01'):c.execute(stage)
        self.assertEqual(calls,[('entity','fresh'),('calibration','parent-snapshot'),('calibration','live-check'),('entity','debt'),('helper','stop'),('calibration','finish')])

    def test_formal_branch_still_calls_original_full_cold(self):
        c,calls=self.controller('experiment');c.execute('verify-w01')
        self.assertEqual(calls,[('entity','cold-verify')]);self.assertFalse(c.live_calibration())

    def test_checker_rejects_a_live_trial_claiming_cold_or_borrowed_reader(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);value=dict(passed=True,verification_mode='live-touched-v1',full_namespace_cold=True)
            (p/'finished.json').write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'live-only'):CHECKER['trial'](p,'off',None,None,None)
            value.update(full_namespace_cold=False,verify_exit={'path':'borrowed-stop'})
            (p/'finished.json').write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'own cold reader'):CHECKER['trial'](p,'off',None,None,None)

if __name__=='__main__':unittest.main()
