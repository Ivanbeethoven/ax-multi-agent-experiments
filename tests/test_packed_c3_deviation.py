"""Offline fixtures for the reviewed C3 packed observer-overhead deviation.

No ssh, subprocess, mount, FUSE or dataset: remote helpers are replaced by local fakes
and the deviation record is the repository's real reviewed plan.json.
"""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import types
import unittest
from unittest.mock import patch

from bench.runtime.common import digest,load,write_new
from bench import suite_state
from bench.suite_plan import build as build_plan
from bench.suite_report import sample as suite_sample
from bench.suite_state import predicates

import tests.test_packed_c3_controller as original

REPO=Path(__file__).resolve().parents[1]
CONTROLLER=REPO/'experiments/unified-suite/pilot-aliyun/scripts/packed-c3-controller.py'
RECORD_PATH=REPO/'experiments/unified-suite/pilot-aliyun/evidence/preparation/c3-packed-overhead-deviation-01/plan.json'
CODE=runpy.run_path(str(CONTROLLER))
METHODS=CODE['PackedMethods']
GATE=CODE['overhead_gate']
SAMPLE_CHECK=CODE['overhead_sample_check']
RECORD=json.loads(RECORD_PATH.read_text(encoding='utf-8'))
CELL_ID=RECORD['applies_to']['cell_id']
ARM=RECORD['applies_to']['arm_id']
GATES=['exact-build-input-inventory','native-route-and-metadata','effective-cache-and-durability','host-capacity-and-maintenance',
    'complete-telemetry-and-overhead','full-base-validation-once','client-cache-evidence']


def calibration(**fields):
    """This run's failed calibration result, carrying the record's own two medians."""
    measured=RECORD['measured']
    value=dict(schema='experiment-observer-overhead-v1',passed=False,
        relative_changes={'makespan':measured['makespan'],'task_p99':measured['task_p99']},
        paired_relative_changes=copy.deepcopy(measured['paired_relative_changes']),max_relative_change=.05,
        metric_contract_sha256='m'*64,workload_contract_sha256='w'*64)
    value.update(fields);return value


def template_pairs():
    """The per-pair percentages exactly as the record's own disclosure template prints them."""
    template=RECORD['disclosure']['result_template']
    return template.split('\u9010\u5bf9 ')[1].split('\uff0c')[0]


