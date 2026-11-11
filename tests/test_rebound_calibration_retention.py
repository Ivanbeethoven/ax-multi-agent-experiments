"""Historical trials are retained only after the new formal actually closes."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
CODE=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/retain-rebound-calibrations.py'))


class ReboundRetention(unittest.TestCase):
    def test_live_unit_and_open_lease_cannot_be_retained_as_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cell={'id':'c'};result=root/'one-cell-runs/c/result.json';result.parent.mkdir(parents=True)
            close=root/'closing.json';close.write_text(json.dumps({'outcome':'complete','cell_id':'c'}))
            ref=CODE['ref'](close);result.write_text(json.dumps({'passed':True,'closing':ref}))
            function=CODE['boundary'];scope=function.__globals__
            with patch.dict(scope,active_conflicts=lambda *args:[]):
                with patch.object(scope['subprocess'],'check_output',return_value='MainPID=4\nActiveState=activating\nSubState=start\nExecMainStatus=0\n'):
                    with self.assertRaisesRegex(ValueError,'must have exited'):function(root,root,cell,'unit')
                with patch.object(scope['subprocess'],'check_output',return_value='MainPID=0\nActiveState=active\nSubState=exited\nExecMainStatus=0\n'):
                    self.assertTrue(function(root,root,cell,'unit')['passed'])
            with patch.dict(scope,active_conflicts=lambda *args:[{'cell_id':'other'}]):
                with self.assertRaisesRegex(ValueError,'lease remains'):function(root,root,cell,'unit')


if __name__=='__main__':unittest.main()
