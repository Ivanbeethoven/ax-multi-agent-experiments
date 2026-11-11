"""Check reuse of six original trial receipts without executing any workload."""
import copy
import json
from pathlib import Path
import runpy
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[1]
CODE=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/continue-flat-after-check.py'))


class FlatContinuation(unittest.TestCase):
    def test_exact_original_six_trials_are_reused_and_changed_receipts_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);trials=[];cohorts=[]
            for index,mode in enumerate(('off','on','on','off','off','on')):
                out=root/str(index);(out/'cohort').mkdir(parents=True);path=out/'cohort/result.json'
                path.write_text(json.dumps(dict(purpose='observer-calibration-'+mode,scope='Unit fixture only')))
                pin=CODE['ref'](path);cohorts.append(pin)
                (out/'finished.json').write_text(json.dumps(dict(passed=True,cohort=pin)))
                trials.append(dict(mode=mode,out=str(out)))
            original={'trials':trials};overhead=dict(passed=True,pairs=[dict(off=cohorts[i],on=cohorts[j]) for i,j in ((0,1),(3,2),(4,5))])
            self.assertEqual(len(CODE['calibrations'](original,overhead)),6)
            changed=copy.deepcopy(overhead);changed['pairs'][0]['off']=cohorts[4]
            with self.assertRaises(ValueError):CODE['calibrations'](original,changed)
            changed=copy.deepcopy(original);changed['trials'][0]['mode']='on'
            with self.assertRaises(ValueError):CODE['calibrations'](changed,overhead)
            Path(cohorts[0]['path']).write_text('{}')
            with self.assertRaises(ValueError):CODE['calibrations'](original,overhead)


if __name__=='__main__':unittest.main()
