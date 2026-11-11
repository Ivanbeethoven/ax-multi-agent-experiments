"""Synthetic filesystem and service evidence; no SSH or running experiment needed."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import runpy
import socket
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO/'experiments/unified-suite/pilot-aliyun/scripts'
RUNNER = runpy.run_path(str(SCRIPTS/'retain-old-c1-calibration.py'))
NODE = runpy.run_path(str(SCRIPTS/'retain-old-c1-calibration-node.py'))
TOOL = runpy.run_path(str(SCRIPTS/'retain-calibrations.py'))


class Boundary(unittest.TestCase):
    def setUp(self):
        self.cell = {'resource_ids': ['host:i-original', 'disk:d-original']}
        self.clock = datetime.now(timezone.utc)
        self.manager = dict(LoadState='loaded', InvocationID='a'*32, ActiveState='active', SubState='exited',
                            MainPID='0', ControlPID='0', Result='success', ExecMainStatus='0')
        self.proof = dict(schema='c123-pool-a-retention-quiescence-v1', passed=True, reviewed=True,
            pool='pool-a', resource_ids=self.cell['resource_ids'], no_active_pool_lease=True, agent_slot_empty=True,
            all_workload_processes_exited=True, all_mounts_absent=True, pending_upload_bytes=0,
            pending_upload_operations=0, observed_at=self.clock.isoformat(), unit='ax-pilot-pa-latest.service',
            manager=self.manager)

    def test_stale_dirty_and_running_boundaries_fail(self):
        check = RUNNER['validate_quiescence']
        check(self.proof, self.cell, self.clock)
        with self.assertRaisesRegex(ValueError, 'stale'):
            check(self.proof, self.cell, self.clock+timedelta(minutes=16))
        with self.assertRaisesRegex(ValueError, 'zero debt'):
            check(dict(self.proof, pending_upload_operations=1), self.cell, self.clock)
        with self.assertRaisesRegex(ValueError, 'not terminal'):
            check(dict(self.proof, manager=dict(self.manager, MainPID='88', SubState='running')), self.cell, self.clock)

    def test_open_lease_stops_before_manager_and_live_a_stops_before_ssh(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); prep = base/'preparation'; prep.mkdir()
            canonical, old = base/'parallel-control-01', base/'c123-control-01'
            for root in (canonical, old):
                root.mkdir(); (root/'state.sqlite').touch()
            for name in ('exit.json', 'live.json'):
                (prep/name).write_text('{}')
            proof = dict(self.proof, source_pins=[RUNNER['ref'](prep/n) for n in ('exit.json', 'live.json')])
            receipt = prep/'quiescence.json'; receipt.write_text(json.dumps(proof))
            request = {'quiescence': RUNNER['ref'](receipt), 'lease_controls': [str(old), str(canonical)]}
            check = RUNNER['boundary']; globals_ = check.__globals__
            manager = '\n'.join(f'{k}={v}' for k, v in self.manager.items())
            active = 'ax-pilot-pa-next.service loaded active running next A job\n'
            with patch.dict(globals_, PILOT=base, PREP=prep, CANONICAL=canonical, ORIGINAL=old):
                with patch.object(globals_['subprocess'], 'check_output') as command:
                    with self.assertRaisesRegex(ValueError, 'leased'):
                        check(request, self.cell, {}, lambda *args: ['lease'])
                    command.assert_not_called()
                with patch.object(globals_['subprocess'], 'check_output', side_effect=[manager, active]) as command:
                    with self.assertRaisesRegex(ValueError, 'next.service'):
                        check(request, self.cell, {}, lambda *args: [])
                    self.assertTrue(all(c.args[0][0]=='systemctl' for c in command.call_args_list))

    def test_b_requires_explicit_allowlist(self):
        units = ('ax-pilot-pa-closed.service loaded active exited old A\n'
                 'ax-pilot-pb-live.service loaded active running B\n')
        self.assertEqual(RUNNER['live_units'](units, set()), ['ax-pilot-pb-live.service'])
        self.assertEqual(RUNNER['live_units'](units, {'ax-pilot-pb-live.service'}), [])

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
