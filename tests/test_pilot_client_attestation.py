"""Attestation must recover the original route without assuming a C1 layout."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[1]
TOOL=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/attest-client-binary.py'))


class ActualRoute(unittest.TestCase):
    def test_each_route_type_uses_its_exact_original_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.json';path.write_text(json.dumps(dict(passed=True)))
            route=TOOL['ref'](path);row=dict(source='/srv/c3/run/result.json',**route)
            for key in ('native_checks','complete_route','complete_base'):
                self.assertEqual(TOOL['original_route']({key:route},[row]),(route,row['source']))
            for transfers in ([],[row,row]):
                with self.assertRaises(ValueError):TOOL['original_route']({'complete_base':route},transfers)
            with self.assertRaises(ValueError):TOOL['original_route']({'native_checks':route,'complete_base':route},[row])
            path.write_text('{}')
            with self.assertRaises(ValueError):TOOL['original_route']({'complete_base':route},[row])
