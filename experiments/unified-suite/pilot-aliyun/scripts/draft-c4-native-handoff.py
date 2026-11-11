#!/usr/bin/env python3
"""Draft one reviewed C4 native move request from the original verified checkpoint.

Read-only preparation: never contacts a client, backend, relay or model. Emits
nothing unless every pin resolves and the checkpoint session/export bytes, the
two client identities and the source-pool backend are all explicit and mutually
consistent. The output is a draft (reviewed=false, execution_enabled=false); the
tested original entry point stays `python3 -m bench.runtime.handoff`.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

SCHEMA = 'c4-native-handoff-request-v1'
PINS_SCHEMA = 'c4-native-handoff-pins-v1'
FIRST_BODY_SHA256 = '3a792e6a0068fc3e73332289cb8be92e16315ae8cb4a2c496ed7d2d630631537'
ACTIVE_RELAY_PORT = 18443
WORKSPACE = '/srv/ax-pilot-session/workspace'
SESSION_STATE = {'workspace': WORKSPACE, 'project': WORKSPACE + '/repo',
                 'config': WORKSPACE + '/opencode.json',
                 'xdg': {name: WORKSPACE + '/' + name for name in ('data', 'state', 'cache', 'config')}}
PHASES = [
    dict(order=1, name='quiesce', via='outer', peer='source', timeout_seconds=60),
    dict(order=2, name='export', via='local', peer='source', timeout_seconds=60),
    dict(order=3, name='source-stop', via='handoff', peer='source', timeout_seconds=60),
    dict(order=4, name='lease_check', via='handoff', peer='source', timeout_seconds=60),
    dict(order=5, name='target-start', via='handoff', peer='target', timeout_seconds=60),
    dict(order=6, name='restore', via='outer', peer='target', timeout_seconds=60),
    dict(order=7, name='resume', via='handoff', peer='target', timeout_seconds=205),
    dict(order=8, name='export_after', via='handoff', peer='target', timeout_seconds=60),
]
REQUIRED_PINS = {
    'plan': 'canonical suite plan',
    'inventory': 'canonical two-pool inventory',
    'checkpoint_receipt': 'verified checkpoint receipt',
    'opencode_release': 'original OpenCode release receipt',
    'handoff_spec': 'reviewed experiment-handoff-v1 spec',
    'handoff_module': 'bench/runtime/handoff.py entrypoint',
    'session_worker': 'bounded native session worker',
    'session_relay': 'bounded native session relay',
    'phase_template': 'draft-session-phase.py',
    'ssh': 'ssh client configuration',
}
SECRET_NAMES = ('token', 'secret', 'password', 'api_key', 'apikey', 'relay_key', 'master_key', 'authorization')
SECRET_SUFFIXES = ('_key', '_secret', '_password')
SHA = re.compile(r'[0-9a-f]{64}')
ATTEMPT = re.compile(r'[a-z0-9-]{3,24}')
POOL = re.compile(r'pool-[a-z0-9-]+')
HOSTNAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]*')
REDIS_URL = re.compile(r'redis://([^:/]+):(\d+)(?:/(\d+))?')
OBJECTS_URL = re.compile(r'http://([^:/]+):(\d+)')
BUCKET = re.compile(r'[a-z0-9][a-z0-9.-]{2,61}')
RELAY_URL = re.compile(r'http://127\.0\.0\.1:(\d{2,5})/v1')


class Refused(ValueError):
    def __init__(self, problems):
        self.problems = tuple(problems)
        super().__init__('refused: ' + '; '.join(self.problems))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def ref(path):
    return {'path': str(path), 'sha256': digest(path)}


def write_private(path, value):
    path = Path(path)
    with open(path, 'x', encoding='utf-8', newline='\n',
              opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def inside(value, root):
    return Path(value).is_relative_to(root) and Path(value) != Path(root)


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


def client_problem(value):
    if not isinstance(value, dict):
        return 'client identity missing'
    for name in ('instance_id', 'private_ip', 'hostname'):
        if not isinstance(value.get(name), str) or not value[name]:
            return name + ' missing'
    if not value['instance_id'].startswith('i-'):
        return 'actual instance ID required'
    try:
        address = ipaddress.ip_address(value['private_ip'])
    except ValueError:
        return 'explicit private role address required'
    if not address.is_private:
        return 'explicit private role address required'
    if not HOSTNAME.fullmatch(value['hostname']):
        return 'actual hostname required'
    if value.get('role', 'client') != 'client':
        return 'client role required'
    return None


def secret_paths(value, prefix=''):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            here = (prefix + '.' + str(key)) if prefix else str(key)
            if (name in SECRET_NAMES or name.endswith(SECRET_SUFFIXES)) and item not in (None, '', False):
                found.append(here)
            found += secret_paths(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += secret_paths(item, '%s[%d]' % (prefix, index))
    return found


def pools_from_inventory(document, source_pool, target_pool):
    values = document.get('pools', [document]) if isinstance(document, dict) else None
    if not isinstance(values, list) or not values:
        raise ValueError('nonempty actual pilot pools required')
    by_pool = {}
    for value in values:
        if not isinstance(value, dict) or not POOL.fullmatch(str(value.get('pool_id'))):
            raise ValueError('explicit pool identity required')
        by_pool[value['pool_id']] = value
    for pool in (source_pool, target_pool):
        if pool not in by_pool:
            raise ValueError('inventory has no pool ' + str(pool))
    return by_pool


def inspect(pins, checkpoint_directory):
    """Return (problems, facts); any problem means no request may be emitted."""
    problems = []
    facts = {}

    def fail(label, message):
        problems.append(label + ': ' + message)

    def read(label, pin):
        try:
            return load(pin['path'])
        except Exception as error:
            fail(label, 'pinned file is not readable JSON (' + type(error).__name__ + ')')
            return None

    if not isinstance(pins, dict):
        return ['pins: document must be a JSON object'], {}

    if pins.get('schema') != PINS_SCHEMA:
        fail('pins.schema', 'expected ' + PINS_SCHEMA)
    if pins.get('purpose') != 'experiment':
        fail('pins.purpose', 'only purpose=experiment is accepted; adapter-test is not a bypass')
    attempt = pins.get('attempt')
    if not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt):
        fail('pins.attempt', 'unique short attempt required')
    else:
        facts['attempt'] = attempt
    for label in secret_paths(pins):
        fail('pins.secrets', 'private config or token value in the pins document: ' + label)

    for label in REQUIRED_PINS:
        message = pin_problem(pins.get(label))
        if message:
            fail(label, message + ' (' + REQUIRED_PINS[label] + ')')
        else:
            facts[label] = pins[label]
    for label in ('session_worker', 'session_relay'):
        if label in facts and 'bounded' not in Path(facts[label]['path']).name:
            fail(label, 'the bounded ' + label.replace('_', ' ') + ' must be pinned, not the unbounded entry point')

    source_pool, target_pool = pins.get('source_pool'), pins.get('target_pool')
    for label, pool in (('source_pool', source_pool), ('target_pool', target_pool)):
        if not isinstance(pool, str) or not POOL.fullmatch(pool):
            fail(label, 'explicit pilot pool required')
    if isinstance(source_pool, str) and source_pool == target_pool:
        fail('target_pool', 'source and target must be two existing client pools')
    facts['source_pool'], facts['target_pool'] = source_pool, target_pool
    if pins.get('roles') != {'source': 'client', 'target': 'client'}:
        fail('pins.roles', "explicit {'source':'client','target':'client'} required")
    if pins.get('restore_method') != 'shared-workspace-db':
        fail('pins.restore_method', 'the shared-workspace-db native restore method is required')

    control = pins.get('canonical_control')
    if not isinstance(control, str) or not Path(control).is_absolute():
        fail('pins.canonical_control', 'absolute canonical control directory required')
    elif not Path(control).is_dir() or Path(control).is_symlink():
        fail('pins.canonical_control', 'canonical control directory missing')
    else:
        facts['control'] = control
        for label in ('plan', 'inventory'):
            if label in facts and not inside(facts[label]['path'], control):
                fail(label, 'canonical control input must live under ' + control)

    session_id, export_sha = pins.get('session_id'), pins.get('original_export_sha256')
    if not isinstance(session_id, str) or not session_id.startswith('ses_'):
        fail('pins.session_id', 'original native session ID required')
    if not isinstance(export_sha, str) or not SHA.fullmatch(export_sha):
        fail('pins.original_export_sha256', 'original native export SHA required')
    facts['session_id'], facts['export_sha256'] = session_id, export_sha

    source_client, target_client = pins.get('source_client'), pins.get('target_client')
    for label, value in (('source_client', source_client), ('target_client', target_client)):
        message = client_problem(value)
        if message:
            fail(label, message)
        else:
            facts[label] = dict(value)
    if 'source_client' in facts and 'target_client' in facts:
        if facts['source_client']['instance_id'] == facts['target_client']['instance_id']:
            fail('target_client', 'move requires two different client instances')
        if facts['source_client']['private_ip'] == facts['target_client']['private_ip']:
            fail('target_client', 'two distinct private client addresses required')

    directory = Path(checkpoint_directory) if isinstance(checkpoint_directory, (str, Path)) else None
    if directory is None or not directory.is_absolute():
        fail('checkpoint', 'absolute original verified checkpoint directory required')
    elif directory.is_symlink() or not directory.is_dir():
        fail('checkpoint', 'original verified checkpoint directory missing')
    else:
        point, tree_path = directory / 'checkpoint.json', directory / 'source-tree.json'
        if not point.is_file() or point.is_symlink():
            fail('checkpoint.checkpoint.json', 'missing')
        if not tree_path.is_file() or tree_path.is_symlink():
            fail('checkpoint.source-tree.json', 'missing')
        if point.is_file() and tree_path.is_file():
            checkpoint = read('checkpoint.checkpoint.json', {'path': point})
            tree = read('checkpoint.source-tree.json', {'path': tree_path})
            if checkpoint is not None and tree is not None:
                facts['checkpoint'] = ref(point)
                facts['source_tree'] = ref(tree_path)
                if not isinstance(checkpoint, dict):
                    fail('checkpoint.checkpoint.json', 'JSON object required')
                else:
                    facts['checkpoint_document'] = checkpoint
                    if checkpoint.get('session_id') != session_id:
                        fail('checkpoint.session_id', 'differs from the pinned original session')
                    if checkpoint.get('session_export_sha256') != export_sha:
                        fail('checkpoint.session_export_sha256', 'differs from the pinned original export SHA')
                    if checkpoint.get('source_process_exited') is not True:
                        fail('checkpoint.source_process_exited', 'the original process must have exited')
                    if checkpoint.get('sqlite_quick_check') != 'ok':
                        fail('checkpoint.sqlite_quick_check', 'the original native database must be intact')
                    if checkpoint.get('contains_provider_master_key') is not False:
                        fail('checkpoint.contains_provider_master_key', 'no provider master key may be in the checkpoint')
                    if checkpoint.get('contains_active_relay_key') is not False:
                        fail('checkpoint.contains_active_relay_key', 'no active relay key may be in the checkpoint')
                if not isinstance(tree, dict) or not tree:
                    fail('checkpoint.source-tree.json', 'nonempty frozen tree required')
                else:
                    journals = [name for name, row in tree.items()
                                if name.endswith(('.db-wal', '.db-journal'))
                                and (not isinstance(row, dict) or row.get('bytes', 0))]
                    if journals:
                        fail('checkpoint.source-tree.json',
                             'unclosed native journal: ' + ', '.join(sorted(journals)))

    if 'checkpoint' in facts and 'checkpoint_receipt' in facts:
        value = read('checkpoint_receipt', facts['checkpoint_receipt'])
        if isinstance(value, dict):
            facts['receipt_document'] = value
            if value.get('passed') is not True:
                fail('checkpoint_receipt', 'passed checkpoint receipt required')
            if value.get('checkpoint_sha256') != facts['checkpoint']['sha256']:
                fail('checkpoint_receipt.checkpoint_sha256', 'differs from the installed checkpoint metadata')
            if value.get('session_id') != session_id:
                fail('checkpoint_receipt.session_id', 'differs from the pinned original session')
            if (value.get('contains_active_relay_key') is not False or
                    value.get('contains_provider_master_key') is not False):
                fail('checkpoint_receipt', 'the receipt must exclude active relay and provider master keys')

    if 'opencode_release' in facts:
        value = read('opencode_release', facts['opencode_release'])
        if isinstance(value, dict):
            opencode_sha = value.get('opencode_sha256')
            if not isinstance(opencode_sha, str) or not SHA.fullmatch(opencode_sha):
                fail('opencode_release.opencode_sha256', 'exact original binary SHA required')
            else:
                facts['opencode_sha256'] = opencode_sha
                if ('checkpoint_document' in facts and
                        facts['checkpoint_document'].get('opencode_binary_sha256') != opencode_sha):
                    fail('opencode_release.opencode_sha256', 'differs from the checkpoint OpenCode binary SHA')
                if ('receipt_document' in facts and
                        facts['receipt_document'].get('opencode_sha256') != opencode_sha):
                    fail('opencode_release.opencode_sha256', 'differs from the checkpoint receipt OpenCode SHA')

    if 'plan' in facts:
        value = read('plan', facts['plan'])
        if isinstance(value, dict):
            facts['plan_document'] = value
            clusters = (value.get('config') or {}).get('clusters')
            if not isinstance(clusters, list) or not clusters:
                fail('plan', 'frozen suite plan with explicit clusters required')
            else:
                ids = {cluster.get('id') for cluster in clusters if isinstance(cluster, dict)}
                for pool in (source_pool, target_pool):
                    if pool not in ids:
                        fail('plan', 'frozen plan has no cluster ' + str(pool))
            cells = value.get('cells')
            if not isinstance(cells, list) or not cells:
                fail('plan', 'frozen plan cells required')
            facts['plan_handoff_cells'] = sorted(c.get('id') for c in cells or []
                                                 if isinstance(c, dict) and c.get('workload') == 'handoff')

    if 'inventory' in facts:
        value = read('inventory', facts['inventory'])
        by_pool = {}
        if value is not None:
            try:
                by_pool = pools_from_inventory(value, source_pool, target_pool)
            except ValueError as error:
                fail('inventory', str(error))
        facts['inventory_pools'] = by_pool
        for side in ('source', 'target'):
            client = facts.get(side + '_client')
            if not client or not by_pool:
                continue
            roles = (by_pool.get(facts[side + '_pool']) or {}).get('roles') or {}
            actual = roles.get('client') or {}
            for name in ('instance_id', 'private_ip', 'hostname'):
                if name in actual and client.get(name) != actual.get(name):
                    fail(side + '_client.' + name, 'differs from the pinned inventory client')

    if 'handoff_spec' in facts:
        value = read('handoff_spec', facts['handoff_spec'])
        if isinstance(value, dict):
            facts['spec_document'] = value
            validate = None
            for label, wanted in (('schema', 'experiment-handoff-v1'), ('mode', 'move'),
                                  ('restore_method', 'shared-workspace-db')):
                if value.get(label) != wanted:
                    fail('handoff_spec.' + label, wanted + ' required')
            if value.get('reviewed') is not True:
                fail('handoff_spec.reviewed', 'reviewed handoff spec required')
            if value.get('session_id') != session_id:
                fail('handoff_spec.session_id', 'differs from the pinned original session')
            if value.get('export_transfer_sha256') != export_sha:
                fail('handoff_spec.export_transfer_sha256', 'differs from the original export SHA')
            if (value.get('source') or {}).get('anchor') != (value.get('target') or {}).get('anchor'):
                fail('handoff_spec', 'source and target must reopen the same shared anchor')
            for side in ('source', 'target'):
                if (value.get(side) or {}).get('session_state') != SESSION_STATE:
                    fail('handoff_spec.' + side + '.session_state',
                         'must be exactly the canonical workspace, repo and XDG paths')
            if 'handoff_module' in facts:
                try:
                    import bench.runtime.handoff as module
                except Exception as error:
                    fail('handoff_module', 'runtime entrypoint not importable (' + type(error).__name__ + ')')
                else:
                    if Path(module.__file__).resolve() != Path(facts['handoff_module']['path']).resolve():
                        fail('handoff_module', 'pinned path is not the imported bench.runtime.handoff entrypoint')
                    else:
                        validate = module.validate
            if validate is not None:
                try:
                    validate(value)
                except Exception as error:
                    fail('handoff_spec.protocol', str(error))

    relay = pins.get('relay')
    if not isinstance(relay, dict):
        fail('relay', 'independent relay endpoint, fixed body SHA, four-request cap and private config required')
    else:
        url = relay.get('url')
        match = RELAY_URL.fullmatch(url) if isinstance(url, str) else None
        if not match:
            fail('relay.url', 'explicit loopback relay URL such as http://127.0.0.1:<port>/v1 required')
        else:
            port = int(match.group(1))
            if port == ACTIVE_RELAY_PORT:
                fail('relay.url', 'the independent relay must not replace the active %d service' % ACTIVE_RELAY_PORT)
            elif not 1024 <= port <= 65535:
                fail('relay.url', 'unprivileged independent port required')
        body = relay.get('first_body_sha256')
        if not isinstance(body, str) or not SHA.fullmatch(body):
            fail('relay.first_body_sha256', 'original first forwarded body SHA required')
        elif body != FIRST_BODY_SHA256:
            fail('relay.first_body_sha256', 'must be the fixed original first forwarded body SHA')
        if relay.get('max_requests') != 4:
            fail('relay.max_requests', 'exactly 4 distinct requests')
        if relay.get('output_tokens') != 512:
            fail('relay.output_tokens', 'the qualified 512-token output cap')
        if relay.get('thinking') != 'disabled':
            fail('relay.thinking', 'disabled thinking required')
        wall = relay.get('wall_seconds')
        if type(wall) is not int or not 1 <= wall <= 900:
            fail('relay.wall_seconds', 'integer 1..900 seconds required')
        message = pin_problem(relay.get('private_config'))
        if message:
            fail('relay.private_config', message + ' (owner-only relay configuration)')
        facts['relay'] = relay

    backend = pins.get('backend')
    if not isinstance(backend, dict):
        fail('backend', 'the source-pool Redis database and RustFS bucket must be given explicitly')
    else:
        facts['backend'] = backend
        if backend.get('pool') != source_pool:
            fail('backend.pool', 'the shared backend must be the source pool')
        redis = backend.get('redis') if isinstance(backend.get('redis'), dict) else {}
        objects = backend.get('objects') if isinstance(backend.get('objects'), dict) else {}
        match = REDIS_URL.fullmatch(redis.get('url') or '')
        if not match:
            fail('backend.redis.url', 'explicit redis://<host>:6379[/db] URL required')
        else:
            host, port, url_database = match.group(1), int(match.group(2)), match.group(3)
            if port != 6379:
                fail('backend.redis.url', 'the original Redis port 6379 required')
            database = redis.get('database')
            if type(database) is not int or not 20 <= database <= 255:
                fail('backend.redis.database', 'explicit database in 20..255 required')
            if url_database is not None and type(database) is int and int(url_database) != database:
                fail('backend.redis.url', 'database in the URL differs from backend.redis.database')
            facts['redis'] = {'host': host, 'port': port, 'database': database}
        match = OBJECTS_URL.fullmatch(objects.get('endpoint') or '')
        if not match:
            fail('backend.objects.endpoint', 'explicit http://<host>:9000 endpoint required')
        else:
            host, port = match.group(1), int(match.group(2))
            if port != 9000:
                fail('backend.objects.endpoint', 'the original RustFS port 9000 required')
            bucket = objects.get('bucket')
            if not isinstance(bucket, str) or not BUCKET.fullmatch(bucket):
                fail('backend.objects.bucket', 'new bucket name required')
            facts['objects'] = {'host': host, 'port': port, 'bucket': bucket}
        roles = (facts.get('inventory_pools', {}).get(source_pool) or {}).get('roles') or {}
        if 'redis' in facts and (roles.get('metadata') or {}).get('private_ip') != facts['redis']['host']:
            fail('backend.redis.url', 'host must be the source-pool metadata address from the pinned inventory')
        if 'objects' in facts and (roles.get('objects') or {}).get('private_ip') != facts['objects']['host']:
            fail('backend.objects.endpoint', 'host must be the source-pool object address from the pinned inventory')

    return problems, facts


def document(pins, facts, pins_path=None):
    return {
        'schema': SCHEMA,
        'reviewed': False,
        'execution_enabled': False,
        'automatic_retry': False,
        'status': 'draft only; review, the GAPS.zh.md closure and an explicit reviewed run request are still required',
        'attempt': facts['attempt'],
        'canonical_control': facts['control'],
        'roles': {'source': 'client', 'target': 'client'},
        'source': dict(facts['source_client'], pool=facts['source_pool'], role='client', peer='source'),
        'target': dict(facts['target_client'], pool=facts['target_pool'], role='client', peer='target'),
        'shared_backend': {
            'pool': facts['source_pool'],
            'redis': dict(facts['redis']),
            'objects': dict(facts['objects']),
            'target_pool_backend_used': False,
            'target_pool_objects_created': False,
            'scope': 'one shared Redis/RustFS backend on the source pool; the target pool contributes only a client',
        },
        'session': {
            'session_id': facts['session_id'],
            'original_export_sha256': facts['export_sha256'],
            'opencode_sha256': facts['opencode_sha256'],
            'checkpoint': facts['checkpoint'],
            'source_tree': facts['source_tree'],
            'checkpoint_receipt': dict(facts['checkpoint_receipt']),
            'first_body_sha256': FIRST_BODY_SHA256,
        },
        'restore': {
            'method': 'shared-workspace-db',
            'session_state': json.loads(json.dumps(SESSION_STATE)),
            'native_entrypoint': {
                'module': 'bench.runtime.handoff',
                'argv': ['python3', '-m', 'bench.runtime.handoff', '--spec', '<reviewed spec>',
                         '--sha256', '<spec sha256>', '--output', '<new evidence directory>'],
                'spec': dict(facts['handoff_spec']),
                'module_pin': dict(facts['handoff_module']),
            },
            'workspace_copy_bytes': 0,
            'native_session_json_transferred_bytes': 0,
            'target_pool_backend_initialized': False,
        },
        'relay': {
            'url': facts['relay']['url'],
            'first_body_sha256': FIRST_BODY_SHA256,
            'max_requests': 4,
            'output_tokens': 512,
            'thinking': 'disabled',
            'wall_seconds': facts['relay']['wall_seconds'],
            'private_config': dict(facts['relay']['private_config']),
            'replaces_active_relay_service': False,
            'provider_master_key_location': 'relay private config only',
            'request_bodies_logged': False,
        },
        'phases': [dict(phase) for phase in PHASES],
        'plan_reference': {
            'plan': dict(facts['plan']),
            'inventory': dict(facts['inventory']),
            'handoff_cells_in_canonical_plan': facts.get('plan_handoff_cells', []),
            'independent_route_plan': None,
            'note': 'the canonical plan is a lock reference only; an independent native route plan is still missing',
        },
        'pins': {label: dict(facts[label]) for label in REQUIRED_PINS},
        'cold_check': {
            'required': True,
            'completed': False,
            'verification': 'full-cold-v1',
            'before_any_native_export_rewrites_the_shared_database': True,
            'scope': 'full tree read on the target after handoff; re-export must not be used to warm it',
        },
        'zero_debt': {
            'required': True,
            'completed': False,
            'samples': 2,
            'requires_normal_stop': True,
            'scope': 'two consecutive native zero-debt samples after the cold full-tree read, then a normal stop',
        },
        'archive_excludes': [
            'relay private configuration files',
            'provider master key and relay key values',
            'request or response bodies and prompt text',
            'Authorization headers and token text',
            'any copy of the workspace tree',
            'any copy of the native session database',
            'target-pool backend credentials',
        ],
        'required_before_execution': [
            'reviewed copy of this draft with reviewed=true and execution_enabled=true; the draft itself never executes',
            'independent native handoff route plan derived from the frozen handoff case',
            'explicit root handoff of both pools after the running source-pool unit closes',
            'fresh two-pool resource guard and a cell_leases audit over every control state.sqlite',
            'source-only checkpoint installation adapter compatible with the bounded worker',
            'independent bounded relay private config, PID receipt and idle SIGTERM',
            'fresh DBSIZE=0 probe of the chosen source-pool Redis database and absence of the new bucket',
            'target mount spec, target binary pins and fresh target boot/slot evidence',
            'outer full-tree cold read, two native zero-debt samples and normal stop',
            'archive validation excluding private configs and token text',
        ],
        'open_gaps': [
            'real two-side handoff/cohort spec and per-phase remote action pins',
            'independent relay service still missing',
            'outer cold/closure implementation still missing',
            'formal admission of the native route still missing',
        ],
        'generated_from': {
            'pins': ref(pins_path) if pins_path is not None else None,
            'checkpoint_directory': facts['checkpoint_directory'],
            'checkpoint': facts['checkpoint'],
            'source_tree': facts['source_tree'],
        },
    }


def build(pins, checkpoint_directory, pins_path=None):
    problems, facts = inspect(pins, checkpoint_directory)
    if problems:
        raise Refused(problems)
    facts['checkpoint_directory'] = str(Path(checkpoint_directory))
    return document(pins, facts, pins_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--pins', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        pins = load(args.pins)
    except Exception as error:
        print(json.dumps({'passed': False, 'refused': True,
                          'problems': ['pins: not readable JSON (' + type(error).__name__ + ')']}))
        return 1
    try:
        value = build(pins, args.checkpoint, args.pins)
    except Refused as error:
        print(json.dumps({'passed': False, 'refused': True, 'problems': list(error.problems)}))
        return 1
    output = Path(args.out)
    if output.exists() or output.is_symlink():
        print(json.dumps({'passed': False, 'refused': True, 'problems': ['out: a new output file is required']}))
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    write_private(output, value)
    print(json.dumps({'passed': True, 'draft': ref(output), 'execution_enabled': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
