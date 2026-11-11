from contextlib import contextmanager
from datetime import timedelta, datetime, timezone
from io import StringIO
import json
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bench import archive_check, experiment_step


SCRIPT = (Path(__file__).resolve().parents[1] /
          'experiments/unified-suite/pilot-aliyun/scripts/stop-idle-pool.py')
M = runpy.run_path(str(SCRIPT))['guardian'].__globals__
C1, C2, ROLES = (M[k] for k in ('C1', 'C2', 'ROLES'))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))
    return M['ref'](path)


class FakeCloud:
    def __init__(self):
        rows, _, jump = M['allowlist'](M['ref'](M['ALLOWLIST']))
        self.scope = dict(region='cn-hangzhou', reuse_jump_instance=jump)
        self.rows = {v['instance']['id']: (role, v) for role, v in rows.items()}
        self.stopped = []
        self.bad_readback = False
        self.calls = []

    def api(self, action, **params):
        self.calls.append((action, params))
        if action == 'DescribeInstances':
            instance, = json.loads(params['InstanceIds'])
            role, row = self.rows[instance]
            stopped = instance in self.stopped
            payload = dict(InstanceId=instance, InstanceChargeType='PostPaid', ZoneId=row['instance']['zone'],
                           InstanceType=row['instance']['type'], InternetMaxBandwidthOut=0,
                           Status='Stopped' if stopped else 'Running',
                           StoppedMode='StopCharging' if stopped and not self.bad_readback else 'KeepCharging',
                           Tags={'Tag': [dict(TagKey='campaign', TagValue=M['CAMPAIGN']),
                                         dict(TagKey='role', TagValue=role)]})
            return dict(RequestId='describe-instance', TotalCount=1, Instances={'Instance':[payload]})
        instance = params['InstanceId']
        _, row = self.rows[instance]
        if action == 'DescribeDisks':
            disks = [dict(DiskId=d['id'], Size=d['gib'], Type=d['type'], Status='In_use', InstanceId=instance,
                          DeleteWithInstance=True) for d in row['disks']]
            return dict(RequestId='describe-disk', TotalCount=len(disks), Disks={'Disk':disks})
        if action != 'StopInstance' or params != dict(InstanceId=instance, ForceStop=False, StoppedMode='StopCharging'):
            raise AssertionError((action, params))
        self.stopped.append(instance)
        return dict(RequestId='stop-request')


