#!/usr/bin/env python3
"""Coordinator controller for one guarded Pool B JuiceFS ``gc`` maintenance step.

What this controller does, in order:

1. verify the reviewed request (``--sha256``) and every member it pins,
2. take the coordinator physical lock
   ``/srv/ax-pilot-control/parallel-control-01/runner-locks`` for the whole
   Pool B resource set (all three hosts and all nine disks, so the stopped
   client cannot be touched by anybody else either),
3. start **only** ``objects`` and ``metadata``; after every single mutation it
   re-reads the client instance and requires ``Stopped`` + ``StopCharging``,
4. ship the reviewed JuiceFS binary from the jump (``juicefs.path`` with the
   pinned SHA256) to the objects node, then drive the node script over ssh:
   the objects node runs ``juicefs gc --threads 1 <meta>`` synchronously, the
   metadata node proves the original AOF identity and that no temporary Redis
   session survived,
5. pull the raw gc stdout/stderr and both JSON-lines protocol logs into the
   fresh output directory,
6. stop both instances again with ``ForceStop=False, StoppedMode='StopCharging'``
   and re-read them,
7. write the cost receipts and finally ``result.json``.

The client ``i-bp1fmmfkcpr8w99j2bsy`` is never passed to ``StartInstance``:
``start_instance`` only accepts the two startable roles, rejects the client ID
explicitly, and ``start_guarded`` re-asserts the client stays StopCharging after
every start. ``--request`` must be a reviewed, one-shot request carrying
``automatic_retry: false``.
"""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cloud  # noqa: E402  (reviewed Aliyun CLI wrapper; no new signatures here)

CLIENT = 'i-bp1fmmfkcpr8w99j2bsy'
INSTANCES = {'objects': 'i-bp1bhdngko7nq3c3jrhp',
             'metadata': 'i-bp14nenf7osz9v1snsp2',
             'client': CLIENT}
START_ROLES = ('objects', 'metadata')
ALL_ROLES = ('objects', 'metadata', 'client')
JUICEFS_SHA256 = '6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
ALLOWLIST_SHA256 = '0a7c6e3751f14f3b4d0a45a30d4352bc0e232fc2e39954fec78535ce770f38cf'
SCHEMA = 'ax-pilot-b-c1-gc-request-v1'
DEFAULT_CONTROL = '/srv/ax-pilot-control/parallel-control-01'
ATTEMPT = re.compile(r'^[a-z0-9][a-z0-9-]{2,40}$')
META_URL = re.compile(r'^redis://10\.20\.2\.169:6379/([0-9]{1,3})$')
SSH_ALIAS = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
MARKER_ROOT = '/run'


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def digest(path):
    raw = Path(path).read_bytes()
    return hashlib.sha256(raw).hexdigest()


def ref(path):
    return {'path': str(path), 'sha256': digest(path)}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def read_json(path):
    return json.loads(Path(path).read_bytes())


def require_pin(pin, name):
    require(isinstance(pin, dict) and Path(pin.get('path', '')).is_absolute()
            and SHA256.match(pin.get('sha256') or '') is not None, 'Absolute '+name+' path and SHA256 required')
    return Path(pin['path'])


def pin_meta_url(value):
    match = META_URL.match(value or '')
    require(match is not None and 20 <= int(match.group(1)) < 256,
            'Original Pool B metadata URL required: redis://10.20.2.169:6379/<20..255>')
    return value


def load_request(path, expected_sha):
    raw = Path(path).read_bytes()
    require(SHA256.match(expected_sha or '') is not None and hashlib.sha256(raw).hexdigest() == expected_sha,
            'Reviewed request SHA256 differs')
    return json.loads(raw)


def validate_request(value):
    require(value.get('schema') == SCHEMA and value.get('reviewed') is True
            and value.get('automatic_retry') is False,
            'Reviewed one-shot '+SCHEMA+' with automatic_retry:false required')
    require(ATTEMPT.match(value.get('attempt') or '') is not None, 'Explicit short attempt name required')
    require(value.get('pool') == 'pool-b', 'Pool B only')
    require(value.get('instances') == INSTANCES, 'Exact Pool B objects/metadata/client identities required')
    pin_meta_url(value.get('meta_url'))
    juicefs = value.get('juicefs') or {}
    require(Path(juicefs.get('path', '')).is_absolute() and juicefs.get('sha256') == JUICEFS_SHA256,
            'Absolute path of the reviewed JuiceFS binary and its pinned SHA256 required')
    for name in ('node_script', 'quote', 'allowlist'):
        require_pin(value.get(name), name)
    require(value['allowlist']['sha256'] == ALLOWLIST_SHA256, 'Frozen Pool B nine-disk allowlist pin required')
    return value


