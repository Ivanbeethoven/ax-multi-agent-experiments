"""Small protocol fixtures only: no FUSE, cloud service, or C3 data generation."""
import copy
import json
import os
from pathlib import Path
import runpy
import tempfile
import types
import unittest
from unittest.mock import patch

from bench.suite_catalog import template,select_pilot_files
from bench.suite_plan import build
from bench.suite_mount import overlay

REPO=Path(__file__).resolve().parents[1]
NODE=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/packed-c3-node.py'))


class NativeDebt(unittest.TestCase):
    def test_fresh_upper_keeps_immutable_lower_and_drafts_the_original_suite_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=select_pilot_files(template(),100000)
            config['cases']=[next(case for case in config['cases'] if case['workload']=='smallfiles')]
            config['metadata_backend']='redis'
            for pool in config['clusters']:pool['metadata_backend']='redis'
            plan=build(config);cell=next(cell for cell in plan['cells'] if cell['arm_id']=='packed')
            plan_path=root/'plan.json';plan_path.write_text(json.dumps(plan))
            base=root/'base';base.mkdir()
            original=dict(product='brewfs',arm='packed',pool=cell['pool'],plan=NODE['ref'](plan_path),
                config=overlay(dict(meta={'backend':'redis','redis':{'url':'redis://127.0.0.1:1/20'}},data={'backend':'s3','s3':{'bucket':'fixture','endpoint':'http://127.0.0.1:1'}},
                    workspace='f014e48c-92cf-4b1f-a074-8e12c2f16d01',entity_volume_id='903648fc-0335-4ac8-b0c2-0f561e813530',workspace_namespace='parent',packed_manifest_key='immutable'),cell['client_settings']))
            path=base/'asset.json';path.write_text(json.dumps(original));unchanged=path.read_bytes()
            a=types.SimpleNamespace(base_asset=base,asset=root/'private',out=root/'fresh',plan=plan_path,cell=cell['id'])
            NODE['fresh'](a);private=json.loads((a.asset/'asset.json').read_text())
            for key in ('workspace','workspace_namespace','entity_volume_id'):self.assertNotEqual(private['config'][key],original['config'][key])
            for key in ('packed_manifest_key','data','meta'):self.assertEqual(private['config'][key],original['config'][key])
            self.assertEqual(path.read_bytes(),unchanged)
            request=root/'action.json';request.write_text(json.dumps(dict(schema='packed-c3-node-action-v1',action='fresh',arguments={
                'base_asset':str(base),'asset':str(root/'next-private'),'out':str(root/'next-fresh'),'plan':str(plan_path),'cell':cell['id']})))
            draft=root/'stage.json';NODE['stage_spec'](types.SimpleNamespace(request=request,out=draft))
            step=json.loads(draft.read_text());self.assertEqual(step['stage'],'create-w01');self.assertEqual(step['cell_id'],cell['id'])
            self.assertIs(step['reviewed'],False);self.assertFalse((root/'state.sqlite').exists());self.assertFalse((root/'sample.json').exists())

    def test_native_status_never_infers_missing_or_boolean_zero(self):
        namespace='f014e48c-92cf-4b1f-a074-8e12c2f16d01'
        status=dict(descriptor=dict(format='fixture',generation=1,lower={'manifest':'fixture'},namespace_id=namespace,state='Active',
            active_lease={'lease_id':namespace,'holder_generation':1}),debt=dict(objects=0,bytes=0))
        self.assertEqual(NODE['status_values'](status,namespace,'Active'),dict(pending_upload_bytes=0,pending_upload_operations=0))
        for value in [None,False,'0',1,-1,float('nan')]:
            broken=copy.deepcopy(status);broken['debt']['bytes']=value
            with self.assertRaises(ValueError):NODE['status_values'](broken,namespace,'Active')
        for change in [dict(namespace_id='another'),dict(state='Quiescent'),dict(active_lease=None)]:
            broken=copy.deepcopy(status);broken['descriptor'].update(change)
            with self.assertRaises(ValueError):NODE['status_values'](broken,namespace,'Active')
        heartbeat=copy.deepcopy(status);heartbeat['descriptor']['active_lease']['expires_ns']=999
        self.assertEqual(NODE['status_authority'](heartbeat),NODE['status_authority'](status))
        heartbeat['descriptor']['active_lease']['holder_generation']=2
        self.assertNotEqual(NODE['status_authority'](heartbeat),NODE['status_authority'](status))

    @unittest.skipIf(os.name=='nt','Native process identity requires Linux')
    def test_one_probe_two_native_samples_and_normal_exit_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);asset=root/'asset';asset.mkdir()
            namespace='f014e48c-92cf-4b1f-a074-8e12c2f16d01'
            (asset/'asset.json').write_text(json.dumps(dict(config=dict(workspace=namespace,entity_volume_id=namespace,meta={'redis':{'url':'redis://127.0.0.1:1/20'}}))))
            # A tiny process implements only the probe's JSONL protocol.
            probe=root/'fixture-probe';probe.write_text("#!/usr/bin/python3\nimport sys,json\nprint(json.dumps({'ready':True}),flush=True)\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({'descriptor':{'format':'fixture','generation':1,'lower':{'manifest':'fixture'},'namespace_id':r['namespace'],'state':'Active','active_lease':{'lease_id':r['namespace'],'holder_generation':1}},'debt':{'objects':0,'bytes':0}}),flush=True)\n")
            probe.chmod(0o755);sha=NODE['digest'](probe)
            manifest=root/'manifest.json';manifest.write_text(json.dumps(dict(files=[dict(file='entity_metadata_probe',sha256=sha)])))
            plan=root/'plan.json';plan.write_text('{}')
            args=types.SimpleNamespace(asset=asset,plan=plan,cell='fixture',manifest=manifest,probe=probe,probe_sha256=sha,out=root/'debt',state='Active')
            with patch.dict(NODE['debt'].__globals__,packed_cell=lambda *a:({'config':{'build_manifest_sha256':NODE['digest'](manifest)}},{'id':'fixture'})):
                NODE['debt'](args)
            result=json.loads((args.out/'result.json').read_text());samples=[json.loads(Path(pin['path']).read_text()) for pin in result['samples']]
            self.assertTrue(result['passed']);self.assertGreaterEqual(samples[1]['monotonic_ns']-samples[0]['monotonic_ns'],10**9)
            self.assertEqual(len((args.out/'stdin.jsonl').read_text().splitlines()),2)
            self.assertEqual(len((args.out/'stdout.jsonl').read_text().splitlines()),3)
            self.assertEqual(json.loads((args.out/'exit.json').read_text())['exit_code'],0)
            process=json.loads((args.out/'process.json').read_text())['process']
            self.assertTrue(all(key in process for key in ('pid','boot_id','start_ticks')))