class StopIdlePool(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(TemporaryDirectory())
        self.root = Path(tmp).resolve()
        self.control = self.root/'parallel-control-01'
        self.second = self.root/'fork-control-01'
        self.out = self.root/'output'; self.out.mkdir()
        self.cloud = FakeCloud()
        allowlist = M['ref'](M['ALLOWLIST'])
        self.rows, resources, _ = M['allowlist'](allowlist)
        self.plan = dict(plan_sha256='plan', config={'clusters':[dict(id='pool-b',resource_ids=sorted(resources))]},
                         cells=[dict(id=cid,pool='pool-b',resource_ids=sorted(resources),stages=['measure','verify'])
                                for cid in (C1,C2)])
        self.system = []
        self.jobs = []
        self.enterContext(patch.dict(M, CONTROL=self.control, check_plan=lambda plan:plan))
        self.enterContext(patch.object(M['socket'], 'gethostname', return_value='ax-c1-jump'))
        self.enterContext(patch.object(M['subprocess'], 'check_output', side_effect=self.manager))
        plan = write(self.control/'suite/plan.json', self.plan)
        cells = {cid:self.make_cell(cid) for cid in (C1,C2)}
        terminal = {}
        for unit,cid in M['TERMINALS'].items():
            journal=dict(UNIT=unit,INVOCATION_ID='1'*32,_BOOT_ID='2'*32)
            manager=dict(LoadState='not-found',ActiveState='inactive',SubState='dead',InvocationID='',MainPID='0',
                         ExecMainStatus='0')  # watch-c2-to-c1.py wait() keeps these exact properties.
            raw=write(self.root/'journal'/unit,dict(systemd=manager,
                start=dict(journal,__CURSOR='s=1',__MONOTONIC_TIMESTAMP='50',MESSAGE='Started '+unit+' - test.'),
                stop=dict(journal,__CURSOR='s=2',__MONOTONIC_TIMESTAMP='100',MESSAGE=unit+': Deactivated successfully.')))
            terminal[unit]=write(self.root/'terminal'/unit,dict(schema='ax-pilot-terminal-unit-v1',
                passed=True,unit=unit,closing=cells[cid]['closing'],invocation_id='1'*32,main_pid=0,control_pid=0,
                exit_status=0,terminal_evidence=raw,source_pins=[cells[cid]['closing']]))
        observed = M['cloud'].now()
        probes = {}; proofs = []; runtime = self.second/'runtime-15-01'
        for role in ROLES:
            # Projection of the native runtime15 fleet schema, preserving the source path layout.
            boot = {'client':'b4316c9e-f7da-47dc-a443-8f4c87a6cf11',
                    'metadata':'166d02b4-1d08-4ee3-bb02-7d75a77e588b',
                    'objects':'6382995a-7168-411b-b972-582ef75828a9'}[role]
            parent=runtime/'probes'/('pool-b-'+role)
            original=write(parent/'probe.json',dict(passed=True,checks=dict(runtime_matches=True,work_root=True,
                data_capacity=True),runtime_mismatches=[],boot_id=boot))
            proofs.extend([original,write(parent/'peer.json',{})])
            live=write(self.root/(role+'-live.json'),dict(at=observed,role=role,boot_id=boot,
                result=dict(safe_for_release=True,live=[],unknown_startup_directories=[],mounts=[],slot='')))
            probes[role] = write(self.root/(role+'-exit.json'), dict(at=observed,role=role,boot_id=boot,
                instance_id=self.rows[role]['instance']['id'],original_probe=original,passed=True,slot_empty=True,
                all_workload_processes_exited=True,all_mounts_absent=True,pending_upload_bytes=0,
                pending_upload_operations=0,live_evidence=live,source_pins=[original]))
        binding=write(runtime/'binding.json',dict(control=str(self.second),runtime=str(runtime),proofs=proofs))
        self.enterContext(patch.dict(M,RUNTIME=runtime,RUNTIME_SHA=binding['sha256']))
        quiescence = write(self.root/'quiescence.json', dict(schema='ax-pilot-b-stop-quiescence-v1',
            at=observed,passed=True,pool='pool-b',closing=cells[C1]['closing'],roles=probes))
        self.request = dict(schema='ax-pilot-stop-idle-pool-v1',pool='pool-b',reviewed=True,runner=M['ref'](SCRIPT),
            allowlist=allowlist,plan=plan,runtime_binding=binding,cells=cells,terminal_units=terminal,quiescence=quiescence,
            lease_controls=[str(self.control),str(self.second)])
        self.request_path=self.root/'request.json'
        for control in (self.control,self.second):
            control.mkdir(exist_ok=True)
            with sqlite3.connect(control/'state.sqlite') as db:
                db.executescript('CREATE TABLE cell_leases(cell_id,pool); CREATE TABLE cell_resources(cell_id,id); '
                                 'CREATE TABLE cells(id,status,result); CREATE TABLE stages(cell_id,name,status);')
                for cid in (C1,C2):
                    db.execute('INSERT INTO cells VALUES (?,?,?)', (cid,'complete',json.dumps(cells[cid]['closing'])))
                    db.executemany('INSERT INTO stages VALUES (?,?,?)',
                                   [(cid,'measure','complete'),(cid,'verify','complete')])

    def make_cell(self, cid):
        base=self.control/'cells'/cid
        native=write(base/'closure.json', dict(cell_id=cid,passed=True,all_exited=True,all_unmounted=True,
                     pending_upload_bytes=0,pending_upload_operations=0))
        sample=write(base/'sample.json', {})
        names, role_archives = [], []
        for role in ROLES:
            target = base/'archives'/(role+'.tar.gz'); target.parent.mkdir(exist_ok=True)
            src = self.root/('src-'+cid+'-'+role); src.mkdir()
            write(src/'part.json',{})
            checked = archive_check.pack(src,['part.json'],target,cid,role)
            name='archives/'+role+'-check.json'
            write(base/name,checked)
            names.extend([name,'archives/'+role+'.tar.gz']); role_archives.append(M['ref'](target))
        seal=base.with_name(cid+'.retention.json')
        experiment_step.seal(base,names,seal)
        close_path=base.with_name(cid+'.close.json')
        close=write(close_path, dict(schema='suite-cell-close-v1',cell_id=cid,plan_sha256='plan',outcome='complete',
            retention=dict(root=str(base),manifest=M['ref'](seal)),sample=sample,archives=role_archives,
            closure={k:dict(evidence=native,equals=v) for k,v in (
                ('processes',{'all_exited':True}),('mounts',{'all_unmounted':True}),
                ('debt',{'pending_upload_bytes':0,'pending_upload_operations':0}))}))
        cal=self.control/'retained-calibrations'/cid
        trials=[dict(tag='trial-'+str(i),mode=mode,roles=list(ROLES if mode=='on' else ROLES[:1]),passed=True)
                for i,mode in enumerate(('off','on','on','off','off','on'))]
        cal_result=write(cal/'result.json',dict(passed=True,cell_id=cid,trials=trials))
        (cal/'closing.json').write_bytes(close_path.read_bytes())
        (cal/'plan.json').write_bytes((self.control/'suite/plan.json').read_bytes())
        files=['result.json','closing.json','plan.json']
        for trial in trials:
            for role in trial['roles']:
                directory=cal/trial['tag']; directory.mkdir(exist_ok=True)
                inner=directory/(role+'.tar.gz')
                inner.write_bytes((base/'archives'/(role+'.tar.gz')).read_bytes())
                checked=directory/(role+'-archive-check.json')
                write(checked,archive_check.verify(inner,M['cloud'].sha(inner)))
                files.extend([str(p.relative_to(cal)) for p in (inner,checked)])
        packed=archive_check.pack(cal,files,cal.with_suffix('.tar.gz'),cid,'six-calibration-trials')
        return dict(closing=close,calibration_result=cal_result,
                    calibration_check=write(cal.with_name(cid+'-archive-check.json'),packed))

    def manager(self, argv, text):
        if argv[1]=='list-units': return '\n'.join(self.system)
        if argv[1]=='list-jobs': return '\n'.join(self.jobs)
        self.fail('Unexpected live systemctl: '+str(argv))

    def run_guardian(self, lines, apply=True):
        request=write(self.request_path,self.request); out=StringIO()
        code=M['guardian'](request,apply,lines,out)
        return code,[json.loads(line) for line in out.getvalue().splitlines()]

    def run_local(self, apply):
        request=write(self.request_path,self.request)
        expect=['authorized' for _ in ROLES] if apply else []
        test=self

        class FakeGuardian:
            def reply(self, timeout):
                return dict(passed=True,state='ready',request=request,runner_sha256=M['cloud'].sha(SCRIPT),
                            apply=apply,public_oss_pending=['pending'],coordinator='ax-c1-jump',
                            **getattr(test,'ready_extra',{}))
            def send(self, command):
                return dict(state=expect.pop(),role=command)
            def alive(self): pass
            def finish(self): test.assertFalse(expect)

        @contextmanager
        def factory():
            yield FakeGuardian()

        return M['execute'](self.cloud,factory,request,self.out,apply)

    def test_dry_run_rehashes_complete_archives_without_oss_and_never_stops(self):
        code, output=self.run_guardian(StringIO('finish\n'),False)
        self.assertEqual(code,0,output)
        self.assertEqual(len(output[0]['public_oss_pending']),8)
        self.assertEqual(output[1]['state'],'finished')
        self.assertTrue(self.run_local(False)['passed'])
        self.assertFalse(self.cloud.stopped)
        self.assertFalse(list(self.out.glob('*-stop-intent.json')))

    def test_tampered_formal_member_receipt_and_active_or_queued_watcher_fail_closed(self):
        watcher='ax-pilot-b-c2-to-c1-watch-01.service'
        self.system=[watcher+' loaded active running']
        _, output=self.run_guardian(StringIO(''))
        self.assertIn('still active',output[0]['error'])
        self.system=[]; self.jobs=['1 '+watcher+' start waiting']
        _, output=self.run_guardian(StringIO(''))
        self.assertIn('job pending',output[0]['error'])
        self.jobs=[]
        (self.control/'cells'/C1/'archives/client-check.json').write_text('{}')
        _, output=self.run_guardian(StringIO(''))
        self.assertIn('archive seal differs',output[0]['error'])

    def test_guardian_rejects_foreign_phase_lease_and_stale_or_unreviewed_proof(self):
        with sqlite3.connect(self.second/'state.sqlite') as db:
            db.execute('INSERT INTO cell_leases VALUES (?,?)',('incomplete','pool-b'))
        _, output=self.run_guardian(StringIO(''))
        self.assertIn('is leased',output[0]['error'])
        with sqlite3.connect(self.second/'state.sqlite') as db:
            db.execute('DELETE FROM cell_leases')
        self.request['reviewed']=False
        _, output=self.run_guardian(StringIO(''))
        self.assertIn('Reviewed exact',output[0]['error'])
        self.request['reviewed']=True
        p=Path(self.request['quiescence']['path']); proof=json.loads(p.read_text())
        proof['at']=(datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat()
        self.request['quiescence']=write(p,proof)
        _, output=self.run_guardian(StringIO(''))
        self.assertIn('within 15 minutes',output[0]['error'])

    def test_guardian_holds_b_lock_in_every_phase_and_eof_releases(self):
        case=self
        class EOFWithLock(StringIO):
            def readline(self):
                for control in (case.control,case.second):
                    with case.assertRaisesRegex(ValueError,'owns physical resource'):
                        with M['resource_guard'](control,case.plan['cells'][0],case.plan): pass
                return ''

        code, output=self.run_guardian(EOFWithLock())
        self.assertEqual(code,2,output)
        self.assertEqual(output[0]['state'],'ready')
        for control in (self.control,self.second):
            with M['resource_guard'](control,self.plan['cells'][0],self.plan): pass

    def test_symlinked_phase_locks_acquired_once_but_both_leases_checked(self):
        shared=self.control/'runner-locks'
        shared.mkdir()
        (self.second/'runner-locks').symlink_to(shared, target_is_directory=True)
        self.assertEqual(len(list(M['unique_guard_controls']((self.control,self.second)))),1)
        case=self

        class DuringGuard(StringIO):
            def readline(self):
                for phase in (case.control,case.second):
                    with case.assertRaisesRegex(ValueError,'owns physical resource'):
                        with M['resource_guard'](phase,case.plan['cells'][0],case.plan):
                            pass
                return super().readline()

        code,output=self.run_guardian(DuringGuard('finish\n'),False)
        self.assertEqual(code,0,output)
        self.assertEqual([r['state'] for r in output],['ready','finished'])
        with M['resource_guard'](self.second,self.plan['cells'][0],self.plan):
            pass
        with sqlite3.connect(self.second/'state.sqlite') as db:
            db.execute('INSERT INTO cell_leases VALUES (?,?)',('alias-phase-lease','pool-b'))
        code,output=self.run_guardian(StringIO(''),False)
        self.assertEqual(code,2,output)
        self.assertIn(str(self.second),output[0]['error'])
        self.assertIn('is leased',output[0]['error'])

    def test_apply_three_graceful_stops_and_attached_disk_readback(self):
        code, output=self.run_guardian(StringIO('\n'.join([*ROLES,'finish',''])))
        self.assertEqual(code,0,output)
        self.assertEqual([r['role'] for r in output[1:-1]],list(ROLES))
        result=self.run_local(True)
        self.assertTrue(result['passed'],result)
        self.assertEqual(len(self.cloud.stopped),3)
        proofs=[json.loads((self.out/(role+'-rate-change-raw.json')).read_text()) for role in ROLES]
        self.assertEqual(sum(len(p['preserved_disk_ids']) for p in proofs),9)
        self.assertTrue(all(p['preserved_disks'] and not p['released'] and p['stopped_mode']=='StopCharging' for p in proofs))
        self.assertFalse(list(self.out.glob('*cost-event*')))

    def test_bad_provider_readback_never_advances_or_retries(self):
        self.cloud.bad_readback=True
        result=self.run_local(True)
        self.assertFalse(result['passed'])
        self.assertEqual(result['attempted_stop_roles'],['client'])
        self.assertEqual(len(self.cloud.stopped),1)
        self.assertFalse(list(self.out.glob('*-rate-change-raw.json')))

    def test_rechecks_manager_at_each_role_boundary(self):
        test = self
        class QueueDuringAuthorization(StringIO):
            def readline(self):
                value=super().readline()
                if value=='metadata\n':
                    test.jobs=['5 ax-pilot-b-c2-to-c1-watch-01.service start waiting']
                return value

        code, output=self.run_guardian(QueueDuringAuthorization('client\nmetadata\nobjects\nfinish\n'))
        self.assertEqual(code,2,output)
        self.assertEqual([x['state'] for x in output[:-1]],['ready','authorized'])
        self.assertIn('job pending',output[-1]['error'])

    def test_guardian_wire_protocol_checks_subprocess_exit_and_disconnect(self):
        body=('import sys; print(\'{"passed":true,"state":"ready"}\',flush=True); '
              'assert sys.stdin.readline()=="finish\\n"; '
              'print(\'{"passed":true,"state":"finished"}\',flush=True)')
        with subprocess.Popen([sys.executable,'-c',body],stdin=subprocess.PIPE,stdout=subprocess.PIPE,bufsize=0) as p:
            session=M['GuardianSession'](p)
            self.assertEqual(session.reply(2)['state'],'ready')
            session.finish()
            with self.assertRaisesRegex(ValueError,'guardian exited'):
                session.send('client')

    def test_live_proof_and_terminal_wrapper_must_agree_with_raw_receipts(self):
        role_path=self.root/'client-exit.json'
        role=json.loads(role_path.read_text()); live=json.loads(Path(role['live_evidence']['path']).read_text())
        live['result']['mounts']=['/srv/ax-client/example/mnt']
        role['live_evidence']=write(Path(role['live_evidence']['path']),live)
        qp=Path(self.request['quiescence']['path']); q=json.loads(qp.read_text())
        q['roles']['client']=write(role_path,role); self.request['quiescence']=write(qp,q)
        _,output=self.run_guardian(StringIO(''))
        self.assertIn('not freshly confirmed idle: client',output[0]['error'])

        unit='ax-pilot-b-c2-jfs-runtime15-01.service'
        path=Path(self.request['terminal_units'][unit]['path']); wrap=json.loads(path.read_text())
        raw=Path(wrap['terminal_evidence']['path']); recorded=json.loads(raw.read_text())
        recorded['stop']['INVOCATION_ID']='3'*32
        wrap['terminal_evidence']=write(raw,recorded); self.request['terminal_units'][unit]=write(path,wrap)
        _,output=self.run_guardian(StringIO(''))
        self.assertIn('Raw manager/journal exit does not match wrapper',output[0]['error'])

    def make_failed_c1(self):
        for control in (self.control,self.second):
            with sqlite3.connect(control/'state.sqlite') as db:
                db.execute("UPDATE cells SET status='pending',result=NULL WHERE id=?", (C1,))
                db.execute('DELETE FROM stages WHERE cell_id=?', (C1,))
        (self.control/'cells'/C1).rename(self.root/'not-used-c1')
        (self.control/'cells'/(C1+'.close.json')).rename(self.root/'not-used-c1-close.json')
        base = self.control/'one-cell-runs'/C1
        tags = ['cal-pool-b-j-mx-mxb01-'+x for x in ('off1','on1','on2','off2','off3','on3')]
        trials = [dict(attempt=tag,mode=mode,out=str(self.control/'calibration'/tag),
                       collector_out='/srv/ax-objects-0/q/'+tags[1]+'-collector')
                  for tag,mode in zip(tags,('off','on','on','off','off','on'))]
        tool = '/srv/ax-pilot-control/code-15/calibrate-c1.py'
        launch = write(self.control/'drafts/pool-b-c1-mixed-after-c2-01/request.json',
            dict(reviewed=True,cell_id=C1,pool='pool-b',control=str(self.control),out=str(base),
                 trials=trials,tools=dict(calibrate=dict(path=tool))))
        write(base/'started.json',dict(cell_id=C1,request=launch))
        failed = write(base/'failed.json',dict(cell_id=C1,passed=False,automatic_retry=False,next_cell=False,
                                              error='Stopped; inspect '+str(base/'trial-3')))
        for i,t in enumerate(trials[:3],1):
            step = base/f'trial-{i}'
            write(step/'intent.json',dict(automatic_retry=False,
                argv=['python3',tool,'--mode',t['mode'],'--attempt',t['attempt'],'--out',t['out']]))
            write(step/'exit.json',dict(exit_code=1 if i==3 else 0))
            (step/'stdout').write_text('')
            (step/'stderr').write_text('Original telemetry failure' if i==3 else '')
            trial=Path(t['out'])
            if i<3:
                cohort=write(trial/'cohort/result.json',dict(passed=True))
                write(trial/'finished.json',dict(passed=True,cohort=cohort))
            else:
                write(trial/'cohort/result.json',dict(passed=False,purpose='observer-calibration-on',
                    workload_passed=True,telemetry_live_check_passed=False,performance_accepted=False))
                write(trial/'cohort/telemetry-failed.json',dict(error_type='ConnectionRefusedError'))

        self.request['schema']=M['FAILED_SCHEMA']
        now=datetime.now(timezone.utc)
        gauge = dict.fromkeys(('juicefs_staging_block_bytes','juicefs_staging_blocks',
                               'juicefs_staging_writing_blocks'),0)
        debt=[write(self.root/f'debt-{i}.json',dict(passed=True,cell_id=C1,
            at=(now-timedelta(seconds=3-i)).isoformat(),monotonic_ns=i*1_100_000_000,
            native_gauges=gauge,pending_upload_bytes=0,pending_upload_operations=0)) for i in range(2)]
        unmount=write(self.root/'unmount.json',dict(passed=True,mount_absent=True,exit_code=0,
                active_workers=[],original_mount=dict(point='/mnt/fio')))
        self.request['cells'][C1]=dict(request=launch,failed=failed,debt_samples=debt,unmount=unmount)
        unit=next(u for u,c in M['TERMINALS'].items() if c==C1)
        path=Path(self.request['terminal_units'][unit]['path'])
        terminal=M['read'](path)
        terminal.pop('closing')
        raw_path=Path(terminal['terminal_evidence']['path'])
        raw=M['read'](raw_path)
        raw['stop']['MESSAGE']=unit+": Failed with result 'exit-code'."
        raw['stop']['UNIT_RESULT']='exit-code'
        journal=self.root/'original-manager-journal.jsonl'
        journal.write_text(''.join(json.dumps(x)+'\n' for x in (raw['start'],
            dict(raw['stop'],__CURSOR='s=exit',EXIT_STATUS='1'),raw['stop'])))
        # A --collect failure can be gone from the manager; journal remains authoritative.
        terminal.update(failed=failed,exit_status=1,source_pins=[failed],
                        terminal_evidence=write(raw_path,raw),journal_evidence=M['ref'](journal))
        self.request['terminal_units'][unit]=write(path,terminal)
        qpath=Path(self.request['quiescence']['path'])
        q=M['read'](qpath)
        q.pop('closing')
        q.update(schema='ax-pilot-b-stop-failed-quiescence-v1',failed=failed,at=now.isoformat())
        for role,evidence in q['roles'].items():
            path=Path(evidence['path'])
            row=M['read'](path)
            raw_path=Path(row['live_evidence']['path'])
            raw=M['read'](raw_path)
            raw['at']=row['at']=q['at']
            row['live_evidence']=write(raw_path,raw)
            row['sync_evidence']=write(self.root/f'{role}-sync-exit.json',dict(exit_code=0))
            q['roles'][role]=write(path,row)
        self.request['quiescence']=write(qpath,q)
        return base

    def test_failed_calibration_preserves_pending_raw_and_can_stop_original_three(self):
        self.make_failed_c1()
        code, output=self.run_guardian(StringIO('\n'.join([*ROLES,'finish',''])),True)
        self.assertEqual(code,0,output)
        ready=output[0]
        self.assertEqual(ready['c1_outcome'],'failed')
        self.assertEqual(set(ready['coordinator_archives']),{C2})
        self.assertEqual(len(ready['public_oss_pending']),4)
        self.assertEqual(sum(len(row['disk_ids']) for row in ready['raw_archive_pending']),9)
        self.assertTrue(all(row['status']=='pending' for row in ready['raw_archive_pending']))
        self.assertEqual(set(ready['c1_failure_evidence']) & {'cohort','telemetry_failure'}, {'cohort','telemetry_failure'})
        self.ready_extra={k:ready[k] for k in ('c1_outcome','c1_failure_evidence','raw_archive_pending')}
        result=self.run_local(True)
        self.assertTrue(result['passed'],result)
        self.assertFalse(result['released'])
        self.assertEqual(result['c1_outcome'],'failed')
        self.assertEqual(result['raw_archive_pending'],ready['raw_archive_pending'])
        self.assertEqual(len(self.cloud.stopped),3)

    def test_failed_calibration_rejects_later_trial_and_nonzero_sync(self):
        base=self.make_failed_c1()
        (base/'trial-4').mkdir()
        _,output=self.run_guardian(StringIO(''))
        self.assertIn('later calibration or formal C1 already started',output[0]['error'])
        (base/'trial-4').rmdir()
        qpath=Path(self.request['quiescence']['path'])
        q=M['read'](qpath)
        row_path=Path(q['roles']['objects']['path'])
        row=M['read'](row_path)
        row['sync_evidence']=write(Path(row['sync_evidence']['path']),dict(exit_code=1))
        q['roles']['objects']=write(row_path,row)
        self.request['quiescence']=write(qpath,q)
        _,output=self.run_guardian(StringIO(''))
        self.assertIn('post-debt sync and live probe required: objects',output[0]['error'])

    def test_failed_calibration_requires_exact_original_jsonl_exit(self):
        self.make_failed_c1()
        unit=next(u for u,c in M['TERMINALS'].items() if c==C1)
        path=Path(self.request['terminal_units'][unit]['path'])
        wrapper=M['read'](path)
        journal=Path(wrapper['journal_evidence']['path'])
        journal.write_text(journal.read_text().replace('"EXIT_STATUS": "1"','"EXIT_STATUS": "0"'))
        wrapper['journal_evidence']=M['ref'](journal)
        self.request['terminal_units'][unit]=write(path,wrapper)
        _,output=self.run_guardian(StringIO(''))
        self.assertIn('does not bind the original JSONL exit',output[0]['error'])

    def test_windows_ssh_keeps_config_path_in_windows_syntax(self):
        ssh='/mnt/c/Windows/System32/OpenSSH/ssh.exe'
        config='C:/Users/HXY/Example Dir/bootstrap-ssh.conf'
        self.assertEqual(str(M['ssh_config_path'](config)),'/mnt/c/Users/HXY/Example Dir/bootstrap-ssh.conf')
        with patch.object(M['subprocess'],'Popen') as popen:
            with M['remote_guardian'](ssh,config,'/srv/guard.py',dict(path='/srv/request.json',sha256='s'),self.out,False):
                argv=popen.call_args.args[0]
                self.assertEqual(argv[:4],[ssh,'-T','-F',config])
                self.assertIn('ax-pilot-jump',argv)


if __name__=='__main__':
    unittest.main()