def load_allowlist(pin):
    path = Path(pin['path'])
    require(digest(path) == pin['sha256'] == ALLOWLIST_SHA256, 'Frozen Pool B allowlist SHA256 differs')
    document = read_json(path)
    require(document.get('schema') == 'ax-pilot-pool-b-release-freeze-public-audit-v1'
            and document.get('passed') is True and document.get('campaign') == 'ax-pilot-20261111-b'
            and document.get('owned_instance_count') == 3 and document.get('owned_disk_count') == 9,
            'Frozen three-instance, nine-disk Pool B allowlist required')
    roles = document.get('roles') or {}
    require(set(roles) == set(ALL_ROLES), 'Allowlist must cover objects, metadata and client')
    for role in ALL_ROLES:
        require(roles[role]['instance']['id'] == INSTANCES[role], 'Allowlist instance differs: '+role)
        disks = roles[role].get('disks') or []
        require(disks and all(str(row.get('id', '')).startswith('d-') for row in disks),
                'Allowlist disk identities required: '+role)
    return document


def resource_ids(allowlist):
    """Every Pool B host and disk; the client is locked too, not only stopped."""
    result = []
    for role in ALL_ROLES:
        result.append('host:'+allowlist['roles'][role]['instance']['id'])
        result.extend('disk:'+row['id'] for row in allowlist['roles'][role]['disks'])
    require(len(result) == 12 and len(set(result)) == 12, 'Expected three hosts and nine disks')
    return result


def frozen_disks(allowlist, role):
    return sorted(row['id'] for row in allowlist['roles'][role]['disks'])


def pin_ledger(path):
    """Original Pool B creation receipts are *inputs* only; never rewritten."""
    path = Path(path)
    require(path.is_absolute() and path.is_dir(), 'Original Pool B creation ledger directory required')
    receipts = {}
    for name in ('client.created.json', 'metadata.created.json', 'objects.created.json'):
        receipt = path/name
        require(receipt.is_file(), 'Original Pool B creation receipt required: '+name)
        receipts[name] = ref(receipt)
    return receipts


GUARD = '''
import json,pathlib,sys
root=pathlib.Path('/srv/ax-pilot-control')
sys.path.insert(0,str(root/'code-19'))
from bench.runtime.pilot_pools import active_conflicts, resource_guard
from bench.suite_plan import check_plan
control=root/'parallel-control-01'
plan=check_plan(json.loads((control/'suite/plan.json').read_bytes()))
cells=[c for c in plan['cells'] if c['pool']=='pool-b']
if not cells:
    raise SystemExit('No pool-b cell in the canonical plan')
cell=cells[0]
with resource_guard(control,cell,plan):
    for state in sorted(root.glob('*/state.sqlite')):
        if active_conflicts(state.parent,cell,plan):
            raise SystemExit('Pool B has a conflicting lease: '+str(state))
    print('held',flush=True)
    if sys.stdin.readline()!='release\\n':
        raise SystemExit('guard connection ended')
'''


WIN_PATH = re.compile(r'^([A-Za-z]):[/\\](.*)$')


def local_config(config):
    """A Windows-style SSH config path stays Windows for ssh.exe but is read here via /mnt."""
    match = WIN_PATH.match(str(config))
    return Path('/mnt/'+match.group(1).lower()+'/'+match.group(2)) if match else Path(config)


