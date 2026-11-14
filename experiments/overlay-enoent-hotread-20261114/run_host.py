"""Build both BrewFS revisions and run the warm FUSE comparison on one Linux host."""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


FEATURES = ('fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,'
            'rustfs-ec-block-size-hint,observability')
VERSIONS = {'before': 'fa630c33ed9691f66b09742ae695aecb0d666a0e',
            'after': '187f62dd2dc820dfdab8148bf70419eb587befa1'}


def save(path, data):
    with path.open('x') as out:
        json.dump(data, out, indent=2)
        out.write('\n')


def processes():
    names = {'cargo', 'rustc', 'rust-lld', 'fio', 'brewfs', 'juicefs', 'fsreplay'}
    active = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            comm = (entry / 'comm').read_text().strip()
            # Docker shim children that have already exited can remain zombies.
            state, ppid = (entry / 'stat').read_text().rsplit(')', 1)[1].split()[:2]
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if comm in names and state != 'Z':
            active.append((entry.name, comm, int(ppid)))
    return active


def snapshot(root):
    memory = int(next(line for line in Path('/proc/meminfo').read_text().splitlines()
                      if line.startswith('MemAvailable:')).split()[1]) << 10
    free = shutil.disk_usage(root).free
    containers = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}'], text=True).splitlines()
    pressure = {name: Path('/proc/pressure', name).read_text() for name in ('cpu', 'io', 'memory')}
    return dict(host=os.uname().nodename, cpu_count=os.cpu_count(), available_ram_bytes=memory,
                cpu_online=Path('/sys/devices/system/cpu/online').read_text().strip(), free_bytes=free,
                loadavg=os.getloadavg(), active_processes=processes(), containers=containers,
                pressure=pressure, time_ns=time.time_ns())


def wait_idle(root, timeout, available_gib, allowed_containers):
    limit = time.monotonic() + timeout
    while True:
        info = snapshot(root)
        if (info['available_ram_bytes'] >= available_gib << 30 and info['free_bytes'] >= 120 << 30
                and not info['active_processes'] and set(info['containers']) <= allowed_containers
                and info['loadavg'][0] < max(2, info['cpu_count'] / 4)):
            return info
        if time.monotonic() >= limit:
            raise RuntimeError(f'Host has no available measurement window: {info}')
        time.sleep(min(10, max(.01, limit - time.monotonic())))


def build(role, source, root, env, jobs):
    if not (source / 'Cargo.toml').is_file():
        raise ValueError(f'Missing BrewFS manifest in {source}')
    # Source archives carry a caller-supplied revision label. A checkout can
    # supply its own revision and working tree status in the build report.
    state = {}
    if (source / '.git').exists():
        state['revision'] = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
        state['working_tree'] = subprocess.check_output(
            ['git', 'status', '--porcelain'], cwd=source, text=True).splitlines()
        if state['working_tree']:
            raise ValueError(f'Use a clean checkout for the {role} build: {source}')
    artifact = root / role
    artifact.mkdir()
    target = root / 'release-target'
    build_env = dict(env, CARGO_TARGET_DIR=str(target))
    # Compile the first-party package from this source tree; retain dependencies.
    clean = ['cargo', 'clean', '--offline', '--release', '-p', 'brewfs']
    subprocess.run(clean, cwd=source, env=build_env, check=True, stdout=subprocess.DEVNULL)
    binaries = ('brewfs', 'packed_snapshot_fixture') if role == 'after' else ('brewfs',)
    build = ['cargo', '--config', 'profile.release.package.brewfs.opt-level=3', 'build',
             '--locked', '--offline', '--release', '--no-default-features', '--features', FEATURES,
             '--jobs', str(jobs),
             '--message-format=json-render-diagnostics']
    for name in binaries:
        build.extend(('--bin', name))
    outputs = {}
    started_ns = time.time_ns()
    with (artifact / 'build.log').open('x') as log:
        child = subprocess.Popen(['nice', '-n', '5', *build], cwd=source, env=build_env,
                                 stdout=subprocess.PIPE, stderr=log, text=True)
        for line in child.stdout:
            log.write(line)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = msg.get('target', {}).get('name')
            if msg.get('reason') == 'compiler-artifact' and msg.get('executable') and name in binaries:
                if msg.get('fresh') is not False:
                    raise RuntimeError(f'Cargo did not recompile {name} from {source}')
                original = Path(msg['executable'])
                dest = artifact / name
                shutil.copy2(original, dest)
                outputs[name] = dict(path=str(dest), bytes=dest.stat().st_size,
                                     rebuilt=True, compiler_source=msg['target']['src_path'])
        if child.wait() != 0 or set(outputs) != set(binaries):
            raise RuntimeError(f'Build did not complete; read {artifact / "build.log"}')
    version = subprocess.check_output([str(artifact / 'brewfs'), '--version'], text=True).strip()
    save(artifact / 'build.json', dict(source_commit=state.get('revision', VERSIONS[role]), source_path=str(source),
                                      checkout=state,
                                      command=build, binaries=outputs, version=version,
                                      started_ns=started_ns, finished_ns=time.time_ns()))
    print(role, 'release ready', flush=True)


