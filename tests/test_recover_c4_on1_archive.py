"""Exercise archival On1 recovery without workers, SSH or a mutable suite state."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from bench.archive_check import pack
from bench.runtime import c4_verify, fork_work
from bench.suite_plan import check_plan

PILOT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun'
SCRIPTS = PILOT/'scripts'


def module(name):
    spec = importlib.util.spec_from_file_location(name,SCRIPTS/(name+'.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


recover = module('recover-c4-on1-archive')
fold = module('fold-c4-observer')
CELL,TAG,POOL,PURPOSE = recover.CELL,recover.TAG,recover.POOL,recover.PURPOSE


def doc(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')
    return recover.ref(path)


def raw(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value,encoding='utf-8')
    return recover.ref(path)


def process(pid):
    return dict(boot_id='57e8a829-a125-4f1a-a5e4-6a44fe392905',pid=pid,start_ticks=123+pid)


def inventory(path):
    return dict(**recover.ref(path),bytes=path.stat().st_size)


class Recovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name).resolve()
        self.control = self.d/'fork-control-01'
        self.cont = self.control/'continuation-on1-01'
        self.trial = self.control/'calibration/flat-on1'
        self.off = self.control/'calibration/flat-off1'
        self.out = self.control/'on1-recovery-01'
        self.asset = '/srv/ax-client/a/asset'
        plan_path = self.control/'suite/plan.json'
        plan_path.parent.mkdir(parents=True)
        shutil.copyfile(PILOT/'evidence/preparation/a-old-c1-observer-reclaim-dryrun-01/fork-plan.json',plan_path)
        self.plan = recover.ref(plan_path)
        plan = check_plan(recover.load(plan_path))
        self.cell = next(c for c in plan['cells'] if c['id']==CELL)
        self.binding = dict(source_tree=dict(path='/srv/ax-client/source',sha256='a'*64),preparation=dict(path='/srv/ax-client/preparation',
                           sha256='b'*64),cell_id=CELL,arm='native')
        self.identity = dict(schema='c4-parent-stat-v1',entries=3,sha256='c'*64)
        off_tag = 'c4-a-flat-off1'
        full = doc(self.off/'raw/client/q'/off_tag/'verify/result.json',
                   dict(verification_policy=c4_verify.REFERENCE,passed=True,child_name=off_tag,
                        full_cold_child=True,parent_unchanged=True,binding=self.binding,parent_identity=self.identity))
        self.full = dict(path=f'/srv/ax-client/q/{off_tag}/verify/result.json',sha256=full['sha256'])
        self.off_finish = doc(self.off/'finished.json',dict(passed=True,purpose='observer-calibration-off',pool=POOL,client_verify=full))
        q = self.control/'qualification/flat'
        self.context = dict(control=str(self.control),asset=self.asset,qualification=str(q),reviewed=True,purpose=PURPOSE,
                            attempt=TAG,out=str(self.trial),cell_id=CELL,verification_policy=c4_verify.TOUCHED,
                            full_reference=self.full,fork=dict(preparation=self.binding['preparation']),
                            collector_name='original-on1-collector',collector_out='/srv/ax-objects-0/q/on1',
                            new_collector_name='original-on1-collector',new_collector_out='/srv/ax-objects-0/q/on1')
        trial_request = dict(self.context,collector_name='stopped-off1-collector',collector_out='/srv/ax-objects-0/q/off1')
        doc(self.trial/'request.json',trial_request)
        doc(self.trial/'context.json',self.context)
        stage_trial = doc(self.cont/'trial.request.json',trial_request)
        overlay = doc(self.cont/'archive-overlay.request.json',dict(trial=stage_trial))
        batch = doc(self.control/'original-batch.json',dict(control=str(self.control),asset=self.asset,cell_id=CELL,
                    calibration_verification=c4_verify.TOUCHED,tools={'admit':recover.ref(SCRIPTS/'admit-c4.py')},
                    trials=[dict(mode='off',attempt=off_tag,out=str(self.off)),
                            dict(mode='on',attempt=TAG,out=str(self.trial),
                                 new_collector_name=self.context['collector_name'],new_collector_out=self.context['collector_out']),
                            *[dict(mode=mode,attempt='other-'+str(i),out=str(self.control/f'other-{i}'))
                              for i,mode in enumerate(('on','off','off','on'))]]))
        wrapper = recover.ref(SCRIPTS/'calibrate-c4-closed-observer.py')
        self.cont_request = doc(self.cont/'request.json',dict(schema='c4-after-directory-hold-v1',reviewed=True,
                                step=2,prefix=[],out=str(self.cont),original_request=batch,archive_overlay=dict(runner=wrapper)))
        doc(self.cont/'trial/intent.json',dict(argv=['/usr/bin/python3',wrapper['path'],'--request',overlay['path'],
                                                   '--sha256',overlay['sha256']],automatic_retry=False))
        trial_exit = doc(self.cont/'trial/exit.json',dict(exit_code=1))
        doc(self.cont/'failed.json',dict(passed=False,error='Inspect original continuation substep '+str(self.cont/'trial'),
                                       automatic_retry=False))
        command = self.trial/'commands/0032'
        raw(self.cont/'trial/stderr','RuntimeError: Inspect failed remote command: '+str(command)+'\n')
        for n in range(33):
            doc(self.trial/'commands'/f'{n:04}'/'exit.json',dict(exit_code=int(n==32)))
        doc(command/'intent.json',dict(role='objects',argv=['python3','-c',f'TAG={TAG!r}\nROOT={recover.SCRATCH!r}\n']))
        raw(command/'stderr','in pack_mapped\nbench/archive_check.py\nValueError: unbounded internal manifest\n')
        metric = doc(q/'native/metrics.json',dict(sample='test'))
        workload = doc(q/'native/workload.json',dict(params=self.cell['params']))
        inv_roles = {}
        observed = []
        bound = {'objects':[]}
        for i,role in enumerate(recover.ROLES):
            worker = self.d/'workers'/role
            mark,close,observ = self.worker(worker,role,process(i+1),metric)
            observed.append(observ)
            bound['objects-scratch' if role=='objects' else role]=close['processes']
            if role=='client':
                self.client_data(worker)
            if role=='objects':
                old = worker/recover.OBS/'legacy-finished.json'
                shutil.copyfile(worker/recover.OBS/'finished.json',old)
                self.fold_receipt = fold.fold(worker/recover.OBS)
            dest = (self.d/'compact' if role=='objects' else self.trial/'archives')
            dest.mkdir(parents=True,exist_ok=True)
            paths = sorted(p.relative_to(worker).as_posix() for p in worker.rglob('*') if p.is_file())
            archive = dest/(role+'.tar.gz')
            check = doc(dest/(role+'-check.json'),pack(worker,paths,archive,CELL,role))
            if role=='objects':
                names = recover.OBJECT_LEAVES | {f'{recover.OBS}/{n}' for n in ('ready.json','collector.log',
                        *(f'mark-{j+1:04}.json' for j in range(len(mark))))}
                legacy = {name:{k:inventory(worker/name)[k] for k in ('bytes','sha256')} for name in names}
                doc(dest/'objects-pack.json',dict(schema='c4-on1-recovered-objects-v1',cell_id=CELL,
                                   tag=TAG,new_measurement=False,formal_acceptance=False,archive=recover.ref(archive),check=check,
                                   original_finished=recover.ref(old),folded_finished=recover.ref(worker/recover.OBS/'finished.json'),
                                   stream=self.fold_receipt['stream'],retained_legacy_members=legacy,
                                   legacy_manifest=dict(bytes=20_430_785,sha256='d'*64)))
                retained = self.d/'coordinator-retained'
                shutil.copytree(dest,retained)
                self.package = recover.ref(retained/'objects-pack.json')
                self.retained = doc(retained/'retained.json',dict(archive=recover.ref(retained/'objects.tar.gz'),
                                    check=recover.ref(retained/'objects-check.json'),worker_receipt=self.package,
                                    archived_only=True,formal_acceptance=False))
            else:
                shutil.copytree(worker,self.trial/'raw'/role)
                inv_roles[role] = dict(archive=inventory(archive),archive_check=check,
                                       closure=inventory(self.trial/'raw'/role/recover.CLOSE))
        bounds = {}
        for role,ids in bound.items():
            path = self.cont/'failure-boundaries-1'/(role+'-stdout')
            bounds[role]=doc(path,dict(original_processes=ids,safe_for_release=True,live=[],unknown_startup_directories=[],
                                       slot='',mounts=[]))
            doc(path.with_name(role+'-exit.json'),dict(exit_code=0))
        doc(self.trial/'observers.json',observed)
        doc(self.trial/'cohort.json',dict(purpose=PURPOSE,observers=observed,metric_contract=metric,workload_contract=workload))
        co = doc(self.trial/'cohort/result.json',dict(purpose=PURPOSE,passed=True,workload_passed=True,
                 telemetry_live_check_passed=True,performance_accepted=False,automatic_retry=False,agents=1,ready_workers=1,
                 completed_workers=1,completed_jobs=1,coordinator_makespan_seconds=10,
                 metric_contract_sha256=metric['sha256'],workload_contract_sha256=workload['sha256']))
        dr = doc(self.trial/'drain-timing.json',dict(passed=True,cell_id=CELL,start_ns=1,end_ns=1000000001,seconds=1))
        client = self.trial/'raw/client/q'/TAG
        self.inv = self.d/'historical.json'
        doc(self.inv,dict(schema='c4-on1-retained-input-inventory-v1',cell_id=CELL,original_trial=str(self.trial),
                          original_finished_exists=False,accepted=False,roles=inv_roles,original_trial_exit=trial_exit,
                          late_commands={f'{n:04}':inventory(self.trial/'commands'/f'{n:04}'/'exit.json') for n in range(28,33)},
                          failure=inventory(command/'stderr'),post_failure_boundaries=bounds,
                          checks=dict(cohort=co,drain=dr,cold_child=inventory(client/'verify/result.json'),
                                      cold_parent=inventory(client/'verify/parent.json'),native_exit=inventory(client/'exit.json'),
                                      verify_exit=inventory(client/'verify-exit.json'))))

    def worker(self, worker,role,identity,metric):
        obs = worker/recover.OBS
        spec = doc(worker/recover.OBS_SPEC/'observer.json',dict(role=role,metric_contract_sha256=metric['sha256']))
        if role=='objects':
            for name in ('service-identities.json','collector-probe.txt','rustfs-probe.txt'):
                raw(worker/recover.OBS_SPEC/name,'original source')
        remote = dict(path=recover.ROLE_ROOTS[role]+'/'+recover.OBS_SPEC+'/observer.json',sha256=spec['sha256'])
        doc(obs/'ready.json',dict(passed=True,spec=remote,metric_contract_sha256=metric['sha256'],process=identity))

        raw(obs/'collector.log','')
        history = []
        marks = []
        for i,label in enumerate(recover.FROZEN_MARKS):
            d = obs/f'sample-{i:06}'
            raw(d/'proc-stat.txt',str(i))
            row = dict(index=i,passed=True,errors=[],monotonic_start_ns=5_000_000_000*i+1,monotonic_end_ns=5_000_000_000*i+2)
            sample = doc(d/'sample.json',dict(**row,files={'proc-stat.txt':{
                         k:inventory(d/'proc-stat.txt')[k] for k in ('bytes','sha256')}}))
            history.append(dict(**row,sample_sha256=sample['sha256']))
            mark = dict(label=label,sample_index=i,monotonic_ns=row['monotonic_end_ns']+1)
            doc(obs/f'mark-{i+1:04}.json',mark)
            marks.append(mark)
        doc(obs/'finished.json',dict(passed=True,spec_sha256=spec['sha256'],metric_contract_sha256=metric['sha256'],
                                     samples=len(history),history=history,marks=marks))
        identities = [identity]
        if role=='client':
            identities += [process(41),process(42)]
        closure = dict(passed=True,cell_id=CELL,all_exited=True,all_unmounted=True,processes=identities)
        if role=='client':
            q = worker/'q'/TAG
            debt = []
            for i in range(2):
                at = f'2026-11-12T02:00:0{i}+00:00'
                name = f'sample-{i}.json'
                p = doc(q/'debt'/name,dict(passed=True,cell_id=CELL,at=at,pending_upload_bytes=0,
                                           pending_upload_operations=0,native_gauges={'pending':0}))
                debt.append(dict(evidence=dict(path=f'/srv/ax-client/q/{TAG}/debt/{name}',sha256=p['sha256']),
                                 observed_at=at))
            req = doc(q/'closure-request.json',dict(schema='suite-closure-request-v1',cell_id=CELL,
                                                    debt_samples=debt,mount_points=[self.asset+'/mnt']))
            closure.update(schema='suite-closure-v1',debt_samples=debt,pending_upload_operations=0,pending_upload_bytes=0,
                           request=dict(path=f'/srv/ax-client/q/{TAG}/closure-request.json',sha256=req['sha256']),
                           mount_points=[dict(point=self.asset+'/mnt',mounts=[])])
        doc(worker/recover.CLOSE,closure)
        return marks,closure,dict(id=POOL+'-'+role,directory=recover.ROLE_ROOTS[role]+'/'+recover.OBS,spec=remote)

    def client_data(self, worker):
        q = worker/'q'/TAG
        for suffix,folder,name,pid in [('', 'main','exit.json',41),('-v','verify-mount','verify-exit.json',42)]:
            identity = process(pid)
            point = self.asset+'/mnt'
            mount = dict(point=point,source='mount',mount_id=8)
            spec = doc(q/folder/'mount.reviewed.json',dict(schema='experiment-mount-v1',reviewed=True,purpose=PURPOSE,
                       anchor='a',mount_point=point,suite_binding=dict(cell_id=CELL,plan=self.plan)))
            ref = dict(path=f'/srv/ax-client/q/{TAG}/{folder}/mount.reviewed.json',sha256=spec['sha256'])
            state = worker/'m'/(TAG+suffix)
            doc(state/'ready.json',dict(passed=True,anchor='a',mount=mount,process=identity,spec=ref))
            doc(state/'process.json',dict(process=identity,spec=ref))
            doc(state/'stopping.json',dict(anchor='a',original_mount=mount,process=identity))
            stopped = dict(passed=True,exit_code=0,mount_absent=True,active_workers=[],original_mount=mount,anchor='a')
            doc(state/'stopped.json',stopped)
            doc(q/name,stopped)
        doc(q/'verify/parent.json',dict(passed=True,cell_id=CELL,full_cold_parent=False,verification_policy=c4_verify.TOUCHED,
                                        parent_identity=self.identity,expected=self.binding['source_tree']))
        doc(q/'verify/result.json',dict(passed=True,cell_id=CELL,files=self.cell['params']['files'],child_name=TAG,
                                        verification_policy=c4_verify.TOUCHED,full_reference=self.full,parent_identity=self.identity,
                                        parent_matches=True,child_matches=True,parent_identity_unchanged=True,full_cold_child=False,
                                        parent_unchanged=False,touched_paths=['d/f'],cold_verification_seconds=1,binding=self.binding))
        work = worker/'w'/TAG/'measure'
        ticks = ('create_start_ns','create_end_ns','first_read_start_ns','first_read_verified_ns','fsync_end_ns','mutation_end_ns')
        clock = doc(work/'clock.json',{k:1_000_000_000*(n+1) for n,k in enumerate(ticks)})
        value = fork_work.logical_work(self.cell['params']['files'],'native-clone')
        doc(work/'result.json',dict(**value,schema='c4-fork-work-v1',passed=True,completed=True,clock=clock,
                                     create_to_first_read_seconds=3,create_command_seconds=1,
                                     creation_fsync_seconds=1,mutation_fsync_seconds=1,wall_seconds=5))

    def request(self):
        return doc(self.d/'recover.request.json',dict(schema=recover.SCHEMA,reviewed=True,
                    original_continuation=self.cont_request,inventory=recover.ref(self.inv),objects_pack=self.package,
                    objects_retained=self.retained,plan=self.plan,off1_finished=self.off_finish,out=str(self.out)))

    def test_recovery_revalidates_real_archives_and_frozen_checks_without_reclassifying_original(self):
        historical = recover.ref(self.cont/'trial/exit.json')
        result = recover.run(self.request())
        self.assertFalse(result['new_measurement'])
        self.assertFalse(result['formal_acceptance'])
        self.assertFalse((self.trial/'finished.json').exists())
        self.assertFalse((self.cont/'result.json').exists())
        self.assertEqual(recover.ref(self.cont/'trial/exit.json'),historical)
        receipt = recover.proof(result['recovery'])
        self.assertEqual(receipt['source_original_exit_code'],1)
        self.assertEqual(receipt['original_exit'],historical)
        self.assertEqual(set(receipt['observer_audits']),set(recover.ROLES))
        self.assertEqual(receipt['observer_audits']['objects']['stream_sha256'],self.fold_receipt['stream']['sha256'])
        self.assertEqual(recover.proof(result['recovered_calibration'])['cohort'],recover.ref(self.trial/'cohort/result.json'))
        self.assertFalse((self.out/'sample.json').exists())
        with self.assertRaisesRegex(ValueError,'fresh separate'):
            recover.run(self.request())


    def test_unsigned_draft_validates_pins_and_space_without_creating_a_trial(self):
        path = Path(self.request()['path'])
        draft = recover.load(path)
        draft['reviewed'] = False
        request = doc(path,draft)
        plan = recover.run(request,validate_only=True)
        self.assertTrue(plan['valid'])
        self.assertFalse(plan['reviewed'])
        self.assertFalse(plan['output_created'])
        self.assertEqual(plan['original_trial_exit_code'],1)
        self.assertEqual(set(plan['archives']),set(recover.ROLES))
        self.assertGreater(plan['required_space']['estimated_bytes'],0)
        self.assertFalse(self.out.exists())
        with self.assertRaisesRegex(ValueError,'human-reviewed'):
            recover.run(request)
        self.assertFalse(self.out.exists())

    def test_rejects_an_earlier_workload_failure_and_leaves_no_output(self):
        doc(self.trial/'commands/0004/exit.json',dict(exit_code=1))
        with self.assertRaisesRegex(ValueError,'sole failed'):
            recover.run(self.request())
        self.assertFalse(self.out.exists())

    def test_rejects_a_claimed_pass_or_missing_historical_role(self):
        original = recover.load(self.inv)
        doc(self.trial/'finished.json',dict(passed=True))
        with self.assertRaisesRegex(ValueError,'already finished'):
            recover.run(self.request())
        (self.trial/'finished.json').unlink()
        del original['post_failure_boundaries']['objects-scratch']
        doc(self.inv,original)
        with self.assertRaisesRegex(ValueError,'Missing original terminal'):
            recover.run(self.request())
        self.assertFalse(self.out.exists())

    def test_rejects_repacked_observer_detail_that_no_longer_binds_original_sample(self):
        recover.run(self.request())
        root = self.out/'raw/objects'
        detail = root/recover.OBS/'fold-receipt.ndjson'
        lines = detail.read_text().splitlines()
        row = json.loads(lines[0])
        row['members']['sample.json']='f'*64
        lines[0]=json.dumps(row)
        detail.write_text('\n'.join(lines)+'\n')
        package,row = recover.objects_input(dict(objects_pack=self.package,objects_retained=self.retained))
        entry = recover.archive_input('objects',row,self.trial)
        with self.assertRaisesRegex(ValueError,'original sample.json SHA'):
            recover.check_objects(root,package,entry[2])