@contextmanager
def remote_locked(ssh, guard_alias='ax-pilot-jump'):
    """Hold the coordinator's physical Pool B locks from this host.

    The Aliyun CLI and the Pool B receipts live on the operator host while the
    lock files live on the coordinator, so the guard runs there and is released
    on every exit path.
    """
    config = Path(ssh['config'])
    require(ssh.get('binary') and local_config(config).is_file(), 'Remote guard needs the pinned SSH transport')
    base = [str(ssh['binary']), '-T', '-F', str(config), '-oBatchMode=yes', '-oConnectTimeout=10',
            '-oGlobalKnownHostsFile=none', '-oForwardAgent=no', guard_alias]
    remote_script = '/tmp/ax-pilot-b-gc-guard-%d.py' % os.getpid()
    upload = subprocess.run(base + ['sh', '-c', 'umask 077; cat > '+shlex.quote(remote_script)],
                            input=GUARD, capture_output=True, text=True)
    require(upload.returncode == 0, 'Coordinator guard upload failed: '+upload.stderr[-400:])
    proc = subprocess.Popen(base + ['python3', '-B', remote_script],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def release():
        try:
            if proc.poll() is None:
                proc.stdin.write('release\n'); proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.stdin.close()
        except Exception:
            pass
        return proc.wait(timeout=60)

    line = proc.stdout.readline().strip()
    if line != 'held':
        detail = proc.stderr.read()[-500:] if proc.poll() is not None else 'unexpected '+repr(line)
        # A refused guard must never be left running with the physical locks held.
        try:
            release()
        except Exception:
            pass
        require(False, 'Coordinator guard unavailable: '+detail)
    try:
        yield
    except Exception:
        try:
            release()
        except Exception:
            pass
        raise
    else:
        require(release() == 0, 'Coordinator guard ended without a clean release')


@contextmanager
def locked(repo, control, ids, ssh=None):
    """Hold the coordinator physical locks; released on every exception path."""
    if ssh:
        with remote_locked(ssh):
            yield
        return
    repo = Path(repo)
    require((repo/'bench/runtime/pilot_pools.py').is_file(), 'Pinned bench runtime repository required')
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from bench.runtime.pilot_pools import resource_guard
    control = Path(control)
    require(control.is_absolute(), 'Absolute coordinator control directory required')
    # cell_resources() evaluates its default eagerly, so supply the exact plan.
    cell = {'pool': 'pool-b', 'resource_ids': list(ids)}
    plan = {'config': {'clusters': [{'id': 'pool-b', 'resource_ids': list(ids)}]}}
    with ExitStack() as stack:
        stack.enter_context(resource_guard(control, cell, plan))
        yield


def ssh_argv(binary, config, alias, remote):
    require(SSH_ALIAS.match(alias or '') is not None, 'Simple approved SSH alias required')
    require(local_config(config).is_file(), 'Pinned SSH config required')
    return [str(binary), '-T', '-F', str(config), '-oBatchMode=yes', '-oStrictHostKeyChecking=yes',
            '-oGlobalKnownHostsFile=none', '-oForwardAgent=no', '-oClearAllForwardings=yes',
            '-oServerAliveInterval=15', '-oServerAliveCountMax=3', alias,
            shlex.join([str(value) for value in remote])]


def remote_text(ssh, alias, remote, timeout=120):
    result = subprocess.run(ssh_argv(ssh['binary'], ssh['config'], alias, remote),
                            capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, 'Remote command failed on '+alias+': '+result.stderr[-400:])
    return result.stdout


def upload_script(ssh, alias, local, remote):
    data = Path(local).read_bytes()
    argv = ssh_argv(ssh['binary'], ssh['config'], alias,
                    ['sh', '-c', 'umask 077; mkdir -p '+shlex.quote(str(Path(remote).parent))
                     +'; cat > '+shlex.quote(remote)])
    subprocess.run(argv, input=data, check=True)
    observed = remote_text(ssh, alias, ['sha256sum', remote]).split()[0]
    require(observed == hashlib.sha256(data).hexdigest(), 'Remote node script SHA256 differs on '+alias)


def local_binary(ssh, remote_path, expected_sha, alias='ax-pilot-jump'):
    """Return local bytes for a pinned binary; pull it from the jump when needed."""
    path = Path(remote_path)
    if path.is_file():
        require(digest(path) == expected_sha, 'Local pinned binary SHA256 differs: '+str(path))
        return path
    folder = Path(tempfile.mkdtemp(prefix='ax-b-gc-'))
    target = folder/'juicefs'
    pull(ssh, alias, remote_path, target)
    require(digest(target) == expected_sha, 'Pulled binary SHA256 differs: '+remote_path)
    return target


def upload_pinned(ssh, alias, local, remote, expected_sha, mode=0o755):
    """Copy the reviewed JuiceFS binary from the jump; no cloud package needed."""
    data = Path(local).read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected_sha,
            'Local pinned binary SHA256 differs: '+str(local))
    argv = ssh_argv(ssh['binary'], ssh['config'], alias,
                    ['sh', '-c', 'umask 077; mkdir -p '+shlex.quote(str(Path(remote).parent))
                     +'; cat > '+shlex.quote(remote)+'; chmod '+format(mode, 'o')+' '+shlex.quote(remote)])
    subprocess.run(argv, input=data, check=True)
    observed = remote_text(ssh, alias, ['sha256sum', remote]).split()[0]
    require(observed == expected_sha, 'Remote JuiceFS binary SHA256 differs on '+alias)


def pull(ssh, alias, remote, local):
    with Path(local).open('xb') as stream:
        result = subprocess.run(ssh_argv(ssh['binary'], ssh['config'], alias, ['cat', remote]),
                                stdout=stream, stderr=subprocess.PIPE)
    require(result.returncode == 0,
            'Failed to pull '+remote+': '+result.stderr.decode('utf-8', 'replace')[-400:])


