#!/usr/bin/env python3
"""Bind completed local assets and actual route checks before cloud startup.

This gate permits environment setup. It does not qualify cloud performance,
observation overhead, unsupported operations or every cell in the suite.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-root', type=Path, required=True)
    p.add_argument('--bundle-receipt', type=Path, required=True)
    p.add_argument('--checks-root', type=Path, help='Optional read-only public copies of the actual JSON receipts, with original relative paths')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    root = a.build_root.resolve(strict=True)
    proofs = {}

    def proof(name, relative, expected):
        path = (a.checks_root or root) / relative
        data = json.loads(path.read_text())
        for key, value in expected.items():
            if type(data.get(key)) is not type(value) or data[key] != value:
                raise ValueError(name + ': actual field differs: ' + key)
        proofs[name] = dict(path=str(path), sha256=digest(path), equals=expected)
        return data

    build_path = root / 'receipts/build-manifest.json'
    build = json.loads(build_path.read_text())
    expected_sources = {'brewfs': ('https://github.com/brewfs/brewfs.git', 'codex/packed-prefetch'),
        'rustfs': ('https://github.com/Ivanbeethoven/rustfs.git', 'feat/dynamic-block-size-rebased')}
    if {x['component'] for x in build['sources']} != set(expected_sources):
        raise ValueError('Both required components must be present')
    for source in build['sources']:
        if (source['repository'], source['branch']) != expected_sources[source['component']]:
            raise ValueError('Wrong source branch')
        latest = subprocess.check_output(['git', 'ls-remote', source['repository'],
            'refs/heads/' + source['branch']], text=True).split()[0]
        if latest != source['source_commit']:
            raise ValueError(source['component'] + ' branch advanced; rebuild and recheck')
    for item in build['files']:
        if digest(root / 'bin' / item['file']) != item['sha256']:
            raise ValueError('Prepared binary changed')
    for item in build['build_attestations']:
        path = Path(item['path'])
        if digest(path) != item['sha256'] or json.loads(path.read_text())['passed'] is not True:
            raise ValueError('Completed build attestation changed')
    image = proof('image', 'receipts/backend-version.json', {'runtime_verified': False})
    if image.get('blocked_reason') or image['source_commit'] != next(x['source_commit'] for x in build['sources'] if x['component'] == 'rustfs'):
        raise ValueError('RustFS image source rejected')
    if image['brewfs_binary_sha256'] != next(x['sha256'] for x in build['files'] if x['file'] == 'brewfs'):
        raise ValueError('Image receipt refers to another BrewFS binary')
    dependencies = json.loads((root / 'dependencies/receipt.json').read_text())
    for name, field in [('juicefs', 'juicefs_binary_sha256'), ('opencode', 'opencode_sha256')]:
        if digest(root / 'bin' / name) != dependencies[field]:
            raise ValueError('Dependency binary changed')
    for name in ('brewfs', 'packed_snapshot_fixture', 'smallfiles', 'full-fuse-replay'):
        report = (root / 'receipts' / (name + '-ldd.txt')).read_text()
        if 'not found' in report or not report.strip():
            raise ValueError('Local runtime dependency check incomplete')
    proof('rustfs_layout', 'qualification/rustfs-layout-03/result.json', {'passed': True})
    proof('collector', 'qualification/collector-04/qualification/result.json', {'passed': True, 'expected_requests': 2})
    proof('native_flat', 'qualification/native-flat-01/result.json', {'passed': True, 'original_volume_reopened': True})
    proof('native_workspace', 'qualification/native-workspace-03/result.json', {'passed': True, 'original_volume_reopened': True})
    proof('entity_native_data', 'qualification/entity-native-03/result.json', {'passed': True, 'challenge': 'data', 'readonly_acl_write_rejected': True})
    proof('entity_packed_data', 'qualification/entity-packed-01/result.json', {'passed': True, 'challenge': 'data', 'readonly_acl_write_rejected': True})
    proof('juicefs_regular', 'qualification/juicefs-03/result.json', {'passed': True, 'fixture_hardlinks': False, 'hardlink_clone_qualified': False})
    proof('complete_trace', 'receipts/trace-strict/verification.json', {'passed': True, 'strict': True, 'callbacks': 1747109})
    proof('million_files', 'receipts/c3-source-verify/result.json', {'passed': True, 'files': 1000000, 'bytes_checked': 4096000000})
    proof('opencode_resume', 'qualification/opencode-session-02/result.json', {'passed': True, 'workspace_only_state': True})
    proof('opencode_no_state', 'qualification/opencode-session-02/negative-without-state/result.json', {'passed': True, 'provider_endpoint_live': False})
    checkpoint = proof('canonical_checkpoint', 'inputs/opencode-checkpoint/checkpoint.json',
        {'source_process_exited': True, 'sqlite_quick_check': 'ok',
         'canonical_project': '/srv/ax-pilot-session/workspace/repo', 'contains_provider_master_key': False})
    if digest(root / 'inputs/opencode-checkpoint/workspace/session-export.json') != checkpoint['session_export_sha256']:
        raise ValueError('Canonical session export changed')
    rename = proof('entity_rename_failure', 'qualification/entity-native-02/result.json', {'passed': False})
    if 'Errno 38' not in rename['error'] or 'renamed.txt' not in rename['error']:
        raise ValueError('Missing actual entity rename rejection')
    proof('entity_rename_closed', 'qualification/entity-native-02/failure-closure.json', {'mount_absent': True, 'original_process_exited': True})
    proof('native_lower_quota', 'qualification/native-quota-01/result.json', {'passed': True, 'input_rows': 32769})
    proof('juicefs_hardlink_failure', 'qualification/juicefs-01/result.json', {'passed': False})
    clone = json.loads(((a.checks_root or root) / 'qualification/juicefs-01/failure-clone-inspection.json').read_text())
    if not clone['base']['tree_equal'] or not clone['child']['tree_equal'] or not clone['base']['hardlinks'] or clone['child']['hardlinks']:
        raise ValueError('Hardlink failure inspection differs')
    proof('juicefs_failure_closed', 'qualification/juicefs-01/failure-closure.json', {'mount_absent': True, 'original_process_exited': True})
    bundle = json.loads(a.bundle_receipt.read_text())
    if bundle.get('passed') is not True or digest(Path(bundle['archive'])) != bundle['sha256']:
        raise ValueError('Deployment bundle rejected')
    result = dict(at=datetime.now(timezone.utc).isoformat(), ready_for_cloud=True,
        binaries_verified=True, rustfs_image_verified=True, local_routes_checked=True, dependencies_prepared=True,
        build_manifest_path=str(build_path), build_manifest_sha256=digest(build_path),
        binaries=[dict(path=str(root / 'bin' / item['file']), sha256=item['sha256']) for item in build['files']] +
            [dict(path=str(root / 'bin' / name), sha256=dependencies[field]) for name, field in
             [('juicefs', 'juicefs_binary_sha256'), ('opencode', 'opencode_sha256')]],
        deployment_bundle=dict(receipt=str(a.bundle_receipt), receipt_sha256=digest(a.bundle_receipt),
            archive=bundle['archive'], sha256=bundle['sha256']), sources=build['sources'], proofs=proofs,
        limitations=[
            'Local RustFS uses four directories on one disk with an explicit local-only bypass; cloud requires four real disks.',
            'Entity data checks do not qualify rename/symlink/link creation; complete trace and C4 fork mutation are blocked by actual rename failure.',
            'Native lower rejects 32769 metadata rows; million-file entity-native conditions remain blocked.',
            'The native-workspace immutable-directory publication bridge is unavailable; do not replace it with another route.',
            'JuiceFS native clone does not preserve hardlink groups in this build; relevant workloads need separate correctness handling.',
            'Cloud service identity, capacity, actual metrics, zero debt, three-pair observation overhead and per-cell gates remain to be checked.'
        ], scope='Environment startup only; no cloud experiment or comparative performance accepted')
    with a.out.open('x') as f:
        json.dump(result, f, indent=2); f.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'proofs'}, indent=2))


if __name__ == '__main__':
    main()
