"""Retaining closed evidence must not interfere with an independent pool."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


FILE = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/retain-calibrations.py'
spec = importlib.util.spec_from_file_location('calibration_pool_retention', FILE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
CLOSED = 'LoadState=loaded\nActiveState=inactive\nSubState=dead\nExecMainStatus=0\nExecMainCode=1\n'


class PoolRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.a = dict(id='a1', pool='pool-a', resource_ids=['host:a', 'disk:a'])
        self.a2 = dict(id='a2', pool='pool-a', resource_ids=['host:a', 'disk:a'])
        self.b = dict(id='b1', pool='pool-b', resource_ids=['host:b', 'disk:b'])
        self.shared = dict(id='b-shared', pool='pool-b', resource_ids=['host:a', 'disk:b'])
        self.plan = dict(cells=[self.a, self.a2, self.b, self.shared], config=dict(clusters=[
            dict(id='pool-a', resource_ids=self.a['resource_ids']),
            dict(id='pool-b', resource_ids=self.b['resource_ids'])]))
        with sqlite3.connect(self.root/'state.sqlite') as db:
            db.execute('CREATE TABLE cell_leases (cell_id TEXT, pool TEXT)')
            db.execute('CREATE TABLE cell_resources (cell_id TEXT, id TEXT)')
            for cell in self.plan['cells']:
                db.executemany('INSERT INTO cell_resources VALUES (?, ?)',
                               [(cell['id'], resource) for resource in cell['resource_ids']])

    def tearDown(self):
        self.tmp.cleanup()

    def started(self, cell_id, value=None, complete=False):
        run = self.root/'one-cell-runs'/cell_id; run.mkdir(parents=True)
        (run/'started.json').write_text(json.dumps(dict(cell_id=cell_id) if value is None else value))
        if complete: (run/'result.json').write_text('{}')

    def lease(self, cell):
        with sqlite3.connect(self.root/'state.sqlite') as db:
            db.execute('INSERT INTO cell_leases VALUES (?, ?)', (cell['id'], cell['pool']))

    def idle(self, cell=None, plan=None):
        with patch.object(module.subprocess, 'check_output', return_value=CLOSED):
            return module.idle(self.root, 'ax-pilot-check', cell or self.a, plan or self.plan)

    def test_independent_pool_lease_and_unfinished_run_do_not_block(self):
        self.lease(self.b); self.started(self.b['id'])
        self.assertEqual(self.idle()['ActiveState'], 'inactive')

    def test_remain_after_exit_requires_a_successful_exit_and_no_main_pid(self):
        raw = ('LoadState=loaded\nActiveState=active\nSubState=exited\nExecMainStatus=0\n'
               'ExecMainCode=1\nMainPID=0\nResult=success\nRemainAfterExit=yes\n')
        with patch.object(module.subprocess, 'check_output', return_value=raw):
            self.assertEqual(module.idle(self.root, 'ax-pilot-check', self.a, self.plan)['SubState'], 'exited')
        for change in (raw.replace('MainPID=0','MainPID=123'), raw.replace('Result=success','Result=exit-code')):
            with patch.object(module.subprocess, 'check_output', return_value=change):
                with self.assertRaisesRegex(ValueError, 'not exited normally'):
                    module.idle(self.root, 'ax-pilot-check', self.a, self.plan)

    def test_same_pool_and_shared_resource_leases_block(self):
        for cell in (self.a2, self.shared):
            with self.subTest(cell=cell['id']):
                self.lease(cell)
                with self.assertRaisesRegex(ValueError, 'remains reserved'):
                    self.idle()
                with sqlite3.connect(self.root/'state.sqlite') as db:
                    db.execute('DELETE FROM cell_leases')

    def test_same_pool_and_shared_resource_unfinished_runs_block(self):
        for cell in (self.a2, self.shared):
            with self.subTest(cell=cell['id']):
                self.started(cell['id'])
                with self.assertRaisesRegex(ValueError, 'remains unfinished'):
                    self.idle()
                shutil.rmtree(self.root/'one-cell-runs'/cell['id'])

    def test_unknown_started_records_cannot_be_silently_skipped(self):
        for value, complete in (({}, False), ({'cell_id':'unknown'}, False),
                                ({'cell_id':'unknown'}, True)):
            with self.subTest(value=value, complete=complete):
                self.started('unknown', value, complete)
                with self.assertRaisesRegex(ValueError, 'Unknown cell'):
                    self.idle()
                shutil.rmtree(self.root/'one-cell-runs/unknown')

    def test_packed_calibration_only_requires_exact_terminal_request(self):
        run = self.root/'one-cell-runs/packed-legacy'
        request = self.root/'packed.json'
        spec = dict(schema='packed-c3-one-cell-pipeline-v1', reviewed=True,
                    out=str(run), cell_id=self.a2['id'], next_cell=False, automatic_retry=False)

        def pin():
            request.write_text(json.dumps(spec))
            return dict(path=str(request), sha256=hashlib.sha256(request.read_bytes()).hexdigest())

        reference = pin()
        self.started('packed-legacy', dict(request=reference))
        with self.assertRaisesRegex(ValueError, 'Unknown cell'):
            self.idle()
        (run/'failed.json').write_text(json.dumps(dict(request=reference, passed=False, automatic_retry=False)))
        self.assertEqual(self.idle()['ActiveState'], 'inactive')
        spec['out'] = str(run.with_name('another-run'))
        wrong = pin()
        (run/'started.json').write_text(json.dumps(dict(request=wrong)))
        (run/'failed.json').write_text(json.dumps(dict(request=wrong, passed=False, automatic_retry=False)))
        with self.assertRaisesRegex(ValueError, 'Unknown cell'):
            self.idle()

    def test_wrong_started_pool_and_modified_selected_cell_are_rejected(self):
        self.started(self.b['id'], dict(cell_id=self.b['id'], pool='pool-a'))
        with self.assertRaisesRegex(ValueError, 'pool differs'):
            self.idle()
        changed = dict(self.a, resource_ids=['host:invented'])
        with self.assertRaisesRegex(ValueError, 'differs from the frozen plan'):
            self.idle(changed)

    def test_observer_prefix_belongs_to_exact_pool_without_duplicates(self):
        cohort = dict(observers=[dict(id='pool-b-'+role) for role in module.ROOTS])
        self.assertEqual(set(module.observer_roles(cohort, 'pool-b')), set(module.ROOTS))
        for observers in ([dict(id='pool-a-client')],
                          [dict(id='pool-b-client'), dict(id='pool-b-client')]):
            with self.assertRaisesRegex(ValueError, 'uniquely belong'):
                module.observer_roles(dict(observers=observers), 'pool-b')

    def test_dynamic_ssh_scp_and_original_archives_for_all_six_trials(self):
        """Exercise routing with real pinned local evidence and real tar checks."""
        cell = dict(self.b, observation_marks=['measure-start', 'measure-end'])
        plan = deepcopy(self.plan); plan['cells'][2] = cell
        (self.root/'suite').mkdir(); (self.root/'suite/plan.json').write_text(json.dumps(plan))
        def pin(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
            return dict(path=str(path), sha256=module.digest(path))
        contract = pin(self.root/'contract.json', dict(purpose='test-only pinned contract'))
        closing = pin(self.root/'closing.json', dict(outcome='complete', cell_id=cell['id'],
            retention=dict(root=str(self.root), manifest=contract)))
        completed = dict(closing=closing)
        completed_ref = pin(self.root/'completed.json', completed)
        trials = []
        for index, mode in enumerate(('off', 'on', 'on', 'off', 'off', 'on')):
            tag = 'cal-route-'+str(index); original = self.root/'calibration'/tag
            peer = dict(repo='/old-worker-code-without-pilot-pools', pins=[])
            cohort = dict(passed=True, workers=[dict(peer=peer, spec=contract)],
                observers=[dict(id='pool-b-'+role, peer=peer, spec=contract)
                           for role in module.ROOTS] if mode=='on' else [])
            cohort.update({key:contract for key in ('inventory', 'metric_contract', 'workload_contract',
                'allocation_qualification', 'hardware_qualification', 'input_qualification',
                'filesystem_qualification')})
            cohort_ref = pin(original/'cohort.json', cohort)
            pin(original/'started.json', dict(cell_id=cell['id']))
            pin(original/'finished.json', dict(passed=True, cohort=cohort_ref))
            trials.append(dict(mode=mode, attempt=tag, out=str(original)))
        request = dict(cell_id=cell['id'], tools={}, trials=trials)
        request_ref = pin(self.root/'request.json', request)
        raw = self.root/'worker-archive-raw'; raw.mkdir(); (raw/'sample.txt').write_bytes(b'original bytes')
        archive = module.archive_check.pack(raw, ['sample.txt'], self.root/'worker.tar.gz', cell['id'], 'test')
        commands = []; configs = []
        def run(argv, **kwargs):
            commands.append(argv)
            if argv[0]=='ssh':
                import base64
                import shlex
                config = json.loads(base64.b64decode(shlex.split(argv[-1])[-1])); configs.append(config)
                kwargs['stdout'].write(json.dumps(dict(archive=archive)).encode())
            elif argv[0]=='scp':
                shutil.copyfile(argv[-2].split(':', 1)[1], argv[-1])
            else: self.fail('Unexpected command: '+str(argv))
            return subprocess.CompletedProcess(argv, 0)
        out = self.root/'retained-calibrations/route-test'
        args = SimpleNamespace(unit='ax-pilot-route', out=str(out), request=str(request_ref['path']))
        with patch.object(module, 'idle', return_value=dict(ActiveState='inactive')), \
                patch.object(module.subprocess, 'check_output', return_value='') as check, \
                patch.object(module.subprocess, 'run', side_effect=run), \
                patch.object(module.experiment_step, 'verify'), patch('builtins.print'):
            module.retain(args, request_ref, request, completed_ref, completed, self.root, cell, plan)
        self.assertEqual(check.call_args.args[0][3], 'pool-b-client')
        self.assertEqual(len(configs), 12)
        self.assertEqual({config['pool'] for config in configs}, {'pool-b'})
        self.assertTrue(all(argv[3].startswith('pool-b-') for argv in commands))
        self.assertEqual(sum(argv[0]=='scp' for argv in commands), 12)
        result = json.loads((out/'result.json').read_text())
        self.assertTrue(result['passed']); self.assertEqual(len(result['trials']), 6)
        final = json.loads((out.parent/'route-test-archive-check.json').read_text())
        self.assertTrue(module.archive_check.verify(final['archive'], final['sha256'])['passed'])


if __name__=='__main__': unittest.main()