class NodeSession:
    """One long-lived ssh stdin/stdout JSON-lines protocol with a node script."""

    def __init__(self, ssh, alias, remote, log_path, error_path):
        self.alias = alias
        self.log_path, self.error_path = Path(log_path), Path(error_path)
        self.log = self.log_path.open('x', encoding='utf-8')
        self.error = self.error_path.open('x', encoding='utf-8')
        argv = ssh_argv(ssh['binary'], ssh['config'], alias, remote)
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.error, text=True, encoding='utf-8', bufsize=1)
        self.rows = queue.Queue()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self.log.write(line)
                self.log.flush()
                self.rows.put(line)
        finally:
            self.rows.put(None)

    def send(self, command):
        require(self.process.poll() is None, 'Node session already exited: '+self.alias)
        self.process.stdin.write(command+'\n')
        self.process.stdin.flush()

    def row(self, deadline):
        while True:
            remaining = deadline-time.monotonic()
            require(remaining > 0, 'Node protocol deadline expired: '+self.alias)
            try:
                line = self.rows.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                # A node that already died will never emit another row; do not
                # hold the physical locks until the long protocol deadline.
                require(self.process.poll() is None or not self.rows.empty(),
                        'Node process exited before '+self.alias+' reported it; inspect '+str(self.error_path))
                continue
            require(line is not None, 'Node closed the protocol unexpectedly: '+self.alias)
            value = json.loads(line)
            require(isinstance(value, dict) and value.get('status') != 'error',
                    'Node reported an error: '+str(value))
            return value

    def wait(self, status, deadline):
        while True:
            value = self.row(deadline)
            if value.get('status') == status:
                return value
            require(value.get('status') in ('preflight', 'snapshot', 'eof', 'ready', 'verify', 'gc', 'usage'),
                    'Unexpected node row: '+str(value.get('status')))

    def finish(self):
        """Close stdin after the node already emitted its `closed` row."""
        try:
            self.process.stdin.close()
        except OSError:
            pass
        code = self.process.wait(timeout=300)
        self.thread.join(timeout=30)
        self.log.close()
        self.error.close()
        require(code == 0, 'Node process exited '+str(code)+'; inspect '+str(self.error_path))

    def kill(self):
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        if self.process.poll() is None:
            try:
                self.process.kill()
            finally:
                self.process.wait(timeout=60)
        self.thread.join(timeout=30)
        for stream in (self.log, self.error):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def drive_nodes(ssh, request, out, options):
    """Start both node scripts, prove the AOF, run gc on objects, then close."""
    attempt = request['attempt']
    remote_dir = MARKER_ROOT+'/b-c1-gc-'+attempt
    remote_script = remote_dir+'/node.py'
    # /run is tmpfs and may be noexec: the reviewed binary lives on the system
    # disk, never on an objects/metadata data disk.
    remote_binary = '/var/tmp/b-c1-gc-'+attempt+'/juicefs'
    gc_out = remote_dir+'/gc'
    script = Path(request['node_script']['path'])
    require(digest(script) == request['node_script']['sha256'], 'Node script SHA256 differs')
    deadline = time.monotonic()+options['ready_deadline_seconds']
    sessions = {}
    try:
        # The objects node carries no JuiceFS package: ship the reviewed binary
        # from the jump and prove its SHA256 remotely before it is ever executed.
        upload_pinned(ssh, 'pool-b-objects',
                      str(local_binary(ssh, request['juicefs']['path'], JUICEFS_SHA256)),
                      remote_binary, JUICEFS_SHA256)
        for role in START_ROLES:
            alias = 'pool-b-'+role
            upload_script(ssh, alias, script, remote_script)
            remote = ['python3', '-u', remote_script, role, '--attempt', attempt,
                      '--gc-timeout', str(options['gc_timeout_seconds']),
                      '--du-timeout', str(options.get('du_timeout_seconds', 1800)),
                      '--eof-grace', str(options['eof_grace_seconds'])]
            if role == 'objects':
                remote += ['--juicefs', remote_binary, '--meta-url', request['meta_url']]
            sessions[role] = NodeSession(ssh, alias, remote, out/('node-'+role+'.jsonl'),
                                         out/('node-'+role+'.stderr.log'))
        ready = {role: sessions[role].wait('ready', deadline) for role in START_ROLES}
        sessions['metadata'].send('verify')
        verify_before = sessions['metadata'].wait('verify', deadline)
        require(verify_before.get('stage') == 'before' and verify_before.get('passed') is True
                and verify_before['aof']['unchanged'] is True,
                'Metadata AOF changed before gc; refuse')
        sessions['objects'].send('gc '+gc_out)
        gc_row = sessions['objects'].wait('gc', time.monotonic()+options['gc_timeout_seconds']+600)
        require(gc_row['exit']['exit_code'] == 0 and gc_row['exit']['timed_out'] is False
                and gc_row['post']['passed'] is True, 'JuiceFS gc did not complete cleanly')
        usage_row = sessions['objects'].wait(
            'usage', time.monotonic()+4*options.get('du_timeout_seconds', 1800)+600)
        sessions['metadata'].send('verify after')
        verify_after = sessions['metadata'].wait('verify', deadline)
        require(verify_after.get('stage') == 'after' and verify_after.get('passed') is True,
                'Post-gc Redis/AOF proof failed')
        closed = {}
        for role in START_ROLES:
            sessions[role].send('close')
            closed[role] = sessions[role].wait('closed', time.monotonic()+900)
            sessions[role].finish()
    finally:
        for session in sessions.values():
            session.kill()
    folder = Path(out)/'gc'
    folder.mkdir(mode=0o700)
    for name in ('gc.stdout', 'gc.stderr', 'gc-exit.json', 'gc-post.json'):
        pull(ssh, 'pool-b-objects', gc_out+'/'+name, folder/name)
    require(digest(folder/'gc.stdout') == gc_row['exit']['stdout']['sha256'], 'Pulled gc stdout differs')
    require(digest(folder/'gc.stderr') == gc_row['exit']['stderr']['sha256'], 'Pulled gc stderr differs')
    require(read_json(folder/'gc-exit.json')['argv'] == gc_row['exit']['argv'], 'Pulled gc argv differs')
    return dict(ready=ready, verify_before=verify_before, gc=gc_row, usage_after=usage_row['usage'],
                verify_after=verify_after, closed=closed,
                files={name: {'path': str(folder/name), 'sha256': digest(folder/name)}
                       for name in ('gc.stdout', 'gc.stderr', 'gc-exit.json', 'gc-post.json')})


