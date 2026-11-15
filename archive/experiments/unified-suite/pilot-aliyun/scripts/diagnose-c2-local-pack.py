#!/usr/bin/env python3
"""Run the original C2 publisher against LocalFS, without S3, Redis or FUSE."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import socket
import stat
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'scripts'))
from full_trace_common import tree

PRODUCER_SHA = 'aa45b1ee9640da92051ead72b17ed41836b57812e05623f860b963e09373b3ab'
INITIAL_SHA = 'c1b9b066e6660301ad944fff78de1910e6388021c6caa90923355848eb1c2847'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('producer', 'case', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=60)
    args = parser.parse_args()
    require(1 <= args.seconds <= 120, 'Use a bounded local diagnostic of at most 120 seconds')
    producer = args.producer.resolve(strict=True)
    case = args.case.resolve(strict=True)
    source = case / 'initial'
    expected_file = case / 'initial-tree.json'
    require(digest(producer) == PRODUCER_SHA and digest(expected_file) == INITIAL_SHA,
            'Require the exact publisher and original C2 initial manifest')
    expected = json.loads(expected_file.read_text())
    require(tree(source) == expected, 'Initial contents, paths or modes differ')
    files = [v for v in expected.values() if v['type'] == 'file']
    directories = [v for v in expected.values() if v['type'] == 'directory']
    require(len(files) == 614 and len(directories) == 100 and
            sum(v['bytes'] for v in files) == 35526497, 'Unexpected original C2 input')
    require(all(p.lstat().st_nlink == 1 for p in source.rglob('*')
                if stat.S_ISREG(p.lstat().st_mode)), 'Unexpected source hardlinks')
    net = os.readlink('/proc/self/ns/net')
    interfaces = [name for _, name in socket.if_nameindex()]
    require(net != os.readlink('/proc/1/ns/net') and set(interfaces) == {'lo'},
            'Run in a separate loopback-only network namespace')
    out = args.out.resolve()
    require(args.out.is_absolute() and not out.is_relative_to(case) and not case.is_relative_to(out),
            'Use a new absolute output directory separate from the original case')
    out.mkdir(mode=0o700)
    tmp, objects = out/'tmp', out/'objects'
    tmp.mkdir()
    (out/'home').mkdir(mode=0o700)
    manifest = out/'manifest-key.txt'
    cpus = sorted(os.sched_getaffinity(0))[:2]
    argv = ['taskset', '--cpu-list', ','.join(map(str, cpus)), str(producer),
        '--source-directory', str(source), '--source-consistency', 'best-effort-detected',
        '--source-hardlink-policy', 'reject-external', '--output-dir', str(objects),
        '--manifest-output', str(manifest), '--prefix', 'base', '--metadata-codec', 'raw',
        '--data-codec', 'raw', '--inline-data', 'off', '--chunk-size', str(4*2**20),
        '--block-size', str(4*2**20), '--frame-policy', 'size-only',
        '--access-profile', 'random-small-file', '--max-random-frame-raw-bytes', '262144']
    env = dict(PATH='/usr/local/bin:/usr/bin:/bin', HOME=str(out/'home'), TMPDIR=str(tmp),
               LANG='C.UTF-8', RUST_LOG='error', NO_COLOR='1', AWS_EC2_METADATA_DISABLED='true')
    for name, mib in dict(ROOTS=16, METADATA_OWNED=256, PLANS=32, CONTROL=1, STORED=32,
                          RAW=32, DECODE_WORKSPACE=8, OUTPUT=32).items():
        env['BREWFS_PACKED_V3_'+name+'_BUDGET_BYTES'] = str(mib*2**20)
    env['BREWFS_WORKSPACE_ROW_CACHE_TTL_MS'] = '60000'
    result = dict(schema='ax-c2-local-publisher-diagnostic-v1', passed=False, formal_sample=False,
        source_commit='ca07159f3da4b670e4aeb878faef928e69bc1634', producer_sha256=PRODUCER_SHA,
        initial_manifest_sha256=INITIAL_SHA, files=614, directories=100, logical_file_bytes=35526497,
        storage='localfs', scope='Directory capture and Packed construction only; not S3 publication or replay',
        network_namespace=net, interfaces=interfaces, allowed_cpus=cpus, timeout_seconds=args.seconds,
        argv=argv, cloud_requests=0, model_calls=0, s3_hint='not-applicable-without-S3')
    save(out/'intent.json', result)
    child = None
    start = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    try:
        with (out/'stdout').open('xb') as stdout, (out/'stderr').open('xb') as stderr:
            child = subprocess.Popen(argv, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                code = child.wait(timeout=args.seconds)
            except BaseException as error:
                result['timed_out'] = isinstance(error, subprocess.TimeoutExpired)
                result['interrupted'] = True
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=3)
                raise
        result['publisher_seconds'] = time.monotonic()-start
        require(code == 0, 'Publisher returned a nonzero exit code')
        key = manifest.read_text().strip()
        rel = Path(key)
        require(not rel.is_absolute() and '..' not in rel.parts, 'Manifest key escapes output')
        manifest_object = (objects/rel).resolve(strict=True)
        require(manifest_object.is_relative_to(objects.resolve()), 'Manifest object escapes output')
        with manifest_object.open('rb') as stream:
            require(stream.read(8) == b'BRFPM005', 'Wrong Packed wire format')
        rows = []
        for path in sorted(objects.rglob('*')):
            require(not path.is_symlink(), 'Unexpected output symlink')
            if path.is_file():
                rows.append(dict(path=path.relative_to(objects).as_posix(), bytes=path.stat().st_size,
                                 sha256=digest(path)))
        require(rows and tree(source) == expected, 'No objects or the original source changed')
        save(out/'objects.json', rows)
        result.update(passed=True, original_source_unchanged=True, manifest_key=key,
                      manifest_sha256=digest(manifest_object), objects=len(rows),
                      stored_bytes=sum(r['bytes'] for r in rows), object_inventory_sha256=digest(out/'objects.json'),
                      source_provenance_sha256=digest(manifest.with_suffix('.source.json')))
    except Exception as error:
        result.update(error_class=type(error).__name__, error=str(error))
        raise
    finally:
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        result.update(exit_code=child.returncode if child else None,
                      child_exited=child is not None and child.poll() is not None,
                      diagnostic_seconds=time.monotonic()-start,
                      child_cpu_user_seconds=after.ru_utime-before.ru_utime,
                      child_cpu_system_seconds=after.ru_stime-before.ru_stime,
                      child_max_rss_kib=after.ru_maxrss)
        save(out/'result.json', result)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
