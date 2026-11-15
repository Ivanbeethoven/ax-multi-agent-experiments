"""Linux-only before/after replay using the original pilot binaries/input.

Run after local CI and the candidate release build. Results are diagnostic,
single-host measurements; failed or timed-out replays have no full-trace latency.
No cloud resources, host page-cache drops, or existing containers are touched.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import tarfile
import time
import urllib.request

TRACE_SHA = '9953d90517eaf520814150f80abccb11f9d74b7ac84ecc56fa394a5f8495aa3d'
RUNTIME_SHA = 'ea35e460321e84a7eae5afa5215cb71c16b410a80d531968e8ca59a62686b971'
JUICEFS_PACKAGE_SHA = '79648a0a67145bf42ca4e8336578c13a5bbc9a0bd34df340b3b93dac1e975247'
JUICEFS_BINARY_SHA = '6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
BIN_SHA = {
    'brewfs': '765bce82ad5b971c4d24fc989f5a6ca201c8c7c3638c5a7c91501a0a1e4e994a',
    'packed_v3_snapshot_fixture': 'a48be898345836ef689d3fef65ab7fc743e0c301a756044c25eec7b3d15bbfc2',
    'full-fuse-replay': 'a352dfe4965efabff55fa53c05a82f7540765ca7dc2c395cd9c7394c5940aa25',
}
METHODS = {
    'baseline-packed': ('baseline', True, 2),
    'candidate-packed': ('candidate', True, 3),
    'baseline-overlay': ('baseline', False, 4),
    'candidate-overlay': ('candidate', False, 5),
    'juicefs': ('juicefs', False, 6),
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Repro:
    def __init__(self, args):
        self.args = args
        self.root = args.root.resolve()
        if self.root.exists():
            raise RuntimeError('Use a new output directory for every run')
        self.root.mkdir(mode=0o700)
        self.out = self.root / 'results'
        self.out.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.created = []
        self.mounts = []
        self.state = {'methods': {}, 'created_containers': self.created,
                      'scope': 'local WSL diagnostic; no host page-cache drop',
                      'correctness_certified': False, 'started_epoch': time.time()}
        self.env = dict(os.environ, RUST_LOG='warn', AWS_REGION='us-east-1',
                        AWS_EC2_METADATA_DISABLED='true',
                        AWS_ACCESS_KEY_ID='ax-local-' + secrets.token_hex(8),
                        AWS_SECRET_ACCESS_KEY=secrets.token_hex(24))
        # Every service endpoint is local. Do not inherit workstation proxies:
        # the Rust HTTP matcher does not recognize NO_PROXY=127.* as an IP
        # exclusion, and routing a loopback backend through it invalidates the
        # measurement. This changes only child processes, never host settings.
        for key in list(self.env):
            if key.lower() in {'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}:
                del self.env[key]
        self.env.update(NO_PROXY='127.0.0.1,localhost,::1',
                        no_proxy='127.0.0.1,localhost,::1')
        self.state['network_scope'] = 'loopback backends; child HTTP proxies disabled'
        self.envfile = self.root / 'services.local.env'
        values = {k: self.env[k] for k in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
                                          'AWS_REGION', 'AWS_EC2_METADATA_DISABLED')}
        values.update(RUSTFS_ACCESS_KEY=values['AWS_ACCESS_KEY_ID'],
                      RUSTFS_SECRET_KEY=values['AWS_SECRET_ACCESS_KEY'])
        self.envfile.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
        self.envfile.chmod(0o600)

    def save(self):
        temp = self.out / 'status.tmp'
        temp.write_text(json.dumps(self.state, indent=2) + '\n')
        temp.replace(self.out / 'status.json')

    def run(self, label, argv, timeout=180, required=True, env=None):
        start = time.monotonic()
        with (self.out / (label + '.stdout.log')).open('w') as stdout, \
             (self.out / (label + '.stderr.log')).open('w') as stderr:
            proc = subprocess.Popen(list(map(str, argv)), env=self.env if env is None else env,
                                    stdout=stdout, stderr=stderr, start_new_session=True)
            timed_out = False
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    code = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    code = proc.wait(timeout=10)
        result = {'exit_code': code, 'timed_out': timed_out,
                  'wall_seconds': time.monotonic() - start}
        (self.out / (label + '.command.json')).write_text(json.dumps(result, indent=2))
        if required and (code or timed_out):
            raise RuntimeError(f'{label} failed; see saved logs')
        return result

    def prepare(self):
        for archive, expected in ((self.args.trace_archive, TRACE_SHA),
                                  (self.args.runtime_archive, RUNTIME_SHA)):
            if digest(archive) != expected:
                raise RuntimeError('Input archive digest differs from the cloud pilot')
        with tarfile.open(self.args.runtime_archive) as tar:
            for name, expected in BIN_SHA.items():
                target = self.bin / name
                with tar.extractfile('bin/' + name) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
                if digest(target) != expected:
                    raise RuntimeError('Original binary digest mismatch')
                target.chmod(0o755)
            if 'juicefs' in self.args.methods:
                package = self.bin / 'juicefs.tar.gz'
                with tar.extractfile('packages/juicefs.tar.gz') as src, package.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
                self.extract_juicefs(package)
        input_root = self.root / 'input'
        input_root.mkdir()
        with tarfile.open(self.args.trace_archive) as tar:
            members = [m for m in tar.getmembers() if m.name.startswith((
                'pytest-full-02/initial/', 'pytest-full-02/capture/'))
                or m.name in ('pytest-full-02/initial', 'pytest-full-02/capture',
                              'pytest-full-02/metrics.json', 'pytest-full-02/initial-tree.json')]
            def checked_attributes(member, destination):
                safe = tarfile.data_filter(member, destination)
                # The known archive is trusted, but paths/links still get the
                # safe-data checks. Preserve initial POSIX ownership and modes.
                return safe.replace(mode=member.mode, uid=member.uid, gid=member.gid,
                                    uname=None, gname=None)
            tar.extractall(input_root, members=members, filter=checked_attributes)
        self.case = input_root / 'pytest-full-02'
        self.metrics = json.loads((self.case / 'metrics.json').read_text())
        assert self.metrics['callbacks'] == 1747109
        initial = (self.case / 'initial').resolve()
        for path in initial.rglob('*'):
            if path.is_symlink() and not path.resolve().is_relative_to(initial):
                raise RuntimeError('Initial symlink escapes the private input root')
        self.state['source'] = {'trace_sha256': TRACE_SHA, 'baseline_binaries': BIN_SHA,
                                'candidate_sha256': digest(self.args.candidate),
                                'observer_sha256': digest(self.args.observer)}
        if 'juicefs' in self.args.methods:
            self.state['source']['juicefs_package_sha256'] = JUICEFS_PACKAGE_SHA
            self.state['source']['juicefs_binary_sha256'] = digest(self.bin / 'juicefs')
        (self.bin / 'candidate').mkdir()
        frozen_candidate = self.bin / 'candidate/brewfs'
        shutil.copy2(self.args.candidate, frozen_candidate)
        self.args.candidate = frozen_candidate
        if self.args.compose_runner:
            frozen_runner = self.bin / 'run_perf_in_container.sh'
            frozen_runner.write_text(self.args.compose_runner.read_text())
            self.args.compose_runner = frozen_runner
            self.args.compose_image = subprocess.check_output(
                ['docker', 'image', 'inspect', '--format', '{{.Id}}', self.args.compose_image],
                text=True).strip()
        # Reuse the measured pilot observer; change only its host-scope label.
        text = self.args.observer.read_text()
        text = text.replace('whole dedicated ECS host; includes cloud agents',
                            'whole local WSL host; includes other local processes')
        (self.bin / 'observer.py').write_text(text)
        shutil.copyfile(Path(__file__), self.root / 'repro_local.source.py')
        self.state['source']['runtime_observer_sha256'] = digest(self.bin / 'observer.py')
        self.state['source']['repro_script_sha256'] = digest(self.root / 'repro_local.source.py')
        self.save()

    def extract_juicefs(self, package):
        # Extract only the verified regular binary; never unpack arbitrary paths.
        if digest(package) != JUICEFS_PACKAGE_SHA:
            raise RuntimeError('Original JuiceFS package digest mismatch')
        with tarfile.open(package) as archive:
            member = archive.getmember('juicefs')
            if not member.isreg():
                raise RuntimeError('JuiceFS package binary is not a regular file')
            target = self.bin / 'juicefs'
            with archive.extractfile(member) as src, target.open('wb') as dst:
                shutil.copyfileobj(src, dst)
        if digest(target) != JUICEFS_BINARY_SHA:
            raise RuntimeError('Original JuiceFS binary digest mismatch')
        target.chmod(0o755)

    def services(self):
        # The existing observer uses these endpoints. Refuse occupied ports.
        ports = (6380, 9000, 9567) if 'juicefs' in self.args.methods else (6380, 9000)
        for port in ports:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', port))
        token = secrets.token_hex(6)
        self.state['backend_images'] = {}
        for kind, image, port, command in (
            ('redis', 'redis:7.2-alpine', '127.0.0.1:6380:6379',
             ['redis-server', '--save', '', '--appendonly', 'yes', '--appendfsync', 'always']),
            ('rustfs', 'rustfs/rustfs:latest', '127.0.0.1:9000:9000', ['/data']),
        ):
            name = 'ax-ai-fix-' + kind + '-' + token
            existing = subprocess.run(['docker', 'container', 'inspect', name],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if existing.returncode == 0:
                raise RuntimeError('Refusing to reuse an existing container name')
            self.state['backend_images'][kind] = subprocess.check_output(
                ['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
            data = self.root / (kind + '-data')
            data.mkdir(mode=0o777)
            data.chmod(0o777)
            # Register the fresh random name before Docker may create a
            # stopped container on a failed start (for example, a port race).
            self.created.append(name)
            self.save()
            self.run(kind + '-start', ['docker', 'run', '-d', '--pull=never', '--name', name,
                '--label', 'ax.ai-trace-fix=' + token, '-p', port, '--env-file', self.envfile,
                '-v', str(data) + ':/data', image, *command])
        for _ in range(60):
            try:
                with urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=1):
                    return
            except OSError:
                time.sleep(1)
        raise RuntimeError('RustFS did not become ready')

    def info(self):
        raw = subprocess.check_output(['docker', 'exec', self.created[0], 'redis-cli',
                                       'INFO', 'ALL'], text=True)
        return dict(line.split(':', 1) for line in raw.splitlines()
                    if ':' in line and not line.startswith('#'))

    def config(self, name, db, bucket, workspace, cache):
        config = self.root / (name + '.yaml')
        config.write_text(f'''mount_point: {self.root / 'mounts' / name}
volume_format: workspace-v1
workspace_namespace: {name}
workspace: {workspace}
data:
  backend: s3
  s3:
    bucket: {bucket}
    endpoint: http://127.0.0.1:9000
    region: us-east-1
    force_path_style: true
    max_concurrency: 8
meta:
  backend: redis
  redis:
    url: redis://127.0.0.1:6380/{db}
  open_file_cache_ttl_ms: 0
  open_file_cache_capacity: 0
cache:
  root: {self.root / 'cache' / name / cache}
  read_memory_bytes: 268435456
  write_memory_bytes: 268435456
  memory_budget_bytes: 536870912
  read_ssd_bytes: 536870912
  write_ssd_bytes: 536870912
  compression: none
  writeback_mode: upload_before_commit
fuse:
  workers: 2
  cache_ttl_ms: 0
''')
        return config

    def mount(self, name, binary, config):
        return self.mount_process(name, [binary, 'mount', '--config', config])

    def mount_process(self, name, argv, env=None):
        target = self.root / 'mounts' / name
        target.mkdir(parents=True, exist_ok=True)
        with (self.out / (name + '-mount.log')).open('a') as log:
            proc = subprocess.Popen(list(map(str, argv)),
                env=self.env if env is None else env, stdout=log, stderr=log, start_new_session=True)
        self.mounts.append((target, proc))
        for _ in range(60):
            if subprocess.run(['mountpoint', '-q', target]).returncode == 0:
                return target
            if proc.poll() is not None:
                raise RuntimeError(name + ' mount exited')
            time.sleep(0.5)
        raise RuntimeError(name + ' mount timed out')

    def unmount(self, target, label):
        self.run(label, ['fusermount3', '-u', target], timeout=30)
        for path, proc in self.mounts:
            if path == target and proc.poll() is None:
                proc.wait(timeout=30)

    def method(self, version, packed, db):
        if version == 'juicefs':
            return self.juicefs_method(db)
        name = version + ('-packed' if packed else '-overlay')
        binary = self.args.candidate if version == 'candidate' else self.bin / 'brewfs'
        bucket = 'ax-repro-' + name
        row = self.state['methods'][name] = {'phase': 'preparing'}
        self.save()
        self.run(name + '-bucket', ['docker', 'run', '--rm', '--pull=never', '--network', 'host',
            '--env-file', self.envfile, 'amazon/aws-cli:latest', '--endpoint-url',
            'http://127.0.0.1:9000', 's3api', 'create-bucket', '--bucket', bucket])
        ws = [binary, 'workspace', '--meta-backend', 'redis', '--meta-url',
              f'redis://127.0.0.1:6380/{db}', '--workspace-namespace', name]
        if packed:
            source = self.root / (name + '-source')
            source.mkdir()
            self.run(name + '-source-copy', ['cp', '-a', '--reflink=never',
                self.case / 'initial', source / 'workload'], timeout=600)
            key = self.root / (name + '-manifest-key.txt')
            row['packed_build'] = self.run(name + '-pack', [self.bin / 'packed_v3_snapshot_fixture',
                '--source-directory', source, '--source-consistency', 'best-effort-detected',
                '--source-hardlink-policy', 'reject-external', '--metadata-codec', 'raw',
                '--data-codec', 'raw', '--inline-data', 'off', '--bucket', bucket,
                '--endpoint', 'http://127.0.0.1:9000', '--region', 'us-east-1',
                '--force-path-style', 'true', '--manifest-output', key], timeout=900)
            cfg = self.config(name, db, bucket, 'null', 'prepare')
            self.run(name + '-init', [*ws, 'init-packed-volume', '--manifest-key',
                key.read_text().strip(), '--data-config', cfg])
        else:
            self.run(name + '-init', [*ws, 'init-volume'])
        initialized = json.loads((self.out / (name + '-init.stdout.log')).read_text())
        workspace = initialized.get('id', initialized.get('workspace_id'))
        assert workspace
        cfg = self.config(name, db, bucket, workspace, 'prepare')
        target = self.mount(name, binary, cfg)
        if not packed:
            self.run(name + '-copy', ['cp', '-a', '--reflink=never',
                self.case / 'initial', target / 'workload'], timeout=900)
        self.unmount(target, name + '-prepare-unmount')
        cfg = self.config(name, db, bucket, workspace, 'measure')
        target = self.mount(name, binary, cfg)
        self.measure(name, target, row)

    def juicefs_method(self, db):
        name = 'juicefs'
        binary = self.bin / name
        bucket = 'ax-repro-juicefs'
        meta = f'redis://127.0.0.1:6380/{db}'
        target = self.root / 'mounts' / name
        row = self.state['methods'][name] = {'phase': 'preparing', 'redis_db': db,
                                            'binary_sha256': digest(binary)}
        self.save()
        self.run(name + '-bucket', ['docker', 'run', '--rm', '--pull=never', '--network', 'host',
            '--env-file', self.envfile, 'amazon/aws-cli:latest', '--endpoint-url',
            'http://127.0.0.1:9000', 's3api', 'create-bucket', '--bucket', bucket])
        env = dict(self.env, ACCESS_KEY=self.env['AWS_ACCESS_KEY_ID'],
                   SECRET_KEY=self.env['AWS_SECRET_ACCESS_KEY'])
        self.run(name + '-format', [binary, 'format', '--no-update', '--storage', 's3',
            '--bucket', 'http://127.0.0.1:9000/' + bucket, '--trash-days', '0',
            '--compress', 'none', meta, 'axjfs'], env=env)
        def mount(phase):
            cache = self.root / 'cache' / name / phase
            # No -d: retain the actual foreground mount PID through unmount.
            return self.mount_process(name, [binary, 'mount', '--enable-xattr', '--enable-cap',
                '--metrics', '127.0.0.1:9567', '--cache-dir', cache, '--cache-size', '512',
                '--buffer-size', '512', '--attr-cache', '0', '--entry-cache', '0',
                '--dir-entry-cache', '0', meta, target], env=env)
        mount('prepare')
        self.run(name + '-copy', ['cp', '-a', '--reflink=never',
            self.case / 'initial', target / 'workload'], timeout=None)
        self.unmount(target, name + '-prepare-unmount')
        mount('measure')
        self.measure(name, target, row)

    def measure(self, name, target, row):
        row['phase'] = 'replay'
        row['redis_before'] = self.info()
        self.save()
        with (self.out / (name + '-observe.stderr.log')).open('w') as observer_log:
            observer = subprocess.Popen(['python3', str(self.bin / 'observer.py'), '--output',
                str(self.out / (name + '-observe.jsonl'))], stdout=subprocess.DEVNULL,
                stderr=observer_log)
        try:
            report = self.out / (name + '-replay.json')
            row['replay_command'] = self.run(name + '-replay', [self.bin / 'full-fuse-replay',
                self.case / 'capture/events.jsonl', self.case / 'capture/blobs',
                target / 'workload', report, '--performance'], timeout=None,
                required=False)
        finally:
            observer.terminate()
            observer.wait(timeout=10)
        row['redis_after'] = self.info()
        if report.exists() and report.stat().st_size:
            row['report'] = json.loads(report.read_text())
        r = row.get('report', {})
        row['completed_workload'] = bool(r.get('completed')
            and row['replay_command']['exit_code'] == 0
            and not row['replay_command']['timed_out']
            and r.get('operations') == self.metrics['callbacks']
            and r.get('remaining_handles') == 0
            and all(r.get(k) == self.metrics[k] for k in ('actual_read_bytes', 'actual_write_bytes')))
        row['phase'] = 'finished'
        self.save()
        self.unmount(target, name + '-final-unmount')

    def cleanup(self):
        errors = list(self.state.get('compose_cleanup_errors', []))
        for target, proc in reversed(self.mounts):
            if subprocess.run(['mountpoint', '-q', target]).returncode == 0:
                try:
                    self.unmount(target, target.name + '-cleanup-unmount')
                except Exception as exc:
                    errors.append(type(exc).__name__ + ': ' + str(target))
            if proc.poll() is None:
                errors.append('mount process still running: ' + str(proc.pid))
        if not errors:
            for name in reversed(self.created):
                result = self.run(name + '-cleanup', ['docker', 'rm', '-f', name], required=False)
                if result['exit_code']:
                    errors.append('container cleanup failed: ' + name)
        self.state['cleanup_errors'] = errors
        self.state['finished_epoch'] = time.time()
        self.save()

    def compose_gate(self, version, db):
        """Use the repository's existing compose workload, in a private project."""
        name = 'compose-' + version
        binary = self.args.candidate if version == 'candidate' else self.bin / 'brewfs'
        bucket = 'ax-repro-' + name
        self.run(name + '-bucket', ['docker', 'run', '--rm', '--pull=never', '--network', 'host',
            '--env-file', self.envfile, 'amazon/aws-cli:latest', '--endpoint-url',
            'http://127.0.0.1:9000', 's3api', 'create-bucket', '--bucket', bucket])
        self.run(name + '-init', [binary, 'workspace', '--meta-backend', 'redis', '--meta-url',
            f'redis://127.0.0.1:6380/{db}', '--workspace-namespace', name, 'init-volume'])
        initialized = json.loads((self.out / (name + '-init.stdout.log')).read_text())
        workspace = initialized.get('id', initialized.get('workspace_id'))
        assert workspace
        artifact = self.out / name
        artifact.mkdir()
        state = self.root / (name + '-state')
        state.mkdir()
        runner = self.args.compose_runner
        project = 'ax-ai-fix-' + version + '-' + secrets.token_hex(6)
        environment = {k: self.env[k] for k in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
                                               'AWS_REGION', 'AWS_EC2_METADATA_DISABLED')}
        environment.update({
            'BREWFS_META_BACKEND': 'redis', 'BREWFS_META_URL': f'redis://127.0.0.1:6380/{db}',
            'BREWFS_VOLUME_FORMAT': 'workspace-v1', 'BREWFS_WORKSPACE_ID': workspace,
            'BREWFS_WORKSPACE_NAMESPACE': name, 'BREWFS_DATA_BACKEND': 's3',
            'BREWFS_S3_BUCKET': bucket, 'BREWFS_S3_ENDPOINT': 'http://127.0.0.1:9000',
            'BREWFS_S3_REGION': 'us-east-1', 'BREWFS_S3_FORCE_PATH_STYLE': 'true',
            'BREWFS_CONFIG_PATH': '/run/brewfs/config.yaml',
            'BREWFS_ARTIFACT_DIR': '/artifacts/run', 'BREWFS_ARTIFACT_ROOT': '/artifacts',
            'XFSTESTS_DIR': '/opt/xfstests-dev', 'RUST_LOG': 'warn',
            'BREWFS_CACHE_ROOT': '/var/lib/brewfs/cache',
            'BREWFS_READ_MEMORY_BYTES': '268435456', 'BREWFS_WRITE_MEMORY_BYTES': '268435456',
            'BREWFS_MEMORY_BUDGET_BYTES': '536870912',
            'BREWFS_READ_SSD_BYTES': '536870912', 'BREWFS_WRITE_SSD_BYTES': '536870912',
            'BREWFS_COMPRESSION': 'none', 'BREWFS_WRITEBACK_MODE': 'upload_before_commit',
            'BREWFS_METADATA_OPEN_CACHE_TTL_MS': '0', 'BREWFS_METADATA_OPEN_CACHE_CAPACITY': '0',
            'BREWFS_FUSE_WORKERS': '2', 'PERF_LOG_TO_CONSOLE': 'false',
            'PERF_TOOLS': getattr(self.args, 'compose_tools', 'fio-seqwrite fio-randwrite fio-randrw metaperf'),
            'PERF_FIO_SIZE': '512m', 'PERF_FIO_RUNTIME': '20', 'PERF_FIO_NUMJOBS': '1',
            'PERF_FIO_DIRECT_MATRIX': '0 1', 'PERF_FIO_DROP_CACHES': 'false',
            'PERF_FIO_COLD_READ_DROP_CACHES': 'false', 'PERF_FIO_PREFILL_DRAIN': 'true',
            'PERF_FIO_PREFILL_REMOUNT': 'true', 'PERF_FIO_POST_WRITE_DRAIN': 'true',
            'PERF_FIO_PREFILL_DRAIN_TIMEOUT_SECS': '120',
            'PERF_FIO_POST_WRITE_DRAIN_TIMEOUT_SECS': '120',
            'PERF_METADATA_POST_TOOL_DRAIN': 'true', 'PERF_METAPERF_SECONDS': '10',
        })
        config = self.root / (name + '.compose.local.json')
        config.write_text(json.dumps({'services': {'perf': {
            'image': self.args.compose_image, 'pull_policy': 'never',
            'entrypoint': ['/usr/bin/tini', '--', 'bash', '/usr/local/bin/run_perf_in_container.sh'],
            'privileged': True, 'devices': ['/dev/fuse:/dev/fuse'], 'network_mode': 'host',
            'ulimits': {'nofile': {'soft': 1048576, 'hard': 1048576}},
            'environment': environment,
            'volumes': [str(runner) + ':/usr/local/bin/run_perf_in_container.sh:ro',
                        str(binary) + ':/usr/local/bin/brewfs:ro',
                        str(artifact) + ':/artifacts', str(state) + ':/var/lib/brewfs'],
        }}}, indent=2))
        config.chmod(0o600)
        compose = ['docker', 'compose', '-p', project, '-f', str(config)]
        row = self.state.setdefault('compose', {})[version] = {
            'phase': 'running', 'runner_sha256': digest(runner), 'project': project,
            'profile': 'workspace-v1, upload_before_commit, 512MiB memory, buffered/direct',
            'image': subprocess.check_output(['docker', 'image', 'inspect', '--format',
                '{{.Id}}', self.args.compose_image], text=True).strip()}
        self.save()
        try:
            row['command'] = self.run(name, [*compose, 'run', '--rm', '--no-deps', 'perf'],
                                      timeout=None, required=False)
            row['phase'] = 'finished'
        finally:
            cleaned = self.run(name + '-down', [*compose, 'down', '-v', '--remove-orphans'],
                               timeout=60, required=False)
            row['cleanup'] = cleaned
            if cleaned['exit_code'] != 0 or cleaned['timed_out']:
                self.state.setdefault('compose_cleanup_errors', []).append(project)
            self.save()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ('root', 'runtime-archive', 'trace-archive', 'candidate', 'observer'):
        p.add_argument('--' + flag, type=Path, required=True)
    p.add_argument('--methods', nargs='+', choices=list(METHODS), default=list(METHODS),
                   help='Whole methods to execute; never selects or filters trace operations')
    p.add_argument('--compose-runner', type=Path,
                   help='Optional repository run_perf_in_container.sh for paired acceptance checks')
    p.add_argument('--compose-image', default='brewfs-xfstests:local',
                   help='Existing image containing actual fio and xfstests metaperf tools')
    args = p.parse_args()
    if len(args.methods) != len(set(args.methods)):
        p.error('Each method may be selected only once')
    if os.geteuid() != 0:
        p.error('Run as root to preserve trace ownership operations')
    run = Repro(args)
    run.state.update(selected_methods=args.methods, replay_timeout_s=None)
    run.save()
    try:
        run.prepare()
        run.services()
        if args.compose_runner:
            run.run('compose-tools-preflight', ['docker', 'run', '--rm', '--pull=never',
                '--entrypoint', '/bin/sh', args.compose_image, '-c',
                'set -e; test -x /opt/xfstests-dev/src/metaperf; test -x /usr/bin/tini; '
                'test -x /usr/bin/fusermount3; fio --version; '
                'sha256sum /opt/xfstests-dev/src/metaperf'])
        for selected in args.methods:
            version, packed, db = METHODS[selected]
            try:
                run.method(version, packed, db)
            except Exception as exc:
                name = selected
                run.state['methods'].setdefault(name, {})['error'] = str(exc)
                run.save()
                # Do not run another measurement over a failed live mount.
                raise
        if args.compose_runner:
            if not all(run.state['methods'].get(name, {}).get('completed_workload')
                       for name in ('candidate-packed', 'candidate-overlay')):
                run.state['compose_skipped'] = 'Candidate did not complete both full traces'
                run.save()
            else:
                for version, db in (('baseline', 10), ('candidate', 11)):
                    run.compose_gate(version, db)
    except Exception as exc:
        run.state['fatal_error'] = {'type': type(exc).__name__, 'message': str(exc)}
        run.save()
        raise
    finally:
        run.cleanup()


if __name__ == '__main__':
    main()