def describe(cloud_client, instance_id):
    value = cloud_client.api('DescribeInstances', InstanceIds=json.dumps([instance_id]))
    rows = value['Instances']['Instance']
    require(len(rows) == 1 and rows[0]['InstanceId'] == instance_id, 'Exact instance readback required: '+instance_id)
    return value, rows[0]


def describe_disks(cloud_client, instance_id):
    value = cloud_client.api('DescribeDisks', InstanceId=instance_id)
    rows = value['Disks']['Disk']
    require(rows and all(row['InstanceId'] == instance_id for row in rows),
            'Exact attached disk readback required: '+instance_id)
    return value, rows


def assert_client_stopped(cloud_client, moment):
    value, row = describe(cloud_client, CLIENT)
    require(row.get('Status') == 'Stopped' and row.get('StoppedMode') == 'StopCharging',
            'Client '+CLIENT+' must stay Stopped/StopCharging ('+moment+'): '
            +str(row.get('Status'))+'/'+str(row.get('StoppedMode')))
    return dict(moment=moment, at=utcnow(), request_id=value.get('RequestId'), row=row)


def start_instance(cloud_client, role, settle_seconds=300):
    require(role in START_ROLES, 'Only objects and metadata may be started: '+str(role))
    instance = INSTANCES[role]
    require(instance != CLIENT, 'The client instance must never be started')
    _, row = describe(cloud_client, instance)
    if not (row.get('Status') == 'Stopped' and row.get('StoppedMode') == 'StopCharging'):
        # A previous stop may still be settling: wait for the exact StopCharging
        # state, but never start something that is running.
        require(row.get('Status') in ('Stopping', 'Stopped'),
                'Instance must be StopCharging before start: '+role
                +' ('+str(row.get('Status'))+'/'+str(row.get('StoppedMode'))+')')
        row = wait_stopped(cloud_client, role, settle_seconds)['row']
    at = utcnow()
    value = cloud_client.api('StartInstance', InstanceId=instance)
    return dict(role=role, instance_id=instance, at=at, request_id=value.get('RequestId'))


def start_guarded(cloud_client, sink=None):
    """Start only objects+metadata and re-prove the client is still stopped."""
    sink = {} if sink is None else sink
    assert_client_stopped(cloud_client, 'before start')
    for role in START_ROLES:
        sink[role] = start_instance(cloud_client, role)
        assert_client_stopped(cloud_client, 'after starting '+role)
    return sink


def wait_running(cloud_client, role, seconds):
    instance = INSTANCES[role]
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        value, row = describe(cloud_client, instance)
        if row.get('Status') == 'Running':
            # Aliyun keeps reporting the last StoppedMode after a start; only the
            # actual status decides readiness.
            return dict(at=utcnow(), request_id=value.get('RequestId'), row=row)
        require(row.get('Status') in ('Pending', 'Starting', 'Stopped', 'Stopping'),
                'Unexpected instance status: '+str(row.get('Status')))
        time.sleep(5)
    raise TimeoutError('Instance did not reach Running: '+instance)


def wait_stopped(cloud_client, role, seconds):
    instance = INSTANCES[role]
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        value, row = describe(cloud_client, instance)
        if row.get('Status') == 'Stopped':
            require(row.get('StoppedMode') == 'StopCharging', 'Instance stopped without StopCharging: '+role)
            return dict(at=utcnow(), request_id=value.get('RequestId'), row=row)
        require(row.get('Status') in ('Running', 'Stopping'), 'Unexpected instance status: '+str(row.get('Status')))
        time.sleep(5)
    raise TimeoutError('Instance did not reach Stopped/StopCharging: '+instance)


def stop_instance(cloud_client, role, seconds):
    instance = INSTANCES[role]
    _, row = describe(cloud_client, instance)
    if row.get('Status') == 'Stopped':
        require(row.get('StoppedMode') == 'StopCharging', 'Stopped instance is not StopCharging: '+role)
        return dict(role=role, instance_id=instance, at=utcnow(), request_id=None,
                    already_stopped=True, row=row)
    require(row.get('Status') == 'Running', 'Unexpected instance status before stop: '+str(row.get('Status')))
    at = utcnow()
    value = cloud_client.api('StopInstance', InstanceId=instance, ForceStop=False, StoppedMode='StopCharging')
    confirmed = wait_stopped(cloud_client, role, seconds)
    return dict(role=role, instance_id=instance, at=at, request_id=value.get('RequestId'),
                readback_request_id=confirmed['request_id'], already_stopped=False, row=confirmed['row'])


