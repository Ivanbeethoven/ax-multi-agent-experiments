"""Small coordinator-only evidence/probe mocks; no SSH or cloud calls."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'experiments/unified-suite/pilot-aliyun/scripts/setup-c4-control.py'
loader=importlib.util.spec_from_file_location('c4_setup_test',PATH)
tool=importlib.util.module_from_spec(loader);loader.loader.exec_module(tool)
from bench.runtime.common import load,write_new,digest


class C4Setup(unittest.TestCase):
    def fixture(self,root):
        old=root/'old';runtime=root/'old-runtime';out=root/'fork';old.mkdir();runtime.mkdir()
        (old/'provenance').mkdir();write_new(old/'provenance/inventory.json',{'fixture':'native cloud inventory'})
        for n in ('build-manifest.json','input-manifest.json'):write_new(old/'provenance'/n,{'fixture':n})
        config=dict(build_manifest_sha256=digest(old/'provenance/build-manifest.json'),input_manifest_sha256=digest(old/'provenance/input-manifest.json'),
            clusters=[dict(id=p,inventory_sha256=digest(old/'provenance/inventory.json'),resource_ids=['host:'+p]) for p in ('pool-a','pool-b')])
        plan=dict(config=config,plan_sha256='new-plan',cells=[dict(id='fork-b',workload='fork',arm_id='juicefs',pool='pool-b'),
            dict(id='fork-a',workload='fork',arm_id='native',pool='pool-a')])
        (old/'suite').mkdir();write_new(old/'suite/plan.json',dict(plan,plan_sha256='old-plan',cells=[dict(workload='fio')]))
        fork=root/'fork-plan.json';write_new(fork,plan)
        nodes=[dict(id=p+'-'+r,cluster_id=p,role='executor' if r=='client' else 'service',repo='/fixture/'+p+'/'+r+'/code-12',
            work_root='/fixture/'+p+'/'+r,host_id=p+r,slots=[dict(id=p+'-client')] if r=='client' else []) for p in ('pool-a','pool-b') for r in ('client','metadata','objects')]
        write_new(runtime/'fleet.json',dict(nodes=nodes));(runtime/'ssh.conf').write_text('original SSH configuration')
        write_new(runtime/'allocation.json',dict(passed=True,inventory_sha256=digest(runtime/'fleet.json')))
        for node in nodes:
            for suffix in ('','-slot') if node['role']=='executor' else ('',):
                p=runtime/'probes'/(node['id']+suffix);p.mkdir(parents=True)
                write_new(p/'probe.json',dict(passed=True,boot_id='boot-'+node['id'],sampled_at='2026-01-01T00:00:00Z'))
                write_new(p/'peer.json',dict(repo=node['repo'],ssh={}))
        (old/'state.sqlite').write_bytes(b'original state, never copied')
        s=dict(source_control=str(old),source_runtime={},fork_plan=tool.ref(fork),control=str(out))
        return old,runtime,out,s

    def create(self,root):
        old,runtime,out,s=self.fixture(root)
        with mock.patch.object(tool.pilot_context,'select',return_value=runtime),mock.patch.object(tool,'check_plan',side_effect=lambda x:x):
            tool.control(s)
        return old,runtime,out,s

    def test_control_preserves_original_bytes_and_does_not_copy_state(self):
        with tempfile.TemporaryDirectory() as directory:
            old,runtime,out,s=self.create(Path(directory))
            self.assertEqual((out/'fleet.json').read_bytes(),(runtime/'fleet.json').read_bytes())
            original=runtime/'probes/pool-b-client/probe.json';retained=out/'probes/pool-b-client/probe.json'
            self.assertEqual(original.read_bytes(),retained.read_bytes());self.assertFalse((out/'state.sqlite').exists())
            self.assertEqual((old/'state.sqlite').read_bytes(),b'original state, never copied')
            self.assertFalse(load(out/'setup.json')['state_created'])
            with mock.patch.object(tool.pilot_context,'select',return_value=runtime),mock.patch.object(tool,'check_plan',side_effect=lambda x:x),self.assertRaises(ValueError):tool.control(s)

    def refresh(self,root):
        old,original,out,s=self.create(root);close=root/'close.json';staging=root/'staging.json'
        write_new(close,dict(schema='suite-cell-close-v1',outcome='complete'));write_new(staging,dict(passed=True))
        request=dict(control=str(out),cell_id='fork-b',canonical_control=str(old),other_lease_controls=[],
            code_directory='code-15',out=str(out/'runtime-15-01'),phase_barriers=[tool.ref(close),tool.ref(staging)])
        calls=[]
        def probe(inv,node_id,slot,ssh,target,repo,host_observer=False):
            calls.append((node_id,slot));target.mkdir(parents=True)
            value=dict(passed=True,boot_id='boot-'+node_id,at='fresh-runtime-probe')
            write_new(target/'probe.json',value);write_new(target/'peer.json',dict(repo='/fresh/code-15'))
            return value
        with mock.patch.object(tool,'check_plan',side_effect=lambda x:x),mock.patch.object(tool,'active_conflicts',return_value=[]),mock.patch.object(tool.fleet,'probe',side_effect=probe):
            binding=tool.runtime(request)
        return old,original,out,request,binding,calls

    def test_runtime_probes_only_selected_pool_and_keeps_other_time(self):
        with tempfile.TemporaryDirectory() as directory:
            _,_,out,request,binding,calls=self.refresh(Path(directory))
            self.assertEqual(len(calls),4);self.assertTrue(all(n.startswith('pool-b-') for n,_ in calls))
            runtime=Path(request['out']);old=load(out/'probes/pool-a-client/probe.json');new=load(runtime/'probes/pool-a-client/probe.json')
            self.assertEqual(old,new);self.assertEqual(new['sampled_at'],'2026-01-01T00:00:00Z')
            self.assertEqual(tool.pilot_context.select(out,binding),runtime)
            allocation=load(runtime/'allocation.json');self.assertEqual(len(allocation['reused_unchanged_nodes']),3)

    def test_runtime_refuses_missing_barrier_without_probing(self):
        with tempfile.TemporaryDirectory() as directory:
            old,_,out,_=self.create(Path(directory))
            s=dict(control=str(out),cell_id='fork-b',canonical_control=str(old),other_lease_controls=[],code_directory='code-15',out=str(out/'runtime'),phase_barriers=[])
            with mock.patch.object(tool,'check_plan',side_effect=lambda x:x),mock.patch.object(tool.fleet,'probe') as probe,self.assertRaises(ValueError):tool.runtime(s)
            probe.assert_not_called()

    def test_derived_hardware_preserves_native_observation_time_and_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);_,_,out,request,binding,_=self.refresh(root)
            cross=root/'cross.json';hardware=root/'hardware.json'
            write_new(cross,dict(schema='pilot-cross-pool-equivalence-v1',passed=True,at='original-native-time',
                plan_sha256='old-plan',inventory_sha256=digest(out/'fleet.json'),cloud_inventory_sha256=digest(out/'provenance/inventory.json'),
                pools=['pool-a','pool-b'],checks=dict(actual_hardware=True),source_pins=[tool.ref(out/'provenance/build-manifest.json')]))
            write_new(hardware,dict(passed=True,pool='pool-b',inventory_sha256=digest(out/'fleet.json'),checks=dict(native=True)))
            s=dict(control=str(out),cell_id='fork-b',runtime_control=binding,original_cross_pool=tool.ref(cross),original_hardware=tool.ref(hardware),out=str(root/'derived'))
            with mock.patch.object(tool,'check_plan',side_effect=lambda x:x):result=tool.hardware(s)
            derived=load(result['cross_pool']['path']);self.assertEqual(derived['at'],'original-native-time')
            self.assertEqual(derived['original_observation'],tool.ref(cross));self.assertEqual(derived['plan_sha256'],'new-plan')
            self.assertNotEqual(derived['inventory_sha256'],load(cross)['inventory_sha256'])
            self.assertIn(tool.ref(cross),derived['source_pins'])

    def test_concrete_requests_use_frozen_bytes_and_stay_unexecutable(self):
        path=PATH.with_name('draft-c4-requests.py');spec=importlib.util.spec_from_file_location('c4_drafts_test',path)
        drafts=importlib.util.module_from_spec(spec);spec.loader.exec_module(drafts)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);code=root/'code-15';control=root/'control';(control/'suite').mkdir(parents=True)
            write_new(control/'suite/plan.json',{'actual':'frozen C4 plan'})
            names=('run-c4-fork-baseline.py','capacity-check.py','prepare-c4-controller.py','calibrate-c4.py',
                   'check-c4-calibration.py','admit-c4.py','run-c4.py','pilot-node.py','c4-fork-node.py','make-observer.py')
            manifest={}
            for n in names:
                p=code/(drafts.PREFIX+n);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(n);manifest[drafts.PREFIX+n]=digest(p)
            runtime_pins={'bench/fs_step.py':'old-sha'};manifest['bench/fs_step.py']='frozen-new-sha'
            manifest_file=root/'manifest.json';write_new(manifest_file,manifest)
            original=root/'original.json';write_new(original,dict(pool='pool-b',product='juicefs',cell_id='actual-c4-cell',
                inventory=dict(path='/original/inventory.json',sha256='actual-cloud-sha'),worker=dict(runtime_pins=runtime_pins,source='/original/fork-source')))
            s=dict(control=str(control),runtime_repo=str(code),code_manifest=tool.ref(manifest_file),original_preparation_draft=str(original),
                out=str(root/'drafts'),other_lease_controls=['/actual/c3-control'],shared_qualification='/original/shared',
                objects_container='actual-container-id',collector_name='original-current-collector',collector_out='/original/collector')
            drafts.draft(s);outer=load(root/'drafts/batch.request.draft.json');prepared=load(root/'drafts/preparation.request.draft.json')
            self.assertFalse(outer['reviewed']);self.assertFalse(prepared['reviewed']);self.assertIsNone(outer['runtime_control']['sha256'])
            self.assertEqual(prepared['worker']['mode'],'prepare');self.assertEqual(prepared['worker']['source'],'/original/fork-source')
            self.assertEqual(prepared['worker']['runtime_pins']['bench/fs_step.py'],'frozen-new-sha')
            self.assertEqual(prepared['worker']['tool']['sha256'],manifest[drafts.PREFIX+'run-c4-fork-baseline.py'])
            self.assertEqual(outer['trials'][0]['collector_name'],'original-current-collector')
            self.assertEqual(outer['trials'][2]['collector_name'],outer['trials'][1]['new_collector_name'])
            self.assertFalse((control/'state.sqlite').exists())
            with self.assertRaises(FileExistsError):drafts.draft(s)

    def test_code_deployment_refuses_active_lease_before_any_ssh(self):
        path=PATH.with_name('deploy-c4-code.py');spec=importlib.util.spec_from_file_location('c4_deploy_test',path)
        deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);control=root/'control';(control/'suite').mkdir(parents=True);canonical=root/'canonical';canonical.mkdir()
            plan=dict(cells=[dict(id='fork-b',pool='pool-b',resource_ids=['host:actual'])],config=dict(clusters=[dict(id='pool-b',resource_ids=['host:actual'])]))
            write_new(control/'suite/plan.json',plan);write_new(control/'fleet.json',{})
            for name in ('archive','installer','ssh'): (root/name).write_text(name)
            manifest=root/'manifest';write_new(manifest,{'experiments/unified-suite/pilot-aliyun/scripts/install-code.py':digest(root/'installer')})
            close=root/'close';staging=root/'staging';write_new(close,dict(schema='suite-cell-close-v1',outcome='complete'));write_new(staging,dict(passed=True))
            s=dict(control=str(control),canonical_control=str(canonical),cell_id='fork-b',code_directory='code-15',
                archive=tool.ref(root/'archive'),manifest=tool.ref(manifest),installer=tool.ref(root/'installer'),ssh_config=tool.ref(root/'ssh'),
                barriers=[tool.ref(close),tool.ref(staging)],out=str(root/'deploy'),other_lease_controls=[])
            with mock.patch.object(deploy,'check_plan',side_effect=lambda x:x),mock.patch.object(deploy,'active_conflicts',return_value=['active']),mock.patch.object(deploy.subprocess,'run') as remote:
                with self.assertRaisesRegex(ValueError,'leased'):deploy.run(s)
                remote.assert_not_called()


if __name__=='__main__':unittest.main()
