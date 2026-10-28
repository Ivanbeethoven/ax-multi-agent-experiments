#!/usr/bin/env python3
"""Linux execution tools for the final protocol; never purchases/releases ECS.

High-frequency work is Rust. Commands run to completion without a replay deadline.
This runner qualifies application diagnostics, not unobserved FUSE/EC/fencing claims.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
RUSTFS_REPO = 'https://github.com/Ivanbeethoven/rustfs'
RUSTFS_BRANCH = 'feat/dynamic-block-size-rebased'
METHODS = ['native', 'native-overlay', 'native-overlay-pack', 'juicefs']
STATES = {'ok', 'failed', 'interrupted', 'invalid', 'not_run', 'unsupported'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def check_binary(item):
    path = Path(item['path']).resolve(strict=True)
    if not re.fullmatch('[0-9a-f]{64}', item['sha256']) or digest(path) != item['sha256']:
        raise ValueError('binary digest mismatch: ' + path.name)
    return str(path)


def capture(argv):
    # No timeout: including long flush, unmount, and replay operations.
    return subprocess.check_output(argv, text=True, stderr=subprocess.PIPE)


def find_mount(path):
    p = subprocess.run(['findmnt', '--json', '--mountpoint', str(path), '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS'],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return json.loads(p.stdout)['filesystems'][0] if p.returncode == 0 else None


def no_credentials_in_url(value):
    u = urlsplit(value)
    if u.username or u.password or u.query or u.fragment:
        raise ValueError('put credentials in the protected environment, not URL arguments')
    return value


def workspace_reply(text):
    # BrewFS can print startup tracing before pretty-printed CLI JSON.
    decoder = json.JSONDecoder()
    for line in re.finditer(r'(?m)^\s*[\[{]', text):
        try:
            value, end = decoder.raw_decode(text[line.start():].lstrip())
        except ValueError:
            continue
        items = value if isinstance(value, list) else [value]
        if items and all(isinstance(v, dict) and 'workspace_id' in v for v in items):
            for v in items:
                if not re.fullmatch('[0-9a-fA-F-]{32,36}', v['workspace_id']):
                    raise ValueError('invalid returned workspace ID')
            return value
    raise ValueError('no structured workspace record in CLI output')


def runtime_pin(pin, container):
    if not pin.get('resolved_by') or datetime.fromisoformat(pin['resolved_at']).tzinfo is None:
        raise ValueError('pin needs resolver and timezone-aware resolution time')
    for k in ['frozen_commit', 'built_from_commit', 'brewfs_client_sha']:
        if not re.fullmatch('[0-9a-f]{40}', pin[k]):
            raise ValueError('full Git SHA required: ' + k)
    if pin['repository'] != RUSTFS_REPO or pin['branch'] != RUSTFS_BRANCH:
        raise ValueError('wrong RustFS source/branch')
    if pin['built_from_commit'] != pin['frozen_commit'] or pin.get('blocked_reason'):
        raise ValueError('unqualified build')
    remote = capture(['git', 'ls-remote', RUSTFS_REPO, 'refs/heads/' + RUSTFS_BRANCH]).split()
    if not remote or remote[0] != pin['frozen_commit']:
        raise ValueError('branch moved/unavailable: freeze and rebuild a new batch')
    image = json.loads(capture(['docker', 'image', 'inspect', pin['image_reference']]))[0]
    # Never serialize container Config.Env (contains credentials).
    actual = json.loads(capture(['docker', 'inspect', '--format',
                                '{{json .Image}}', container]))
    running = capture(['docker', 'inspect', '--format', '{{.State.Running}}', container]).strip()
    if running != 'true' or actual != image['Id']:
        raise ValueError('running container is not the pinned image')
    if image.get('Config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != pin['frozen_commit']:
        raise ValueError('image source label mismatch')
    wanted = pin['image_digest']
    if not re.fullmatch('sha256:[0-9a-f]{64}', wanted):
        raise ValueError('invalid image digest')
    if not pin['image_reference'].endswith('@'+wanted):
        raise ValueError('image reference must include the frozen digest')
    if not any(x.endswith('@' + wanted) for x in image.get('RepoDigests', [])):
        raise ValueError('image registry digest mismatch')
    return {'image_id': actual, 'digest': wanted, 'commit': pin['frozen_commit'], 'container': container}


def check_endpoint_container(endpoint, container):
    u=urlsplit(endpoint)
    local={'127.0.0.1','::1'}
    for interface in json.loads(capture(['ip','-json','addr','show'])):
        local.update(a['local'] for a in interface.get('addr_info',[]))
    addresses={x[4][0] for x in socket.getaddrinfo(u.hostname,u.port or 9000)}
    if not addresses or not addresses.issubset(local):
        raise ValueError('this runner requires a colocated pinned RustFS; distributed qualification is separate')
    network=capture(['docker','inspect','--format','{{.HostConfig.NetworkMode}}',container]).strip()
    if network=='host':
        # The deployment generator fixes host-network RustFS API to port 9000.
        if u.port != 9000: raise ValueError('unexpected host-network API port')
    else:
        ports=json.loads(capture(['docker','inspect','--format','{{json .NetworkSettings.Ports}}',container]))
        if not any(int(p['HostPort'])==(u.port or 80) for p in (ports.get('9000/tcp') or [])):
            raise ValueError('S3 endpoint is not the pinned container published API port')
    return {'resolved_addresses':sorted(addresses),'network_mode':network,'port':u.port}


def snapshot(redis_url=None):
    result = {'time_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns()}
    for name in ['stat', 'diskstats', 'meminfo', 'vmstat']:
        try:
            result[name] = Path('/proc/' + name).read_text()
        except OSError:
            result[name] = None
    result['client_cgroup'] = None
    try:
        line = next(v for v in Path('/proc/self/cgroup').read_text().splitlines() if v.startswith('0::'))
        group = Path('/sys/fs/cgroup') / line[3:].lstrip('/')
        result['client_cgroup'] = {k: (group/k).read_text().strip() for k in
                                  ('memory.current', 'memory.max', 'memory.swap.current', 'memory.swap.max',
                                   'memory.events', 'cpu.max', 'cpu.stat') if (group/k).is_file()}
    except (OSError, StopIteration):
        pass
    result['redis'] = None
    if redis_url:
        try:
            raw = capture(['redis-cli', '-u', redis_url, '--raw', 'INFO', 'ALL'])
            result['redis'] = dict(line.strip().split(':', 1) for line in raw.splitlines()
                                   if ':' in line and not line.startswith('#'))
        except (OSError, subprocess.CalledProcessError):
            pass
    return result


def redis_delta(before, after):
    a, b = before.get('redis'), after.get('redis')
    if not a or not b:
        return None
    result = {}
    for key in ['total_commands_processed', 'used_cpu_sys', 'used_cpu_user', 'total_net_input_bytes', 'total_net_output_bytes']:
        if key in a and key in b:
            d = float(b[key]) - float(a[key])
            result[key] = d if d >= 0 else None
    result['used_memory_before'] = int(a['used_memory']) if 'used_memory' in a else None
    result['used_memory_after'] = int(b['used_memory']) if 'used_memory' in b else None
    result['scope'] = 'entire Redis server; includes observer INFO requests and other clients'
    return result


def qualify_cgroup(expected, actual):
    if not actual:
        raise ValueError('missing cgroup-v2 resource observations')
    values={key:int(actual[field]) for key,field in
            [('memory_max_bytes','memory.max'),('swap_max_bytes','memory.swap.max')]}
    quota,period=actual['cpu.max'].split()
    values.update(cpu_quota_us=int(quota),cpu_period_us=int(period))
    if values != expected:
        raise ValueError('effective client resource limits differ from requested limits')
    return values


class Observer:
    """Host-only, five-second sampling; no per-operation instrumentation."""
    def __init__(self, output, enabled=True):
        self.output, self.enabled = Path(output), enabled
        self.stop = threading.Event()

    def __enter__(self):
        def loop():
            with self.output.open('x') as f:
                while not self.stop.is_set():
                    f.write(json.dumps(snapshot()) + '\n'); f.flush()
                    self.stop.wait(5)
        if self.enabled:
            self.thread = threading.Thread(target=loop, daemon=True); self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        if self.enabled:
            self.thread.join()


class Session:
    def __init__(self, spec, output):
        self.s = spec
        self.out = Path(output).resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        self.events = []
        self.number = 0
        self.mount_serial = 0
        self.root = Path(spec['work_root']).resolve()
        if self.root == Path('/') or self.root.exists():
            raise ValueError('work_root must be a new dedicated directory')
        if self.root.is_relative_to(self.out) or self.out.is_relative_to(self.root):
            raise ValueError('results and measured work_root must be disjoint')
        self.root.mkdir(parents=True)
        self.mp = self.root / 'mount'; self.mp.mkdir()
        self.process = None
        self.workspace = spec.get('workspace_id')
        self.children = []
        self.env = dict(os.environ)
        # JuiceFS format reads ACCESS_KEY/SECRET_KEY; BrewFS uses the AWS names.
        if spec.get('method') == 'juicefs':
            for dst, src in [('ACCESS_KEY','AWS_ACCESS_KEY_ID'), ('SECRET_KEY','AWS_SECRET_ACCESS_KEY')]:
                if src in self.env:
                    self.env[dst] = self.env[src]

    def command(self, name, argv, env=None):
        self.number += 1
        stem = f'{self.number:04}-{name}'
        start = time.monotonic()
        record = {'phase': name, 'argv': argv, 'status': 'interrupted', 'started_ns': time.time_ns()}
        try:
            with (self.out / (stem + '.stdout')).open('x') as out, (self.out / (stem + '.stderr')).open('x') as err:
                p = subprocess.Popen(argv, stdout=out, stderr=err, env=env or self.env)
                try:
                    rc = p.wait()
                except BaseException:
                    # Explicit operator interruption only, never a wall-clock deadline.
                    if p.poll() is None:
                        p.send_signal(signal.SIGINT); p.wait()
                    raise
                record.update(returncode=rc, status='ok' if rc == 0 else 'failed')
                if rc:
                    raise RuntimeError('phase failed; inspect local log: ' + stem)
        finally:
            record.update(wall_seconds=time.monotonic() - start, stdout=stem + '.stdout', stderr=stem + '.stderr')
            self.events.append(record); save(self.out / 'phases.json', self.events)
        return (self.out / (stem + '.stdout')).read_text()

    def preflight(self):
        if sys.platform != 'linux':
            raise ValueError('execute on a Linux worker')
        s = self.s
        if s['method'] not in METHODS:
            raise ValueError('unknown method')
        for key, default, minimum in [('files',256,1),('file_bytes',4096,1),('operations',10000,1),
                                     ('reps',3,1),('fork_count',1,1),('consumers',1,0),('neighbor_operations',0,0)]:
            value=s.get(key,default)
            if type(value) is not int or value<minimum:
                raise ValueError('invalid integer workload parameter: '+key)
        if s.get('fuse_ttl_ms') is not None and (type(s['fuse_ttl_ms']) is not int or s['fuse_ttl_ms'] < 0):
            raise ValueError('fuse_ttl_ms must be a nonnegative integer or null')
        if s.get('fuse_keep_cache') is not None and type(s['fuse_keep_cache']) is not bool:
            raise ValueError('fuse_keep_cache must be boolean or null')
        relative=PurePosixPath(s.get('corpus_relative','ax-corpus'))
        if relative.is_absolute() or '..' in relative.parts or str(relative)=='.':
            raise ValueError('corpus_relative must stay below the measured mount')
        if s.get('expected_cgroup_limits'):
            actual_limits=qualify_cgroup(s['expected_cgroup_limits'],snapshot().get('client_cgroup'))
            save(self.out/'client-resource-qualification.json',{'passed':True,'effective':actual_limits})
        no_credentials_in_url(s['redis_url']); no_credentials_in_url(s['endpoint'])
        self.brewfs = check_binary(s['brewfs']) if s['method'] != 'juicefs' else None
        self.juicefs = check_binary(s['juicefs']) if s['method'] == 'juicefs' else None
        self.probe = check_binary(s['probe'])
        facts = runtime_pin(load(s['backend_pin']), s['rustfs_container'])
        facts['endpoint_binding']=check_endpoint_container(s['endpoint'],s['rustfs_container'])
        disk=Path(s['data_mount']).resolve(strict=True)
        mounted=find_mount(disk)
        if not mounted or mounted['fstype'] not in ('ext4','xfs') or not self.root.is_relative_to(disk):
            raise ValueError('work_root must be on the declared mounted data filesystem')
        if disk.stat().st_dev==Path('/').stat().st_dev:
            raise ValueError('workload and caches must not use the system filesystem')
        if s['method'] != 'juicefs' and s['brewfs']['source_sha'] != load(s['backend_pin'])['brewfs_client_sha']:
            raise ValueError('BrewFS source SHA differs from backend pin')
        if s['method'] != 'juicefs' and s['brewfs']['sha256'] != load(s['backend_pin'])['brewfs_binary_sha256']:
            raise ValueError('BrewFS executable differs from the frozen binary')
        # Refuse a reused metadata DB; never FLUSHDB. Remounts retain this DB.
        existing = int(capture(['redis-cli', '-u', s['redis_url'], 'DBSIZE']).strip())
        if existing != 0 and not (s['method'] == 'native-overlay' and s.get('parent_revision')):
            raise ValueError('new dedicated empty Redis database required')
        facts.update(method=s['method'], binaries={k: s[k] for k in ['brewfs', 'juicefs', 'probe'] if k in s},
                     runner_sha256=digest(__file__),
                     experiment_commit=capture(['git','-C',str(REPO),'rev-parse','HEAD']).strip(),
                     experiment_tree_dirty=bool(capture(['git','-C',str(REPO),'status','--porcelain']).strip()),
                     endpoint=s['endpoint'], bucket=s['bucket'], work_root=str(self.root),
                     filesystem=json.loads(capture(['findmnt', '--json', '--target', str(self.root)])),
                     disks=json.loads(capture(['lsblk', '--json', '-b', '-o', 'NAME,SIZE,TYPE,MOUNTPOINTS'])) )
        save(self.out / 'preflight.json', facts)

    def initialize(self):
        s = self.s
        if s['method'] == 'juicefs':
            block=s.get('client_block_bytes',4194304)
            if block % 1024 or not 65536 <= block <= 16777216:
                raise ValueError('JuiceFS block size must be 64 KiB..16 MiB in whole KiB')
            self.command('format', [self.juicefs, 'format', '--storage', 's3', '--bucket',
                                   s['endpoint'].rstrip('/') + '/' + s['bucket'], '--block-size', str(block//1024), '--compress', 'none',
                                   s['redis_url'], s['volume_name']])
        elif s['method'] != 'native':
            cmd = [self.brewfs, 'workspace', '--meta-backend', 'redis', '--meta-url', s['redis_url']]
            if s['method'] == 'native-overlay' and s.get('parent_revision'):
                # The CLI takes an exact immutable revision; it does not seal a live parent.
                cmd += ['fork', s['parent_revision'], '--count', str(s.get('fork_count', 1))]
            elif s['method'] == 'native-overlay-pack':
                # Same S3 path for fixture and mount, without credential values in YAML.
                config = self.out / 'data-config.json'
                save(config, {'data': {'backend': 's3', 's3': {'bucket': s['bucket'], 'endpoint': s['endpoint'],
                                                             'region': s.get('region', 'us-east-1'), 'force_path_style': True}}})
                cmd += ['init-packed-volume', '--manifest-key', s['manifest_key'], '--data-config', str(config)]
            else:
                cmd += ['init-volume']
            self.fork_started = time.monotonic()
            result = workspace_reply(self.command('init', cmd))
            if isinstance(result, list):
                if len(result) != s.get('fork_count', 1):
                    raise ValueError('fork returned a different child count')
                self.children = result
                result = result[0]
            self.workspace = result['workspace_id']
            save(self.out / 'workspace.json', result)

    def mount(self, ttl=None, keep=None):
        if ttl is None:
            ttl = self.s.get('fuse_ttl_ms')
        if keep is None:
            keep = self.s.get('fuse_keep_cache')
        self.mount_serial += 1
        self.requested_controls={'ttl_ms':ttl,'keep_cache':keep,
                                 'client_block_bytes':self.s.get('client_block_bytes',4194304),
                                 'ec_block_hint':self.s.get('ec_block_hint')}
        cache = self.root / f'cache-{self.mount_serial}'
        if self.s['method'] != 'juicefs':
            # BrewFS defaults its cache to a system-disk path. Make each mount's
            # client cache explicit and place it below this run's data disk.
            config = dict(self.s.get('brewfs_cache', {}))
            allowed = {'read_memory_bytes', 'write_memory_bytes', 'read_ssd_bytes', 'write_ssd_bytes',
                       'memory_budget_bytes', 'compression', 'verify_cache_checksum', 'writeback_mode'}
            if set(config)-allowed:
                raise ValueError('unsupported BrewFS cache control')
            config['cache_root'] = str(cache)
            save(str(cache)+'.json', {'cache': config})
            save(self.out/f'client-cache-{self.mount_serial}.json',{'requested':config,'effective_observation':None})
        cmd, env = mount_command(self.s, self.mp, cache, self.workspace, ttl, keep)
        for name, value in env.items():
            if value is None:
                self.env.pop(name, None)
            else:
                self.env[name] = value
        save(self.out / f'mount-{self.mount_serial}.json', {'argv': cmd, 'requested_controls': env,
             'effective_fuse_reply_ttl_ms': None, 'effective_open_keep_cache': None,
             'note': 'process configuration is recorded; actual FUSE reply observations require a qualified probe'})
        self.mount_log = (self.out / f'mount-{self.mount_serial}.log').open('x')
        try:
            self.process = subprocess.Popen(cmd, env=self.env, stdout=self.mount_log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 60  # Mount startup only, never replay.
            while not find_mount(self.mp):
                if self.process.poll() is not None:
                    raise RuntimeError('mount exited before a filesystem appeared')
                if time.monotonic() > deadline:
                    raise RuntimeError('mount startup failed to become ready in 60 seconds')
                time.sleep(.1)
            fs = find_mount(self.mp)
            if not fs['fstype'].startswith('fuse'):
                raise RuntimeError('mountpoint is not FUSE')
            save(self.out / f'mounted-{self.mount_serial}.json', fs)
        except BaseException:
            self.unmount()
            raise

    def unmount(self):
        if self.process is None:
            return
        if find_mount(self.mp):
            self.command('unmount', ['fusermount3', '-u', str(self.mp)])
            if find_mount(self.mp):
                raise RuntimeError('unmount returned but mount remains')
        elif self.process.poll() is None:
            # Failed startup only; no replay in progress.
            self.process.send_signal(signal.SIGINT)
        rc = self.process.wait()
        self.mount_log.close(); self.process = None
        if rc != 0:
            raise RuntimeError(f'filesystem process exited {rc}')

    def probe_run(self, mode, root, files, size, operations, label, sample=100):
        before = snapshot(self.s['redis_url'])
        output = self.command(label, [self.probe, mode, str(root), str(files), str(size), str(operations),
                                      str(self.s.get('seed', 42)), str(sample)])
        after = snapshot(self.s['redis_url'])
        value = json.loads(output)
        value.update(status='ok', measurement_scope='application', formal_qualified=False,
                     files=files, file_bytes=size, seed=self.s.get('seed',42),
                     requested_controls=self.requested_controls,
                     db=redis_delta(before, after), fuse_bytes=None, http_payload_bytes=None,
                     backend_pread_bytes=None, device_bytes=None)
        save(self.out / (label + '.json'), value)
        save(self.out / (label + '-counters.json'), {'before': before, 'after': after})
        return value


def mount_command(s, mp, cache, workspace, ttl, keep):
    if s['method'] == 'juicefs':
        # Foreground; writeback deliberately absent. New cache per mount, no live deletion.
        cmd = [s['juicefs']['path'], 'mount', s['redis_url'], str(mp), '--cache-dir', str(cache),
               '--cache-size', str(s.get('cache_mib', 8192)),
               '--buffer-size', str(s.get('buffer_mib', 1024)), '--enable-xattr', '--enable-cap']
        if ttl is not None:
            cmd += ['--attr-cache', str(ttl/1000), '--entry-cache', str(ttl/1000),
                    '--dir-entry-cache', str(ttl/1000)]
        return cmd, {}
    cmd = [s['brewfs']['path'], 'mount', '--config', str(cache)+'.json', '--data-backend', 's3', '--s3-bucket', s['bucket'],
           '--s3-endpoint', s['endpoint'], '--s3-region', s.get('region', 'us-east-1'),
           '--s3-force-path-style', 'true', '--meta-backend', 'redis', '--meta-url', s['redis_url'],
           '--block-size', str(s.get('client_block_bytes', 4194304))]
    if ttl is not None:
        cmd += ['--fuse-cache-ttl-ms', str(ttl)]
    if s['method'] != 'native':
        if not workspace:
            raise ValueError('workspace identity missing')
        cmd += ['--volume-format', 'workspace-v1', '--workspace', workspace]
    if s.get('ec_block_hint') is not None:
        cmd += ['--s3-rustfs-ec-block-size-hint', str(s['ec_block_hint'])]
    cmd += [str(mp)]
    return cmd, {'BREWFS_CACHE_TTL_MS': str(ttl) if ttl is not None else None,
                 'BREWFS_FUSE_KEEP_CACHE': str(keep).lower() if keep is not None else None,
                 'BREWFS_FUSE_DIRECT_IO': 'false', 'BREWFS_FUSE_READ_DIRECT_IO': 'false',
                 'BREWFS_FUSE_WRITE_DIRECT_IO': 'false'}


def diagnostics(session):
    s = session.s
    n, size, ops = s.get('files', 256), s.get('file_bytes', 4096), s.get('operations', 10000)
    corpus = session.mp / 'ax-diagnostic-corpus'
    session.mount()
    session.probe_run('prepare', corpus, n, size, n, 'prepare', sample=0)
    session.probe_run('verify', corpus, n, size, n, 'verify', sample=0)
    session.unmount()
    # Independent process/remount readback, outside performance interval.
    session.mount()
    session.probe_run('verify', corpus, n, size, n, 'reopen-verify', sample=0)
    session.unmount()
    arms = [('cold', 0, False), ('warm', 0, False), ('keep-off', 0, False),
            ('keep-on', 0, True), ('ttl-0', 0, False), ('ttl-1000', 1000, False)]
    for rep in range(1, s.get('reps', 3) + 1):
        order = arms.copy(); random.Random(s.get('seed', 42) + rep).shuffle(order)
        for label, ttl, keep in order:
            if s['method'] == 'juicefs' and label.startswith('keep-'):
                save(session.out / f'{label}-{rep}.json', {'status': 'unsupported', 'reason': 'no equivalent KEEP_CACHE switch'})
                continue
            session.mount(ttl, keep)
            if label == 'cold' and s.get('dedicated_host_drop_caches'):
                os.sync(); Path('/proc/sys/vm/drop_caches').write_text('3\n')
            if label in ['warm', 'keep-on', 'keep-off']:
                # Visit every file rather than random sampling with replacement.
                session.probe_run('verify', corpus, n, size, n, f'prewarm-{label}-{rep}', sample=0)
            mode = 'stat' if label.startswith('ttl-') else 'read'
            session.probe_run(mode, corpus, n, size, ops, f'{label}-{rep}')
            session.unmount()
    save(session.out / 'cache-scope.json', {'cold': 'new client process and mount; new JuiceFS disk cache',
         'host_page_cache_dropped': bool(s.get('dedicated_host_drop_caches')),
         'remote_backend_cache': 'uncontrolled', 'same_corpus': True,
         'scope': 'diagnostic; not fully cold storage or a verified effective FUSE TTL comparison'})


def packed_queries(session):
    """Consumer/probe overlap on an existing immutable lower; no synthetic build cost."""
    s = session.s
    if s['method'] != 'native-overlay-pack' and not (s['method'] == 'native-overlay' and s.get('parent_revision')):
        raise ValueError('packed-queries requires a packed manifest or exact native parent_revision containing the same corpus')
    session.mount()
    corpus = session.mp / s.get('corpus_relative', 'ax-corpus')
    n, size = s['files'], s['file_bytes']
    session.probe_run('verify', corpus, n, size, n, 'parent-verify', sample=0)
    # One Rust process per consumer. Each writes its own output file.
    # Shared DB counters measured only around the whole batch, never summed per consumer.
    probe_root = session.mp / 'ax-neighbor'
    session.probe_run('prepare', probe_root, 256, 4096, 256, 'neighbor-prepare', sample=0)
    session.unmount(); session.mount()
    if s.get('warm', False):
        session.probe_run('verify', corpus, n, size, n, 'parent-prewarm', sample=0)
    before = snapshot(s['redis_url']); start = time.monotonic()
    jobs = []
    def launch(label, mode, root, files, size, ops):
        stdout = (session.out / (label + '.stdout')).open('x')
        stderr = (session.out / (label + '.stderr')).open('x')
        args=[session.probe, mode, str(root), str(files), str(size), str(ops), str(s.get('seed',42)), '100']
        interval=s.get('neighbor_interval_ns',0) if label=='neighbor' else s.get('consumer_interval_ns',0)
        args += [str(interval),str(s.get('neighbor_slo_ns',0) if label=='neighbor' else 0)]
        p = subprocess.Popen(args, stdout=stdout, stderr=stderr,
                             env={**session.env,'AX_START_UNIX_NS':str(common_start)})
        jobs.append((label, p, stdout, stderr))
    try:
        common_start=time.time_ns()+5_000_000_000
        if s.get('neighbor_operations', 0):
            launch('neighbor', 'write', probe_root, 256, 4096, s['neighbor_operations'])
        for i in range(s.get('consumers', 1)):
            launch(f'consumer-{i}', s.get('query', 'stat'), corpus, n, size, s['operations'])
        statuses = {}
        for label, process, out, err in jobs:
            statuses[label] = 'ok' if process.wait() == 0 else 'failed'
            out.close(); err.close()
        save(session.out / 'consumers.json', {'statuses': statuses, 'wall_seconds': time.monotonic()-start,
             'db': redis_delta(before, snapshot(s['redis_url'])), 'formal_qualified': False,
             'neighbor_load': 'fixed arrival table; queued operations retained' if s.get('neighbor_interval_ns',0) else 'closed-loop',
             'common_start_unix_ns':common_start, 'sustainable_rate_qualified':False})
        if 'failed' in statuses.values():
            raise RuntimeError('consumer/probe failure; retain all observations')
    finally:
        for _, p, out, err in jobs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT); p.wait()
            out.close(); err.close()
        session.unmount()


def object_io(session):
    s = session.s
    session.mount()
    root = session.mp / 'object-corpus'
    n, size, ops = s.get('files', 64), s.get('file_bytes', 16777216), s.get('operations', 1024)
    session.probe_run('prepare', root, n, size, n, 'prepare', sample=0)
    session.probe_run('verify', root, n, size, n, 'verify', sample=0)
    session.unmount()
    for mode in ['seqread', 'sparse4k', 'write']:
        session.mount()
        session.probe_run(mode, root, n, size, ops, mode)
        session.unmount()
    save(session.out/'layout.json', {'client_block_bytes_requested': s.get('client_block_bytes',4194304),
         'backend_hint_requested':s.get('ec_block_hint'), 'persisted_block_bytes':None, 'internal_read_quantum':None,
         'dynamic_layout_qualified':False, 'note':'no-op/default is retained; input flags do not prove changed layout'})


def fork_base(session):
    s = session.s
    if s['method'] != 'native-overlay' or not s.get('parent_revision') or not session.children:
        raise ValueError('fork-base requires native-overlay with an existing exact parent_revision')
    rows=[]
    for i, child in enumerate(session.children):
        session.workspace=child['workspace_id']
        session.mount()
        # Small semantic gate, outside bulk throughput intervals.
        session.probe_run('verify', session.mp/s.get('corpus_relative','ax-corpus'), 1,
                          s['file_bytes'], 1, f'child-{i}-first-correct-read', sample=0)
        first=time.monotonic()-session.fork_started
        if any(session.mp.glob('ax-private-child-*')):
            raise ValueError('private child data leaked into another fork or parent was not clean')
        session.probe_run('prepare', session.mp/f'ax-private-child-{i}',1,1048576,1,
                          f'child-{i}-first-fsync-write',sample=0)
        session.unmount()
        rows.append({'workspace_id':session.workspace,'first_correct_read_since_fork_request_seconds':first,
                     'fsync_and_unmount_since_fork_request_seconds':time.monotonic()-session.fork_started})
    save(session.out/'fork.json',{'status':'ok','children':rows,'parent_revision':s['parent_revision'],
         'modified_parent_freeze_measured':False,'remote_durability_qualified':False,'formal_qualified':False,
         'scope':'existing immutable base, sequential child mounts; not the modified-parent F1 experiment'})


def prepare_trace_root(session, case, tree):
    """Check the same initial tree, without masking an existing packed lower."""
    started=time.monotonic()
    root=session.mp/'trace-root'
    if session.s['method']=='native-overlay-pack':
        mode='existing_packed_lower'
        if not root.is_dir():
            raise ValueError('packed manifest must contain trace-root with the original initial tree')
    else:
        mode='materialized_copy'
        session.command('initial-copy', ['cp', '-a', str(case/'initial'), str(root)])
    if tree(root) != load(case/'initial-tree.json'):
        raise ValueError('initial tree mismatch')
    save(session.out/'trace-preparation.json', {'mode':mode, 'wall_seconds':time.monotonic()-started,
         'includes_tree_verification':True,'packed_build_freeze_upload_seconds':None,
         'note':'packed construction and publication are separate and are not included in this time'})
    return root


def trace(session):
    s = session.s
    binary = check_binary(s['replayer'])
    case = Path(s['trace_case']).resolve(strict=True)
    # Content-addressed inventory made at input qualification; check all bytes before replay.
    manifest = load(s['trace_manifest'])
    if digest(s['trace_manifest']) != s['trace_manifest_sha256']:
        raise ValueError('trace manifest changed')
    for name, expected in manifest.items():
        path = (case / name).resolve(strict=True)
        if not path.is_relative_to(case) or digest(path) != expected:
            raise ValueError('trace input digest mismatch')
    required = {'capture/events.jsonl', 'metrics.json', 'initial-tree.json'}
    if not required.issubset(manifest):
        raise ValueError('incomplete trace inventory')
    for root in [case/'capture/blobs', case/'initial']:
        for f in root.rglob('*'):
            if f.is_file() and not f.is_symlink() and f.relative_to(case).as_posix() not in manifest:
                raise ValueError('trace payload absent from manifest')
    session.mount()
    sys.path.insert(0, str(REPO / 'scripts'))
    from full_trace_common import tree
    root = prepare_trace_root(session, case, tree)
    if s.get('qualify_reopen', False):
        marker=session.mp/'.ax-reopen-qualification'
        content=b'ax explicit fsync and new mount readback v1\n'
        with marker.open('xb') as f:
            f.write(content);f.flush();os.fsync(f.fileno())
        session.unmount()
        session.mount()
        root = session.mp/'trace-root'
        if marker.read_bytes()!=content:
            raise ValueError('explicit fsync marker did not survive independent remount')
        marker.unlink()
        if tree(root) != load(case/'initial-tree.json'):
            raise ValueError('independent remount readback mismatch')
        save(session.out/'reopen-qualification.json', {'passed': True, 'explicit_file_fsync_returned':True, 'marker_readback_equal':True, 'same_host_new_mount_process': True,
             'cross_host_durability_qualified': False, 'note': 'outside replay timing; tree validation warms cache'})
    before = snapshot(s['redis_url']); start = time.monotonic()
    session.command('trace-performance', [binary, str(case/'capture/events.jsonl'), str(case/'capture/blobs'),
                                         str(root), str(session.out/'replay.json'), '--performance'])
    replay = load(session.out/'replay.json'); expected = load(case/'metrics.json')
    if not replay.get('completed') or replay.get('remaining_handles') != 0:
        raise ValueError('trace did not finish with zero live handles')
    for result_key, input_key in [('operations', 'callbacks'), ('actual_read_bytes', 'actual_read_bytes'),
                                   ('actual_write_bytes', 'actual_write_bytes')]:
        if replay.get(result_key) != expected[input_key]:
            raise ValueError('trace count mismatch: ' + result_key)
    save(session.out/'trace-result.json', {'status': 'ok', 'wall_seconds': time.monotonic()-start,
         'db': redis_delta(before, snapshot(s['redis_url'])), 'completed': True, 'strict_correctness': False,
         'total_timeout_s': None, 'manifest_sha256': s['trace_manifest_sha256'], 'replay': replay})
    session.unmount()


def packed_build(spec, output):
    """Actual immutable-directory producer; output time includes upload/publication."""
    out = Path(output); out.mkdir(parents=True, exist_ok=False)
    pin=load(spec['backend_pin'])
    save(out/'runtime-pin.json',runtime_pin(pin,spec['rustfs_container']))
    check_endpoint_container(spec['endpoint'],spec['rustfs_container'])
    binary = check_binary(spec['fixture'])
    if spec['fixture']['source_sha'] != pin['brewfs_client_sha']:
        raise ValueError('packed producer source differs from pinned BrewFS source')
    source = Path(spec['source']).resolve(strict=True)
    # Let the producer verify readonly Btrfs snapshot semantics, not a user boolean.
    cmd = [binary, '--source-directory', str(source), '--source-consistency', 'snapshot-backed',
           '--source-hardlink-policy', 'reject-external', '--bucket', spec['bucket'],
           '--endpoint', no_credentials_in_url(spec['endpoint']), '--region', spec.get('region', 'us-east-1'),
           '--force-path-style', 'true', '--manifest-output', str(out/'manifest-key.txt'),
           '--output-dir', str(out/'objects')]
    start = time.monotonic(); status = 'interrupted'
    try:
        with (out/'build.stdout').open('x') as stdout, (out/'build.stderr').open('x') as stderr:
            rc = subprocess.run(cmd, stdout=stdout, stderr=stderr).returncode
        status = 'ok' if rc == 0 and (out/'manifest-key.txt').is_file() else 'failed'
    finally:
        save(out/'build.json', {'status': status, 'argv': cmd, 'wall_seconds': time.monotonic()-start,
             'freeze_seconds': None, 'freeze_cost_included': False,
             'build_upload_publish_seconds_separately': None, 'formal_qualified': False,
             'note': 'producer total only; preceding immutable snapshot creation must be measured separately'})
    return 0 if status == 'ok' else 1


def run(spec, mode, output):
    started=time.monotonic()
    session = Session(spec, output)
    status, reason, cleanup = 'interrupted', None, None
    try:
        session.preflight(); session.initialize()
        with Observer(session.out/'host.jsonl', spec.get('observe', True)):
            {'diagnostics': diagnostics, 'packed-queries': packed_queries, 'object-io': object_io,
             'fork-base': fork_base, 'trace': trace}[mode](session)
        status = 'ok'
    except KeyboardInterrupt:
        status, reason = 'interrupted', 'operator interruption'
    except Exception as exc:
        status, reason = 'failed', str(exc)
    finally:
        try:
            session.unmount()
        except Exception as exc:
            cleanup = str(exc); status = 'failed'
        save(session.out/'result.json', {'status': status, 'reason': reason, 'cleanup_error': cleanup,
             'method': spec['method'], 'mode': mode, 'formal_qualified': False, 'total_timeout_s': None,
             'runner_wall_seconds':time.monotonic()-started, 'batch_id':spec.get('batch_id'),
             'scope': 'local worker diagnostic; no claim of lifecycle, EC layout, or cross-host qualification',
             'retained_work_root': str(session.root)})
    return 0 if status == 'ok' else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['diagnostics', 'packed-queries', 'packed-build', 'object-io', 'fork-base', 'trace'])
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    spec = load(a.spec)
    if a.mode == 'packed-build':
        return packed_build(spec, a.output)
    return run(spec, a.mode, a.output)


if __name__ == '__main__':
    raise SystemExit(main())
