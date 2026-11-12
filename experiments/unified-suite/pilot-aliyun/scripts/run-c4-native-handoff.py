#!/usr/bin/env python3
"""Outer C4 native handoff driver skeleton: two-pool guard, fixed order, refuse first.

Holds the original canonical resource guard for both existing client pools and the
source-pool backend, audits every control `cell_leases`, and walks the reviewed
order quiesce → export → source-stop → lease_check → target-start → restore →
resume → export_after. Only the source-side native export and the pinned original
`python3 -m bench.runtime.handoff` entry point are external actions; the phases
inside handoff are verified from its receipt instead of being run twice (a second
resume would exceed the four-request relay cap).

Every external action goes through one injectable executor. Without --execute the
default executor refuses, prints the complete command and touches nothing.
Failures retain the first evidence, never retry, and keep the two-pool guard
until a reviewed close probe reports both clients and the source backend safe.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

RUN_SCHEMA = 'c4-native-handoff-run-v1'
REQUEST_SCHEMA = 'c4-native-handoff-request-v1'
FIRST_BODY_SHA256 = '3a792e6a0068fc3e73332289cb8be92e16315ae8cb4a2c496ed7d2d630631537'
WORKSPACE = '/srv/ax-pilot-session/workspace'
SESSION_STATE = {'workspace': WORKSPACE, 'project': WORKSPACE + '/repo',
                 'config': WORKSPACE + '/config/opencode.json',
                 'xdg': {name: WORKSPACE + '/source/' + name for name in ('data', 'state', 'cache', 'config')}}
PHASES = ('quiesce', 'export', 'source-stop', 'lease_check', 'target-start', 'restore', 'resume', 'export_after')
SIDE = {'quiesce': 'source', 'export': 'source', 'source-stop': 'source', 'lease_check': 'source',
        'target-start': 'target', 'restore': 'target', 'resume': 'target', 'export_after': 'target'}
EXTERNAL = ('quiesce', 'restore')
HANDOFF_ORDER = ('quiesce', 'source-stop', 'lease_check', 'target-start', 'restore', 'resume', 'export_after')
FORBIDDEN_PROGRAMS = ('cp', 'rsync', 'scp', 'sftp', 'dd', 'tar', 'format', 'clone', 'copy')
COPY_HINTS = ('--reflink', '--recursive', '--parents')
SHA = re.compile(r'[0-9a-f]{64}')
ATTEMPT = re.compile(r'[a-z0-9-]{3,24}')


class Refused(ValueError):
    def __init__(self, problems):
        self.problems = tuple(problems)
        super().__init__('refused: ' + '; '.join(self.problems))


class ExecutorRefused(RuntimeError):
    """The default executor: nothing was executed; the full command is retained."""


class ExecutorFailed(RuntimeError):
    def __init__(self, argv, code, stdout='', stderr=''):
        self.argv, self.code, self.stdout, self.stderr = list(argv), code, stdout or '', stderr or ''
        super().__init__('command failed with exit code %s: %s' % (code, shlex.join(list(map(str, argv)))))


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def pin_problem(pin):
    if not isinstance(pin, dict):
        return 'pin missing'
    if not isinstance(pin.get('path'), str) or not pin['path']:
        return 'absolute pinned path required'
    if not Path(pin['path']).is_absolute():
        return 'absolute pinned path required'
    if Path(pin['path']).is_symlink():
        return 'symlink refused'
    if not Path(pin['path']).is_file():
        return 'pinned file missing: ' + pin['path']
    if not isinstance(pin.get('sha256'), str) or not SHA.fullmatch(pin['sha256']):
        return 'sha256 pin missing'
    if digest(pin['path']) != pin['sha256']:
        return 'sha256 differs from the pinned bytes'
    return None


def argv_problems(argv):
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
        return ['nonempty argv of strings required']
    problems = []
    program = Path(argv[0]).name
    if program != 'ssh' and not program.startswith('python'):
        problems.append('only ssh or python3 may be executed, not ' + program)
    for token in argv:
        name = Path(token).name
        if name in FORBIDDEN_PROGRAMS or name.startswith('mkfs'):
            problems.append('workspace/native-database copy or format action refused: ' + token)
        if token in COPY_HINTS:
            problems.append('copy action refused: ' + token)
        if 'adapter-test' in token:
            problems.append('purpose=adapter-test is not a bypass')
    return problems


def refuse(argv, stdin=None):
    command = shlex.join(list(map(str, argv)))
    print('refused: would execute: ' + command, flush=True)
    raise ExecutorRefused(command)


def refuse_probe(side):
    raise ExecutorRefused('no reviewed close probe for ' + side)


def subprocess_executor(env=None):
    def execute(argv, stdin=None):
        argv = [str(item) for item in argv]
        completed = subprocess.run(argv, input=stdin, text=True,
                                   capture_output=True, env=env)
        if completed.returncode:
            raise ExecutorFailed(argv, completed.returncode, completed.stdout, completed.stderr)
        return completed.stdout
    return execute


def probe_from(request, executor):
    spec = request.get('close_probe') or {}

    def probe(side):
        argv = spec.get(side)
        if not argv:
            raise ExecutorRefused('no reviewed close probe for ' + side)
        return json.loads(executor(list(argv)))
    return probe


def inspect(request):
    """Return (problems, facts); any problem means no phase may run."""
    problems = []
    facts = {}

    def fail(label, message):
        problems.append(label + ': ' + message)

    if not isinstance(request, dict):
        return ['request: JSON object required'], {}
    if request.get('schema') != RUN_SCHEMA:
        fail('request.schema', RUN_SCHEMA + ' required')
    if request.get('reviewed') is not True:
        fail('request.reviewed', 'an explicitly reviewed run request is required; a draft never executes')
    attempt = request.get('attempt')
    if not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt):
        fail('request.attempt', 'unique short attempt required')
    else:
        facts['attempt'] = attempt

    draft = None
    message = pin_problem(request.get('draft'))
    if message:
        fail('request.draft', message + ' (the reviewed draft request)')
    else:
        try:
            draft = load(request['draft']['path'])
        except Exception as error:
            fail('request.draft', 'not readable JSON (' + type(error).__name__ + ')')
            draft = None
    pins = {}
    if not isinstance(draft, dict):
        return problems, facts
    facts['draft'] = draft

    if draft.get('schema') != REQUEST_SCHEMA:
        fail('draft.schema', REQUEST_SCHEMA + ' required')
    if draft.get('reviewed') is not True:
        fail('draft.reviewed', 'the reviewed copy of the draft request is required')
    if draft.get('execution_enabled') is not True:
        fail('draft.execution_enabled', 'the unreviewed draft is not executable')
    if draft.get('automatic_retry') is not False:
        fail('draft.automatic_retry', 'automatic retry must stay disabled')
    if draft.get('attempt') != attempt:
        fail('draft.attempt', 'differs from the run attempt')
    control = draft.get('canonical_control')
    if not isinstance(control, str) or not Path(control).is_absolute() or not Path(control).is_dir():
        fail('draft.canonical_control', 'existing absolute canonical control directory required')
    else:
        facts['control'] = control
    restore = draft.get('restore') or {}
    if restore.get('method') != 'shared-workspace-db':
        fail('draft.restore.method', 'shared-workspace-db required')
    if restore.get('session_state') != SESSION_STATE:
        fail('draft.restore.session_state', 'the canonical workspace, repo, config and XDG paths must be exact')
    if restore.get('workspace_copy_bytes') != 0 or restore.get('native_session_json_transferred_bytes') != 0:
        fail('draft.restore', 'no workspace or native session JSON copy is permitted')
    session = draft.get('session') or {}
    session_id, export_sha = session.get('session_id'), session.get('original_export_sha256')
    if not isinstance(session_id, str) or not session_id.startswith('ses_'):
        fail('draft.session.session_id', 'original native session ID required')
    if not isinstance(export_sha, str) or not SHA.fullmatch(export_sha):
        fail('draft.session.original_export_sha256', 'original native export SHA required')
    facts['session_id'], facts['export_sha256'] = session_id, export_sha
    relay = draft.get('relay') or {}
    if relay.get('max_requests') != 4:
        fail('draft.relay.max_requests', 'the relay cap must stay at four distinct requests')
    if relay.get('output_tokens') != 512:
        fail('draft.relay.output_tokens', 'the 512-token output cap required')
    if relay.get('thinking') != 'disabled':
        fail('draft.relay.thinking', 'disabled thinking required')
    if relay.get('first_body_sha256') != FIRST_BODY_SHA256:
        fail('draft.relay.first_body_sha256', 'the fixed original first forwarded body SHA required')
    source, target = draft.get('source'), draft.get('target')
    for label, value in (('draft.source', source), ('draft.target', target)):
        if not isinstance(value, dict):
            fail(label, 'client identity required')
            continue
        if not isinstance(value.get('instance_id'), str) or not value['instance_id'].startswith('i-'):
            fail(label, 'actual client instance ID required')
        if not isinstance(value.get('pool'), str) or not value['pool'].startswith('pool-'):
            fail(label + '.pool', 'explicit pilot pool required')
    if isinstance(source, dict) and isinstance(target, dict):
        if source.get('instance_id') == target.get('instance_id'):
            fail('draft.target', 'two different client instances required')
        if source.get('pool') == target.get('pool'):
            fail('draft.target.pool', 'two different client pools required')
        if isinstance(source.get('pool'), str) and isinstance(target.get('pool'), str):
            facts['pools'] = (source['pool'], target['pool'])
    pins = draft.get('pins') if isinstance(draft.get('pins'), dict) else {}
    for label in ('plan', 'handoff_spec'):
        message = pin_problem(pins.get(label))
        if message:
            fail('draft.pins.' + label, message)
        else:
            facts[label] = pins[label]

    message = pin_problem(request.get('plan'))
    if message:
        fail('request.plan', message + ' (the frozen canonical plan)')
    elif 'plan' in facts and request['plan']['sha256'] != facts['plan']['sha256']:
        fail('request.plan', 'differs from the reviewed draft plan pin')
    else:
        facts['plan'] = request['plan']

    handoffs = request.get('handoffs')
    if not isinstance(handoffs, dict):
        fail('request.handoffs', 'explicit root handoff proofs for both pools required')
    elif 'pools' in facts:
        for pool in facts['pools']:
            message = pin_problem(handoffs.get(pool))
            if message:
                fail('request.handoffs.' + pool, message + ' (explicit root handoff proof)')
                continue
            try:
                proof = load(handoffs[pool]['path'])
            except Exception as error:
                fail('request.handoffs.' + pool, 'not readable JSON (' + type(error).__name__ + ')')
                continue
            if not isinstance(proof, dict) or proof.get('pool') != pool or proof.get('safe_for_handoff') is not True:
                fail('request.handoffs.' + pool, 'explicit safe_for_handoff proof for this pool required')

    peers = request.get('peers')
    if not isinstance(peers, dict):
        fail('request.peers', 'source and target peers required')
    else:
        for side in ('source', 'target'):
            peer = peers.get(side)
            if not isinstance(peer, dict):
                fail('request.peers.' + side, 'peer missing')
                continue
            if not isinstance(peer.get('ssh'), str) or not peer['ssh']:
                fail('request.peers.' + side + '.ssh', 'ssh alias required')
            if peer.get('role') != 'client':
                fail('request.peers.' + side + '.role', 'client role required')
            if not isinstance(peer.get('repo'), str) or not Path(peer['repo']).is_absolute():
                fail('request.peers.' + side + '.repo', 'absolute runtime repo required')
            message = pin_problem(peer.get('worker'))
            if message:
                fail('request.peers.' + side + '.worker', message + ' (bounded native worker)')
            elif 'bounded' not in Path(peer['worker']['path']).name:
                fail('request.peers.' + side + '.worker', 'bounded native worker required')
            elif 'session_worker' in pins and peer['worker']['sha256'] != pins['session_worker']['sha256']:
                fail('request.peers.' + side + '.worker', 'differs from the reviewed draft worker pin')
        facts['peers'] = peers

    phases = request.get('phases')
    if not isinstance(phases, dict):
        fail('request.phases', 'every phase in the fixed order must be declared')
    else:
        if list(phases) != list(PHASES):
            fail('request.phases', 'declare exactly ' + ' → '.join(PHASES))
        for name in PHASES:
            phase = phases.get(name)
            if not isinstance(phase, dict):
                fail('request.phases.' + name, 'phase declaration missing')
                continue
            if phase.get('peer') != SIDE[name]:
                fail('request.phases.' + name + '.peer', 'must be ' + SIDE[name])
            timeout = phase.get('timeout_seconds')
            if type(timeout) is not int or not 1 <= timeout <= 900:
                fail('request.phases.' + name + '.timeout_seconds', 'integer 1..900 seconds required')
            argv = phase.get('argv')
            if name not in EXTERNAL:
                if argv not in (None, []):
                    fail('request.phases.' + name + '.argv',
                         'this phase runs inside the pinned handoff entry point and must not be executed twice')
                continue
            for problem in argv_problems(argv):
                fail('request.phases.' + name + '.argv', problem)
            if not isinstance(argv, list) or not argv:
                continue
            if name == 'quiesce':
                if not any(Path(item).name == 'session-worker-bounded.py' for item in argv):
                    fail('request.phases.quiesce.argv', 'the bounded native export worker must be the source-side action')
                if '--sha256' not in argv or argv.index('--sha256') + 1 >= len(argv) or \
                        not SHA.fullmatch(argv[argv.index('--sha256') + 1]):
                    fail('request.phases.quiesce.argv', 'pinned source phase request SHA required')
                if '--out' not in argv:
                    fail('request.phases.quiesce.argv', 'fresh source phase output directory required')
            else:
                if argv[1:3] != ['-m', 'bench.runtime.handoff']:
                    fail('request.phases.restore.argv',
                         'the original entry point python3 -m bench.runtime.handoff is required')
                for flag in ('--spec', '--sha256', '--output'):
                    if flag not in argv:
                        fail('request.phases.restore.argv', flag + ' required')
                if '--sha256' in argv and argv.index('--sha256') + 1 < len(argv):
                    if 'handoff_spec' in facts and argv[argv.index('--sha256') + 1] != facts['handoff_spec']['sha256']:
                        fail('request.phases.restore.argv', 'must run the reviewed handoff spec bytes')
    close_probe = request.get('close_probe')
    if close_probe is not None:
        if not isinstance(close_probe, dict) or set(close_probe) != {'source', 'target'}:
            fail('request.close_probe', "explicit {'source':argv,'target':argv} required")
        else:
            for side in ('source', 'target'):
                for problem in argv_problems(close_probe[side]):
                    fail('request.close_probe.' + side, problem)
    facts['phases'] = phases if isinstance(phases, dict) else {}
    return problems, facts


def settle(probe, sides, deadline_seconds, interval_seconds, sleep, clock):
    end = clock() + max(0.0, float(deadline_seconds))
    attempts = 0
    outcomes = {}
    while True:
        attempts += 1
        pending = []
        try:
            for side in sides:
                value = probe(side)
                outcomes[side] = value
                if not isinstance(value, dict) or value.get('safe') is not True:
                    pending.append(side)
        except ExecutorRefused as error:
            return {'safe': False, 'probe': 'refused', 'detail': str(error)[:1000],
                    'attempts': attempts, 'sides': outcomes}
        except (ExecutorFailed, ValueError, OSError) as error:
            return {'safe': False, 'probe': 'failed', 'detail': str(error)[:1000],
                    'attempts': attempts, 'sides': outcomes}
        if not pending:
            return {'safe': True, 'probe': 'ok', 'attempts': attempts, 'sides': outcomes}
        if clock() >= end:
            return {'safe': False, 'probe': 'deadline', 'attempts': attempts, 'sides': outcomes}
        sleep(max(0.0, min(interval_seconds, end - clock())))


def run(request, output, executor=None, probe=None, conflicts=None,
        deadline_seconds=0.0, interval_seconds=1.0, sleep=time.sleep, clock=time.monotonic):
    problems, facts = inspect(request)
    if problems:
        raise Refused(problems)
    executor = executor or refuse
    probe = probe or refuse_probe
    draft = facts['draft']
    output = Path(output)

    from bench.runtime.common import load as bench_load, pinned, private_dir, write_new
    from bench.runtime.pilot_pools import active_conflicts, resource_guard
    from bench.session_check import checkpoint as native_checkpoint
    from bench.suite_plan import check_plan

    conflicts = conflicts or active_conflicts
    plan = check_plan(bench_load(pinned(facts['plan'])))
    pools = facts['pools']
    cells = {}
    for pool in pools:
        matches = [cell for cell in plan['cells'] if cell['pool'] == pool]
        if not matches:
            raise Refused(['plan: no cell for ' + pool])
        cells[pool] = matches[0]
    attempt = facts['attempt']
    session_id = facts['session_id']
    export_sha = facts['export_sha256']
    status = {name: 'pending' for name in PHASES}
    state = {'phase': None, 'attempted': [], 'export': None, 'export_sha256': None, 'handoff': None}

    def write_bytes_private(path, data):
        with Path(path).open('xb') as stream:
            stream.write(data)
        Path(path).chmod(0o600)

    def directory_for(order, name):
        path = output / ('%02d-%s' % (order, name))
        path.mkdir(mode=0o700)
        return path

    def intent(directory, order, name, argv=None, delegated_to=None):
        write_new(directory / 'intent.json', {
            'schema': 'c4-native-handoff-intent-v1', 'attempt': attempt, 'order': order,
            'phase': name, 'peer': SIDE[name], 'via': 'handoff' if delegated_to else 'outer',
            'argv': None if delegated_to else list(argv or []),
            'delegated_to': delegated_to, 'timeout_seconds': facts['phases'][name]['timeout_seconds'],
            'automatic_retry': False})

    def external(name, order, argv, verify):
        directory = directory_for(order, name)
        intent(directory, order, name, argv)
        started = clock()
        state['attempted'].append(list(argv))
        try:
            raw = executor(list(argv))
        except ExecutorRefused as error:
            write_new(directory / 'exit.json', {'executed': False, 'refused': True, 'exit_code': None,
                                                'command': str(error), 'seconds': clock() - started,
                                                'automatic_retry': False})
            write_new(directory / 'result.json', {'passed': False, 'phase': name, 'refused': True,
                                                  'automatic_retry': False})
            raise
        except ExecutorFailed as error:
            write_bytes_private(directory / 'stdout', error.stdout.encode('utf-8'))
            write_bytes_private(directory / 'stderr', error.stderr.encode('utf-8'))
            write_new(directory / 'exit.json', {
                'executed': True, 'refused': False, 'exit_code': error.code,
                'stdout': {'sha256': sha256_bytes(error.stdout.encode('utf-8')),
                           'bytes': len(error.stdout.encode('utf-8'))},
                'stderr': {'sha256': sha256_bytes(error.stderr.encode('utf-8')),
                           'bytes': len(error.stderr.encode('utf-8'))},
                'seconds': clock() - started, 'automatic_retry': False})
            write_new(directory / 'result.json', {'passed': False, 'phase': name,
                                                  'automatic_retry': False})
            raise
        data = raw.encode('utf-8')
        write_bytes_private(directory / 'stdout', data)
        write_new(directory / 'exit.json', {
            'executed': True, 'refused': False, 'exit_code': 0,
            'stdout': {'sha256': sha256_bytes(data), 'bytes': len(data)},
            'seconds': clock() - started, 'automatic_retry': False})
        try:
            outcome = verify(json.loads(raw))
        except Exception as error:
            write_new(directory / 'result.json', {
                'passed': False, 'phase': name, 'checks': {},
                'error_type': type(error).__name__, 'error': str(error)[:2000],
                'automatic_retry': False})
            raise Refused([name + ': ' + str(error)[:500]])
        checks, evidence = outcome if isinstance(outcome, tuple) else (outcome, {})
        write_new(directory / 'result.json', {
            'passed': all(checks.values()), 'phase': name, 'checks': checks,
            'evidence': evidence, 'verified_from': 'executor', 'automatic_retry': False})
        if not all(checks.values()):
            raise Refused([name + ': ' + ', '.join(key for key, value in checks.items() if not value)])
        status[name] = 'passed'
        return checks

    def local(name, order, checks):
        directory = directory_for(order, name)
        write_new(directory / 'intent.json', {
            'schema': 'c4-native-handoff-intent-v1', 'attempt': attempt, 'order': order,
            'phase': name, 'peer': SIDE[name], 'via': 'local', 'argv': None,
            'verified_from': 'the quiesce native export bytes', 'automatic_retry': False})
        write_new(directory / 'exit.json', {'executed': False, 'refused': False, 'exit_code': None,
                                            'verified_from': 'quiesce export bytes',
                                            'automatic_retry': False})
        write_new(directory / 'result.json', {'passed': all(checks.values()), 'phase': name,
                                              'checks': checks, 'verified_from': 'quiesce',
                                              'automatic_retry': False})
        if not all(checks.values()):
            raise Refused([name + ': ' + ', '.join(key for key, value in checks.items() if not value)])
        status[name] = 'passed'

    def delegated_intent(name):
        order = PHASES.index(name) + 1
        directory = directory_for(order, name)
        intent(directory, order, name, delegated_to='06-restore')
        return directory

    def delegated_result(name, checks):
        order = PHASES.index(name) + 1
        directory = output / ('%02d-%s' % (order, name))
        write_new(directory / 'exit.json', {'executed': False, 'refused': False, 'exit_code': None,
                                            'verified_from': 'handoff receipt phases',
                                            'automatic_retry': False})
        write_new(directory / 'result.json', {'passed': all(checks.values()), 'phase': name,
                                              'checks': checks, 'verified_from': 'handoff-receipt',
                                              'automatic_retry': False})
        if not all(checks.values()):
            raise Refused([name + ': ' + ', '.join(key for key, value in checks.items() if not value)])
        status[name] = 'passed'

    def receipt_checks():
        receipt = state['handoff']
        phases = receipt.get('phases') if isinstance(receipt.get('phases'), dict) else {}
        starts = []
        order_ok = True
        for name in HANDOFF_ORDER:
            row = phases.get(name)
            if not isinstance(row, dict) or type(row.get('start_ns')) is not int:
                order_ok = False
                continue
            starts.append(row['start_ns'])
        if starts != sorted(starts) or len(set(starts)) != len(starts):
            order_ok = False
        validation = receipt.get('session_validation') if isinstance(receipt.get('session_validation'), dict) else {}
        ack = receipt.get('first_completed_tool_ack_seconds')
        checks = {
            'handoff_passed': receipt.get('passed') is True,
            'mode_move': receipt.get('mode') == 'move',
            'restore_method': receipt.get('restore_method') == 'shared-workspace-db',
            'session_id': receipt.get('session_id') == session_id,
            'workspace_copy_zero': receipt.get('workspace_copy_bytes') == 0,
            'native_json_transferred_zero': receipt.get('native_session_json_transferred_bytes') == 0,
            'state_ready_excludes_resume': receipt.get('state_ready_includes_model_resume') is False,
            'resume_tool_ack': isinstance(ack, (int, float)) and not isinstance(ack, bool),
            'prefix_preserved': (validation.get('passed') is True and
                                 (state['checkpoint'] is None or
                                  validation.get('prefix_messages') == len(state['checkpoint']['message_ids']))),
            'new_completed_tools': isinstance(validation.get('new_completed_tools'), int) and
                                   validation.get('new_completed_tools') >= 1,
            'phase_order': order_ok,
        }
        return checks, phases, validation

    def close_pending():
        for name in PHASES:
            order = PHASES.index(name) + 1
            directory = output / ('%02d-%s' % (order, name))
            if not directory.is_dir():
                continue
            if not (directory / 'result.json').exists():
                if not (directory / 'exit.json').exists():
                    write_new(directory / 'exit.json', {'executed': False, 'refused': False,
                                                        'exit_code': None, 'skipped': True,
                                                        'automatic_retry': False})
                if not (directory / 'result.json').exists():
                    write_new(directory / 'result.json', {'passed': False, 'phase': name,
                                                          'skipped': True, 'automatic_retry': False})

    def summary(passed, error=None):
        write_new(output / 'phases.json', {
            'schema': 'c4-native-handoff-phases-v1', 'attempt': attempt, 'passed': passed,
            'order': list(PHASES), 'canonical_side': dict(SIDE), 'status': dict(status),
            'error_type': error, 'automatic_retry': False})

    def verify_quiesce(value):
        if not isinstance(value, dict) or not isinstance(value.get('info'), dict):
            raise ValueError('native export object required')
        state['export'] = value
        state['checkpoint'] = native_checkpoint(value)
        transfer = (json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8')
        state['export_sha256'] = sha256_bytes(transfer)
        checks = {'session_id': value['info'].get('id') == session_id,
                  'export_sha256_matches_pinned': state['export_sha256'] == export_sha,
                  'transfer_bytes': len(transfer) > 0}
        if not all(checks.values()):
            raise ValueError('quiesce export refused: ' +
                             ', '.join(key for key, passed in checks.items() if not passed))
        return checks, {'session_id': session_id, 'export_sha256': state['export_sha256'],
                        'transfer_bytes': len(transfer),
                        'messages': len(value.get('messages') or [])}

    def verify_handoff(value):
        if not isinstance(value, dict):
            raise ValueError('handoff result object required')
        state['handoff'] = value
        checks, phases, validation = receipt_checks()
        if not all(checks.values()):
            raise ValueError('handoff receipt refused: ' +
                             ', '.join(key for key, passed in checks.items() if not passed))
        return checks, {'handoff_result_sha256':
                        sha256_bytes((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8')),
                        'handoff_phases_recorded': sorted(phases),
                        'session_validation': validation}

    control = Path(facts['control'])
    with ExitStack() as stack:
        for pool in pools:
            try:
                stack.enter_context(resource_guard(control, cells[pool], plan))
            except ValueError as error:
                raise Refused(['guard: ' + str(error)])
        for path in sorted(control.parent.glob('*/state.sqlite')):
            for pool in pools:
                active = conflicts(path.parent, cells[pool], plan)
                if active:
                    raise Refused(['leases: active original cell lease on %s in %s' % (pool, path)])
        output = private_dir(output)
        write_new(output / 'started.json', {
            'schema': 'c4-native-handoff-started-v1', 'attempt': attempt,
            'request': {'path': str(request['draft']['path'])}, 'draft': dict(request['draft']),
            'guard': {'pools': list(pools), 'resources': [pool + ':' + pool for pool in pools]},
            'phase_order': list(PHASES), 'model_calls_max': draft['relay']['max_requests'],
            'automatic_retry': False})
        try:
            state['phase'] = 'quiesce'
            external('quiesce', 1, facts['phases']['quiesce']['argv'], verify_quiesce)
            state['phase'] = 'export'
            local('export', 2, {'session_matches': state['export']['info']['id'] == session_id,
                                'export_sha_matches_pinned': state['export_sha256'] == export_sha})
            for name in ('source-stop', 'lease_check', 'target-start', 'resume', 'export_after'):
                delegated_intent(name)
            state['phase'] = 'restore'
            external('restore', 6, facts['phases']['restore']['argv'], verify_handoff)
            state['phase'] = 'source-stop'
            checks, phases, validation = receipt_checks()
            delegated_result('source-stop', {
                'recorded': True,
                'before_target_start': 'target-start' in phases and
                (phases['source-stop']['start_ns'] < phases['target-start']['start_ns'])})
            delegated_result('lease_check', {
                'recorded': True,
                'before_target_start': (phases['lease_check']['start_ns'] < phases['target-start']['start_ns'])})
            delegated_result('target-start', {'recorded': True})
            state['phase'] = 'resume'
            delegated_result('resume', {'recorded': True, 'first_completed_tool_ack': checks['resume_tool_ack']})
            state['phase'] = 'export_after'
            delegated_result('export_after', {
                'recorded': True, 'session_validation': checks['prefix_preserved'],
                'new_completed_tools': checks['new_completed_tools']})
            result = {
                'schema': 'c4-native-handoff-result-v1', 'passed': True, 'attempt': attempt,
                'session_id': session_id, 'original_export_sha256': export_sha,
                'source_pool': pools[0], 'target_pool': pools[1],
                'restore_method': 'shared-workspace-db',
                'phases': list(PHASES), 'phase_status': dict(status),
                'model_calls_max': draft['relay']['max_requests'],
                'resume_requests_issued_by_outer_driver': 0,
                'workspace_copy_bytes': 0, 'native_session_json_transferred_bytes': 0,
                'handoff_receipt': {'path': str(output / '06-restore' / 'stdout'),
                                    'sha256': digest(output / '06-restore' / 'stdout')},
                'closure_complete': False,
                'scope': 'native state restore through the original entry point only; cold full-tree read, '
                         'two zero-debt samples, normal stop and archive remain separate',
                'automatic_retry': False}
            write_new(output / 'result.json', result)
            summary(True)
            return result
        except BaseException as error:
            close_pending()
            failed = {'schema': 'c4-native-handoff-failed-v1', 'passed': False, 'attempt': attempt,
                      'phase': state['phase'], 'error_type': type(error).__name__,
                      'error': str(error)[:4000], 'automatic_retry': False,
                      'first_failure_retained': True,
                      'external_actions_attempted': state['attempted']}
            if state['attempted']:
                closed = settle(probe, ('source', 'target'), deadline_seconds, interval_seconds, sleep, clock)
                failed['safe_close'] = closed
                if not closed.get('safe'):
                    write_new(output / 'lock-held.json', {
                        'schema': 'c4-native-handoff-lock-held-v1', 'passed': False,
                        'safe_close': closed, 'automatic_retry': False,
                        'pool_reuse': 'refused until a reviewed close probe shows both clients and the '
                                      'source backend safe',
                        'scope': 'the OS lock ends when this process exits; this receipt forbids pool reuse'})
            failed['retained'] = sorted(path.name for path in output.iterdir())
            summary(False, type(error).__name__)
            write_new(output / 'failed.json', failed)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--execute', action='store_true',
                        help='actually run the reviewed commands; without it the executor only refuses')
    args = parser.parse_args(argv)
    try:
        path = args.request
        if not path.is_absolute() or path.is_symlink():
            raise ValueError('absolute pinned reviewed request required')
        if not SHA.fullmatch(args.sha256):
            raise ValueError('sha256 of the reviewed request required')
        if digest(path) != args.sha256:
            raise ValueError('reviewed request changed')
        request = load(path)
    except Exception as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__,
                          'automatic_retry': False}))
        return 1
    executor = subprocess_executor() if args.execute else refuse
    probe = probe_from(request, executor) if args.execute else refuse_probe
    try:
        run(request, args.out, executor=executor, probe=probe,
            deadline_seconds=300.0 if args.execute else 0.0)
    except Exception as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__,
                          'error': str(error)[:1000], 'automatic_retry': False}))
        return 1
    print(json.dumps({'passed': True, 'output': str(args.out), 'automatic_retry': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