def verify_configuration(cloud_client, allowlist):
    checked = {}
    for role in ALL_ROLES:
        _, row = describe(cloud_client, INSTANCES[role])
        frozen = allowlist['roles'][role]['instance']
        require(row.get('InstanceType') == frozen['type'] and row.get('ZoneId') == frozen['zone']
                and row.get('InstanceChargeType') == 'PostPaid'
                and row.get('InternetMaxBandwidthOut') == 0,
                'Instance configuration differs from the frozen allowlist: '+role)
        _, disks = describe_disks(cloud_client, INSTANCES[role])
        require(sorted(row['DiskId'] for row in disks) == frozen_disks(allowlist, role),
                'Attached disk set differs from the frozen allowlist: '+role)
        require(all(row.get('Status') == 'In_use' and row.get('DeleteWithInstance') is True for row in disks),
                'Every frozen disk must stay attached and delete-with-instance: '+role)
        checked[role] = dict(instance=row, disks=disks)
    return checked


def load_quote(pin):
    path = require_pin(pin, 'quote')
    require(digest(path) == pin['sha256'], 'Cost quote SHA256 differs')
    return read_json(path), pin


def quote_rates(quote, allowlist):
    require(quote.get('currency') == 'CNY' and quote.get('cloud_changed') is False,
            'Dated CNY disk quote that did not change the cloud required')
    when = datetime.fromisoformat(quote['at'])
    require(when.utcoffset() == timedelta(0), 'Explicit UTC quote time required')
    by_role = {role: [] for role in ALL_ROLES}
    for row in quote.get('disks') or []:
        require(row.get('role') in ALL_ROLES and row.get('disk_id') not in
                [seen['disk_id'] for seen in by_role[row['role']]], 'Quote disk role/id differs')
        rate = row.get('quoted_hourly_cny')
        require(type(rate) in (int, float) and math.isfinite(rate) and rate > 0,
                'Finite positive hourly disk rate required')
        by_role[row['role']].append(row)
    for role in ALL_ROLES:
        require(sorted(row['disk_id'] for row in by_role[role]) == frozen_disks(allowlist, role),
                'Quote must price the original frozen disks exactly once: '+role)
    stopped = {role: sum(float(row['quoted_hourly_cny']) for row in by_role[role]) for role in ALL_ROLES}
    total = quote.get('total_disk_hourly_cny')
    require(type(total) in (int, float) and abs(sum(stopped.values())-float(total)) < 1e-9,
            'Quote total differs from its per-disk rows')
    instances = {row.get('role'): row for row in quote.get('instances') or []}
    require(set(instances) == set(ALL_ROLES), 'Quote must price the three original instances')
    for role in ALL_ROLES:
        require(instances[role].get('instance_id') == INSTANCES[role]
                and float(instances[role].get('hourly_trade_price', 0)) > 0,
                'Quote instance identity/rate differs: '+role)
    running = {role: stopped[role]+float(instances[role]['hourly_trade_price']) for role in ALL_ROLES}
    return dict(disks=by_role, stopped=stopped, running=running, instances=instances)


def rate_proof(role, phase, at, rate, row, api_ids, quote_pin, rates, allowlist, request_pin,
               node_script_pin):
    return dict(schema='ax-pilot-stopcharging-cost-rate-evidence-v1', passed=True, pool='pool-b', role=role,
                configuration_verified=True, resource_id=INSTANCES[role], at=at,
                status=row.get('Status'), stopped_mode=row.get('StoppedMode'), released=False,
                preserved_disks=True, preserved_disk_ids=frozen_disks(allowlist, role),
                quoted_hourly_cny=float(rate), quote=quote_pin,
                quote_scope=('running-instance-and-attached-disks-only' if phase == 'running'
                             else 'attached-system-and-data-disks-only'),
                retained_disks=rates['disks'][role],
                api_request_ids=api_ids,
                sources=dict(request=request_pin, node_script=node_script_pin))


