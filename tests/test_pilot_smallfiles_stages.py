"""C3 keeps its own lifecycle, complete readback and explicit workspace identity."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
FILE=ROOT/'experiments/unified-suite/pilot-aliyun/scripts/run-c1.py'
spec=importlib.util.spec_from_file_location('pilot_flat_stages',FILE)
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


class SmallfileStages(unittest.TestCase):
    def cell(self,root,kind='smallfiles',workspaces=1):
        root=Path(root);control=root/'control';control.mkdir()
        cell=dict(id='unit-native',arm_id='native',workload=kind,arm=dict(product='brewfs'),
                  params=dict(agents=1,workspaces=workspaces,block_bytes=4096))
        (control/'suite').mkdir();(control/'suite/plan.json').write_text(json.dumps(dict(cells=[cell])))
        context=root/'context.json';context.write_text(json.dumps(dict(out=str(root/'out'),control=str(control),
            cell_id=cell['id'],qualification=str(root/'qualification'),smallfiles=dict(
                binary=dict(path='/original',sha256='a'*64),verifier=dict(path='/full-verifier',sha256='b'*64),
                copier=dict(path='/copy',sha256='c'*64)),trace=dict(case='/trace'))))
        return runner.Cell(context)

    def test_all_million_files_and_original_parent_are_checked_after_cold_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.cell(tmp);calls=[];marks=[]
            c.helper=lambda action,**fields:calls.append((action,fields));c.mark=marks.append
            c.execute('verify-w01')
            self.assertEqual([x[0] for x in calls],['draft-mount','start','verify-smallfiles','smallfiles-base-check','stop'])
            child=calls[2][1]
            self.assertEqual(child['smallfiles_verifier'],'/full-verifier')
            self.assertTrue(child['smallfiles_state'].endswith('/draft/workload/expected-touched.tsv'))
            self.assertEqual(child['workspace'],1)
            self.assertEqual(marks,['verify-end'])

    def test_creation_uses_pinned_independent_materializer_and_normal_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.cell(tmp);calls=[]
            c.helper=lambda action,**fields:calls.append((action,fields))
            c.execute('create-w01')
            self.assertEqual([x[0] for x in calls],['draft-mount','start','copy-smallfiles','debt','stop'])
            self.assertEqual(calls[2][1]['smallfiles_copier'],'/copy')
            self.assertEqual(calls[2][1]['smallfiles_copier_sha256'],'c'*64)

    def test_observer_mark_names_match_frozen_workspace_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.cell(tmp);p=c.control/'probes/pool-a-client';p.mkdir(parents=True)
            (p/'peer.json').write_text('{}')
            c.observers=lambda:[dict(id='pool-a-client',directory='/observer')]
            with patch.object(runner.fleet,'call') as call:c.mark('measure-start')
            self.assertEqual(call.call_args.kwargs['label'],'measure-start-w01')

    def test_more_than_one_workspace_cannot_silently_use_single_workspace_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'exactly one C3 workspace'):self.cell(tmp,workspaces=2)

    def test_fio_still_runs_crc_child_and_parent_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.cell(tmp,kind='fio');calls=[];c.mark=lambda label:None
            c.helper=lambda action,**fields:calls.append(action)
            c.execute('verify')
            self.assertEqual(calls,['draft-mount','start','fio','fio-base-check','stop'])


if __name__=='__main__':unittest.main()
