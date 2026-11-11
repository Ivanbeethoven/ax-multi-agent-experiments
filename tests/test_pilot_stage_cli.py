"""Exercise the real stage CLI before any cloud admission or filesystem I/O."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/run-c1.py'
SPEC=importlib.util.spec_from_file_location('pilot_stage_cli',FILE)
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class StageCLI(unittest.TestCase):
    def invoke(self,stages,label):
        class FakeCell:
            def __init__(self,context):
                self.cell={'stages':stages}
            def execute(self,stage):
                called.append(stage)
        called=[]
        with patch.object(module,'Cell',FakeCell),patch.object(sys,'argv',[
            'run-c1.py','--context','fixture.json','--stage',label]):
            module.main()
        return called

    def test_all_c3_workspace_stage_names_reach_original_dispatch(self):
        stages=['observe-start',*[s+'-w01' for s in ('create','mount','measure','drain','unmount','verify')],
                'observe-stop','archive']
        for label in stages:
            with self.subTest(label=label):self.assertEqual(self.invoke(stages,label),[label])

    def test_c1_stage_names_remain_valid_and_other_workspace_is_rejected(self):
        self.assertEqual(self.invoke(['create','measure'],'create'),['create'])
        with self.assertRaisesRegex(ValueError,'frozen cell'):
            self.invoke(['create-w01'],'create-w02')


if __name__=='__main__':unittest.main()