def write_cost(out, allowlist, quote, quote_pin, rates, starts, running, stops, request_pin, node_script_pin):
    folder = Path(out)/'cost'
    folder.mkdir(mode=0o700)
    proofs = {}
    for role in START_ROLES:
        proofs[(role, 'running')] = rate_proof(
            role, 'running', running[role]['at'], rates['running'][role], running[role]['row'],
            dict(start_request_id=starts[role]['request_id'],
                 running_readback_request_id=running[role]['request_id']),
            quote_pin, rates, allowlist, request_pin, node_script_pin)
        proofs[(role, 'stopped')] = rate_proof(
            role, 'stopped', stops[role]['at'], rates['stopped'][role], stops[role]['row'],
            dict(stop_request_id=stops[role]['request_id'],
                 stopped_readback_request_id=stops[role].get('readback_request_id')),
            quote_pin, rates, allowlist, request_pin, node_script_pin)
        write_new(folder/(role+'-running-rate.json'), proofs[(role, 'running')])
        write_new(folder/(role+'-stopped-rate.json'), proofs[(role, 'stopped')])
    running_doc = dict(schema='ax-pilot-b-c1-gc-rates-v1', passed=True, phase='running', pool='pool-b',
                       currency='CNY', at=min(running[role]['at'] for role in START_ROLES),
                       roles={role: proofs[(role, 'running')] for role in START_ROLES},
                       total_hourly_cny=sum(rates['running'][role] for role in START_ROLES),
                       quote=quote_pin, request=request_pin)
    stopped_doc = dict(schema='ax-pilot-b-c1-gc-rates-v1', passed=True, phase='stopped', pool='pool-b',
                       currency='CNY', at=max(stops[role]['at'] for role in START_ROLES),
                       roles={role: proofs[(role, 'stopped')] for role in START_ROLES},
                       total_hourly_cny=sum(rates['stopped'][role] for role in START_ROLES),
                       quote=quote_pin, request=request_pin)
    write_new(folder/'running-rate.json', running_doc)
    write_new(folder/'stopped-rate.json', stopped_doc)
    events = []
    for role in START_ROLES:
        events.append(dict(resource_id=INSTANCES[role], at=running[role]['at'], kind='rate-change',
                           quoted_hourly_cny=float(rates['running'][role]),
                           evidence=ref(folder/(role+'-running-rate.json'))))
    for role in START_ROLES:
        events.append(dict(resource_id=INSTANCES[role], at=stops[role]['at'], kind='rate-change',
                           quoted_hourly_cny=float(rates['stopped'][role]),
                           evidence=ref(folder/(role+'-stopped-rate.json'))))
    events.sort(key=lambda row: row['at'])
    document = dict(schema='ax-pilot-cost-events-v1', reviewed=True, pool='pool-b', currency='CNY',
                    total_disk_hourly_cny=float(quote['total_disk_hourly_cny']),
                    total_running_hourly_cny=sum(rates['running'][role] for role in START_ROLES),
                    events=events,
                    rate_documents=dict(running=ref(folder/'running-rate.json'),
                                        stopped=ref(folder/'stopped-rate.json')),
                    source_events=None,
                    note='Only objects and metadata changed rate; the client stayed StopCharging and is not re-priced.')
    write_new(folder/'events.json', document)
    return dict(running=ref(folder/'running-rate.json'), stopped=ref(folder/'stopped-rate.json'),
                events=ref(folder/'events.json'))