def other_container_io(allowed):
    if not allowed:
        return {}
    command = ['docker', 'stats', '--no-stream', '--format', '{{.Name}} {{.BlockIO}}', *sorted(allowed)]
    return dict(line.split(' ', 1) for line in subprocess.check_output(command, text=True).splitlines())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before-source', 'after-source', 'out', 'toolchain', 'cargo-home'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--redis-image', default='')
    parser.add_argument('--rustfs-image', required=True)
    parser.add_argument('--lock', type=Path, action='append', default=[])
    parser.add_argument('--jobs', type=int, default=6)
    parser.add_argument('--available-ram-gib', type=int, default=64)
    parser.add_argument('--allow-idle-container', action='append', default=[])
    parser.add_argument('--wait-idle-seconds', type=int, default=600)
    parser.add_argument('--reuse-builds', type=Path, help='Run measurements using a completed build directory')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('the isolated FUSE/RustFS test requires root')
    root = args.out.resolve()
    root.mkdir()
    scratch = root / 'scratch'
    scratch.mkdir()
    allowed = set(args.allow_idle_container)
    env = {key: value for key, value in os.environ.items() if not key.startswith('CARGO_PROFILE_')}
    env.update(PATH=str(args.toolchain.resolve()) + ':' + os.environ['PATH'],
               CARGO_HOME=str(args.cargo_home.resolve()), CARGO_NET_OFFLINE='true',
               CARGO_INCREMENTAL='0', CARGO_PROFILE_RELEASE_DEBUG='0')
    try:
        with ExitStack() as stack:
            locks = ['/run/lock/ax-overlay-open-diagnostic.lock', *map(str, args.lock)]
            for path in dict.fromkeys(locks):
                lock = stack.enter_context(open(path, 'a'))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            save(root / 'host-before-build.json', wait_idle(root, args.wait_idle_seconds, args.available_ram_gib, allowed))
            build_root = args.reuse_builds.resolve() if args.reuse_builds else root
            if args.reuse_builds:
                shutil.copyfile(build_root / 'rustc.txt', root / 'rustc.txt')
            else:
                (root / 'rustc.txt').write_text(subprocess.check_output(['rustc', '-Vv'], env=env, text=True))
            for image in filter(None, (args.redis_image, args.rustfs_image)):
                subprocess.run(['docker', 'image', 'inspect', '--format', '{{json .RepoTags}}', image], check=True)
            if not args.redis_image:
                for binary in ('redis-server', 'redis-cli'):
                    subprocess.run([binary, '--version'], check=True)
            for role, path in (('before', args.before_source), ('after', args.after_source)):
                if args.reuse_builds:
                    receipt = build_root / role / 'build.json'
                    prior = json.loads(receipt.read_text())
                    assert Path(prior['source_path']) == path.resolve()
                    out = root / role
                    out.mkdir()
                    shutil.copyfile(receipt, out / 'build.json')
                    for name, item in prior['binaries'].items():
                        binary = build_root / role / name
                        assert Path(item['path']) == binary and binary.stat().st_size == item['bytes']
                else:
                    build(role, path.resolve(), root, env, args.jobs)
            packages = Path(__file__).resolve().parents[1] / 'python-packages'
            measure_env = dict(os.environ, TMPDIR=str(scratch))
            if packages.is_dir():
                measure_env['PYTHONPATH'] = str(packages)
            subprocess.run([sys.executable, '-c', 'import boto3, botocore'], env=measure_env, check=True)
            time.sleep(3)
            for fixture in ('two-files', 'many-files-10000'):
                save(root / (fixture + '-host-before.json'),
                     wait_idle(root, args.wait_idle_seconds, args.available_ram_gib, allowed))
                before_io = other_container_io(allowed)
                command = [sys.executable, str(Path(__file__).with_name('run.py')), '--fixture', fixture,
                           '--before', str(build_root / 'before/brewfs'), '--after', str(build_root / 'after/brewfs'),
                           '--producer', str(build_root / 'after/packed_snapshot_fixture'),
                           '--before-build', str(root / 'before/build.json'),
                           '--after-build', str(root / 'after/build.json'), '--source-commit', VERSIONS['before'],
                           '--redis-image', args.redis_image, '--rustfs-image', args.rustfs_image,
                           '--out', str(root / fixture)]
                save(root / (fixture + '-command.json'), command)
                with (root / (fixture + '.log')).open('x') as log:
                    subprocess.run(command, env=measure_env, stdout=log, stderr=subprocess.STDOUT, check=True)
                save(root / (fixture + '-host-after.json'), snapshot(root))
                after_io = other_container_io(allowed)
                save(root / (fixture + '-ambient-container-io.json'), dict(before=before_io, after=after_io))
                if before_io != after_io:
                    raise RuntimeError('Neighboring containers performed detectable disk I/O')
                print(fixture, 'complete', flush=True)
    except BaseException:
        (root / 'exit-code.txt').write_text('1\n')
        raise
    else:
        (root / 'exit-code.txt').write_text('0\n')


if __name__ == '__main__':
    main()
