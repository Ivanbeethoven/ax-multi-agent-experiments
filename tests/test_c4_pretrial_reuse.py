"""Local-only checks for reusing the original cold-verified C4 base."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import runpy
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

REPO=Path(__file__).resolve().parents[1]
SCRIPT=REPO/'experiments/unified-suite/pilot-aliyun/scripts/run-c4-batch.py'
RUN=runpy.run_path(str(SCRIPT))
ENV=RUN['main'].__globals__
CELL=RUN['REUSE_CELL']
PROBE_ROLES=RUN['ROLES']


class Reuse(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()
        self.control=self.root/'fork';self.canonical=self.root/'canonical';self.runtime=self.root/'runtime'
        self.old=self.control/'original';self.out=self.control/'reuse';self.outer_dir=self.root/'outer'
        self.repo=self.root/'code-19';self.original_runner=self.repo/RUN['SCRIPT_DIR']/'run-c4-batch.py'
        self.clock=datetime(2026,11,11,11,tzinfo=timezone.utc)
        self.worker=dict(out='/srv/ax-client/preparation/c4-original',asset='/srv/ax-client/a/c4-original',
                         database=32,source_tree=self.remote('/srv/ax-client/inputs/tree.json'),
                         fixture=self.remote('/srv/ax-client/inputs/fixture.json'),mode='prepare')
        self.prep=self.remote(self.worker['out']+'/result.json')
        self.tools={name:self.file(self.repo/RUN['SCRIPT_DIR']/(name+'.py'),{}) for name in
                    ('baseline','capacity','prepare','calibrate','check','admit','run')}
        self.plan=dict(config={'clusters':[{'id':'pool-a','resource_ids':['host:i-a']}]},
                       cells=[dict(id=CELL,pool='pool-a',params={'files':100000},arm_id='native',
                                  resource_ids=['host:i-a'],stages=['measure'])])
        self.cell=self.plan['cells'][0]
        self.plan_pin=self.file(self.control/'suite/plan.json',self.plan)
        self.inventory=self.file(self.control/'provenance/inventory.json',{})
        self.runtime_pin=self.file(self.runtime/'binding.json',{})
        self.fleet=self.file(self.runtime/'fleet.json',{})
        self.base_dir=self.root/'prepared'
        self.prep_pin=self.file(self.outer_dir/'base.request.json',{'out':str(self.base_dir),'worker':self.worker})
        self.s=dict(schema='c4-fork-batch-v1',reviewed=True,control=str(self.control),cell_id=CELL,
            initialize_state=False,out=str(self.out),tools=self.tools,preparation_request=self.prep_pin,
            asset=self.worker['asset'],qualification=str(self.control/'qualification'),runtime_control=self.runtime_pin,
            objects_container='container',node_tool={},fork_tool={},observer_tool_name='observer',
            observer_tool_sha256='a'*64,collector_name='collector',collector_out='/collector',
            formal_attempt='formal',calibration_verification=RUN['c4_verify'].TOUCHED,
            trials=[dict(mode=mode,attempt=f'c4-{i}',out=str(self.control/f'cal-{i}'),
                         collector_name='collector',collector_out='/collector')
                    for i,mode in enumerate(('off','on','on','off','off','on'),1)],
            shared_qualification='/shared',hardware={})
        self.old_body=dict(self.s,out=str(self.old))
        self.original_batch=self.file(self.outer_dir/'batch.request.json',self.old_body)
        self.new_batch=self.file(self.root/'new-batch.json',self.s)
        manifest=self.file(self.root/'manifest.json',{str(self.original_runner.relative_to(self.repo)):
                          self.file(self.original_runner,{})['sha256']})
        self.outer=self.file(self.root/'outer.json',dict(schema='c4-native-flat-continuation-v1',
            reviewed=True,database=self.worker['database'],out=str(self.outer_dir),manifest=manifest))
        self.file(self.outer_dir/'batch/intent.json',dict(argv=['/usr/bin/python3',str(self.original_runner),
            '--request',self.original_batch['path'],'--sha256',self.original_batch['sha256']],automatic_retry=False))
        self.file(self.outer_dir/'batch/exit.json',dict(exit_code=1))
        self.file(self.outer_dir/'failed.json',dict(passed=False,error='Preserve original failure: '+str(self.outer_dir/'batch'),
                                                automatic_retry=False))
        self.file(self.old/'request.json',self.old_body)
        self.file(self.old/'locks-held.json',dict(request=self.original_batch,canonical_control=str(self.canonical),
            new_state=str(self.control/'state.sqlite'),pool='pool-a',resource_ids=['host:i-a']))
        self.validated=dict(control=self.canonical,plan=self.plan,cell=self.cell,wanted=['host:i-a'],
            s=dict(inventory=self.inventory,**RUN['load'](Path(self.prep_pin['path']))),w=self.worker,
            out=self.base_dir,controls=[self.canonical,self.control],request_path=Path(self.prep_pin['path']))
        self.original_prep=RUN['load'](Path(self.prep_pin['path']))
        self.file(self.base_dir/'request.json',self.original_prep)
        self.file(self.base_dir/'exit.json',dict(exit_code=0))
        lock=self.file(self.base_dir/'locks-held.json',dict(request=self.prep_pin,canonical_control=str(self.canonical),
                                                           pool='pool-a',resource_ids=['host:i-a']))
        self.base_result=dict(passed=True,purpose='asset-preparation',pool='pool-a',product='brewfs',cell_id=CELL,
            files=100000,bytes=409600000,work_tag='child-01',formal_admission=False,performance_accepted=False,
            source_tree=self.worker['source_tree'],fixture=self.worker['fixture'],
            asset=self.remote(self.worker['asset']+'/asset.json'),
            cold_base_check=self.remote(self.worker['out']+'/cold-base-check.json'),
            normal_exits=[self.remote(self.worker['out']+f'/normal-stop-{i}.json') for i in (1,2)])
        result=self.file(self.base_dir/'worker-result.json',self.base_result)
        self.file(self.base_dir/'result.json',dict(passed=True,cell_id=CELL,pool='pool-a',remote_evidence=self.worker['out'],
            formal_admission=False,performance_accepted=False,result=result,lock=lock))
        self.before=dict(self.snapshot(-120,False),cell_id=CELL,files=100000)
        self.before['source_pins'].append(self.plan_pin)
        self.before_pin=self.file(self.old/'capacity-before.json',self.before)
        self.after=self.snapshot(-90,True);self.fresh=self.snapshot(-5,True)
        self.after_pin=self.file(self.old/'capacity-after.json',self.after)
        self.fresh_pin=self.file(self.root/'fresh.json',self.fresh)
        self.old_capacity=self.capacity(False)
        self.old_cap_pin=self.file(self.old/'capacity.json',self.old_capacity)
        self.file(self.old/'capacity-before/exit.json',{'exit_code':0})
        self.file(self.old/'capacity-after/exit.json',{'exit_code':0})
        self.file(self.old/'capacity-budget/exit.json',{'exit_code':2})
        self.file(self.old/'capacity-budget/intent.json',dict(argv=['python3',self.tools['capacity']['path'],'budget',
            '--before',self.before_pin['path'],'--after',self.after_pin['path'],'--extra-full-copies','7','--reserve','.2',
            '--out',self.old_cap_pin['path']],automatic_retry=False))
        self.failed=self.file(self.old/'failed.json',dict(passed=False,error='Inspect original failed C4 substep '+
                                                     str(self.old/'capacity-budget'),automatic_retry=False))
        journal=self.file(self.root/'journal.json',{})
        verifier=self.file(self.root/'verifier.py',{})
        self.forensic=self.file(self.root/'forensic.json',dict(schema='c4-aof-recovery-proof-v1',passed=True,
            cell_id=CELL,pool='pool-a',before=self.before_pin,after=self.after_pin,original_capacity=self.old_cap_pin,journal_evidence=journal,
            verifier=verifier,retirement_bytes=15,retirement_inodes=3,retired_aof_logical_bytes=7))
        self.producer=self.file(self.root/'producer.py',{})
        self.cap=self.capacity(True)
        self.cap_pin=self.file(self.root/'corrected.json',self.cap)
        self.receipt_dir=self.root/'quiescence'
        self.unit='ax-pilot-pa-original.service'
        self.manager=dict(LoadState='loaded',InvocationID='b'*32,ActiveState='failed',SubState='failed',
                          MainPID='0',ControlPID='0',Result='exit-code',ExecMainStatus='1')
        self.boundary,self.quiescence=self.evidence()
        q_pin=self.write_quiescence()
        self.reuse=dict(schema='c4-pretrial-reuse-v1',reviewed=True,new_batch=self.new_batch,
            original_batch=self.original_batch,original_outer=self.outer,quiescence=q_pin,
            capacity=self.cap_pin,capacity_producer=self.producer,recovery_proof=self.forensic,runner=RUN['ref'](SCRIPT))

    @staticmethod
    def file(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value))
        return RUN['ref'](path)

    @staticmethod
    def remote(path):
        return dict(path=path,sha256='a'*64)

    def stamp(self,seconds):
        return (self.clock+timedelta(seconds=seconds)).isoformat()

    def snapshot(self,seconds,prep):
        nodes={}
        for i,role in enumerate(PROBE_ROLES):
            nodes[role]=dict(boot_id=role,sampled_at=self.stamp(seconds),disks=[
                dict(path=f'/dev/{role}-{j}',device=10*i+j,serial=f'{role}-{j}',device_bytes=1000,
                     total_bytes=1000000,total_inodes=4000,used_bytes=90 if prep and role=='metadata' else 100,
                     used_inodes=100) for j in range(4 if role=='objects' else 1)])
        nodes['objects'].update(container_id='container',image='image',pid=17)
        nodes['metadata']['redis']=dict(used_memory=100,maxmemory=1000,aof_current_size=10 if prep else 12,process_id=42)
        if prep:
            nodes['client']['preparation']=dict(self.prep,value=self.base_result,normal_exits=[
                dict(pin,value=dict(passed=True,mount_absent=True,exit_code=0)) for pin in self.base_result['normal_exits']])
        return dict(at=self.stamp(seconds),inventory=self.fleet,pool='pool-a',nodes=nodes,
                    source_pins=[self.fleet,self.tools['capacity']]+([self.before_pin] if prep else []))

    def capacity(self,corrected):
        rows=[]
        rewrite=4096*17
        before_rows=RUN['capacity_rows'](self.before)
        for (role,path,quantity),(used,total) in RUN['capacity_rows'](self.after).items():
            affected=role=='metadata' and quantity in ('bytes','redis-aof-bytes')
            copies=0 if role=='client' else 7
            increment={'bytes':105,'inodes':103,'redis-aof-bytes':17}.get(quantity,0) if role=='metadata' and corrected else 0
            row=dict(role=role,quantity=quantity,extra_full_copies=copies,measured_base_increment=increment,
                     passed=corrected or not affected)
            if path:row['path']=path
            if corrected:used=max(used,before_rows[role,path,quantity][0])
            if total:
                row.update(projected_used=used+copies*increment,total=total)
                if corrected and role=='metadata' and quantity=='bytes':
                    row.update(rewrite_headroom_bytes=rewrite,projected_used=row['projected_used']+rewrite)
                if corrected and role=='metadata' and quantity=='inodes':
                    row.update(rewrite_headroom_inodes=16,projected_used=row['projected_used']+16)
            else:
                row.update(projected_aof_bytes=used+copies*increment,included_in_metadata_disk_forecast=True)
            rows.append(row)
        doc=dict(passed=corrected,before=self.before_pin,after=self.after_pin,extra_full_copies=7,
                 reserve_fraction=.2,rows=rows)
        if corrected:
            doc.update(schema='c4-aof-reconciled-capacity-v1',at=self.stamp(-4),cell_id=CELL,pool='pool-a',preparation=self.prep,
                original_capacity=self.old_cap_pin,producer=self.producer,recovery_proof=self.forensic,fresh=self.fresh_pin,
                rewrite_headroom_bytes=rewrite,
                source_pins=[self.before_pin,self.after_pin,self.old_cap_pin,self.producer,self.forensic,self.fresh_pin])
        return doc

    def evidence(self):
        ids=dict(UNIT=self.unit,INVOCATION_ID=self.manager['InvocationID'],_BOOT_ID='boot')
        start=dict(ids,__CURSOR='start',__REALTIME_TIMESTAMP=str(int(self.clock.timestamp()*1e6)-300_000_000),
                   MESSAGE=self.outer['path']+' '+self.outer['sha256'])
        end=dict(ids,__CURSOR='end',__REALTIME_TIMESTAMP=str(int(self.clock.timestamp()*1e6)-60_000_000),
                 MESSAGE=self.unit+": Failed with result 'exit-code'.")
        source=dict(role='client',script_sha256='c'*64,result=dict(passed=True,
            sources=[self.prep,self.base_result['cold_base_check'],*self.base_result['normal_exits'],
                *(self.remote(self.worker['out']+f'/start-{i}/stdout') for i in (1,2))],
            debt=self.remote(self.worker['out']+'/import-debt/result.json'),
            samples=[self.remote(self.worker['out']+f'/import-debt/sample-{i}.json') for i in (1,2)],
            values=[dict(passed=True,cell_id=CELL,pending_upload_bytes=0,pending_upload_operations=0,
                native_gauges={'bytes':0},monotonic_ns=i*10**9) for i in (1,2)]))
        live={role:dict(role=role,script_sha256='c'*64,result=dict(safe_for_release=True,live=[],
              unknown_startup_directories=[],slot='',mounts=[],original_processes=[])) for role in PROBE_ROLES}
        b=dict(source_batch=self.original_batch,source_outer=self.outer,runtime=self.runtime_pin,
               controls=list(map(str,self.validated['controls'])),live=live,closed_base=source,
               unit=dict(show=self.manager,start=start,end=end))
        q=dict(schema='c123-pool-a-retention-quiescence-v1',passed=True,reviewed=True,pool='pool-a',observed_at=self.stamp(-10),
               unit=self.unit,manager=self.manager,resource_ids=['host:i-a'],no_active_pool_lease=True,agent_slot_empty=True,
               all_workload_processes_exited=True,all_mounts_absent=True,pending_upload_bytes=0,pending_upload_operations=0)
        return b,q

    def write_quiescence(self):
        pin=self.file(self.receipt_dir/'boundary.json',self.boundary)
        return self.file(self.receipt_dir/'result.json',dict(self.quiescence,source_pins=[self.failed,self.after_pin,pin]))

    def gates(self):
        live='\n'.join(f'{key}={value}' for key,value in self.manager.items())
        with patch.object(RUN['subprocess'],'check_output',return_value=live) as call:
            result=RUN['pretrial_reuse'](self.s,self.reuse,self.old,self.validated,self.control,
                                        self.runtime,self.validated['controls'],self.clock)
        self.assertEqual(call.call_args.args[0][:3],['systemctl','show',self.unit])
        return result

    def test_original_valid_pretrial_evidence_allows_only_the_preserved_base(self):
        self.validated['s']=self.original_prep
        self.assertEqual(self.gates(),(self.prep,Path(self.cap_pin['path'])))
        actual=self.file(self.root/'reuse.json',self.reuse)
        self.assertEqual(RUN['load_reuse'](actual,self.new_batch,self.s)[1],self.old)
        with self.assertRaisesRegex(ValueError,'only the output'):
            RUN['load_reuse'](actual,self.new_batch,dict(self.s,formal_attempt='other'))
        with self.assertRaisesRegex(ValueError,'runner-bound'):
            RUN['load_reuse'](self.file(self.root/'reuse.json',dict(self.reuse,reviewed=False)),self.new_batch,self.s)

    def test_original_cannot_be_past_capacity_or_have_other_exit(self):
        self.validated['s']=self.original_prep
        path=self.old/'trial-5';path.mkdir()
        with self.assertRaisesRegex(ValueError,'Already used'):
            self.gates()
        path.rmdir()
        self.file(self.old/'capacity-budget/exit.json',{'exit_code':1})
        with self.assertRaisesRegex(ValueError,'capacity budget exit 2'):
            self.gates()

    def test_fresh_quiescence_for_all_roles_and_exact_manager(self):
        self.validated['s']=self.original_prep
        self.quiescence['observed_at']=self.stamp(-901)
        self.reuse['quiescence']=self.write_quiescence()
        with self.assertRaisesRegex(ValueError,'fresh pretrial'):
            self.gates()
        self.quiescence['observed_at']=self.stamp(-10)
        self.boundary['live']['objects']['result']['live']=[{'pid':88}]
        self.reuse['quiescence']=self.write_quiescence()
        with self.assertRaisesRegex(ValueError,'not proven absent'):
            self.gates()
        self.boundary['live']['objects']['result']['live']=[]
        self.reuse['quiescence']=self.write_quiescence()
        with patch.object(RUN['subprocess'],'check_output',return_value='InvocationID=other'):
            with self.assertRaisesRegex(ValueError,'invocation changed'):
                RUN['pretrial_reuse'](self.s,self.reuse,self.old,self.validated,self.control,self.runtime,
                                     self.validated['controls'],self.clock)

    def test_capacity_must_be_bound_recent_complete_and_conservative(self):
        self.validated['s']=self.original_prep
        for name,change,message in (
            ('proof',lambda c:c.update(recovery_proof=self.producer),'exact C4 base'),
            ('copy count',lambda c:c.update(extra_full_copies=6),'7-copy'),
            ('reserve',lambda c:c.update(reserve_fraction=.19),'20-percent'),
            ('missing row',lambda c:c['rows'].pop(),'All native capacity rows'),
            ('failed row',lambda c:c['rows'][0].update(passed=False),'row status'),
            ('lower projection',lambda c:c['rows'][0].update(projected_used=99),'20-percent reserve'),
            ('no headroom',lambda c:c['rows'][0].update(projected_used=800001),'20-percent reserve'),
            ('lost rewrite space',lambda c:next(row for row in c['rows'] if 'rewrite_headroom_bytes' in row)
             .update(rewrite_headroom_bytes=44),'rewrite headroom'),
        ):
            with self.subTest(name=name):
                changed=deepcopy(self.cap);change(changed)
                self.reuse['capacity']=self.file(self.root/'corrected.json',changed)
                with self.assertRaisesRegex(ValueError,message):
                    self.gates()

    def test_frozen_validator_gets_only_unused_shadow_output(self):
        def validate(args):
            value=RUN['load'](Path(args.request))
            path=Path(value.pop('out'))
            self.assertFalse(path.exists())
            self.assertEqual(value,{k:v for k,v in self.original_prep.items() if k!='out'})
            return {'out':path}
        baseline=SimpleNamespace(validate_request=Mock(side_effect=validate))
        actual=RUN['baseline_context'](self.s,baseline,self.reuse)
        self.assertEqual(actual['out'],self.base_dir)
        self.assertEqual(actual['request_path'],Path(self.prep_pin['path']))
        self.assertEqual(RUN['load'](Path(self.prep_pin['path'])),self.original_prep)

    def test_main_gate_failure_cannot_import_or_dispatch_and_pass_uses_original_pipeline(self):
        for db in (self.control,self.canonical):
            db.mkdir(exist_ok=True);(db/'state.sqlite').touch()
        manager=Mock()
        baseline=SimpleNamespace(run_remote_locked=manager)
        calls=[]
        # Leave subsequent stage semantics untouched; a tiny fake child provides
        # the receipts read by the common pipeline.
        stages={str(self.tools[k]['path']):k for k in self.tools}
        policies=[]

        def run(argv,**kwargs):
            stage=stages[argv[1]];calls.append(stage)
            if stage=='calibrate':
                body=RUN['load'](Path(argv[3]))
                policies.append(body['verification_policy'])
                if len(policies)>1:
                    self.assertEqual(body['full_reference']['path'],'/srv/ax-client/q/c4-1/verify/result.json')
                proof=self.file(Path(body['out'])/'verify.json',dict(child_name=body['attempt'],
                    binding=dict(asset=self.base_result['asset'],source_tree=self.base_result['source_tree']),
                    expected_child={'sha256':'child'}))
                self.file(Path(body['out'])/'finished.json',dict(collector_name='collector',collector_out='/collector',
                          work={},client_verify=proof,parent_verify={},native_exit={},verify_exit={},closure={}))
            if stage=='check':
                self.file(Path(argv[argv.index('--out')+1])/'result.json',{})
            if stage=='admit':
                body=RUN['load'](Path(argv[3]))
                self.assertEqual(Path(body['capacity']['path']).read_bytes(),Path(self.cap_pin['path']).read_bytes())
            if stage=='run':
                self.file(self.control/'cells'/CELL/'sample.json',{})
                self.file(self.control/'cells'/(CELL+'.close.json'),{})
            return SimpleNamespace(returncode=0)

        connection=sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.row_factory=sqlite3.Row
        connection.executescript(f"CREATE TABLE cells(id,status,result); INSERT INTO cells VALUES ('{CELL}','pending',NULL);"
                                 "CREATE TABLE stages(cell_id,name,status); CREATE TABLE qualifications(cell_id);")
        argv=[str(SCRIPT),'--request',self.new_batch['path'],'--sha256',self.new_batch['sha256'],
              '--reuse-request',self.outer['path'],'--reuse-sha256',self.outer['sha256']]
        gate=Mock(side_effect=ValueError('gate refused'))
        validated=dict(self.validated,s={'inventory':self.inventory})
        with patch.object(sys,'argv',argv), patch.dict(ENV,load_reuse=lambda *a:(self.reuse,self.old),
                module=lambda *a:baseline,baseline_context=lambda *a:validated,check_plan=lambda p:p,
                ensure_conflicts=lambda *a:None,resource_guard=lambda *a:nullcontext(),pretrial_reuse=gate,
                load=lambda p:self.s if p==Path(self.new_batch['path']) else RUN['load'](p)), \
                patch.object(RUN['pilot_context'],'select',return_value=self.runtime), \
                patch.object(RUN['suite_state'],'transaction',return_value=nullcontext(connection)), \
                patch.object(RUN['c4_verify'],'reference_identity') as reference, \
                patch.object(RUN['subprocess'],'run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'gate refused'):
                RUN['main']()
            self.assertFalse(self.out.exists())
            self.assertEqual(calls,[])
            gate.side_effect=None;gate.return_value=(self.prep,Path(self.cap_pin['path']))
            connection.execute("INSERT INTO stages VALUES (?, 'measure', 'pending')",(CELL,))
            with self.assertRaisesRegex(ValueError,'no stage or qualification records'):
                RUN['main']()
            self.assertEqual(calls,[])
            connection.execute('DELETE FROM stages')
            RUN['main']()
        reference.assert_called_once()
        self.assertEqual(policies,[RUN['c4_verify'].REFERENCE,*[RUN['c4_verify'].TOUCHED]*5])
        self.assertEqual(calls,['prepare',*['calibrate']*6,'check','admit','run'])
        manager.assert_not_called()
        self.assertEqual((self.out/'capacity.json').read_bytes(),Path(self.cap_pin['path']).read_bytes())
        self.assertEqual((self.old/'capacity.json').read_bytes(),Path(self.old_cap_pin['path']).read_bytes())
        self.assertFalse(RUN['load'](self.out/'pretrial-reuse.json')['base_reimported'])


if __name__=='__main__':
    unittest.main()