def apply(request, request_pin, out, cloud_client, ssh, options):
    """Run one attempt. The lock is held across start, gc evidence and stop."""
    validate_request(request)
    require(isinstance(options.get('ssh'), dict) and options['ssh'].get('binary'), 'SSH transport required')
    allowlist = options.get('allowlist') or load_allowlist(request['allowlist'])
    ids = resource_ids(allowlist)
    out = Path(out)
    out.mkdir(mode=0o700)
    write_new(out/'intent.json', dict(schema='ax-pilot-b-c1-gc-intent-v1', at=utcnow(),
                                      attempt=request['attempt'], automatic_retry=False,
                                      request=request_pin, instances=INSTANCES,
                                      start_roles=list(START_ROLES), never_start=CLIENT,
                                      meta_url=request['meta_url'], juicefs=request['juicefs'],
                                      node_script=request['node_script'], quote=request['quote'],
                                      allowlist=request['allowlist'], resource_ids=ids,
                                      inputs=dict(ledger=options.get('ledger')),
                                      scope='One guarded Pool B JuiceFS gc maintenance window; the client '
                                            'instance stays StopCharging throughout.'))
    starts, running, stops, checked = {}, {}, {}, None
    warnings = []
    nodes = None
    client_row = None
    failure = None
    errors = []
    with locked(options['repo'], options['control'], ids, ssh if options.get('guard_remote') else None):
        try:
            start_guarded(cloud_client, starts)
            for role in START_ROLES:
                running[role] = wait_running(cloud_client, role, options['start_deadline_seconds'])
            assert_client_stopped(cloud_client, 'after start readback')
            checked = verify_configuration(cloud_client, allowlist)
            nodes = drive_nodes(ssh, request, out, options)
        except Exception as error:
            failure = error
        finally:
            for role in START_ROLES:
                if role in starts:
                    try:
                        stops[role] = stop_instance(cloud_client, role, options['start_deadline_seconds'])
                    except Exception as error:
                        errors.append('stop '+role+': '+repr(error))
            try:
                client_row = assert_client_stopped(cloud_client, 'after stop')
            except Exception as error:
                errors.append('client: '+repr(error))
    quote = quote_pin = rates = None
    cost = None
    try:
        quote, quote_pin = load_quote(request['quote'])
        rates = quote_rates(quote, allowlist)
        if starts:
            require(len(stops) == len(START_ROLES) and len(running) == len(START_ROLES),
                    'Both instances must have an observed running and stopped readback for cost events')
            cost = write_cost(out, allowlist, quote, quote_pin, rates, starts, running, stops,
                              request_pin, request['node_script'])
    except Exception as error:
        warnings.append('cost: '+repr(error))
        folder = out/'cost'
        folder.mkdir(mode=0o700, exist_ok=True)
        write_new(folder/'incomplete.json', dict(passed=False, error=str(error), automatic_retry=False,
                                                 starts=starts,
                                                 running={role: running[role]['at'] for role in running},
                                                 stops={role: stops[role]['at'] for role in stops}))
    members = dict(request=request_pin, node_script=request['node_script'], juicefs=request['juicefs'],
                   quote=request['quote'], allowlist=request['allowlist'])
    if failure is None and errors:
        failure = ValueError('; '.join(errors))
    result = dict(schema='ax-pilot-b-c1-gc-result-v1', passed=failure is None,
                  at=utcnow(), attempt=request['attempt'], automatic_retry=False, out=str(out),
                  instances=INSTANCES, start_roles=list(START_ROLES), never_started=CLIENT,
                  meta_url=request['meta_url'], members=members, resource_ids=ids,
                  inputs=dict(ledger=options.get('ledger')),
                  cloud=dict(starts=starts, running={role: {k: v for k, v in running[role].items()
                                                            if k != 'row'} for role in running},
                             stops={role: {k: v for k, v in stops[role].items() if k != 'row'}
                                    for role in stops},
                             client=client_row),
                  configuration=checked, nodes=nodes, cost=cost, errors=errors, warnings=warnings,
                  scope='Only objects and metadata were woken for one guarded JuiceFS gc; the client stayed '
                        'Stopped/StopCharging and both instances returned to StopCharging. Not a settled bill.')
    if failure is not None:
        write_new(out/'failed.json', dict(passed=False, attempt=request['attempt'], at=utcnow(),
                                          error=str(failure), errors=errors, automatic_retry=False,
                                          cloud=result['cloud'], nodes=nodes, cost=cost))
        raise failure
    write_new(out/'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--sha256', required=True, help='Exact SHA256 of the reviewed request')
    parser.add_argument('--out', type=Path, required=True, help='Fresh evidence directory')
    parser.add_argument('--control', type=Path, default=Path(DEFAULT_CONTROL),
                        help='Coordinator control directory holding runner-locks')
    parser.add_argument('--repo', type=Path, help='Pinned bench runtime checkout (default: this checkout)')
    parser.add_argument('--ssh-config', type=Path, required=True, help='Pinned self-contained SSH config')
    parser.add_argument('--ssh-binary', default=shutil.which('ssh'))
    parser.add_argument('--guard-remote', action='store_true',
                        help='Hold the coordinator Pool B locks over SSH (needed when the Aliyun CLI runs here)')
    parser.add_argument('--aliyun', default=shutil.which('aliyun') or shutil.which('aliyun.exe'))
    parser.add_argument('--ledger', type=Path,
                        help='Original Pool B creation ledger, pinned as an input only (never written)')
    args = parser.parse_args()
    repo = args.repo or Path(__file__).resolve().parents[4]
    if not (repo/'bench/runtime/pilot_pools.py').is_file():
        parser.error('Provide --repo with the pinned bench runtime checkout')
    if not (args.ssh_binary and local_config(args.ssh_config).is_file() and args.aliyun):
        parser.error('Configured Aliyun CLI, ssh executable and pinned SSH config required')
    request = load_request(args.request, args.sha256)
    request_pin = ref(args.request)
    require(request_pin['sha256'] == args.sha256, 'Reviewed request changed while it was being read')
    ssh = dict(binary=str(args.ssh_binary), config=str(args.ssh_config))
    options = dict(repo=repo, control=args.control, ssh=ssh, guard_remote=bool(getattr(args,'guard_remote',False)),
                   ledger=pin_ledger(args.ledger) if args.ledger else None,
                   start_deadline_seconds=int(request.get('start_deadline_seconds', 900)),
                   ready_deadline_seconds=int(request.get('ready_deadline_seconds', 1800)),
                   gc_timeout_seconds=int(request.get('gc_timeout_seconds', 14400)),
                   du_timeout_seconds=int(request.get('du_timeout_seconds', 1800)),
                   eof_grace_seconds=int(request.get('eof_grace_seconds', 60)))
    require(0 < options['start_deadline_seconds'] <= 3600, 'Bounded start deadline required')
    require(60 <= options['ready_deadline_seconds'] <= 7200, 'Bounded node ready deadline required')
    require(60 <= options['gc_timeout_seconds'] <= 43200, 'Bounded gc timeout required')
    require(60 <= options['du_timeout_seconds'] <= 7200, 'Bounded du timeout required')
    require(0 <= options['eof_grace_seconds'] <= 600, 'Bounded EOF grace required')
    client = cloud.Cloud(args.aliyun, args.out)
    result = apply(request, request_pin, args.out, client, ssh, options)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
