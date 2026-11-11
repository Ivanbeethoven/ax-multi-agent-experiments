"""Synthetic filesystem and service evidence; no SSH or running experiment needed."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import runpy
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from bench.runtime.pilot_pools import resource_guard


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO/'experiments/unified-suite/pilot-aliyun/scripts'
RUNNER = runpy.run_path(str(SCRIPTS/'retain-old-c1-calibration.py'))
NODE = runpy.run_path(str(SCRIPTS/'retain-old-c1-calibration-node.py'))
TOOL = runpy.run_path(str(SCRIPTS/'retain-calibrations.py'))


class Boundary(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.prep = self.base/'preparation'
        self.prep.mkdir()
        self.canonical = self.base/'parallel-control-01'
        self.old = self.base/'c123-control-01'
        self.cgroup = self.base/'cgroup'
        for root in (self.canonical, self.old):
            root.mkdir()
            with sqlite3.connect(root/'state.sqlite') as db:
                db.execute('CREATE TABLE cell_leases (pool TEXT, cell_id TEXT)')
        self.cell = {'resource_ids': ['host:i-original', 'disk:d-original']}
        self.clock = datetime.now(timezone.utc)
        self.manager = dict(LoadState='loaded', InvocationID='a'*32, ActiveState='active', SubState='exited',
                            MainPID='0', ControlPID='0', Result='success', ExecMainStatus='0')
        self.proof = dict(schema='c123-pool-a-retention-quiescence-v1', passed=True, reviewed=True,
            pool='pool-a', resource_ids=self.cell['resource_ids'], no_active_pool_lease=True, agent_slot_empty=True,
            all_workload_processes_exited=True, all_mounts_absent=True, pending_upload_bytes=0,
            pending_upload_operations=0, observed_at=self.clock.isoformat(), unit='ax-pilot-pa-latest.service',
            manager=self.manager)
        for name in ('exit.json', 'live.json'):
            (self.prep/name).write_text('{}')
        self.proof['source_pins'] = [RUNNER['ref'](self.prep/n) for n in ('exit.json', 'live.json')]
        receipt = self.prep/'quiescence.json'
        receipt.write_text(json.dumps(self.proof))
        self.request = {'quiescence': RUNNER['ref'](receipt), 'lease_controls': [str(self.old), str(self.canonical)],
                        'resident_units': {}}
        self.states = {self.proof['unit']: dict(self.manager, ControlGroup='')}
        self.jobs = ''
        self.assets, self.relay = RUNNER['ASSETS'], RUNNER['RELAY']
        self.script = self.base/'session-relay.py'
        self.script.write_text('# reviewed relay\n')
        self.resident(self.assets, ['/usr/bin/python3', '-m', 'http.server', '18080',
            '--bind', '10.20.0.10', '--directory', str(self.base/'assets')])
        self.resident(self.relay, ['/usr/bin/python3', '-B', str(self.script), '--port', '18443'], self.script)
        override = patch.dict(RUNNER['boundary'].__globals__, PILOT=self.base, PREP=self.prep,
                              CANONICAL=self.canonical, ORIGINAL=self.old, CGROUP_ROOT=self.cgroup)
        override.start()
        self.addCleanup(override.stop)

    def resident(self, unit, argv, script=None):
        fragment = self.base/unit
        fragment.write_text('[Service]\nExecStart='+' '.join(argv)+'\n')
        # Keep the expected hash independent of the implementation under test.
        command_sha = hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode()).hexdigest()
        self.request['resident_units'][unit] = dict(invocation_id='b'*32, fragment=RUNNER['ref'](fragment),
            drop_ins=[], exec_start_sha256=command_sha, script=RUNNER['ref'](script) if script else None)
        self.states[unit] = dict(LoadState='loaded', ActiveState='active', SubState='running', MainPID='42',
            ControlPID='0', ControlGroup='/system.slice/'+unit, InvocationID='b'*32, FragmentPath=str(fragment),
            DropInPaths='', NeedDaemonReload='no', ExecStart='{ path='+argv[0]+' ; argv[]='+' '.join(argv)+
            ' ; ignore_errors=no ; start_time=[Wed 2026-11-11] ; stop_time=[n/a] ; pid=42 ; code=(null) ; status=0/0 }')

    def systemctl(self, argv, **kwargs):
        self.assertEqual(argv[0], 'systemctl')
        if argv[1] == 'list-units':
            return '\n'.join(f"{u} {v['LoadState']} {v['ActiveState']} {v['SubState']} pilot" for u, v in self.states.items())
        if argv[1] == 'list-jobs':
            return self.jobs
        self.assertEqual(argv[1], 'show')
        return '\n'.join(f'{arg[2:]}={self.states[argv[2]][arg[2:]]}' for arg in argv[3:] if arg.startswith('-p'))

    def check(self):
        with patch.object(RUNNER['subprocess'], 'check_output', side_effect=self.systemctl):
            return RUNNER['boundary'](self.request, self.cell)

    def test_stale_dirty_and_running_boundaries_fail(self):
        check = RUNNER['validate_quiescence']
        check(self.proof, self.cell, self.clock)
        with self.assertRaisesRegex(ValueError, 'stale'):
            check(self.proof, self.cell, self.clock+timedelta(minutes=16))
        with self.assertRaisesRegex(ValueError, 'zero debt'):
            check(dict(self.proof, pending_upload_operations=1), self.cell, self.clock)
        with self.assertRaisesRegex(ValueError, 'not terminal'):
            check(dict(self.proof, manager=dict(self.manager, MainPID='88', SubState='running')), self.cell, self.clock)

    def test_complete_database_inventory_and_all_leases_gate_before_manager(self):
        extra = self.base/'fork-control-01'
        extra.mkdir()
        with sqlite3.connect(extra/'state.sqlite') as db:
            db.execute('CREATE TABLE cell_leases (pool TEXT, cell_id TEXT)')
        with patch.object(RUNNER['subprocess'], 'check_output') as command:
            with self.assertRaisesRegex(ValueError, 'inventory differs'):
                RUNNER['boundary'](self.request, self.cell)
            self.request['lease_controls'].append(str(extra))
            for root, pool in ((self.canonical, 'pool-a'), (extra, 'pool-b'), (self.old, 'unknown')):
                with self.subTest(root=root, pool=pool):
                    with sqlite3.connect(root/'state.sqlite') as db:
                        db.execute('INSERT INTO cell_leases VALUES (?, ?)', (pool, 'busy'))
                    with self.assertRaisesRegex(ValueError, f'leased.*{pool}'):
                        RUNNER['boundary'](self.request, self.cell)
                    with sqlite3.connect(root/'state.sqlite') as db:
                        db.execute('DELETE FROM cell_leases')
            command.assert_not_called()
        self.assertEqual(self.check()['lease_controls'], sorted([str(extra), str(self.old), str(self.canonical)]))

    def test_only_exact_reviewed_residents_may_stay_running(self):
        self.assertEqual(set(self.check()['resident_units']), {self.assets, self.relay})
        for unit in ('ax-pilot-pa-next.service', 'ax-pilot-b-c2-jfs-runtime15-01.service',
                     'ax-pilot-b-c2-to-c1-watch-01.service', 'ax-pilot-pb-legacy.service',
                     'ax-pilot-assets-copy.service'):
            with self.subTest(unit=unit):
                self.states[unit] = dict(self.states[self.assets])
                with self.assertRaisesRegex(ValueError, f'Pilot service still active: {unit}'):
                    self.check()
                del self.states[unit]
        self.request['allow_live_b_units'] = ['ax-pilot-pb-legacy.service']
        with self.assertRaisesRegex(ValueError, 'exact asset and C4 relay'):
            self.check()

    def test_resident_definition_script_and_invocation_are_pinned(self):
        state = self.states[self.relay]
        for key, value, expected in (('InvocationID', 'c'*32, 'invocation'),
                ('NeedDaemonReload', 'yes', 'loaded definition'),
                ('ExecStart', state['ExecStart'].replace('18443', '18444'), 'command changed'),
                ('DropInPaths', '/etc/systemd/other.conf', 'drop-in set changed')):
            with self.subTest(key=key), patch.dict(state, {key: value}):
                with self.assertRaisesRegex(ValueError, expected):
                    self.check()
        fragment = Path(state['FragmentPath'])
        original = fragment.read_text()
        fragment.write_text(original+'# replaced')
        with self.assertRaisesRegex(ValueError, 'Changed or misplaced input'):
            self.check()
        fragment.write_text(original)
        self.script.write_text('# replaced relay\n')
        with self.assertRaisesRegex(ValueError, 'Changed or misplaced input'):
            self.check()

    def test_terminal_name_is_insufficient_if_pid_cgroup_or_manager_job_remains(self):
        unit = 'ax-pilot-b-c2-to-c1-watch-01.service'
        group = '/system.slice/'+unit
        self.states[unit] = dict(self.states[self.proof['unit']], ControlGroup=group)
        with patch.dict(self.states[unit], MainPID='42'):
            with self.assertRaisesRegex(ValueError, 'live PID'):
                self.check()
        path = self.cgroup/group.lstrip('/')
        path.mkdir(parents=True)
        events = path/'cgroup.events'
        events.write_text('populated 1\nfrozen 0\n')
        with self.assertRaisesRegex(ValueError, 'cgroup is populated'):
            self.check()
        events.write_text('populated 0\nfrozen 0\n')
        self.jobs = '12 '+unit+' start waiting\n'
        with self.assertRaisesRegex(ValueError, 'Pending pilot manager job'):
            self.check()
        self.jobs = '13 systemd-timesyncd.service start waiting\n'
        self.assertEqual(set(self.check()['resident_units']), {self.assets, self.relay})

    def test_dual_lock_interoperates_with_existing_runner_locks_and_releases_on_contention(self):
        clusters = [{'id': 'pool-a', 'resource_ids': ['disk:d-a', 'disk:d-shared']},
                    {'id': 'pool-b', 'resource_ids': ['disk:d-b', 'disk:d-shared']}]
        a, b = [dict(pool=c['id'], resource_ids=c['resource_ids']) for c in clusters]
        extra_cell = dict(pool='pool-b', resource_ids=['disk:d-extra'])
        plan = {'config': {'clusters': clusters}, 'cells': [a, b, extra_cell]}
        resources = RUNNER['lock_resources'](plan)
        self.assertEqual(resources, ['disk:d-a', 'disk:d-b', 'disk:d-extra', 'disk:d-shared', 'pool:pool-a', 'pool:pool-b'])
        directory = self.canonical/'runner-locks'
        directory.mkdir()
        guard = RUNNER['two_pool_guard']
        with resource_guard(self.canonical, b, plan):
            with self.assertRaisesRegex(ValueError, 'owns physical resource: disk:d-b'):
                with guard(directory, resources):
                    self.fail('Archive entered while B owned its resources')
            # d-a sorts before d-b, so it must be released after partial acquisition.
            name = hashlib.sha256(b'disk:d-a').hexdigest()+'.lock'
            import fcntl
            with (directory/name).open() as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with guard(directory, resources):
            for cell in (a, b, extra_cell):
                with self.assertRaisesRegex(ValueError, 'owns physical resource'):
                    with resource_guard(self.canonical, cell, plan):
                        self.fail('Existing runner entered during archival')
        for cell in (a, b, extra_cell):
            with resource_guard(self.canonical, cell, plan):
                pass

    def test_watcher_start_between_role_packs_blocks_the_next_dispatch(self):
        request = self.prep/'c123-old-c1-retention-unit-01/request.reviewed.json'
        request.parent.mkdir()
        out = request.parent/'run'
        role_configs = {role: {'tag': 'trial'} for role in RUNNER['ROLE_ROOTS']}
        selected = {'trials': [{'node_pack': role_configs}]}
        a, b = dict(self.cell, pool='pool-a'), {'pool': 'pool-b', 'resource_ids': ['host:i-b']}
        plan = {'config': {'clusters': [dict(cell, id=cell['pool']) for cell in (a, b)]}, 'cells': [a, b]}
        (self.canonical/'runner-locks').mkdir()
        coordinator_file = self.old/'cohort.json'
        coordinator_file.write_bytes(b'{}')
        members = {'cohort.json': {'bytes': 2, 'sha256': NODE['sha'](coordinator_file)}}
        fleet = self.canonical/'fleet.json'
        fleet.write_bytes(b'{}')
        s = dict(self.request, draft={'sha256': hashlib.sha256(b'{}').hexdigest()}, cell_id='old-c1',
                 fleet=RUNNER['ref'](fleet))
        script = self.base/'dispatch.py'
        script.write_text('# worker script never executed\n')
        calls = []

        def dispatch(ssh, role, worker, envelope, directory):
            phase = envelope['phase']
            calls.append((phase, role))
            if phase == 'pack':
                self.states['ax-pilot-b-c2-to-c1-watch-01.service'] = dict(self.states[self.assets])
            return dict(bytes_written=0, scratch='/var/tmp/'+request.parent.name,
                        sources=[dict(tag='trial', bound_bytes=128)])

        runner = RUNNER['run']
        globals_ = runner.__globals__
        collect = Mock(return_value=[])
        patched = dict(
            load_input=lambda *args: (s, {'node_retainer': RUNNER['ref'](script)}, selected,
                REPO, plan, a, self.base/'ssh.conf', script, {'build': lambda *a: {}}, NODE, {}),
            identities=lambda *args: {role: {} for role in role_configs},
            coordinator_manifest=lambda *args: members, disk_room=lambda *args: {},
            dispatch=dispatch, collect=collect)
        with patch.dict(globals_, patched), patch.object(globals_['sys'], 'path', globals_['sys'].path[:]), \
                patch.object(RUNNER['subprocess'], 'check_output', side_effect=self.systemctl):
            with self.assertRaisesRegex(ValueError, 'Pilot service still active: ax-pilot-b-c2-to-c1-watch-01.service'):
                runner({'path': str(request), 'sha256': 'reviewed'}, out, REPO/'runner.py')
        self.assertEqual(calls, [('preflight', role) for role in role_configs]+[('pack', 'client')])
        collect.assert_called_once()
        self.assertFalse((out/'result.json').exists())
        self.assertFalse((request.parent/'calibrations.tar.gz').exists())
        failure = json.loads((out/'failed.json').read_bytes())
        self.assertTrue(failure['remote_pack_attempted'])
        self.assertTrue(failure['recheck_worker_quiescence_before_next_activity'])
        self.assertFalse(failure['automatic_retry'])
        for cell in (a, b):
            with resource_guard(self.canonical, cell, plan):
                pass  # Even this mid-archive failure must drop the two pool locks.

    def test_coordinator_archive_must_match_the_frozen_member_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); src = base/'original'; src.mkdir()
            evidence = src/'cohort.json'; evidence.write_text('{}')
            package = base/'closed.tar.gz'; tool = TOOL['archive_check']
            tool.pack(src, ['cohort.json'], package, 'original', 'controller')
            good = {'cohort.json': {'bytes': 2, 'sha256': NODE['sha'](evidence)}}
            RUNNER['bind_packed_manifest'](package, good, tool.MANIFEST)
            with self.assertRaisesRegex(ValueError, 'differ from the frozen draft'):
                RUNNER['bind_packed_manifest'](package, {'cohort.json': {'bytes': 2, 'sha256': 'f'*64}}, tool.MANIFEST)


class Worker(unittest.TestCase):
    def fixture(self, base):
        root, scratch = base/'data', base/'scratch'; root.mkdir(); scratch.mkdir()
        runtime = root/'code-18'; old = root/'code-01'; old.mkdir()
        orig = old/'source.py'; orig.write_text('old source')
        runtime_files = {}
        for name in NODE['RUNTIME_FILES'] | {NODE['TOOL']}:
            path = runtime/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(name)
            runtime_files[name] = NODE['sha'](path)
        tool_sha = runtime_files.pop(NODE['TOOL'])
        configs = []
        for i in range(1, 4):
            tag = f'cal-j-rd-on{i}'
            obs = root/'q'/(tag+'-observer')/'observer.json'; obs.parent.mkdir(parents=True); obs.write_text('{}')
            raw = root/'o'/tag; raw.mkdir(parents=True)
            (raw/'ready.json').write_text('{"process":{"pid":99999999}}')
            (raw/'sample.json').write_text(f'original data {i}')
            configs.append(dict(role='objects', pool='pool-a', tag=tag, mode='on', root=str(root), code=str(old),
                cell_id='old', bundle='c123-cal-recovery-old', code_pins=[dict(path=str(orig), sha256=NODE['sha'](orig))],
                worker_spec=None, observer_sha256=NODE['sha'](obs), marks=['measure-start']))
        envelope = dict(schema='c123-role-retention-dispatch-v1', reviewed=True, role='objects',
            run='c123-old-c1-retention-unit-01', cell_id='old', retention_code_dir='code-18',
            identity={}, runtime_files=runtime_files, retainer_sha256=tool_sha, configs=configs, phase='preflight')
        return root, scratch, envelope

    def test_scratch_must_be_on_a_different_device_and_have_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); root = base/'data'; scratch = base/'scratch'; root.mkdir(); scratch.mkdir()
            identity = dict(data_device=root.stat().st_dev, hostname=socket.gethostname(),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
            with patch.dict(NODE['check_identity'].__globals__, SCRATCH=scratch):
                with patch.object(NODE['os'].path, 'ismount', return_value=True):
                    with self.assertRaisesRegex(ValueError, 'different filesystem'):
                        NODE['check_identity'](root, identity)
                with patch.dict(NODE['scratch_room'].__globals__, space=lambda path: (64*1024**2, 1024)):
                    with self.assertRaisesRegex(ValueError, 'lacks reviewed room'):
                        NODE['scratch_room'](root, 10**6, 3)

    def test_mutated_input_rejected_and_direct_pack_preserves_originals(self):
        def exercise(mutate):
            with tempfile.TemporaryDirectory() as tmp:
                root, scratch, envelope = self.fixture(Path(tmp).resolve())
                actual = TOOL['node_pack'].__globals__
                worker = NODE['work']
                with patch.dict(actual, ROOTS={'objects': str(root)}, alive=lambda identity: False,
                                audit=lambda *args: {'passed': True}):
                    with patch.dict(worker.__globals__, ROOTS={'objects': str(root)}, SCRATCH=scratch,
                                    check_identity=lambda *args: {'same_host': True}, scratch_room=lambda *args: {},
                                    load_retainer=lambda *args: TOOL):
                        preflight = worker(envelope)
                        self.assertEqual(preflight['bytes_written'], 0)
                        self.assertEqual(list(scratch.iterdir()), [])
                        changed = root/'o/cal-j-rd-on1/sample.json'
                        if mutate:
                            changed.write_text('changed')
                            with self.assertRaisesRegex(ValueError, 'changed after capacity preflight'):
                                worker(dict(envelope, phase='pack', preflight=preflight))
                            self.assertEqual(list(scratch.iterdir()), [])
                            return
                        packed = worker(dict(envelope, phase='pack', preflight=preflight))
                        self.assertFalse(packed['raw_staging_created'])
                        self.assertEqual(len(packed['archives']), 3)
                        self.assertEqual(changed.read_text(), 'original data 1')
                        self.assertFalse(list((root/'retained-calibrations').rglob('raw')))
                        for a in packed['archives']:
                            archive = a['archive']; p = Path(archive['archive'])
                            self.assertEqual(p.parent.parent, scratch)
                            self.assertTrue(TOOL['archive_check'].verify(p, archive['sha256'])['passed'])
        exercise(True)
        exercise(False)


if __name__ == '__main__':
    unittest.main()
