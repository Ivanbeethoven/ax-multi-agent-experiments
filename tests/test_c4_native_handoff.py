"""Offline checks for the C4 native handoff draft and its outer driver.

No client, backend, relay, model, mount or cgroup is touched. The driver's
external executor is injected; the default executor must only refuse.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from bench.runtime.common import digest, write_new
from bench.runtime.pilot_pools import pool_inventory, resource_guard, resource_ids
from bench.suite_catalog import template
from bench.suite_plan import build, check_plan

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / 'experiments/unified-suite/pilot-aliyun/scripts'
DRAFT = runpy.run_path(str(SCRIPTS / 'draft-c4-native-handoff.py'))
DRIVER = runpy.run_path(str(SCRIPTS / 'run-c4-native-handoff.py'))

SESSION_ID = 'ses_e39418e40ffer6Ar6szDXn1kx2'
OPENCODE_SHA = '025e625d7ee316f9ce246056997551869b72e4bffa778dd3ad8248ba0e014233'
FIRST_BODY_SHA = '3a792e6a0068fc3e73332289cb8be92e16315ae8cb4a2c496ed7d2d630631537'
WORKSPACE = '/srv/ax-pilot-session/workspace'
SESSION_STATE = {'workspace': WORKSPACE, 'project': WORKSPACE + '/repo',
                 'config': WORKSPACE + '/config/opencode.json',
                 'xdg': {name: WORKSPACE + '/source/' + name for name in ('data', 'state', 'cache', 'config')}}
ANCHOR = {'volume': 'workspace-native-v2', 'workspace': 'ws-c4-native-handoff'}


def native_export():
    return {'info': {'id': SESSION_ID},
            'messages': [{'info': {'id': 'msg_1'}, 'parts': [{'type': 'text', 'text': 'prepare'}]},
                         {'info': {'id': 'msg_2'},
                          'parts': [{'type': 'tool', 'callID': 'call_1', 'state': {'status': 'completed'}}]}]}


def handoff_result(**overrides):
    names = ('quiesce', 'source-stop', 'lease_check', 'checkpoint-transfer-check',
             'target-start', 'restore', 'resume', 'export_after')
    phases = {name: {'start_ns': 1000 + index * 10, 'end_ns': 1005 + index * 10}
              for index, name in enumerate(names)}
    value = {'passed': True, 'mode': 'move', 'session_id': SESSION_ID,
             'restore_method': 'shared-workspace-db', 'native_session_json_transferred_bytes': 0,
             'workspace_copy_bytes': 0, 'state_ready_includes_model_resume': False,
             'first_completed_tool_ack_seconds': 12.5, 'phases': phases,
             'session_validation': {'passed': True, 'session_id': SESSION_ID,
                                    'prefix_messages': 2, 'new_completed_tools': 1}}
    value.update(overrides)
    return value


def cloud_inventory():
    document = {'campaign_id': 'c4-native-handoff-offline', 'pools': []}
    for pool, subnet in (('pool-a', 1), ('pool-b', 2)):
        roles = {}
        for index, role in enumerate(('client', 'metadata', 'objects'), 10):
            roles[role] = dict(instance_id='i-' + pool + '-' + role,
                               private_ip='10.20.%d.%d' % (subnet, index),
                               hostname='ax-%s-%s' % (pool, role),
                               data_disk_ids=['d-' + pool + '-' + role])
        document['pools'].append(dict(pool_id=pool, roles=roles))
    return document


class Fixture:
    """One complete offline pin set for the draft and the driver."""

    def __init__(self, root):
        self.root = Path(root)
        self.inventory = cloud_inventory()
        self.control = self.root / 'control'
        (self.control / 'suite').mkdir(parents=True)
        (self.control / 'provenance').mkdir()
        self.inventory_path = self.put('control/provenance/inventory.json', self.inventory)
        self.plan_path = self.put('control/suite/plan.json', self.plan())
        self.export = native_export()
        self.export_sha = hashlib.sha256(
            (json.dumps(self.export, ensure_ascii=False) + '\n').encode('utf-8')).hexdigest()
        self.checkpoint_dir = self.root / 'checkpoint'
        self.checkpoint_dir.mkdir()
        self.put('checkpoint/checkpoint.json', {
            'session_id': SESSION_ID, 'session_export_sha256': self.export_sha,
            'canonical_workspace': WORKSPACE, 'canonical_project': SESSION_STATE['project'],
            'opencode_binary_sha256': OPENCODE_SHA, 'source_process_exited': True,
            'sqlite_quick_check': 'ok', 'contains_provider_master_key': False,
            'resume_prompt': 'offline fixture only',
            'expected_final_sha256': '0' * 64})
        self.put('checkpoint/source-tree.json', {
            'repo/checkpoint.txt': {'type': 'file', 'mode': 0o644, 'bytes': 6,
                                    'sha256': '0' * 64},
            'config/opencode.json': {'type': 'file', 'mode': 0o600, 'bytes': 10,
                                     'sha256': '1' * 64}})
        self.receipt_path = self.put('receipt.json', {
            'passed': True, 'checkpoint_sha256': digest(self.checkpoint_dir / 'checkpoint.json'),
            'session_id': SESSION_ID, 'opencode_sha256': OPENCODE_SHA,
            'contains_provider_master_key': False, 'contains_active_relay_key': False})
        self.release_path = self.put('release.json', {'opencode_sha256': OPENCODE_SHA})
        self.source_peer = 'pool-a-client'
        self.target_peer = 'pool-b-client'
        self.source_cohort = self.put('cohort/source.json', self.cohort(self.source_peer))
        self.target_cohort = self.put('cohort/target.json', self.cohort(self.target_peer))
        self.spec_path = self.put('spec.json', {
            'schema': 'experiment-handoff-v1', 'reviewed': True, 'mode': 'move',
            'restore_method': 'shared-workspace-db', 'session_id': SESSION_ID,
            'export_transfer_sha256': self.export_sha, 'filesystem': 'juicefs',
            'source': {'node_id': 'node-a', 'peer': self.source_peer, 'anchor': dict(ANCHOR),
                       'mount_directory': WORKSPACE.rsplit('/', 1)[0],
                       'session_state': json.loads(json.dumps(SESSION_STATE))},
            'target': {'node_id': 'node-b', 'peer': self.target_peer, 'anchor': dict(ANCHOR),
                       'mount_directory': WORKSPACE.rsplit('/', 1)[0],
                       'session_state': json.loads(json.dumps(SESSION_STATE))},
            'quiesce': self.pin(self.source_cohort), 'lease_check': self.pin(self.source_cohort),
            'restore': self.pin(self.target_cohort), 'resume': self.pin(self.target_cohort),
            'export_after': self.pin(self.target_cohort)})
        self.worker_path = self.put('tools/session-worker-bounded.py', {'offline': 'bounded worker'})
        self.relay_path = self.put('tools/session-relay-bounded.py', {'offline': 'bounded relay'})
        self.phase_template_path = self.put('tools/draft-session-phase.py', {'offline': 'phase template'})
        self.ssh_path = self.put('tools/ssh.conf', {'offline': 'ssh config'})
        self.private_path = self.root / 'private' / 'relay.json'
        self.private_path.parent.mkdir()
        self.private_path.write_text(json.dumps({'offline': 'never archived'}) + '\n')
        self.private_path.chmod(0o600)
        self.pins = {
            'schema': 'c4-native-handoff-pins-v1', 'purpose': 'experiment',
            'attempt': 'c4-native-handoff-01',
            'source_pool': 'pool-a', 'target_pool': 'pool-b',
            'roles': {'source': 'client', 'target': 'client'},
            'restore_method': 'shared-workspace-db',
            'canonical_control': str(self.control),
            'session_id': SESSION_ID, 'original_export_sha256': self.export_sha,
            'source_client': self.client('pool-a'), 'target_client': self.client('pool-b'),
            'plan': self.pin(self.plan_path), 'inventory': self.pin(self.inventory_path),
            'checkpoint_receipt': self.pin(self.receipt_path),
            'opencode_release': self.pin(self.release_path),
            'handoff_spec': self.pin(self.spec_path),
            'handoff_module': self.pin(REPO / 'bench/runtime/handoff.py'),
            'session_worker': self.pin(self.worker_path), 'session_relay': self.pin(self.relay_path),
            'phase_template': self.pin(self.phase_template_path), 'ssh': self.pin(self.ssh_path),
            'relay': {'url': 'http://127.0.0.1:18444/v1', 'first_body_sha256': FIRST_BODY_SHA,
                      'max_requests': 4, 'output_tokens': 512, 'thinking': 'disabled',
                      'wall_seconds': 900, 'private_config': self.pin(self.private_path)},
            'backend': {'pool': 'pool-a',
                        'redis': {'url': 'redis://10.20.1.11:6379', 'database': 210},
                        'objects': {'endpoint': 'http://10.20.1.12:9000',
                                    'bucket': 'ax-c4-native-handoff-01'}},
        }
        self.pins_path = self.put('pins.json', self.pins)
        self.draft = DRAFT['build'](self.copy(self.pins), self.checkpoint_dir, self.pins_path)
        self.reviewed_path = self.put('reviewed-draft.json',
                                      self.reviewed(self.draft))
        self.draft_path = self.put('draft.json', self.draft)
        self.proof = {pool: self.put('handoff-%s.json' % pool,
                                     {'schema': 'pilot-pool-handoff-v1', 'pool': pool,
                                      'safe_for_handoff': True})
                      for pool in ('pool-a', 'pool-b')}
        self.request = self.run_request()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        write_new(path, value)
        return path

    @staticmethod
    def copy(value):
        return json.loads(json.dumps(value))

    @staticmethod
    def reviewed(value):
        result = json.loads(json.dumps(value))
        result.update(reviewed=True, execution_enabled=True)
        return result

    def pin(self, path):
        return {'path': str(path), 'sha256': digest(path)}

    def client(self, pool):
        role = pool_inventory(self.inventory, pool)['roles']['client']
        return {'instance_id': role['instance_id'], 'private_ip': role['private_ip'],
                'hostname': role['hostname'], 'role': 'client'}

    def plan(self):
        config = template()
        config['cases'] = config['cases'][:1]
        config.update(input_manifest_sha256='a' * 64, build_manifest_sha256='b' * 64)
        for cluster in config['clusters']:
            actual = pool_inventory(self.inventory, cluster['id'])
            cluster.update(resource_ids=resource_ids(actual), inventory_sha256='c' * 64)
        return build(config)

    def cohort(self, peer):
        return {'schema': 'experiment-cohort-v1', 'agents': 1, 'workers': [{'peer': peer}]}

    def run_request(self):
        ssh = ['ssh', '-F', str(self.ssh_path), self.source_peer]
        return {
            'schema': 'c4-native-handoff-run-v1', 'reviewed': True, 'execution_enabled': True,
            'attempt': 'c4-native-handoff-01', 'draft': self.pin(self.reviewed_path),
            'plan': self.pin(self.plan_path),
            'handoffs': {pool: self.pin(self.proof[pool]) for pool in ('pool-a', 'pool-b')},
            'peers': {'source': {'ssh': self.source_peer, 'role': 'client',
                                 'repo': '/srv/ax-client/code-11',
                                 'worker': self.pin(self.worker_path)},
                      'target': {'ssh': self.target_peer, 'role': 'client',
                                 'repo': '/srv/ax-client/code-11',
                                 'worker': self.pin(self.worker_path)}},
            'phases': {
                'quiesce': {'peer': 'source', 'timeout_seconds': 60,
                            'argv': ssh + ['python3',
                                           '/srv/ax-client/preparation/session-worker-bounded.py',
                                           '--request', '/srv/ax-client/preparation/phase.json',
                                           '--sha256', 'd' * 64,
                                           '--out', '/srv/ax-client/preparation/out']},
                'export': {'peer': 'source', 'timeout_seconds': 60},
                'source-stop': {'peer': 'source', 'timeout_seconds': 60},
                'lease_check': {'peer': 'source', 'timeout_seconds': 60},
                'target-start': {'peer': 'target', 'timeout_seconds': 60},
                'restore': {'peer': 'target', 'timeout_seconds': 60,
                            'argv': ['python3', '-m', 'bench.runtime.handoff',
                                     '--spec', str(self.spec_path),
                                     '--sha256', digest(self.spec_path),
                                     '--output', '/srv/ax-pilot-control/preparation/c4-native-01']},
                'resume': {'peer': 'target', 'timeout_seconds': 205},
                'export_after': {'peer': 'target', 'timeout_seconds': 60}},
            'close_probe': {'source': ssh,
                            'target': ['ssh', '-F', str(self.ssh_path), self.target_peer]},
        }


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.f = Fixture(self.root)

    def problems(self, function, *arguments, **keywords):
        with self.assertRaises(ValueError) as raised:
            function(*arguments, **keywords)
        return list(getattr(raised.exception, 'problems', (str(raised.exception),)))

    def assertRefused(self, problems, *labels):
        text = '; '.join(problems)
        for label in labels:
            self.assertIn(label, text)

    def executor(self, export, handoff):
        calls = []

        def execute(argv, stdin=None):
            calls.append([str(item) for item in argv])
            if 'bench.runtime.handoff' in argv:
                return json.dumps(handoff)
            return json.dumps(export)

        return execute, calls

    def out(self, name='out'):
        return self.root / name


class DraftTests(Case):
    def test_valid_pins_emit_a_reviewable_draft(self):
        value = DRAFT['build'](Fixture.copy(self.f.pins), self.f.checkpoint_dir, self.f.pins_path)
        self.assertEqual(value['schema'], 'c4-native-handoff-request-v1')
        self.assertIs(value['reviewed'], False)
        self.assertIs(value['execution_enabled'], False)
        self.assertIs(value['automatic_retry'], False)
        self.assertEqual(value['roles'], {'source': 'client', 'target': 'client'})
        self.assertEqual(value['source']['pool'], 'pool-a')
        self.assertEqual(value['target']['pool'], 'pool-b')
        self.assertEqual(value['source']['instance_id'], 'i-pool-a-client')
        self.assertEqual(value['target']['instance_id'], 'i-pool-b-client')
        self.assertEqual(value['shared_backend']['redis']['database'], 210)
        self.assertEqual(value['shared_backend']['objects']['bucket'], 'ax-c4-native-handoff-01')
        self.assertEqual(value['shared_backend']['pool'], 'pool-a')
        self.assertIs(value['shared_backend']['target_pool_backend_used'], False)
        self.assertEqual(value['relay']['max_requests'], 4)
        self.assertEqual(value['relay']['output_tokens'], 512)
        self.assertEqual(value['relay']['thinking'], 'disabled')
        self.assertEqual(value['relay']['first_body_sha256'], FIRST_BODY_SHA)
        self.assertIs(value['relay']['replaces_active_relay_service'], False)
        self.assertEqual(value['session']['session_id'], SESSION_ID)
        self.assertEqual(value['session']['original_export_sha256'], self.f.export_sha)
        self.assertEqual(value['session']['opencode_sha256'], OPENCODE_SHA)
        self.assertEqual(value['restore']['method'], 'shared-workspace-db')
        self.assertEqual(value['restore']['session_state'], SESSION_STATE)
        self.assertEqual(value['restore']['native_entrypoint']['module'], 'bench.runtime.handoff')
        self.assertEqual(value['restore']['native_entrypoint']['argv'][1:3], ['-m', 'bench.runtime.handoff'])
        self.assertIs(value['restore']['target_pool_backend_initialized'], False)
        self.assertEqual([phase['name'] for phase in value['phases']],
                         ['quiesce', 'export', 'source-stop', 'lease_check',
                          'target-start', 'restore', 'resume', 'export_after'])
        self.assertTrue(value['cold_check']['required'])
        self.assertIs(value['cold_check']['completed'], False)
        self.assertTrue(value['zero_debt']['required'])
        self.assertEqual(value['zero_debt']['samples'], 2)
        self.assertIs(value['zero_debt']['completed'], False)
        self.assertTrue(any('workspace tree' in item for item in value['archive_excludes']))
        self.assertTrue(any('native session database' in item for item in value['archive_excludes']))
        self.assertTrue(any('relay private configuration' in item for item in value['archive_excludes']))
        self.assertEqual(set(value['pins']), set(DRAFT['REQUIRED_PINS']))
        self.assertEqual(value['relay']['private_config'], self.f.pin(self.f.private_path))
        self.assertNotIn('never archived', json.dumps(value))

    def test_missing_pins_are_refused_with_the_exact_gap(self):
        cases = {
            'handoff_spec': ('handoff_spec', 'reviewed experiment-handoff-v1 spec'),
            'relay_body': ('relay.first_body_sha256', 'original first forwarded body SHA'),
            'target_client': ('target_client', 'client identity missing'),
            'backend_database': ('backend.redis.database', 'database in 20..255'),
            'checkpoint_session': ('checkpoint.session_id', 'pinned original session'),
            'checkpoint_export': ('checkpoint.session_export_sha256', 'original export SHA'),
        }
        for name, (label, text) in cases.items():
            with self.subTest(name=name):
                pins = Fixture.copy(self.f.pins)
                if name == 'handoff_spec':
                    pins.pop('handoff_spec')
                elif name == 'relay_body':
                    pins['relay'].pop('first_body_sha256')
                elif name == 'target_client':
                    pins.pop('target_client')
                elif name == 'backend_database':
                    pins['backend']['redis'].pop('database')
                elif name == 'checkpoint_session':
                    checkpoint = json.loads((self.f.checkpoint_dir / 'checkpoint.json').read_text())
                    checkpoint['session_id'] = 'ses_some_other_session'
                    (self.f.checkpoint_dir / 'checkpoint.json').write_text(
                        json.dumps(checkpoint) + '\n')
                elif name == 'checkpoint_export':
                    checkpoint = json.loads((self.f.checkpoint_dir / 'checkpoint.json').read_text())
                    checkpoint['session_export_sha256'] = 'e' * 64
                    (self.f.checkpoint_dir / 'checkpoint.json').write_text(
                        json.dumps(checkpoint) + '\n')
                problems = self.problems(DRAFT['build'], pins, self.f.checkpoint_dir)
                self.assertRefused(problems, label, text)

    def test_state_reopen_style_bypasses_and_unsafe_pins_are_refused(self):
        mutations = [
            ('adapter-test', lambda pins: pins.update(purpose='adapter-test'), 'purpose'),
            ('active-relay', lambda pins: pins['relay'].update(url='http://127.0.0.1:18443/v1'),
             'relay.url'),
            ('unbounded-worker',
             lambda pins: pins.update(session_worker=self.f.pin(self.f.phase_template_path)),
             'session_worker'),
            ('same-instance',
             lambda pins: pins['target_client'].update(
                 instance_id=pins['source_client']['instance_id']),
             'target_client'),
            ('secret-in-pins',
             lambda pins: pins['relay'].update(api_key='never-log-provider-key'), 'pins.secrets'),
            ('relay-cap', lambda pins: pins['relay'].update(max_requests=5), 'relay.max_requests'),
            ('output-tokens', lambda pins: pins['relay'].update(output_tokens=1024),
             'relay.output_tokens'),
            ('foreign-database', lambda pins: pins['backend'].update(pool='pool-b'), 'backend.pool'),
            ('foreign-backend',
             lambda pins: pins['backend']['redis'].update(url='redis://10.20.1.99:6379'),
             'backend.redis.url'),
        ]
        for name, mutate, label in mutations:
            with self.subTest(name=name):
                pins = Fixture.copy(self.f.pins)
                mutate(pins)
                problems = self.problems(DRAFT['build'], pins, self.f.checkpoint_dir)
                self.assertRefused(problems, label)

    def test_unclosed_native_journal_and_missing_checkpoint_are_refused(self):
        tree = json.loads((self.f.checkpoint_dir / 'source-tree.json').read_text())
        tree['data/opencode.db-wal'] = {'type': 'file', 'mode': 0o600, 'bytes': 32, 'sha256': '2' * 64}
        (self.f.checkpoint_dir / 'source-tree.json').write_text(json.dumps(tree) + '\n')
        problems = self.problems(DRAFT['build'], Fixture.copy(self.f.pins), self.f.checkpoint_dir)
        self.assertRefused(problems, 'checkpoint.source-tree.json', 'unclosed native journal')
        problems = self.problems(DRAFT['build'], Fixture.copy(self.f.pins), self.root / 'nowhere')
        self.assertRefused(problems, 'checkpoint', 'original verified checkpoint directory missing')

    def test_draft_file_is_private_and_refuses_to_overwrite(self):
        output = self.root / 'request.json'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(DRAFT['main'](['--checkpoint', str(self.f.checkpoint_dir),
                                            '--pins', str(self.f.pins_path),
                                            '--out', str(output)]), 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text())['reviewed'], False)
            self.assertEqual(DRAFT['main'](['--checkpoint', str(self.f.checkpoint_dir),
                                            '--pins', str(self.f.pins_path),
                                            '--out', str(output)]), 1)


class DriverTests(Case):
    @unittest.skipUnless(os.name == 'posix', 'the two-pool guard uses POSIX flock')
    def test_default_executor_refuses_and_performs_no_external_action(self):
        request = self.f.put('run.json', self.f.request)
        with patch('subprocess.run') as run, contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(DRIVER['main'](['--request', str(request),
                                             '--sha256', digest(request),
                                             '--out', str(self.out())]), 1)
        run.assert_not_called()
        self.assertIn('would execute', printed.getvalue())
        self.assertIn('session-worker-bounded.py', printed.getvalue())
        failed = json.loads((self.out() / 'failed.json').read_text())
        self.assertIs(failed['passed'], False)
        self.assertIs(failed['automatic_retry'], False)
        self.assertEqual(failed['phase'], 'quiesce')
        exit_value = json.loads((self.out() / '01-quiesce' / 'exit.json').read_text())
        self.assertIs(exit_value['executed'], False)
        self.assertIs(exit_value['refused'], True)
        self.assertIn('ssh', exit_value['command'])

    def test_phase_order_delegation_and_two_external_actions(self):
        executor, calls = self.executor(self.f.export, handoff_result())
        result = DRIVER['run'](self.f.request, self.out(), executor=executor)
        self.assertIs(result['passed'], True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any('session-worker-bounded.py' in item for item in calls[0]))
        self.assertEqual(calls[1][1:3], ['-m', 'bench.runtime.handoff'])
        phases = json.loads((self.out() / 'phases.json').read_text())
        self.assertEqual(phases['order'], list(DRIVER['PHASES']))
        self.assertEqual(set(phases['status'].values()), {'passed'})
        for order, name in enumerate(DRIVER['PHASES'], 1):
            intent = json.loads((self.out() / ('%02d-%s' % (order, name)) / 'intent.json').read_text())
            self.assertEqual(intent['order'], order)
            self.assertEqual(intent['phase'], name)
            self.assertEqual(intent['peer'], DRIVER['SIDE'][name])
            self.assertIs(intent['automatic_retry'], False)
        export = json.loads((self.out() / '02-export' / 'result.json').read_text())
        self.assertIs(export['passed'], True)
        self.assertEqual(export['checks']['export_sha_matches_pinned'], True)
        stored = json.loads((self.out() / 'result.json').read_text())
        self.assertIs(stored['closure_complete'], False)
        self.assertEqual(stored['resume_requests_issued_by_outer_driver'], 0)
        self.assertEqual(stored['workspace_copy_bytes'], 0)
        self.assertEqual(stored['native_session_json_transferred_bytes'], 0)
        self.assertIs(stored['automatic_retry'], False)

    def test_declared_order_and_entry_point_are_enforced(self):
        phases = self.f.request['phases']
        order = list(DRIVER['PHASES'])
        order[2], order[3] = order[3], order[2]
        request = Fixture.copy(self.f.request)
        request['phases'] = {name: phases[name] for name in order}
        problems = self.problems(DRIVER['run'], request, self.out())
        self.assertRefused(problems, 'request.phases')
        request = Fixture.copy(self.f.request)
        request['phases']['restore'] = dict(request['phases']['restore'],
                                            argv=['python3', '-c', 'print(1)'])
        problems = self.problems(DRIVER['run'], request, self.out('second'))
        self.assertRefused(problems, 'request.phases.restore.argv', 'bench.runtime.handoff')
        request = Fixture.copy(self.f.request)
        request['phases']['resume'] = dict(request['phases']['resume'],
                                           argv=['ssh', 'pool-b-client', 'python3', '-m'])
        problems = self.problems(DRIVER['run'], request, self.out('third'))
        self.assertRefused(problems, 'request.phases.resume.argv', 'must not be executed twice')

    def test_failure_retains_first_evidence_and_never_retries(self):
        calls = []

        def execute(argv, stdin=None):
            calls.append([str(item) for item in argv])
            if 'bench.runtime.handoff' in argv:
                raise DRIVER['ExecutorFailed'](argv, 3, '', 'offline handoff failure')
            return json.dumps(self.f.export)

        with self.assertRaises(DRIVER['ExecutorFailed']):
            DRIVER['run'](self.f.request, self.out(), executor=execute)
        self.assertEqual(len(calls), 2)
        failed = json.loads((self.out() / 'failed.json').read_text())
        self.assertEqual(failed['phase'], 'restore')
        self.assertIs(failed['passed'], False)
        self.assertIs(failed['automatic_retry'], False)
        self.assertEqual(len(failed['external_actions_attempted']), 2)
        self.assertIn('01-quiesce', failed['retained'])
        self.assertIn('06-restore', failed['retained'])
        exit_value = json.loads((self.out() / '06-restore' / 'exit.json').read_text())
        self.assertEqual(exit_value['exit_code'], 3)
        self.assertIs(exit_value['refused'], False)
        lock = json.loads((self.out() / 'lock-held.json').read_text())
        self.assertIs(lock['passed'], False)
        self.assertIs(lock['automatic_retry'], False)
        self.assertEqual(lock['safe_close']['probe'], 'refused')
        for name in ('source-stop', 'lease_check', 'target-start', 'resume', 'export_after'):
            directory = self.out() / ('%02d-%s' % (DRIVER['PHASES'].index(name) + 1, name))
            self.assertTrue((directory / 'intent.json').is_file())
            self.assertTrue((directory / 'exit.json').is_file())
            self.assertTrue((directory / 'result.json').is_file())

    def test_guard_is_released_on_the_exception_path(self):
        plan = check_plan(json.loads(self.f.plan_path.read_text()))
        cells = {pool: next(cell for cell in plan['cells'] if cell['pool'] == pool)
                 for pool in ('pool-a', 'pool-b')}

        def execute(argv, stdin=None):
            raise DRIVER['ExecutorFailed'](argv, 1, '', 'offline refusal')

        with self.assertRaises(DRIVER['ExecutorFailed']):
            DRIVER['run'](self.f.request, self.out(), executor=execute)
        with resource_guard(self.f.control, cells['pool-a'], plan):
            with resource_guard(self.f.control, cells['pool-b'], plan):
                pass

    def test_safe_close_releases_after_a_positive_probe(self):
        probes = []

        def execute(argv, stdin=None):
            raise DRIVER['ExecutorFailed'](argv, 1, '', 'offline refusal')

        def probe(side):
            probes.append(side)
            return {'safe': True, 'side': side}

        with self.assertRaises(DRIVER['ExecutorFailed']):
            DRIVER['run'](self.f.request, self.out(), executor=execute, probe=probe)
        self.assertEqual(sorted(probes), ['source', 'target'])
        failed = json.loads((self.out() / 'failed.json').read_text())
        self.assertIs(failed['safe_close']['safe'], True)
        self.assertFalse((self.out() / 'lock-held.json').exists())

    def test_exact_target_paths_and_xdg_are_required(self):
        reviewed = json.loads(self.f.reviewed_path.read_text())
        for name, session_state in (
                ('project', dict(SESSION_STATE, project=WORKSPACE + '/elsewhere')),
                ('xdg', dict(SESSION_STATE, xdg=dict(SESSION_STATE['xdg'], cache='/tmp/cache'))),
                ('root', dict(SESSION_STATE, workspace='/srv/other/workspace'))):
            with self.subTest(name=name):
                changed = Fixture.copy(reviewed)
                changed['restore']['session_state'] = session_state
                request = Fixture.copy(self.f.request)
                request['draft'] = self.f.pin(self.f.put('reviewed-%s.json' % name, changed))
                problems = self.problems(DRIVER['run'], request, self.out(name))
                self.assertRefused(problems, 'draft.restore.session_state')

    def test_unreviewed_draft_and_missing_handoff_are_refused(self):
        request = Fixture.copy(self.f.request)
        request['draft'] = self.f.pin(self.f.draft_path)
        problems = self.problems(DRIVER['run'], request, self.out())
        self.assertRefused(problems, 'draft.execution_enabled')
        request = Fixture.copy(self.f.request)
        request['handoffs'].pop('pool-b')
        problems = self.problems(DRIVER['run'], request, self.out('second'))
        self.assertRefused(problems, 'request.handoffs.pool-b', 'root handoff proof')
        request = Fixture.copy(self.f.request)
        request['close_probe'] = {
            'source': ['scp', '/tmp/x', 'pool-a-client:/tmp/x'],
            'target': ['ssh', '-F', str(self.f.ssh_path), self.f.target_peer]}
        problems = self.problems(DRIVER['run'], request, self.out('third'))
        self.assertRefused(problems, 'request.close_probe.source', 'copy')

    def test_active_cell_lease_refuses_before_any_phase(self):
        (self.root / 'other-control').mkdir()
        (self.root / 'other-control' / 'state.sqlite').write_bytes(b'')

        def conflicts(control, cell, plan):
            return [{'cell_id': 'some-active-cell', 'pool': 'pool-a'}]

        executor, calls = self.executor(self.f.export, handoff_result())
        problems = self.problems(DRIVER['run'], self.f.request, self.out(),
                                 executor=executor, conflicts=conflicts)
        self.assertRefused(problems, 'leases', 'active original cell lease on pool-a')
        self.assertEqual(calls, [])

    def test_copy_actions_and_adapter_test_purpose_are_refused(self):
        for forbidden in (['rsync', '-a', '/srv/ax-pilot-session/workspace', '/tmp/x'],
                          ['ssh', '-F', str(self.f.ssh_path), 'pool-a-client', 'cp', '-a', '/x', '/y'],
                          ['python3', '-m', 'bench.runtime.handoff', '--spec',
                           str(self.f.spec_path), '--sha256', 'd' * 64, '--output', '/tmp/x',
                           '--reflink'],
                          ['ssh', '-F', str(self.f.ssh_path), 'pool-a-client', 'python3',
                           '/srv/ax-client/preparation/session-worker-bounded.py',
                           '--request', 'purpose=adapter-test', '--sha256', 'd' * 64, '--out', '/x']):
            with self.subTest(forbidden=forbidden[0]):
                request = Fixture.copy(self.f.request)
                name = 'restore' if 'bench.runtime.handoff' in forbidden else 'quiesce'
                request['phases'][name] = dict(request['phases'][name], argv=forbidden)
                problems = self.problems(DRIVER['run'], request, self.out(forbidden[0]))
                self.assertRefused(problems, 'request.phases.' + name + '.argv')

    def test_handoff_receipt_order_zero_copy_and_ack_are_verified(self):
        broken = [
            ('copy', handoff_result(workspace_copy_bytes=1024), 'workspace_copy_zero'),
            ('native-json', handoff_result(native_session_json_transferred_bytes=2048),
             'native_json_transferred_zero'),
            ('no-ack', handoff_result(first_completed_tool_ack_seconds=None), 'resume_tool_ack'),
            ('prefix', handoff_result(session_validation={'passed': True, 'prefix_messages': 1,
                                                          'new_completed_tools': 1}),
             'prefix_preserved'),
            ('no-tool', handoff_result(session_validation={'passed': True, 'prefix_messages': 2,
                                                            'new_completed_tools': 0}),
             'new_completed_tools'),
        ]
        order = ('quiesce', 'source-stop', 'lease_check', 'target-start', 'restore', 'resume',
                 'export_after')
        phases = {name: {'start_ns': 1000 + (10 - index) * 10, 'end_ns': 1005}
                  for index, name in enumerate(order)}
        broken.append(('order', handoff_result(phases=phases), 'phase_order'))
        for name, receipt, label in broken:
            with self.subTest(name=name):
                executor, calls = self.executor(self.f.export, receipt)
                problems = self.problems(DRIVER['run'], self.f.request, self.out(name),
                                         executor=executor)
                self.assertRefused(problems, label)
                self.assertEqual(len(calls), 2)
                failed = json.loads((self.out(name) / 'failed.json').read_text())
                self.assertEqual(failed['phase'], 'restore')


if __name__ == '__main__':
    unittest.main()
