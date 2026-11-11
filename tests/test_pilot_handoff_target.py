"""Destination specs preserve the source volume without a format or state copy."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.prepare import anchor
from bench.suite_catalog import template
from bench.suite_plan import build

FILE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/draft-handoff-target.py'
spec=importlib.util.spec_from_file_location('pilot_handoff_target',FILE)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def save(path,value):write_new(path,value);return dict(path=str(path),sha256=digest(path))


class DestinationMount(unittest.TestCase):
    def fixture(self,root):
        config=template();config['metadata_backend']='redis'
        for pool in config['clusters']:pool['metadata_backend']='redis'
        config['cases']=[c for c in config['cases'] if c['workload']=='handoff']
        plan=build(config);plan_ref=save(root/'plan.json',plan)
        cell=next(c for c in plan['cells'] if c['arm']['product']=='juicefs')
        client=deepcopy(cell['client_settings']);client['metadata_url']='redis://10.20.2.162:6379/77'
        binary=Path(sys.executable).resolve();binary_ref=dict(path=str(binary),sha256=digest(binary))
        command=save(root/'source-command.json',dict(schema='experiment-step-v1',reviewed=True,
            executable_sha256=binary_ref['sha256']))
        source=save(root/'source-mount.json',dict(schema='experiment-mount-v1',reviewed=True,
            filesystem='juicefs',anchor=anchor('juicefs',client),purpose='experiment',
            mount_point=str(root/'shared'),suite_binding=dict(plan=plan_ref,cell_id=cell['id']),command=command))
        asset=save(root/'asset.json',dict(product='juicefs',config=client,plan=plan_ref))
        return dict(reviewed=True,plan=plan_ref,cell_id=cell['id'],source_asset=asset,
            source_mount=source,source_command=command,binary=binary_ref,purpose='experiment')

    def test_new_destination_opens_original_volume_without_copying_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);request=self.fixture(root);result=module.draft(request,root/'target')
            value=load(pinned(result['mount']));command=load(pinned(value['command']))
            self.assertEqual(result['anchor'],load(pinned(request['source_mount']))['anchor'])
            self.assertEqual(command['argv'][1:4],['mount','redis://10.20.2.162:6379/77',str(root/'shared')])
            self.assertEqual(command['inherit_environment'],[])
            self.assertEqual(value['budget']['cpus'],2)
            self.assertEqual(value['budget']['memory_bytes'],8*1024**3)
            self.assertTrue(value['reviewed']);self.assertTrue(command['reviewed'])
            self.assertFalse(result['mount_started']);self.assertEqual(result['workspace_copy_bytes'],0)
            self.assertEqual(list((root/'shared').iterdir()),[])

    def test_different_original_binary_or_anchor_is_rejected_before_drafting(self):
        for kind in ('binary','anchor'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);request=self.fixture(root)
                if kind=='binary':
                    value=load(pinned(request['source_command']));value['executable_sha256']='f'*64
                    request['source_command']=save(root/'wrong-command.json',value)
                    source=load(pinned(request['source_mount']));source['command']=request['source_command']
                    request['source_mount']=save(root/'wrong-mount.json',source)
                else:
                    value=load(pinned(request['source_asset']));value['config']['metadata_url']='redis://10.20.2.162:6379/78'
                    request['source_asset']=save(root/'wrong-asset.json',value)
                with self.assertRaises(ValueError):module.draft(request,root/'target')
                self.assertFalse((root/'target').exists())


if __name__=='__main__':unittest.main()
