import importlib.util
import copy
import io
import runpy
from contextlib import redirect_stdout
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'experiments/unified-suite/pilot-aliyun/scripts/run-c1-storage-step.py'
SPEC = importlib.util.spec_from_file_location('c1_storage_step', SCRIPT)
c1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c1)


class StepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.control = self.root/'b-c1-after-reboot-01'
        self.canonical = self.root/'parallel-control-01'
        self.canonical.mkdir()
        self.control.mkdir()
        (self.control/'state.sqlite').touch()
        self.control_patch = patch.object(c1, 'CONTROL', self.control)
        self.control_patch.start()
        self.addCleanup(self.control_patch.stop)
        self.canonical_patch = patch.object(c1, 'CANONICAL', self.canonical)
        self.canonical_patch.start()
        self.addCleanup(self.canonical_patch.stop)

    def write(self, path, doc):
        p = self.root/path
        p.parent.mkdir(parents=True, exist_ok=True)
        c1.write_new(p, doc)
        return c1.ref(p)

    def test_missing_independent_capacity_refuses_before_any_worker_access(self):
        with patch.object(c1.subprocess, 'run', side_effect=AssertionError('worker access')):
            with self.assertRaisesRegex(ValueError, 'Missing independent full C1'):
                c1.capacity_basis({}, {}, None, None, None, None, 'on')

    def test_c2_copy_budget_is_not_a_c1_peak_qualification(self):
        p = self.write('c2-budget.json', dict(schema='pilot-capacity-budget-v1', passed=True))
        with self.assertRaisesRegex(ValueError, 'independent C1 phase'):
            c1.capacity_basis({'capacity_qualification': p}, {}, None, None, None, None, 'off')

    def test_available_space_is_checked_per_disk_after_growth(self):
        out = self.control/'step'
        out.mkdir()
        stamp = c1.now()
        snap = dict(at=stamp.isoformat(), nodes={r:dict(sampled_at=stamp.isoformat()) for r in c1.ROLES})
        poor = ('objects','/srv/ax-objects-2','bytes')
        rich = ('objects','/srv/ax-objects-3','bytes')
        rows = {poor: (100, 24), rich: (100, 90)}
        capacities = {poor:dict(data=5), rich:dict(data=5), ('coordinator',None,'bytes'):dict(data=10),
                      ('coordinator',None,'inodes'):dict(data=1)}
        local = {('coordinator',None,'bytes'): (409600,204800), ('coordinator',None,'inodes'): (100,40)}
        with patch.object(c1, 'native_rows', return_value=(rows, ('identity',), 12)), \
                patch.object(c1, 'coordinator_rows', return_value=local):
            with self.assertRaisesRegex(ValueError, 'ax-objects-2'):
                c1.capacity_gate({'snapshot_tool': {}}, snap, None, None, ('identity',), capacities, stamp, out, out, out)

    def test_real_resource_lock_excludes_parallel_owner(self):
        cell = dict(pool='pool-b', resource_ids=['disk:d-example'])
        plan = dict(config=dict(clusters=[dict(id='pool-b', resource_ids=['disk:d-example'])]))
        with c1.resource_guard(self.control, cell, plan):
            with ExitStack() as stack, self.assertRaisesRegex(ValueError, 'owns physical resource'):
                c1.guards(stack, [self.control], cell, plan)

    def test_untested_step_cannot_skip_prefix_or_reuse_started_trial(self):
        out = self.control/'one-cell-runs'/c1.CELL
        trials = [dict(collector_name='ax-pilot-first', collector_out='/srv/ax-objects-0/q/first',
                       out=str(self.control/'calibration'/label)) for label in c1.LABELS[:-1]]
        with self.assertRaisesRegex(ValueError, 'completed prefix'):
            c1.prefix_chain(dict(step=2,prefix=[]), dict(trials=trials), out, None, None)
        Path(trials[0]['out']).mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'no replay'):
            c1.prefix_chain(dict(step=1,prefix=[]), dict(trials=trials), out, None, None)

    def test_step_one_only_invokes_original_calibrator(self):
        tool = self.write('calibrate-c1.py', {})
        out = self.control/'step-1'
        out.mkdir()
        current = c1.collector('ax-pilot-first','/srv/ax-objects-0/q/first')
        t = dict(attempt='cal-pool-b-j-mx-test-off1', mode='off', out=str(self.control/'trial'))
        batch = dict(qualification=str(self.control/'qualification'), node_tool=dict(path='/remote/tool.py',sha256='x'),
                     observer_tool=dict(name='make-observer.py',sha256='y'), tools=dict(calibrate=tool),
                     trials=[t], runtime_control=self.write('runtime-binding.json', {}))
        with patch.object(c1, 'substep', return_value={'exit': 0}) as run, \
                patch.object(c1, 'trial_finish', return_value=({'proof': True}, current, {})):
            self.assertEqual(c1.launch(out,batch,dict(case='fio-randrw',arm=dict(product='juicefs')),1,current)['collector'],
                             current)
        directory, cmd = run.call_args.args
        self.assertEqual(directory, out/'calibrate')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(cmd[1], Path(tool['path']))
        self.assertEqual(cmd[cmd.index('--attempt')+1], t['attempt'])
        self.assertEqual(cmd[cmd.index('--mode')+1], 'off')

    def test_original_failed_overhead_never_admits(self):
        tool = self.write('check-calibration.py', {})
        out = self.control/'step-7'
        out.mkdir()
        current = c1.collector('ax-pilot-first','/srv/ax-objects-0/q/first')
        batch = dict(qualification=str(self.control/'qualification'), node_tool=dict(path='/remote/tool.py',sha256='x'),
                     observer_tool=dict(name='make-observer.py',sha256='y'), tools=dict(check=tool),
                     trials=[dict(out=label) for label in c1.LABELS[:-1]],
                     runtime_control=self.write('runtime-binding.json', {}))
        with patch.object(c1, 'substep', side_effect=ValueError('original check rejected overhead')) as run:
            with self.assertRaisesRegex(ValueError, 'original check rejected'):
                c1.launch(out,batch,{},7,current)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], out/'check')
        self.assertEqual(c1.load(out/'pairs.json'), [dict(off='off1',on='on1'),dict(off='off2',on='on2'),
                                                   dict(off='off3',on='on3')])

    def test_cleanup_before_prior_result_is_rejected(self):
        prior = self.write('prior.json', dict(at=c1.now().isoformat()))
        source = {'path': '/original/request.json', 'sha256': 'digest'}
        receipt = self.write('cleanup.json', dict(schema='pilot-prestep-cleanup-v1', original_request=source,
            pool='pool-b', step=2, prefix=[prior], outcome='not-needed', actions=[],
            finished_at=(c1.now()-c1.timedelta(seconds=3)).isoformat()))
        with self.assertRaisesRegex(ValueError, 'precedes a finished trial'):
            c1.cleanup(dict(original_request=source,step=2,prefix=[prior],cleanup=receipt),c1.now())

    def test_handwritten_cleanup_pass_cannot_claim_actual_deletion(self):
        source = {'path': '/original/request.json', 'sha256': 'digest'}
        receipt = self.write('cleanup.json', dict(schema='pilot-prestep-cleanup-v1', original_request=source,
            pool='pool-b', step=1, prefix=[], outcome='cleaned', actions=[dict(path='/srv/ax-objects-0/o/old',
            passed=True, groups=[])], finished_at=c1.now().isoformat()))
        with self.assertRaisesRegex(ValueError, 'B cleanup executor'):
            c1.cleanup(dict(original_request=source,step=1,prefix=[],cleanup=receipt),c1.now())

    def native_snapshot(self):
        inventory, nodes = {}, {}
        for role in c1.ROLES:
            points = c1.role_mounts(role)
            serials = [f'{role}{i}' for i in range(len(points))]
            inventory[role] = dict(data_disk_ids=['d-'+serial for serial in serials])
            nodes[role] = dict(boot_id='6382995a-7168-411b-b972-582ef75828a9', disks=[
                dict(path=path, serial=serial, device=i+1, device_bytes=1024, total_bytes=1000,
                     available_bytes=800, total_inodes=100, available_inodes=90)
                for i,(path,serial) in enumerate(zip(points,serials))])
        nodes['metadata']['redis'] = dict(maxmemory=50, used_memory=20, aof_current_size=12, process_id=9)
        nodes['objects'].update(container_id='1'*64, image='sha256:image', pid=12)
        fleet = self.write('runtime/fleet.json', {})
        tool = self.write('capacity-check.py', {})
        snapshot = dict(inventory=fleet,pool='pool-b',source_pins=[fleet,tool],nodes=nodes)
        return snapshot, dict(roles=inventory), self.root/'runtime', tool

    def capacity_fixture(self):
        snapshot, inventory, runtime, tool = self.native_snapshot()
        snapshot['at'] = c1.now().isoformat()
        for role in c1.ROLES:
            snapshot['nodes'][role]['sampled_at'] = snapshot['at']
            self.write('runtime/probes/'+c1.alias(c1.POOL,role)+'/probe.json',
                       dict(boot_id=snapshot['nodes'][role]['boot_id']))
        plan = self.write('plan.json', dict(cells=[dict(id=c1.CELL)]))
        q = self.root/'qualification'/'juicefs'
        work = self.write('qualification/juicefs/workload.json', {})
        metric = self.write('qualification/juicefs/metrics.json', {})
        asset = self.write('qualification/assets/juicefs/asset.json', {})
        original = self.write('original-native.json', dict(allocated=1, aof=12))
        batch = dict(runtime_control=original,node_tool=original,observer_tool=original,tools={})
        rows = c1.native_rows(snapshot, inventory, runtime, tool)[0]
        rows.update({('coordinator',None,'bytes'): (1000,900), ('coordinator',None,'inodes'): (100,90)})
        budgets = []
        for (role,path,quantity) in rows:
            parts = {name: 20 for name in c1.GROWTH}
            basis = {name: dict(sources=[original], rationale='Fixture allocation bound for '+name,
                terms=[dict(source=0,pointer=['allocated'],numerator=20,denominator=1)]) for name in c1.GROWTH}
            budgets.append(dict(role=role,path=path,quantity=quantity,basis=basis,**parts))
        cap = dict(schema='c1-mixed-fio-capacity-qualification-v2', passed=True,reviewed=True,
            pool=c1.POOL,cell_id=c1.CELL,modes=['off','on','formal'],plan=plan,workload=work,metrics=metric,
            asset=asset,runtime=batch['runtime_control'],node_tool=original,observer_tool=original,tools={},
            reserve_percent=20,history_is_lower_bound=True,rows=budgets,
            current_snapshot=self.write('snapshot.json',snapshot))
        lower = {(path,unit): 10 for path in c1.role_mounts('objects') for unit in ('bytes','inodes')}
        return cap, dict(snapshot_tool=tool), batch, q, plan, inventory, runtime, lower

    def test_one_completed_history_and_current_snapshot_can_cover_all_modes(self):
        cap, request, batch, q, plan, inventory, runtime, lower = self.capacity_fixture()
        request['capacity_qualification'] = self.write('capacity.json', cap)
        with patch.object(c1, 'history_lower_bound', return_value=lower), \
                patch.object(c1.subprocess, 'run', side_effect=AssertionError('no workload or worker contact')):
            for mode in ('off','on','formal'):
                identity, budgets, stamp = c1.capacity_basis(request,batch,q,plan,inventory,runtime,mode)
                self.assertEqual(len(budgets),15)
                self.assertEqual(identity[0][1], '6382995a-7168-411b-b972-582ef75828a9')

    def test_historical_lower_bound_does_not_replace_missing_margins_or_sources(self):
        cap, request, batch, q, plan, inventory, runtime, lower = self.capacity_fixture()
        for index, alteration in enumerate(('under_lower','missing_margin','missing_source')):
            changed = copy.deepcopy(cap)
            row = next(r for r in changed['rows'] if r['role']=='objects' and r['quantity']=='bytes')
            if alteration == 'under_lower':
                row['data_peak'] = 9
                row['basis']['data_peak']['terms'][0]['numerator'] = 9
            elif alteration == 'missing_margin':
                row['missed_peak_margin'] = 0
                row['basis']['missed_peak_margin'].update(terms=[],not_applicable=True)
            else:
                row['basis']['preparation_margin']['sources'] = []
            request['capacity_qualification'] = self.write('capacity-'+str(index)+'.json',changed)
            with patch.object(c1,'history_lower_bound',return_value=lower), self.assertRaises(ValueError):
                c1.capacity_basis(request,batch,q,plan,inventory,runtime,'off')

    def test_old_boot_current_snapshot_cannot_qualify(self):
        cap, request, batch, q, plan, inventory, runtime, lower = self.capacity_fixture()
        snap = c1.load(c1.pinned(cap['current_snapshot']))
        snap['nodes']['objects']['boot_id'] = '11111111-1111-1111-1111-111111111111'
        cap['current_snapshot'] = self.write('wrong-boot.json',snap)
        request['capacity_qualification'] = self.write('capacity.json',cap)
        with patch.object(c1,'history_lower_bound',return_value=lower), \
                self.assertRaisesRegex(ValueError,'freshly qualified runtime boot'):
            c1.capacity_basis(request,batch,q,plan,inventory,runtime,'on')

    def test_retained_real_on1_samples_are_only_per_disk_lower_bounds(self):
        history = SCRIPT.parent.parent/'evidence/preparation/b-object-readonly-inspection-01/coordinator/readonly-disks.stdout.json'
        trial = 'parallel-control-01/calibration/'+c1.HISTORY_TAG
        finished = self.write(trial+'/finished.json',{})
        self.write(trial+'/collector.json',dict(name='ax-pilot-old',out='/srv/ax-objects-0/q/old'))
        work = self.write('history-contract/workload.json',{})
        metric = self.write('history-contract/metrics.json',{})
        self.write(trial+'/cohort.json',dict(workload_contract=work,metric_contract=metric))
        observed = c1.load(history)['observers'][c1.HISTORY_TAG]
        audit = self.write(trial+'/objects-raw-audit.json',dict(
            finished_sha256=observed['lifecycle']['finished.json']['sha256'],samples=observed['sample_count']))
        with patch.object(c1,'same_contracts'), \
                patch.object(c1,'trial_finish',return_value=(finished,{},dict(raw_audits={'pool-b-objects':audit}))) as complete:
            lower = c1.history_lower_bound(dict(history=c1.ref(history),history_finished=finished),None,{})
        self.assertEqual(complete.call_count,1)
        self.assertEqual(set(lower),{(p,u) for p in c1.role_mounts('objects') for u in ('bytes','inodes')})
        self.assertGreater(lower['/srv/ax-objects-0','bytes'],18_000_000_000)
        for i in (1,2,3):
            self.assertGreater(lower[f'/srv/ax-objects-{i}','bytes'],17_000_000_000)

    def test_new_controller_tools_omit_old_runtime_binding(self):
        tool = self.write('calibrate-c1.py', {})
        out = self.control/'step-1'
        out.mkdir()
        current = c1.collector('ax-pilot-first','/srv/ax-objects-0/q/first')
        t = dict(attempt='cal-pool-b-j-mx-rb1-off1', mode='off', out=str(self.control/'trial'))
        batch = dict(qualification=str(self.control/'qualification'), node_tool=dict(path='/remote/tool.py',sha256='x'),
                     observer_tool=dict(name='make-observer.py',sha256='y'), tools=dict(calibrate=tool),trials=[t])
        with patch.object(c1,'substep',return_value={'exit':0}) as launch, \
                patch.object(c1,'trial_finish',return_value=({'proof':True},current,{})):
            c1.launch(out,batch,dict(case='fio-randrw',arm=dict(product='juicefs')),1,current)
        argv = launch.call_args.args[1]
        self.assertEqual(argv[argv.index('--control')+1],self.control)
        self.assertNotIn('--runtime-control',argv)
        self.assertEqual(c1.pilot_context.select(self.control),self.control)

    def test_frozen_contract_semantics_survive_new_qualification_paths(self):
        for name in ('old','new'):
            self.write(name+'/juicefs/workload.json',dict(schema='fio',params={'size':64},seed=1,
                binary_pins=[dict(path='/usr/bin/fio',sha256='f'*64)],input_pins=[name]))
            self.write(name+'/juicefs/metrics.json',dict(schema='metrics',bindings=['native'],source_pins=[name]))
            self.write(name+'/assets/juicefs/asset.json',dict(bucket='same-original'))
        c1.same_contracts(self.root/'old/juicefs',self.root/'new/juicefs')
        changed = c1.load(self.root/'new/juicefs/metrics.json')
        changed['bindings'] = ['different']
        (self.root/'new/juicefs/metrics.json').write_text(c1.json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'metric semantics changed'):
            c1.same_contracts(self.root/'old/juicefs',self.root/'new/juicefs')

    def controller_fixture(self):
        from bench import suite_state
        plan = c1.check_plan(c1.load(SCRIPT.parent.parent/'evidence/preparation/parallel-control-01/plan.json'))
        fleet = dict(nodes=[dict(id=c1.alias(c1.POOL,role),role='executor' if role=='client' else 'service',repo='/old')
                            for role in c1.ROLES])
        probes, checks = [], {}
        for role in c1.ROLES:
            node = c1.alias(c1.POOL,role)
            for name in [node]+([node+'-slot'] if role=='client' else []):
                probe = self.write('b-c1-after-reboot-01/probes/'+name+'/probe.json',dict(
                    passed=True,node_id=node,inventory_sha256='pending',boot_id='new-boot'))
                self.write('b-c1-after-reboot-01/probes/'+name+'/peer.json',{})
                probes.append(probe)
            maintenance = self.write('maintenance/'+role+'.json',dict(boot_id='new-boot'))
            checks[role] = dict(passed=True,maintenance=maintenance,probe=probes[-2 if role=='client' else -1])
        for root in (self.canonical,self.control):
            self.write(str(root.relative_to(self.root))+'/fleet.json',fleet)
            for name in ('suite/plan.json','provenance/inventory.json','provenance/build-manifest.json',
                         'provenance/input-manifest.json'):
                self.write(str(root.relative_to(self.root))+'/'+name,plan if name=='suite/plan.json' else {})
        for i,pin in enumerate(probes):
            data = c1.load(pin['path']); data['inventory_sha256'] = c1.digest(self.control/'fleet.json')
            Path(pin['path']).write_text(c1.json.dumps(data))
            probes[i] = c1.ref(pin['path'])
        for role in c1.ROLES:
            checks[role]['probe'] = c1.ref(self.control/'probes'/c1.alias(c1.POOL,role)/'probe.json')
        self.write('b-c1-after-reboot-01/allocation.json',dict(passed=True,inventory_sha256=c1.digest(self.control/'fleet.json'),probes=probes))
        self.write('b-c1-after-reboot-01/qualification/hardware.json',dict(passed=True,pool=c1.POOL,
            inventory_sha256=c1.digest(self.control/'fleet.json'),checks=checks))
        (self.canonical/'runner-locks').mkdir()
        (self.control/'runner-locks').symlink_to(self.canonical/'runner-locks',target_is_directory=True)
        (self.control/'state.sqlite').unlink()
        suite_state.initialize(self.control/'state.sqlite',plan)
        suite_state.initialize(self.canonical/'state.sqlite',plan)
        common = dict(tools={},node_tool={},observer_tool={})
        original = self.write('original-request.json',dict(control=str(self.canonical),cell_id=c1.CELL,
            trials=[dict(attempt=c1.HISTORY_TAG)],qualification=str(self.canonical/'qualification'),**common))
        return dict(frozen_request=original),dict(qualification=str(self.control/'qualification'),**common),plan

    def test_independent_initialized_controller_keeps_canonical_lock_and_new_boot(self):
        request,batch,plan = self.controller_fixture()
        with patch.object(c1,'same_contracts'):
            c1.controller_basis(request,batch,plan)
        cell = dict(pool=c1.POOL,resource_ids=['disk:shared-test'])
        lockplan = dict(config=dict(clusters=[dict(id=c1.POOL,resource_ids=cell['resource_ids'])]))
        with c1.resource_guard(self.canonical,cell,lockplan), ExitStack() as stack:
            with self.assertRaisesRegex(ValueError,'owns physical resource'):
                c1.guards(stack,[self.control,self.canonical],cell,lockplan)
        with c1.sqlite3.connect(self.control/'state.sqlite') as db:
            db.execute("INSERT INTO events(utc,kind,payload) VALUES ('test','inherited','{}')")
        with patch.object(c1,'same_contracts'), self.assertRaisesRegex(ValueError,'new empty controller'):
            c1.controller_basis(request,batch,plan)

    def test_original_draft_tool_builds_new_control_request_for_actual_frozen_cell(self):
        plan = c1.load(SCRIPT.parent.parent/'evidence/preparation/parallel-control-01/plan.json')
        cell = next(c for c in plan['cells'] if c['id']==c1.CELL)
        self.write('b-c1-after-reboot-01/suite/plan.json',plan)
        fleet = self.write('b-c1-after-reboot-01/fleet.json',{})
        q = self.control/'qualification-mixed'
        self.write('b-c1-after-reboot-01/qualification-mixed/juicefs/workload.json',dict(params=cell['params']))
        source = self.write('unit-test-only-native.json',dict(purpose='unit-test-only'))
        cross = self.write('comparison.json',dict(schema='pilot-cross-pool-equivalence-v1',passed=True,
            plan_sha256=plan['plan_sha256'],inventory_sha256=fleet['sha256'],pools=['pool-a','pool-b'],
            checks={'unit-test-only':True},source_pins=[source]))
        draft = SCRIPT.with_name('draft-one-c1.py')
        output = self.control/'drafts/request.json'
        args = [str(draft),'--control',str(self.control),'--qualification',str(q),'--cell',c1.CELL,
            '--node-tool','/srv/ax-client/code-01/pilot-node.py','--node-tool-sha256','a'*64,
            '--observer-tool-name','make-observer.py','--observer-tool-sha256','b'*64,
            '--collector-name','ax-pilot-current','--collector-out','/srv/ax-objects-0/q/current',
            '--cross-pool-qualification',cross['path'],'--latest-check',source['path'],'--attempt-suffix','rb01',
            '--run-directory',str(self.control/'one-cell-runs'/(c1.CELL+'-rb01')),'--out',str(output)]
        for option,key in [('calibrator','calibrate'),('checker','check'),('admitter','admit'),('runner','run')]:
            args += ['--'+option,str(SCRIPT.with_name(c1.TOOLS[key]))]
        with patch.object(c1.sys,'argv',args), redirect_stdout(io.StringIO()):
            runpy.run_path(str(draft),run_name='__main__')
        batch,out = c1.original_batch(dict(original_request=c1.ref(output),
            snapshot_tool=c1.ref(SCRIPT.with_name('capacity-check.py'))))
        self.assertEqual(batch['control'],str(self.control))
        self.assertNotIn('runtime_control',batch)
        self.assertEqual([t['mode'] for t in batch['trials']],['off','on','on','off','off','on'])
        self.assertTrue(all(Path(t['out']).is_relative_to(self.control) for t in batch['trials']))

    def test_current_service_evidence_cannot_describe_an_old_process(self):
        cap,request,batch,q,plan,inventory,runtime,lower = self.capacity_fixture()
        request['capacity_qualification'] = self.write('capacity.json',cap)
        snapshot = c1.load(cap['current_snapshot']['path'])
        pins = []
        for role in c1.ROLES:
            value = dict(boot_id=snapshot['nodes'][role]['boot_id'])
            if role=='objects':value['rustfs'] = dict(id=snapshot['nodes'][role]['container_id'])
            if role=='metadata':value['redis'] = dict(process_id=str(snapshot['nodes'][role]['redis']['process_id']))
            pins.append(self.write('services/'+c1.alias(c1.POOL,role)+'.json',value))
        c1.current_services(request,dict(source_pins=pins),runtime)
        changed = c1.load(pins[-1]['path']); changed['rustfs']['id'] = '2'*64
        pins[-1] = self.write('wrong-services/pool-b-objects.json',changed)
        with self.assertRaisesRegex(ValueError,'qualified RustFS container'):
            c1.current_services(request,dict(source_pins=pins),runtime)

    def test_fresh_original_base_crc_is_bound_to_new_boot_and_native_fio(self):
        params = dict(rw='randrw',file_bytes_per_job=67108864,io_bytes_per_job=67108864,block_bytes=4096,
            numjobs=1,ioengine='psync',iodepth=1,time_based=False,direct=False,fsync_every_write=True,
            rwmixread=75,cache_state='client-cold')
        asset = dict(mount_point='/srv/ax-client/a/original/mnt',seed=1,plan={'path':'/original/plan.json','sha256':'p'})
        self.write('qualification/assets/juicefs/asset.json',asset)
        self.write('runtime/probes/pool-b-client/probe.json',dict(boot_id='new-boot'))
        spec = dict(filesystem='juicefs',mount_point=asset['mount_point'],anchor={'base':'original'},
                    suite_binding=dict(plan=asset['plan'],cell_id=c1.CELL))
        pins = dict(mount_spec=self.write('base/mount.json',spec))
        ready = dict(passed=True,process={'boot_id':'new-boot'},spec=pins['mount_spec'],
                     anchor=spec['anchor'],mount={'point':asset['mount_point']})
        pins['mount_ready'] = self.write('base/ready.json',ready)
        pins['mount_exit'] = self.write('base/stopped.json',dict(passed=True,exit_code=0,mount_absent=True,
            anchor=ready['anchor'],original_mount=ready['mount']))
        argv = c1.fio_workload.arguments(dict(binary={'path':'/usr/bin/fio'},params=params,seed=1,
            root=asset['mount_point']+'/base-4096'),'verify',Path('/native/verify/fio.json'))
        raw = {'global options':{},'jobs':[]}
        options = raw['global options']
        for argument in argv[1:]:
            key,value = argument[2:].split('=',1)
            if key=='name':
                options = {}
                raw['jobs'].append(dict(jobname=value,error=0,read={'io_bytes':67108864},write={'io_bytes':0},
                                        **{'job options':options}))
            options[key] = value
        pins['fio_raw'] = self.write('base/fio.json',raw)
        pins['fio_result'] = self.write('base/result.json',dict(passed=True,completed=True,phase='verify',params=params,
            seed=1,application_bytes=67108864,raw_sha256=pins['fio_raw']['sha256']))
        pins['fio_exit'] = self.write('base/fio-exit.json',dict(exit_code=0))
        c1.base_recheck(dict(base_recheck=pins),self.root/'qualification/juicefs',dict(params=params),self.root/'runtime')
        ready['process']['boot_id'] = 'old-boot'
        pins['mount_ready'] = self.write('base/old-ready.json',ready)
        with self.assertRaisesRegex(ValueError,'this boot'):
            c1.base_recheck(dict(base_recheck=pins),self.root/'qualification/juicefs',dict(params=params),self.root/'runtime')

    def test_actual_archive_must_bind_native_trial_bytes(self):
        tag = 'cal-pool-b-j-mx-new-off1'
        proof = self.write('trial/finished.json', dict(
            client_verify=self.write('client/q/'+tag+'/verify/result.json',dict(passed=True)),
            native_exit=self.write('client/q/'+tag+'/exit.json',dict(passed=True)),
            verify_exit=self.write('client/q/'+tag+'/verify-exit.json',dict(passed=True))))
        pins = dict(debt=self.write('client/q/'+tag+'/debt/result.json',dict(passed=True)),
                    started=self.write('trial/started.json',{}),cohort=self.write('trial/cohort.json',{}))
        archives = {}
        for role, root in dict(coordinator=self.root/'trial',client=self.root/'client').items():
            target = self.root/(role+'.tar.gz')
            files = sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())
            c1.archive_check.pack(root,files,target,c1.CELL,'calibration-'+tag+'-'+role)
            archives[role] = c1.ref(target)
        trial = dict(attempt=tag,mode='off')
        c1.archived_trial(archives,trial,proof,pins)
        changed = self.write('other-finished.json',dict(client_verify=c1.ref(self.root/'trial/started.json'),
            native_exit=c1.load(proof['path'])['native_exit'], verify_exit=c1.load(proof['path'])['verify_exit']))
        with self.assertRaisesRegex(ValueError,'bind original native evidence'):
            c1.archived_trial(archives,trial,changed,pins)

    def test_root_device_aliases_or_swapped_serials_cannot_pass(self):
        snap, inventory, runtime, tool = self.native_snapshot()
        self.assertEqual(len(c1.native_rows(snap, inventory, runtime, tool)[0]), 13)
        disks = snap['nodes']['objects']['disks']
        first, second = disks[0]['serial'], disks[1]['serial']
        disks[0]['serial'], disks[1]['serial'] = second, first
        with self.assertRaisesRegex(ValueError, 'owned storage devices'):
            c1.native_rows(snap,inventory,runtime,tool)
        disks[0]['serial'], disks[1]['serial'] = first, second
        disks[0]['device'] = disks[1]['device']
        with self.assertRaisesRegex(ValueError, 'owned storage devices'):
            c1.native_rows(snap,inventory,runtime,tool)


if __name__ == '__main__':
    unittest.main()
