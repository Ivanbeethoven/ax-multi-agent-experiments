"""Purchase/deletion failure boundaries; no cloud requests or billable resources."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/cloud.py'
spec = importlib.util.spec_from_file_location('aliyun_pilot', FILE)
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


class PilotResourceBoundaries(unittest.TestCase):
    def test_single_pool_admits_one_qualified_arm_and_keeps_unqualified_arms_pending(self):
        sys.path.insert(0, str(FILE.parents[4]))
        from bench.suite_catalog import template
        from bench.suite_plan import build
        from bench import suite_state as state
        config = template()
        config['cases'] = [next(c for c in config['cases'] if c['workload']=='trace')]
        config['clusters'] = config['clusters'][:1]
        config['placement'] = json.loads((cloud.ROOT/'scope.json').read_text())['placement']
        config['input_manifest_sha256'] = 'a'*64
        config['build_manifest_sha256'] = 'b'*64
        config['clusters'][0]['inventory_sha256'] = 'c'*64
        plan = build(config)
        cell = next(c for c in plan['cells'] if c['arm_id']=='native')
        self.assertEqual(plan['blocks'][0]['pools'], ['pool-a'])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root/'state.sqlite'
            state.initialize(db, plan)
            def evidence(name, value):
                file = root/name
                file.write_text(json.dumps(value))
                return state.reference(file)
            proof = evidence('unit-test-proof.json', {'passed':True, 'purpose':'unit-test-only'})
            state.qualify(db, evidence('qualified.json', dict(schema='suite-qualification-v1',
                plan_sha256=plan['plan_sha256'], cell_id=cell['id'],
                checks=[dict(gate=g, evidence=proof, equals={'passed':True}) for g in cell['required_gates']])))
            state.register_asset(db, evidence('asset.json', dict(schema='suite-asset-v1',
                plan_sha256=plan['plan_sha256'], asset_id=cell['asset_id'], native_binding={'test_only':True},
                timings_seconds={'test_only':0}, stages={s:dict(evidence=proof, equals={'passed':True})
                for s in ['source-verify','import','full-base-verify','cache-reset']})))
            lease = state.claim(db, 'pool-a', 'operator-1')
            self.assertEqual(lease['cell']['id'], cell['id'])
            with self.assertRaisesRegex(ValueError, 'another operator'):
                state.claim(db, 'pool-a', 'operator-2')
            with state.transaction(db) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM cells WHERE status='pending'").fetchone()[0], 4)

    def test_cli_help_with_success_exit_is_not_an_api_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            driver = cloud.Cloud('unused', Path(tmp))
            result = subprocess.CompletedProcess([], 0, '{"schemaVersion":"v1","message":"invalid flag"}', '')
            with patch.object(cloud.subprocess, 'run', return_value=result), self.assertRaises(RuntimeError):
                driver.api('DescribeInstances')
            self.assertEqual(len(list(Path(tmp).glob('*.json'))), 1)

    def test_incomplete_build_prevents_any_purchase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate = root/'gate.json'
            gate.write_text('{"ready_for_cloud": false}')
            driver = cloud.Cloud('unused', root)
            with patch.object(driver, 'api') as api, self.assertRaises(ValueError):
                driver.create('objects', root/'ledger', 'ax-pilot-20261111', 'image', gate)
            api.assert_not_called()

    def test_branch_advancement_prevents_purchase_and_ledger_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root/'build.json'
            manifest.write_text(json.dumps({'sources':[
                {'component':'brewfs','repository':'https://github.com/brewfs/brewfs.git',
                 'branch':'codex/packed-prefetch','source_commit':'a'*40},
                {'component':'rustfs','repository':'https://github.com/Ivanbeethoven/rustfs.git',
                 'branch':'feat/dynamic-block-size-rebased','source_commit':'a'*40}]}))
            gate = root/'gate.json'
            gate.write_text(json.dumps(dict(ready_for_cloud=True, build_manifest_path=str(manifest),
                build_manifest_sha256=cloud.sha(manifest), binaries_verified=True,
                rustfs_image_verified=True, local_routes_checked=True, dependencies_prepared=True)))
            driver = cloud.Cloud('unused', root)
            with patch.object(driver, 'api') as api, patch.object(cloud.subprocess, 'check_output',
                return_value='b'*40+'\trefs/heads/main\n'), self.assertRaisesRegex(ValueError, 'branch advanced'):
                driver.create('objects', root/'ledger', 'ax-pilot-20261111', 'image', gate)
            api.assert_not_called()
            self.assertFalse((root/'ledger/objects.request.json').exists())

    def test_existing_jump_cannot_be_deleted_even_with_mislabelled_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            driver = cloud.Cloud('unused', root)
            (root/'objects.created.json').write_text(json.dumps(dict(instance_id=driver.scope['reuse_jump_instance'],
                preexisting=False, role='objects', campaign='ax-pilot-20261111')))
            closure = root/'closure.json'
            closure.write_text(json.dumps(dict(results_archived=True, workloads_stopped=True, mounts_absent=True)))
            with patch.object(driver, 'api') as api, self.assertRaisesRegex(ValueError, 'preexisting jump'):
                driver.release(root, 'objects', closure)
            api.assert_not_called()

    def test_changed_preparation_receipt_or_binary_rejects_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);driver=cloud.Cloud('unused',root)
            evidence=root/'evidence.json';evidence.write_text('{"passed":true}')
            binary=root/'binary';binary.write_bytes(b'actual binary')
            archive=root/'bundle';archive.write_bytes(b'actual bundle')
            receipt=root/'bundle.json';receipt.write_text('{"passed":true}')
            gate=dict(proofs={'native':dict(path=str(evidence),sha256=cloud.sha(evidence),equals={'passed':True})},
                binaries=[dict(path=str(binary),sha256=cloud.sha(binary))],
                deployment_bundle=dict(receipt=str(receipt),receipt_sha256=cloud.sha(receipt),archive=str(archive),sha256=cloud.sha(archive)))
            driver.verify_local_receipts(gate)
            evidence.write_text('{"passed":false}')
            with self.assertRaisesRegex(ValueError,'evidence changed'):
                driver.verify_local_receipts(gate)
            evidence.write_text('{"passed":true}')
            binary.write_bytes(b'replaced binary')
            with self.assertRaisesRegex(ValueError,'binary changed'):
                driver.verify_local_receipts(gate)

    def test_changed_ownership_prevents_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            driver = cloud.Cloud('unused', root)
            (root/'objects.created.json').write_text(json.dumps(dict(instance_id='i-owned', preexisting=False,
                role='objects', campaign='ax-pilot-20261111')))
            closure = root/'closure.json'
            closure.write_text(json.dumps(dict(results_archived=True, workloads_stopped=True, mounts_absent=True)))
            with patch.object(driver, 'api', return_value={'Instances':{'Instance':[{'Tags':{'Tag':[]}}]}}) as api:
                with self.assertRaisesRegex(ValueError, 'Ownership tags'):
                    driver.release(root, 'objects', closure)
            self.assertEqual([c.args[0] for c in api.call_args_list], ['DescribeInstances'])

    def test_control_disk_never_deletes_system_disk_or_unclosed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);driver=cloud.Cloud('unused',root)
            campaign='ax-pilot-20261111'
            (root/'coordinator-disk.created.json').write_text(json.dumps(dict(disk_id='d-candidate',
                campaign=campaign,preexisting=False,instance_id=driver.scope['reuse_jump_instance'])))
            closure=root/'closure.json'
            closure.write_text(json.dumps(dict(results_archived=True,workloads_stopped=True,control_disk_unmounted=True)))
            disk=dict(DiskId='d-candidate',Type='system',Size=100,Status='Available',InstanceId='',
                Tags={'Tag':[{'TagKey':'campaign','TagValue':campaign},{'TagKey':'role','TagValue':'coordinator-data'}]})
            with patch.object(driver,'api',return_value={'Disks':{'Disk':[disk]}}) as api:
                with self.assertRaisesRegex(ValueError,'ownership/type/size'):
                    driver.control_disk('delete-control-disk',root,campaign,closure=closure)
                self.assertEqual([c.args[0] for c in api.call_args_list],['DescribeDisks'])
            disk['Type']='data';closure.write_text('{}')
            with patch.object(driver,'api',return_value={'Disks':{'Disk':[disk]}}) as api:
                with self.assertRaisesRegex(ValueError,'results/writers'):
                    driver.control_disk('delete-control-disk',root,campaign,closure=closure)
                self.assertEqual([c.args[0] for c in api.call_args_list],['DescribeDisks'])


if __name__ == '__main__':
    unittest.main()
