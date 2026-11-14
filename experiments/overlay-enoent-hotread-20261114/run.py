"""Run the matched, warm Redis/FUSE comparison with local service tags."""
import argparse
from pathlib import Path
import sys
import types


HERE = Path(__file__).resolve().parent
BASE = HERE.parent / 'hot-lower-20261112' / 'run_local.py'
DETAIL = HERE.parent / 'overlay-open-a355-20261113' / 'run_detail.py'


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'Unexpected runner source at {old.splitlines()[0]}')
    return source.replace(old, new, 1)


def load(name, path, source):
    runner = types.ModuleType(name)
    runner.__file__ = str(path)
    exec(compile(source, str(path), 'exec'), vars(runner))
    return runner


def prepare(base, detail, redis_image, rustfs_image, fixture):
    # Preserve the mount, byte-by-byte verification, timings and metrics from
    # the earlier comparison. Use build receipts for source/artifact records.
    start = base.index('\nREDIS = ')
    end = base.index('\ndef save(', start)
    base = base[:start] + f'\nREDIS = {redis_image!r}\nRUSTFS = {rustfs_image!r}\n' + base[end:]
    start = base.index('\ndef sha(')
    end = base.index('\n@contextmanager', start)
    base = base[:start] + base[end:]
    base = replace(base, 'import hashlib\n', '')
    for old, new in (
        ('source_patch_sha256=sha(args.source_patch), binary_sha256=sha(args.binary),',
         'before_build_receipt=str(args.source_patch), before_binary=str(args.binary),'),
        ('harness_sha256=sha(Path(__file__).resolve()),', ''),
        ('producer_sha256=sha(args.producer), images=', 'producer=str(args.producer), images='),
        ('candidate_binary_sha256=sha(args.candidate_binary),', 'after_binary=str(args.candidate_binary),'),
        ('candidate_source_patch_sha256=sha(args.candidate_source_patch),',
         'after_build_receipt=str(args.candidate_source_patch),'),
        ("            assert docker('image', 'inspect', '--format', '{{.Id}}', image).stdout.strip() == image\n"
         "        result['backend_version'] = verify_backend(invoke)",
         "            docker('image', 'inspect', '--format', '{{json .RepoTags}}', image)\n"
         "        result['backend_version'] = {image: json.loads(docker('image', 'inspect', '--format', "
         "'{{json .Config.Labels}}', image).stdout) for image in (REDIS, RUSTFS)}"),
        ("            for scenario in scenarios:\n", "            for scenario in scenarios:\n"
         f"                if scenario != {fixture!r}:\n                    continue\n"),
        ("                        assert sha(args.binary) == result['binary_sha256'], 'binary changed during pairing'\n", ''),
        ("                        if args.candidate_binary:\n                            assert sha(args.candidate_binary) == result['candidate_binary_sha256'], 'candidate changed during pairing'\n", ''),
        ('binary_sha256=sha(active_binary),', 'binary=str(active_binary),'),
        ('expected_sha256={name: hashlib.sha256(content).hexdigest() for name, content in expected.items()},',
         'expected_files=len(expected), expected_bytes=sum(map(len, expected.values())),'),
        ("                            cell['metadata_journal_sha256'] = sha(journal)\n", ''),
        (next(line + '\n' for line in base.splitlines() if "(args.out / 'SHA256SUMS')" in line), ''),
        ("                            order = list(permutations(('production', 'candidate', 'packed-direct')))[pair % 6]",
         "                            order = (('production', 'candidate', 'packed-direct'),\n"
         "                                     ('candidate', 'packed-direct', 'production'),\n"
         "                                     ('packed-direct', 'production', 'candidate'),\n"
         "                                     ('packed-direct', 'candidate', 'production'),\n"
         "                                     ('production', 'packed-direct', 'candidate'),\n"
         "                                     ('candidate', 'production', 'packed-direct'))[pair % 6]"),
    ):
        base = replace(base, old, new)
    detail = detail[:detail.index('\ndef main(')]
    detail = replace(detail, 'import hashlib\n', '')
    if not redis_image:
        base = replace(base, "containers = [owner + '-redis', owner + '-rustfs']",
                       "containers = [owner + '-rustfs']\n    redis_process = None")
        assert base.count('for image in (REDIS, RUSTFS)') == 2
        base = base.replace('for image in (REDIS, RUSTFS)', 'for image in (RUSTFS,)')
        start = base.index("        docker(*common, '--name', containers[0]")
        end = base.index('        rustenv =', start)
        base = base[:start] + """        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            redis_port = reservation.getsockname()[1]
        redis_command = ['redis-server', '--bind', '127.0.0.1', '--port', str(redis_port),
                         '--dir', str(work), '--appendonly', 'no', '--save', '',
                         '--maxmemory', '256mb', '--maxmemory-policy', 'noeviction']
        result['commands'].append(redis_command)
        result['redis_version'] = invoke(['redis-server', '--version']).stdout.strip()
        with (args.out / 'redis.log').open('xb') as redis_log:
            redis_process = subprocess.Popen(redis_command, env=env, stdout=redis_log,
                                             stderr=subprocess.STDOUT)
""" + base[end:]
        base = base.replace('containers[1]', 'containers[0]')
        base = replace(base, "        redis_port = int(docker('port', containers[0], '6379/tcp').stdout.strip().rsplit(':', 1)[1])\n", '')
        base = replace(base, "        wait_for(lambda: docker('exec', containers[0], 'redis-cli', 'ping', check=False).stdout.strip() == 'PONG', 'Redis')",
                       """        def redis_ready():
            if redis_process.poll() is not None:
                raise RuntimeError('Redis exited before listening')
            return invoke(['redis-cli', '-h', '127.0.0.1', '-p', str(redis_port),
                           'ping'], check=False).stdout.strip() == 'PONG'
        wait_for(redis_ready, 'Redis')""")
        base = replace(base, '        for name in containers:', """
        if redis_process is not None:
            if redis_process.poll() is None:
                redis_process.terminate()
            try:
                cleanup['redis_exit_code'] = redis_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                redis_process.kill()
                cleanup['redis_exit_code'] = redis_process.wait(timeout=10)
            if cleanup['redis_exit_code'] != 0:
                result['passed'] = False
        for name in containers:""")
    return base, detail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('before', 'after', 'producer', 'before-build', 'after-build', 'out'):
        parser.add_argument('--' + key, required=True, type=Path)
    for key in ('source-commit', 'rustfs-image'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--redis-image', default='', help='Use the installed redis-server if omitted')
    parser.add_argument('--fixture', choices=('two-files', 'many-files-10000'), required=True)
    parser.add_argument('--pairs', type=int, default=3)
    args = parser.parse_args()
    fixture = args.fixture
    large = fixture == 'many-files-10000'
    base, detail = prepare(BASE.read_text(encoding='utf-8'), DETAIL.read_text(encoding='utf-8'),
                           args.redis_image, args.rustfs_image, fixture)
    runner = load('hotread_runner', BASE, base)
    stats = load('hotread_timing', DETAIL, detail)
    from run_host import processes
    runner.os.environ['BREWFS_CACHE_TTL_MS'] = '0'
    for key in ('BREWFS_HOT_PATH_PROFILE', 'BREWFS_WORKSPACE_ROW_CACHE_POLICY'):
        runner.os.environ.pop(key, None)
    save = runner.save
    last = {}

    def measured(root, expected):
        last.update(root=root, expected=expected)
        return stats.measured_round(root, expected)

    def checked_save(output, value):
        if output.name in ('warm.json', 'result.json'):
            foreign = [(pid, name) for pid, name, ppid in processes()
                       if name != 'brewfs' or ppid != runner.os.getpid()]
            if foreign:
                raise RuntimeError(f'Concurrent storage or build activity: {foreign}')
        if output.name == 'warm.json' and large:
            assert last['root'].name == output.parent.name
            proof = stats.directory_page_probe(last['root'], last['expected'])
            assert proof['entries'] == 9999 and proof['user_getdents64_calls'] > 2
            save(output.with_name('directory-pages.json'), proof)
        if output.name == 'result.json':
            for phase in ('before', 'first', 'after'):
                stats.assert_zero_ttl(output.with_name(phase + '.stats').read_text())
            value['effective_kernel_ttl_checked_zero'] = True
        save(output, value)

    runner.measured_round = measured
    runner.save = checked_save
    sys.argv = [sys.argv[0], '--binary', str(args.before.resolve()),
                '--candidate-binary', str(args.after.resolve()), '--producer', str(args.producer.resolve()),
                '--source-patch', str(args.before_build.resolve()),
                '--candidate-source-patch', str(args.after_build.resolve()),
                '--source-commit', args.source_commit, '--out', str(args.out.resolve()),
                '--workspace-format', 'workspace', '--build-profile', 'optimized-release',
                '--cache-state', 'on', '--metadata-sync', 'remote', '--pairs', str(args.pairs),
                '--comparison', 'overlay-total', '--object-meter', 'client',
                '--total-temperatures', 'warm', '--total-lower-build', 'candidate',
                '--total-row-cache-bytes', str(64 << 20), '--passes', '2' if large else '256',
                '--total-fixture', 'many10000' if large else 'standard',
                '--total-payload-cache-bytes', str((64 if large else 8) << 20)]
    runner.main()


if __name__ == '__main__':
    main()