class Records(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.n=0;self.count=0
    def tearDown(self):self.tmp.cleanup()
    def write(self,name,value):
        path=self.root/name
        if not path.parent.exists():path.parent.mkdir(parents=True)
        write_new(path,value);return path
    def put(self,name,value):
        """Absolute-path plus sha256 pin of a JSON fixture, as the controller's ref() builds it."""
        return CODE['ref'](self.write(name,value))
    def record(self,mutate=None,cell_id=CELL_ID,arm=None):
        self.n+=1
        value=copy.deepcopy(RECORD);value['applies_to']['cell_id']=cell_id
        if arm is not None:value['applies_to']['arm_id']=arm
        if mutate:mutate(value)
        return self.put('deviation-'+str(self.n)+'.json',value)


class OverheadGate(Records):
    def refused(self,pin,pattern,checked=None):
        with self.assertRaisesRegex(ValueError,pattern):GATE(calibration() if checked is None else checked,pin,CELL_ID,ARM)

    def test_1_failed_result_without_record_keeps_the_original_refusal(self):
        self.refused(None,'Three exact original calibration pairs within five percent required')
        # A passed:true result whose numbers exceed 0.05 is still refused exactly as before.
        self.refused(None,'Three exact original calibration pairs within five percent required',calibration(passed=True))
        # A result without per-metric changes never was a usable calibration either.
        self.refused(None,'Three exact original calibration pairs within five percent required',calibration(relative_changes={}))

    def test_2_reviewed_record_covering_this_result_releases_with_disclosure(self):
        pin=self.record()
        release=GATE(calibration(),pin,CELL_ID,ARM)
        self.assertEqual(release['reference'],pin)
        self.assertEqual(release['original_threshold'],.05)
        self.assertEqual(release['measured']['relative_changes'],calibration()['relative_changes'])
        self.assertEqual(release['reference']['sha256'],digest(Path(pin['path'])))
        # The same numbers may be recorded under measured.relative_changes instead of measured.<metric>.
        explicit=self.record(lambda value:value['measured'].__setitem__('relative_changes',dict(calibration()['relative_changes'])))
        self.assertEqual(GATE(calibration(),explicit,CELL_ID,ARM)['reference'],explicit)

    def test_3_unreviewed_record_is_refused(self):
        self.refused(self.record(lambda value:value.__setitem__('reviewed',False)),'requires reviewed:true')
        self.refused(self.record(lambda value:value.pop('reviewed')),'requires reviewed:true')
        self.refused(self.record(lambda value:value.__setitem__('reviewed','true')),'requires reviewed:true')

    def test_4_record_for_another_cell_or_arm_is_refused(self):
        # A record covering neither this cell id nor this arm name is not applicable.
        self.refused(self.record(lambda value:value['applies_to'].update(cell_id='16f4ac389d2a2da6-r1-native',arm_id='native')),
            'does not cover cell')
        self.refused(self.record(lambda value:value['applies_to'].update(cell_id='some-other-cell',arm_id='native')),
            'does not cover cell')
        # Wildcards never expand the applied scope.
        self.refused(self.record(lambda value:value['applies_to'].__setitem__('cell_id','16f4ac389d2a2da6-*')),
            'not a wildcard')
        self.refused(self.record(lambda value:value['applies_to'].__setitem__('arm_id','*')),'not a wildcard')

    def test_5_record_must_match_this_result_field_by_field(self):
        changed=lambda value:value['measured'].__setitem__('makespan',
            float(f"{value['measured']['makespan']+1e-4:.12f}"))
        self.refused(self.record(changed),'measured.relative_changes.makespan differs from this calibration result')
        # The same record cannot be applied to another run whose median differs.
        moved=calibration(relative_changes={'makespan':round(RECORD['measured']['makespan'],4),
            'task_p99':RECORD['measured']['task_p99']})
        self.refused(self.record(),'measured.relative_changes.makespan differs from this calibration result',moved)
        self.refused(self.record(lambda value:value['measured'].__setitem__('relative_changes',
            {'makespan':RECORD['measured']['makespan'],'task_p99':RECORD['measured']['task_p99'],'extra':.01})),
            'measured fields differ')
        # Nor may the record rewrite the original failure into a pass or raise the old threshold.
        self.refused(self.record(lambda value:value['measured'].__setitem__('passed',True)),
            'must still record the original gate as not passed')
        self.refused(self.record(lambda value:value['deviation'].__setitem__('original_threshold',.06)),
            'deviation.original_threshold must still be 0.05')
        self.refused(self.record(lambda value:value['decision'].__setitem__('accepted',False)),
            'decision.accepted must be true')

    def test_6_pin_must_match_the_record_bytes(self):
        pin=self.record();tampered=dict(pin);tampered['sha256']='0'*64
        self.refused(tampered,'sha256 does not match the record bytes')
        self.refused(dict(pin,path='deviation.json'),'path must be absolute')
        self.refused(dict(pin,path=str(self.root/'missing.json')),'missing or unreadable')
        Path(pin['path']).write_text('{}')
        self.refused(pin,'sha256 does not match the record bytes')

    def test_7_passing_result_is_unchanged_and_needs_no_record(self):
        passed=calibration(passed=True,relative_changes={'makespan':.02850520107112564,'task_p99':.028588390887341997})
        self.assertIsNone(GATE(passed,None,CELL_ID,ARM))
        # Once the original gate passed, even an unresolvable pin is never opened.
        self.assertIsNone(GATE(passed,{'path':str(self.root/'absent.json'),'sha256':'f'*64},CELL_ID,ARM))

    def test_8_disclosure_uses_the_recorded_template_wording(self):
        release=GATE(calibration(),self.record(),CELL_ID,ARM);text=release['text']
        self.assertIn(ARM,text)
        self.assertIn(RECORD['disclosure']['short_label'],text)
        self.assertIn(template_pairs(),text)
        self.assertIn('-6.21%',text)
        self.assertIn('cold-read',text)
        self.assertIn('\u5176\u4f59\u95e8\u69db\u4e0d\u53d8',text)
        self.assertIn(release['reference']['path'],text)
        self.assertIn(release['reference']['sha256'],text)


class SamplePredicate(Records):
    def controller(self,checked,pin=None):
        c=types.SimpleNamespace(id=CELL_ID,arm=ARM,out=self.root/'out',s={})
        c.out.mkdir(exist_ok=True)
        c.s['overhead']=self.put('overhead.json',checked)
        if pin is not None:c.s['observer_overhead_deviation']=pin
        return c

    def test_passed_result_keeps_the_original_predicate(self):
        c=self.controller(calibration(passed=True,relative_changes={'makespan':.0285,'task_p99':.0286}))
        check,disclosure=SAMPLE_CHECK(c,load(c.s['overhead']['path']))
        self.assertEqual(check,dict(evidence=c.s['overhead'],equals={'passed':True}))
        self.assertIsNone(disclosure);self.assertFalse((c.out/'observer-overhead-disclosure.json').exists())

    def test_failed_result_without_record_keeps_the_original_predicate(self):
        c=self.controller(calibration())
        check,disclosure=SAMPLE_CHECK(c,load(c.s['overhead']['path']))
        self.assertEqual(check,dict(evidence=c.s['overhead'],equals={'passed':True}))
        self.assertIsNone(disclosure);self.assertFalse((c.out/'observer-overhead-disclosure.json').exists())

    def test_failed_result_with_record_gets_a_reviewed_predicate(self):
        c=self.controller(calibration(),self.record())
        check,disclosure=SAMPLE_CHECK(c,load(c.s['overhead']['path']))
        self.assertIsNotNone(disclosure)
        self.assertTrue(check['equals']['valid']);self.assertFalse(check['equals']['passed'])
        self.assertEqual(check['equals']['reference.sha256'],c.s['observer_overhead_deviation']['sha256'])
        payload=load(check['evidence']['path']);predicates(payload,check['equals'])
        self.assertLessEqual(payload['max_relative_change'],.05)
        self.assertEqual(payload['measured_relative_changes'],calibration()['relative_changes'])
        self.assertIn(template_pairs(),payload['disclosure'])


class Admission(Records):
    def build(self,attach=True,mutate=None,passed=False):
        self.count+=1
        root=self.root/str(self.count);root.mkdir()
        control=root/'control';(control/'suite').mkdir(parents=True);(control/'provenance').mkdir()
        write_new(control/'suite/plan.json',dict(cells=[]))
        for name in ('build-manifest.json','input-manifest.json'):write_new(control/'provenance'/name,{'file':name})
        runtime=root/'runtime';runtime.mkdir();write_new(runtime/'fleet.json',{'nodes':[]})
        write_new(runtime/'allocation.json',dict(passed=True,inventory_sha256=digest(runtime/'fleet.json')))
        qualification=root/'qualification';q=qualification/'packed';q.mkdir(parents=True)
        (qualification/'assets/packed').mkdir(parents=True)
        normal=root/'normal-exit.json';write_new(normal,dict(passed=True,exit_code=0,mount_absent=True))
        source=root/'source.json';write_new(source,dict(passed=True))
        base_check=root/'full-base-check.json';write_new(base_check,dict(passed=True))
        cloud_base=root/'cloud-base.json';write_new(cloud_base,dict(passed=True))
        filesystem=dict(passed=True,original_cloud_base=CODE['ref'](cloud_base),
            proofs=dict(source=CODE['ref'](source),full_base_check=CODE['ref'](base_check),base_normal_exit=CODE['ref'](normal)))
        write_new(q/'filesystem.json',filesystem);write_new(q/'input.json',dict(passed=True))
        write_new(q/'metrics.json',{'schema':'metric-contract'});write_new(q/'workload.json',{'schema':'workload-contract'})
        write_new(qualification/'hardware.json',dict(passed=True,inventory_sha256=digest(runtime/'fleet.json')))
        producer=root/'producer';producer.mkdir();write_new(producer/'exit.json',dict(wall_seconds=12.5))
        asset=dict(pool='pool-a',plan=dict(sha256=digest(control/'suite/plan.json')),
            config=dict(packed_manifest_key='packed/manifest.json',workspace='ws-1'),producer=dict(path=str(producer/'command.json')))
        write_new(qualification/'assets/packed/asset.json',asset)
        plan=dict(plan_sha256='P'*64,config=dict(build_manifest_sha256=digest(control/'provenance/build-manifest.json'),
            input_manifest_sha256=digest(control/'provenance/input-manifest.json')),cells=[])
        write_new(root/'latest.json',dict(passed=True,build_manifest_sha256=plan['config']['build_manifest_sha256']))
        overhead=calibration(metric_contract_sha256=digest(q/'metrics.json'),
            workload_contract_sha256=digest(q/'workload.json'))
        if passed:overhead=dict(overhead,passed=True,relative_changes={'makespan':.0285,'task_p99':.0286})
        overhead_path=root/'overhead.json';write_new(overhead_path,overhead)
        capacity=root/'capacity.json';write_new(capacity,dict(passed=True))

        class Controller:
            def check_inputs(self):self.checked=True
            def get(self,role,remote,relative):self.gets.append((role,remote,relative));return Path(remote)
        c=Controller();c.gets=[]
        c.control=control;c.qualification=q;c.runtime=runtime;c.plan=plan;c.purpose='experiment'
        c.id=CELL_ID;c.pool='pool-a';c.arm=ARM;c.out=root/'out'
        c.cell=dict(id=CELL_ID,arm_id=ARM,pool='pool-a',asset_id='asset-1',client_settings={},required_gates=GATES)
        c.s=dict(control=str(control),cell_id=CELL_ID,owner='offline-fixture',qualification=str(qualification),overhead=str(overhead_path))

        class Database:
            def execute(self,*argv):return types.SimpleNamespace(fetchone=lambda:(1,))
        class Transaction:
            def __enter__(self):return Database()
            def __exit__(self,*argv):return False
        class State:
            def __init__(self):self.qualified=None;self.assets=[];self.claimed=0
            def qualify(self,path,pin):self.qualified=pin
            def register_asset(self,path,pin):self.assets.append(pin)
            def transaction(self,path):return Transaction()
            def claim(self,path,pool,owner):self.claimed+=1;return dict(already_claimed=False,cell=c.cell)
        state=State();pin=None
        if attach:
            pin=self.record(mutate);c.s['observer_overhead_deviation']=pin
        return types.SimpleNamespace(c=c,state=state,pin=pin,latest=root/'latest.json',capacity=capacity,
            overhead=overhead_path,root=root)

    def admit(self,env,cross=None):
        namespace=CODE['admit_locked'].__globals__
        patches=dict(cell=lambda path:(env.c,{}),state=env.state,capacity_gate=lambda path,c:{'passed':True})
        stream=io.StringIO()
        with patch.dict(namespace,patches),redirect_stdout(stream):
            CODE['admit_locked'](env.root/'admit-request.json',env.overhead,env.latest,cross,env.capacity)
        return json.loads(stream.getvalue())

    def refused(self,env,pattern):
        with self.assertRaisesRegex(ValueError,pattern):self.admit(env)
        self.assertFalse(env.c.out.exists())

    def test_1_admission_without_record_still_refuses(self):
        self.refused(self.build(attach=False),'Three exact original calibration pairs within five percent required')

    def test_2_admission_releases_and_pins_the_record_in_context(self):
        env=self.build();result=self.admit(env)
        self.assertTrue(result['admitted']);self.assertEqual(result['cell_id'],CELL_ID)
        self.assertEqual(result['observer_overhead_deviation'],env.pin)
        context=load(env.c.out/'context.json')
        self.assertEqual(context['observer_overhead_deviation'],env.pin)
        self.assertEqual(context['overhead'],CODE['ref'](env.overhead))
        self.assertIn(RECORD['disclosure']['short_label'],context['observer_overhead_disclosure'])
        self.assertIn(template_pairs(),context['observer_overhead_disclosure'])
        self.assertIn(ARM,result['disclosure'])
        self.assertEqual(env.state.qualified['sha256'],digest(env.c.out/'qualification.json'))
        self.assertEqual(env.state.claimed,1)

    def test_3_admission_refuses_an_unreviewed_record(self):
        self.refused(self.build(mutate=lambda value:value.__setitem__('reviewed',False)),'requires reviewed:true')

    def test_4_admission_refuses_a_record_for_another_cell(self):
        self.refused(self.build(mutate=lambda value:value['applies_to'].update(cell_id='16f4ac389d2a2da6-r1-native',arm_id='native')),
            'does not cover cell')
        self.refused(self.build(mutate=lambda value:value['applies_to'].__setitem__('cell_id','16f4ac389d2a2da6-*-packed')),
            'not a wildcard')

    def test_5_admission_refuses_a_mismatched_measurement(self):
        self.refused(self.build(mutate=lambda value:value['measured'].__setitem__('makespan',
            float(f"{value['measured']['makespan']+1e-4:.12f}"))),'measured.relative_changes.makespan differs')

    def test_6_admission_refuses_a_wrong_pin_sha(self):
        env=self.build();env.pin['sha256']='0'*64
        self.refused(env,'sha256 does not match the record bytes')

    def test_7_passing_calibration_needs_no_record_and_is_unchanged(self):
        env=self.build(attach=False,passed=True);result=self.admit(env)
        self.assertTrue(result['admitted'])
        self.assertNotIn('observer_overhead_deviation',result);self.assertNotIn('disclosure',result)
        context=load(env.c.out/'context.json')
        self.assertNotIn('observer_overhead_deviation',context)
        self.assertNotIn('observer_overhead_disclosure',context)
        self.assertEqual(context['overhead'],CODE['ref'](env.overhead))

    def test_reviewed_failure_qualifies_and_claims_in_real_sqlite(self):
        env=self.build();c=env.c
        frozen=load(RECORD_PATH.parent.parent/'parallel-control-01/plan.json')
        plan=build_plan(dict(frozen['config'],**c.plan['config']))
        plan_path=c.control/'suite/plan.json';plan_path.write_text(json.dumps(plan))
        c.plan=plan;c.cell=next(cell for cell in plan['cells'] if cell['id']==CELL_ID)
        asset_path=c.qualification.parent/'assets/packed/asset.json';asset=load(asset_path)
        asset['plan']['sha256']=digest(plan_path)
        asset['config'].update(c.cell['client_settings']);asset_path.write_text(json.dumps(asset))
        db=c.control/'state.sqlite';suite_state.initialize(db,plan);env.state=suite_state
        cross=env.root/'cross-pool.json'
        write_new(cross,dict(schema='pilot-cross-pool-equivalence-v1',passed=True,plan_sha256=plan['plan_sha256'],
            inventory_sha256=digest(c.runtime/'fleet.json'),pools=sorted({x['pool'] for x in plan['cells']}),
            checks={'fixture':True},source_pins=[CODE['ref'](env.latest)]))

        original=CODE['ref'](env.overhead)
        self.assertTrue(self.admit(env,cross=cross)['admitted'])
        with suite_state.transaction(db) as state:
            registered=json.loads(state.execute('SELECT receipt FROM qualifications WHERE cell_id=?',(CELL_ID,)).fetchone()[0])
            self.assertEqual(state.execute('SELECT cell_id FROM cell_leases WHERE pool=?',(c.pool,)).fetchone()[0],CELL_ID)
        check=next(x for x in suite_state.evidence(registered)['checks'] if x['gate']=='complete-telemetry-and-overhead')
        result=suite_state.evidence(check['evidence']);predicates(result,check['equals'])
        self.assertTrue(result['valid'])
        self.assertFalse(result['passed'])
        self.assertEqual(result['original_overhead'],original)
        self.assertEqual(result['reference'],env.pin)
        self.assertFalse(suite_state.evidence(original)['passed'])


class SampleAcceptance(Records):
    def fixture(self):
        c,roots=original.PackedController.fixture(self,'experiment')
        c.arm=ARM;return c,roots

    def archive(self,c,roots):
        read_text=Path.read_text
        def fixture_read(path,*argv,**keywords):
            return '' if str(path)=='/sys/fs/cgroup/ax-slot-1/cgroup.procs' else read_text(path,*argv,**keywords)
        # The fixture instance comes from the original test module, so patch that namespace.
        with patch.dict(original.METHODS.archive.__globals__,ROOTS=roots),patch.object(Path,'read_text',fixture_read):c.archive()

    @unittest.skipUnless(os.name=='posix','Original archive reads Linux process/mount identity')
    def test_8_failed_sample_without_record_keeps_the_original_refusal(self):
        c,roots=self.fixture();c.s['overhead']=self.put('failed-overhead.json',calibration())
        self.archive(c,roots)
        sample=load(c.out/'sample.json')
        self.assertEqual(sample['checks']['observer_overhead'],dict(evidence=c.s['overhead'],equals={'passed':True}))
        self.assertFalse((c.out/'observer-overhead-disclosure.json').exists())
        self.assertEqual(sample['limitations'],['One agent and one pilot repetition. Fresh client entity; shared backend/page caches remain observed.'])
        checked=suite_sample(CODE['ref'](c.out/'sample.json'),c.plan,c.cell)
        self.assertFalse(checked['accepted'])
        self.assertTrue(any(reason.startswith('observer_overhead') for reason in checked['reasons']),checked['reasons'])

    @unittest.skipUnless(os.name=='posix','Original archive reads Linux process/mount identity')
    def test_8_deviated_sample_is_accepted_with_disclosure_text(self):
        c,roots=self.fixture()
        c.s['overhead']=self.put('failed-overhead.json',calibration())
        c.s['observer_overhead_deviation']=self.record(cell_id=c.cell['id'])
        self.archive(c,roots)
        sample=load(c.out/'sample.json')
        self.assertTrue(sample['checks']['observer_overhead']['equals']['valid'])
        self.assertFalse(sample['checks']['observer_overhead']['equals']['passed'])
        self.assertIn(template_pairs(),sample['limitations'][-1]);self.assertIn(ARM,sample['limitations'][-1])
        self.assertIn('-6.21%',sample['limitations'][-1])
        checked=suite_sample(CODE['ref'](c.out/'sample.json'),c.plan,c.cell)
        self.assertTrue(checked['accepted'],checked['reasons'])


if __name__=='__main__':unittest.main()
