"""A finished calibration must not count as a formally accepted experiment."""
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
TOOL=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/audit-progress.py'))


class Progress(unittest.TestCase):
    def test_finished_calibration_is_separate_from_pending_formal_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);trial=root/'calibration/off1';trial.mkdir(parents=True)
            (trial/'finished.json').write_text(json.dumps(dict(passed=True,makespan_seconds=2)))
            run=root/'run';(run/'trial-1').mkdir(parents=True)
            (run/'trial-1/exit.json').write_text(json.dumps(dict(exit_code=0)))
            request=root/'request.json';request.write_text(json.dumps(dict(out=str(run),cell_id='c',pool='pool-b',trials=[dict(attempt='off1',mode='off',out=str(trial))])))
            with sqlite3.connect(root/'state.sqlite') as db:
                db.executescript('CREATE TABLE campaign(plan TEXT);CREATE TABLE cells(id TEXT,status TEXT,result TEXT);CREATE TABLE cell_leases(cell_id TEXT,pool TEXT,owner TEXT);CREATE TABLE events(sequence INTEGER,utc TEXT,kind TEXT,payload TEXT);')
                db.execute('INSERT INTO campaign VALUES (?)',(json.dumps(dict(plan_sha256='a'*64,cells=[dict(id='c',pool='pool-b',workload='smallfiles',arm_id='juicefs')])),))
                db.execute("INSERT INTO cells VALUES ('c','pending',NULL)")
            unchanged=(root/'state.sqlite').read_bytes()
            formal=TOOL['controller'](root)
            with patch.dict(TOOL['request_progress'].__globals__,subprocess=types.SimpleNamespace(run=lambda *a,**k:types.SimpleNamespace(returncode=0,stdout='ActiveState=active\n',stderr=''))):
                running=TOOL['request_progress'](request,'unit')
            self.assertTrue(running['trials'][0]['finished.json']['value']['passed'])
            self.assertEqual(running['trials'][0]['trial_exit']['value']['exit_code'],0)
            self.assertEqual(formal['accepted_formal_samples'],0)
            self.assertEqual(formal['counts'],{'pending':1})
            self.assertEqual((root/'state.sqlite').read_bytes(),unchanged)
