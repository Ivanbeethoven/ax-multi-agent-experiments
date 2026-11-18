"""Prepare and mount the file sets used by fork and Packed experiments."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import struct
import time
from uuid import uuid4

import cluster_lib as cl
import c4_support as support


def brewfs_binary(volume):
    if volume['engine'] == 'brewfs-packed' and os.environ.get('AX_PACKED_BREWFS_BINARY'):
        return Path(os.environ['AX_PACKED_BREWFS_BINARY'])
    return cl.BIN_DIR / 'brewfs'


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def metadata_arguments(volume):
    if volume.get('meta_backend') == 'tikv':
        return ['--meta-backend', 'tikv', '--meta-tikv-pd-endpoints', cl.PD_ENDPOINT]
    return ['--meta-backend', 'redis', '--meta-url', support.REDIS_URL]


def admin(volume, arguments, out):
    result = support.command([str(cl.BIN_DIR / 'ax-brewfs-admin'), *metadata_arguments(volume),
        '--workspace-namespace', volume['namespace'], *arguments], out, secrets=True)
    return cl.extract_json_from_cli(result.stdout)


def mount(volume, workspace, identifier, out, host='A366', slot=1, overrides=None):
    engine = volume['engine']
    request = {'id': identifier, 'phase': 'measure', 'engine': engine,
        'host': cl.HOST_BY_NAME[host]['ssh'], 'slot': slot, 'workspace': workspace,
        'parent_workspace': volume.get('base', 'none'), 'purpose': 'suite',
        'binary': str(brewfs_binary(volume) if engine.startswith('brewfs') else cl.BIN_DIR / engine),
        'cache_state': 'cold', 'windows': ['measure'], 'root': str(cl.RUN_ROOT),
        'overrides': overrides or {}}
    endpoint = cl.endpoint_for_slot(slot, cl.ALL_HOST_NAMES.index(host))
    if engine.startswith('brewfs'):
        spec = {'workspace_uuid': workspace, 'workspace_namespace': volume['namespace'],
            's3_bucket': volume['bucket'], 's3_endpoint': endpoint,
            'rustfs_ec_block_size_hint': 'off' if volume.get('dynamic') == 'off' else 'auto',
            'meta_backend': volume.get('meta_backend', 'redis'), 'pd_endpoints': [cl.PD_ENDPOINT]}
        if volume.get('manifest_key'):
            spec['packed_manifest_key'] = volume['manifest_key']
    else:
        spec = {'redis_db': volume.get('redis_db', 0), 'meta_url': volume['meta_url'],
                'subdir': workspace, 's3_bucket_url': endpoint + '/' + volume['bucket']}
    config = out / (identifier + '-mounts.json')
    cl.generate_mounts_json([request], config)
    return cl.start_mounts(config, {identifier: spec}, out, register_collector=False)


def corpus(files):
    root = cl.INPUT_DIR / 'smallfiles-v2' / str(files)
    marker = root / 'ready.json'
    # Keep the source marker outside the directory that is copied or packed.
    data = root / 'data'
    if marker.exists():
        return data
    for index in range((files + 999) // 1000):
        (data / 'small' / f'd{index:04d}').mkdir(parents=True, exist_ok=True)
    def write(index):
        state = index
        buf = bytearray(4096)
        mask = (1 << 64) - 1
        for offset in range(0, 4096, 8):
            state = (state + 0x9E3779B97F4A7C15) & mask
            value = ((state ^ (state >> 30)) * 0xBF58476D1CE4E5B9) & mask
            value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
            struct.pack_into('<Q', buf, offset, value ^ (value >> 31))
        (data / 'small' / f'd{index//1000:04d}' / f'f{index:07d}').write_bytes(buf)
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(write, range(files)))
    save(marker, {'files': files, 'file_bytes': 4096, 'files_per_directory': 1000})
    return data


def init_packed_reader(volume, namespace, out):
    clone = dict(volume, namespace=namespace)
    result = support.command([str(brewfs_binary(clone)), 'workspace', *metadata_arguments(clone),
        '--workspace-namespace', namespace, 'init-packed-volume', '--manifest-key', volume['manifest_key'],
        '--data-config', volume['data_config']], out, secrets=True)
    clone['base'] = cl.extract_json_from_cli(result.stdout)['workspace_id']
    return clone


def prepare_dataset(system, dynamic, files, out):
    tag = f'{system}-{dynamic}-{files}'
    volume = {'system': system, 'engine': 'brewfs-packed' if system == 'brewfs-packed' else
        ('brewfs-native-overlay' if system.startswith('brewfs') else 'juicefs'),
        'bucket': (f'{cl.RUN_PREFIX}-p-{dynamic}-{files}' if system == 'brewfs-packed'
                   else f'{cl.RUN_PREFIX}-packed-{tag}'),
        'namespace': f'{cl.RUN_NAMESPACE}_packed_' + out.relative_to(cl.RUN_ROOT).as_posix().replace('/', '_').replace('-', '_'),
        'meta_backend': 'tikv' if system in ('brewfs-native-tikv', 'brewfs-packed', 'juicefs-tikv') else 'redis',
        'files': files, 'dynamic': dynamic}
    source = corpus(files)
    if system == 'brewfs-native-tikv':
        volume['bucket'] = f'{cl.RUN_PREFIX}-native-upload-{uuid4().hex[:16]}'
    else:
        cl.delete_bucket_and_objects(volume['bucket'])
    cl.ensure_bucket(volume['bucket'])
    data = {'data': {'backend': 's3', 's3': {'bucket': volume['bucket'],
        'endpoint': cl.RUSTFS_ENDPOINTS[0], 'region': 'us-east-1',
        'force_path_style': True, 'disable_payload_checksum': True}}}
    config = out / 'data.json'
    save(config, data)
    volume['data_config'] = str(config)
    before = cl.collector_mark_all(tag, 'prepare-start')
    begin = time.monotonic()
    if system == 'brewfs-packed':
        manifest = out / 'manifest-key.txt'
        support.command(['env', 'TOKIO_WORKER_THREADS=8', 'RAYON_NUM_THREADS=8',
            str(cl.BIN_DIR / 'packed_snapshot_fixture'), '--output-dir', str(out / 'packed-build'),
            '--source-directory', str(source), '--source-consistency', 'best-effort-detected',
            '--source-hardlink-policy', 'reject-external', '--bucket', volume['bucket'],
            '--endpoint', cl.RUSTFS_ENDPOINTS[0], '--region', 'us-east-1', '--force-path-style', 'true',
            '--s3-rustfs-ec-block-size-hint', 'off' if dynamic == 'off' else 'auto', '--manifest-output', str(manifest)],
            out / 'publish', secrets=True)
        volume['manifest_key'] = manifest.read_text().strip()
        volume = init_packed_reader(volume, volume['namespace'], out / 'register')
    else:
        if system.startswith('brewfs'):
            volume['base'] = admin(volume, ['init-shared-volume'], out / 'init')['workspace_id']
        else:
            volume['redis_db'] = 5
            if system == 'juicefs-tikv':
                volume['meta_url'] = f'tikv://175.168.3.55:2379/{volume["namespace"]}'
            else:
                cl.redis_flush_db(5)
                volume['meta_url'] = f'redis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/5'
            support.command([str(cl.BIN_DIR / 'juicefs'), '--no-agent', 'format', '--storage=s3',
                '--bucket=' + cl.RUSTFS_ENDPOINTS[0] + '/' + volume['bucket'], '--block-size=4096',
                '--compress=none', '--trash-days=0', volume['meta_url'], volume['bucket']], out / 'format', secrets=True)
            volume['base'] = '/'
        mounted = mount(volume, volume['base'], tag + '-prepare', out)
        cl.collector_set_mounts({'A366': mounted})
        try:
            target = Path(mounted[0]['mount_directory'])
            for directory in (source / 'small').iterdir():
                (target / 'small' / directory.name).mkdir(parents=True, exist_ok=True)
            def copy(directory):
                for path in directory.iterdir():
                    shutil.copyfile(path, target / 'small' / directory.name / path.name)
            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(copy, (source / 'small').iterdir()))
            volume['upload'] = cl.wait_uploads(mounted)
        finally:
            cl.stop_mounts(mounted, out, clear_collector=True, remove_cache=True)
    volume['prepare_seconds'] = time.monotonic() - begin
    after = cl.collector_mark_all(tag, 'prepare-end')
    save(out / 'prepare-start.json', before)
    save(out / 'prepare-end.json', after)
    volume['backend_delta'] = cl.summarize_phase_delta(before, after)
    save(out / 'volume.json', volume)
    return volume
