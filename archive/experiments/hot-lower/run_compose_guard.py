#!/usr/bin/env python3
"""Run the repository's fio-randrw guard with pinned, local Compose services.

One invocation per build. Use the matching source checkout; alternate invocations
between builds. This checks the ordinary writable path, not hot Packed reads.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from run_local import REDIS, RUSTFS, save, sha, verify_backend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--tools', nargs='+', choices=('fio-seqwrite', 'fio-randwrite', 'fio-randrw'), default=['fio-randrw'])
    parser.add_argument('--direct', type=int, choices=(0, 1))
    parser.add_argument('--runtime', type=int, default=20, help='fio active seconds; increase for sufficient mixed-write tail samples')
    args = parser.parse_args()
    assert 10 <= args.runtime <= 300
    args.repository = args.repository.resolve()
    args.binary = args.binary.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    project = 'hot-guard-' + uuid.uuid4().hex[:10]
    env = dict(os.environ, COMPOSE_PROJECT_NAME=project, BREWFS_REUSE_HOST_BINARY='1',
               # This Compose file publishes no host ports. Prevent the legacy
               # runner's port cleanup from examining another task's ports.
               REDIS_HOST_PORT='0', RUSTFS_S3_HOST_PORT='0', RUSTFS_CONSOLE_HOST_PORT='0',
               PERF_LOG_TO_CONSOLE='false', PERF_FIO_SIZE='512m', PERF_FIO_RUNTIME=str(args.runtime),
               PERF_FIO_RANDSEED='20261112', PERF_FIO_RANDREPEAT='1',
               PERF_FIO_DROP_CACHES='0', PERF_FIO_COLD_READ_DROP_CACHES='0',
               AWS_ACCESS_KEY_ID='localguard', AWS_SECRET_ACCESS_KEY='localguardsecret',
               AWS_EC2_METADATA_DISABLED='true', BREWFS_S3_BUCKET='hot-guard',
               RUST_LOG='warn')
    if args.direct is not None:
        env['PERF_FIO_DIRECT'] = str(args.direct)
    def invoke(argv, **kw):
        return subprocess.run(list(map(str, argv)), env=env, check=True, text=True, **kw)
    def image(name):
        return invoke(['docker', 'image', 'inspect', '--format', '{{.Id}}', name], capture_output=True).stdout.strip()
    for digest in (REDIS, RUSTFS):
        assert image(digest) == digest
    perf_image, aws_image = image('brewfs-xfstests-perf:local'), image('amazon/aws-cli:latest')
    backend = verify_backend(lambda argv, **kw: invoke(argv, capture_output=True, **kw))
    shared = dict(AWS_ACCESS_KEY_ID='localguard', AWS_SECRET_ACCESS_KEY='localguardsecret',
                  AWS_DEFAULT_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true')
    compose = dict(services={
        'redis': dict(image=REDIS, pull_policy='never', command=['redis-server', '--save', '', '--appendonly', 'yes'],
                      healthcheck=dict(test=['CMD', 'redis-cli', 'ping'], interval='2s', timeout='2s', retries=30),
                      mem_limit='256m', cpus=1),
        'rustfs': dict(image=RUSTFS, pull_policy='never', user='0:0', mem_limit='2g', cpus=2,
                       environment=dict(RUSTFS_ACCESS_KEY='localguard', RUSTFS_SECRET_KEY='localguardsecret',
                                        RUSTFS_ADDRESS='0.0.0.0:9000', RUSTFS_CONSOLE_ENABLE='false',
                                        RUSTFS_VOLUMES='/data/rustfs{0...3}', RUSTFS_UNSAFE_BYPASS_DISK_CHECK='true',
                                        RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE='true', RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED='true',
                                        RUSTFS_EC_READ_QUANTUM_ENABLE='true', RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED='true'),
                       volumes=[f'disk{i}:/data/rustfs{i}' for i in range(4)]),
        'rustfs-init': dict(image=aws_image, pull_policy='never', environment=shared,
                            depends_on={'rustfs': {'condition': 'service_started'}, 'redis': {'condition': 'service_healthy'}},
                            entrypoint=['/bin/sh', '-ec'],
                            command=["timeout 90 sh -ec 'while true; do "
                                     "aws --cli-connect-timeout 2 --cli-read-timeout 3 --endpoint-url http://rustfs:9000 s3api head-bucket --bucket hot-guard >/dev/null 2>&1 && exit 0; "
                                     "aws --cli-connect-timeout 2 --cli-read-timeout 3 --endpoint-url http://rustfs:9000 s3api create-bucket --bucket hot-guard >/dev/null 2>&1 && exit 0; "
                                     "sleep 1; done'"]),
        'perf': dict(image=perf_image, pull_policy='never', privileged=True, devices=['/dev/fuse:/dev/fuse'],
                     mem_limit='16g', cpus=4,
                     entrypoint=['/usr/bin/tini', '--', 'bash', '/usr/local/bin/run_perf_in_container.sh'],
                     environment={**shared, 'BREWFS_META_BACKEND': 'redis', 'BREWFS_META_URL': 'redis://redis:6379/0',
                                  'BREWFS_DATA_BACKEND': 's3', 'BREWFS_S3_BUCKET': 'hot-guard',
                                  'BREWFS_S3_ENDPOINT': 'http://rustfs:9000', 'BREWFS_S3_REGION': 'us-east-1',
                                  'BREWFS_S3_FORCE_PATH_STYLE': 'true', 'BREWFS_ARTIFACT_ROOT': '/artifacts',
                                  'BREWFS_LOG_FILE': '/artifacts/brewfs.log', 'RUST_LOG': 'warn'},
                     volumes=[f'{args.binary}:/usr/local/bin/brewfs:ro',
                              f'{args.repository}/docker/compose-xfstests/run_perf_in_container.sh:/usr/local/bin/run_perf_in_container.sh:ro',
                              f'{args.repository}/tools/perf/perf_manifest.py:/usr/local/bin/perf_manifest.py:ro',
                              f'{args.repository}/tools/perf/perf_lines_observe.py:/usr/local/bin/perf_lines_observe.py:ro',
                              f'{args.repository}/docker/compose-xfstests/artifacts:/artifacts', 'state:/var/lib/brewfs'])
    }, volumes={**{f'disk{i}': {} for i in range(4)}, 'state': {}})
    config = args.out / 'compose.json'
    save(config, compose)
    env['BREWFS_PERF_COMPOSE_FILE'] = str(config)
    context_binary = args.repository / 'target/docker/brewfs'
    assert not context_binary.exists(), 'use a clean task checkout; do not overwrite an existing Docker binary'
    context_binary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.binary, context_binary)
    context_binary.chmod(0o755)
    artifacts = args.repository / 'docker/compose-xfstests/artifacts'
    artifacts.mkdir(parents=True, exist_ok=True)
    before = set(artifacts.iterdir())
    command = ['bash', args.repository / 'docker/compose-xfstests/run_redis_perf.sh', '--s3',
               '--writeback-throughput-profile', '--tools', ' '.join(args.tools)]
    record = dict(binary_sha256=sha(args.binary), argv=list(map(str, command)), passed=False,
                  backend_version=backend,
                  source_commit=invoke(['git', '-C', args.repository, 'rev-parse', 'HEAD'], capture_output=True).stdout.strip(),
                  project=project, tools=args.tools, direct=env.get('PERF_FIO_DIRECT', '0'), runtime_seconds=args.runtime,
                  images=dict(redis=REDIS, rustfs=RUSTFS, perf=perf_image, aws_cli=aws_image))
    try:
        if source_patch := env.get('BREWFS_SOURCE_PATCH'):
            shutil.copyfile(source_patch, args.out / 'source.patch')
            record['source_patch_origin'] = source_patch
        else:
            with (args.out / 'source.patch').open('w') as patch:
                invoke(['git', '-C', args.repository, 'diff', '--binary', 'HEAD'], stdout=patch)
        record['source_patch_sha256'] = sha(args.out / 'source.patch')
        with (args.out / 'runner.log').open('x') as log:
            invoke(command, stdout=log, stderr=subprocess.STDOUT)
        record['passed'] = True
    finally:
        for path in sorted(set(artifacts.iterdir()) - before):
            if path.is_dir():
                shutil.copytree(path, args.out / path.name)
        done = subprocess.run(['docker', 'compose', '-f', str(config), 'down', '-v', '--remove-orphans'], env=env,
                              capture_output=True, text=True)
        record['cleanup_exit'] = done.returncode
        record['passed'] &= done.returncode == 0
        context_binary.unlink()
        save(args.out / 'run.json', record)
        (args.out / 'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(args.out).as_posix()}\n'
                                                   for p in sorted(args.out.rglob('*')) if p.is_file()))


if __name__ == '__main__':
    main()
