#!/usr/bin/env python3
"""Pack the exact prepared Linux assets once for private-node distribution.

The explicit code allowlist excludes provider configuration, HOME, SSH keys,
trace payloads and local service data. Every archived member is hashed and
independently checked by the same archive verifier used for experiment results.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.archive_check import pack


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked(path, expected):
    if path.is_symlink() or not path.is_file() or digest(path) != expected:
        raise ValueError('Prepared asset differs: ' + str(path))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--image-receipt', type=Path, required=True)
    parser.add_argument('--entity-reader-result', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = args.build_root.resolve(strict=True)
    build_path = root / 'receipts/build-manifest.json'
    build = json.loads(build_path.read_text())
    image = json.loads(args.image_receipt.read_text())
    dependencies_path = root / 'dependencies/receipt.json'
    dependencies = json.loads(dependencies_path.read_text())
    runtime_path = root / 'dependencies/opencode-runtime/receipt.json'
    runtime = json.loads(runtime_path.read_text())
    if image['source_commit'] != next(s['source_commit'] for s in build['sources'] if s['component'] == 'rustfs'):
        raise ValueError('Image and build source differ')
    for proof in build['build_attestations']:
        checked(Path(proof['path']), proof['sha256'])
    for source in build['sources']:
        tip = subprocess.check_output(['git', 'ls-remote', source['repository'],
            'refs/heads/' + source['branch']], text=True).split()[0]
        if tip != source['source_commit']:
            raise ValueError(source['component'] + ' advanced; rebuild before packaging')
    args.out.mkdir(parents=True, exist_ok=False)
    stage = args.out / 'payload'
    stage.mkdir()
    files = []

    def put(source, relative, expected=None):
        if expected:
            checked(source, expected)
        elif source.is_symlink() or not source.is_file():
            raise ValueError('Regular package input required: ' + str(source))
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Copies keep the bundle independent of subsequent source-file edits.
        shutil.copy2(source, target)
        files.append(relative)

    for entry in build['files']:
        put(root / 'bin' / entry['file'], 'bin/' + entry['file'], entry['sha256'])
    for name in ('juicefs', 'opencode'):
        put(root / 'bin' / name, 'bin/' + name,
            dependencies['juicefs_binary_sha256' if name == 'juicefs' else 'opencode_sha256'])
    proof_path = args.entity_reader_result
    proof = json.loads(proof_path.read_text())
    if proof['exit_code'] != 0 or '--test' in proof['argv']:
        raise ValueError('A successful deployable reader build is required')
    brewfs_proof = next(p for p in build['build_attestations'] if p['component'] == 'brewfs')
    if proof['build_attestation_sha256'] != brewfs_proof['sha256']:
        raise ValueError('Reader must link this batch\'s exact completed BrewFS library')
    reader = Path(proof['argv'][proof['argv'].index('-o') + 1])
    if reader.name != 'verify_entity_remote':
        raise ValueError('Expected the original-namespace reader')
    put(reader, 'bin/verify_entity_remote', proof['binary_sha256'])
    put(proof_path, 'provenance/verify_entity_remote.json')
    reader_source = Path(proof['argv'][proof['argv'].index('--crate-name') + 2])
    put(reader_source, 'provenance/reader/' + reader_source.name, proof['source_sha256'])
    for module in proof['local_modules']:
        put(reader_source.parent / module['file'], 'provenance/reader/' + module['file'], module['sha256'])
    image_dir = args.image_receipt.parent
    for name in ('registry-image.tar', 'registry-data.tar'):
        put(image_dir / name, 'images/' + name, image['archives'][name]['sha256'])
    put(args.image_receipt, 'provenance/rustfs-image.json')
    put(build_path, 'provenance/build-manifest.json')
    put(dependencies_path, 'provenance/dependencies.json')
    for proof in build['build_attestations']:
        put(Path(proof['path']), 'provenance/' + proof['component'] + '-build.json', proof['sha256'])
    runtime_archive = runtime_path.parent / runtime['archive']['path']
    put(runtime_archive, 'runtime/opencode-config-dependencies.tar', runtime['archive']['sha256'])
    put(runtime_path, 'provenance/opencode-runtime.json')
    collector_path = root / 'dependencies/otel-collector/receipt.json'
    collector = json.loads(collector_path.read_text())
    put(Path(collector['archive']['path']), 'images/otel-collector-image.tar', collector['archive']['sha256'])
    put(collector_path, 'provenance/otel-collector.json')
    for directory in ('bench', 'infra/rustfs',
            'experiments/unified-suite/pilot-aliyun/scripts'):
        for path in sorted((REPO / directory).rglob('*.py')):
            if '__pycache__' not in path.parts:
                put(path, 'repo/' + path.relative_to(REPO).as_posix())
    for path in [REPO / 'scenarios/k8s-containers/archives.json',
            REPO / 'scripts/full_trace_common.py',
            REPO / 'scenarios/k8s-containers/scripts/download_archives.py',
            REPO / 'scenarios/k8s-containers/scripts/check_archives.py',
            REPO / 'scenarios/k8s-containers/scripts/restore_parts.py',
            REPO / 'experiments/retest-2026-10-29/scripts/inspect-layout.py',
            REPO / 'experiments/unified-suite/pilot-aliyun/scope.json']:
        put(path, 'repo/' + path.relative_to(REPO).as_posix())
    for path in sorted((REPO / 'experiments/unified-suite/pilot-aliyun/profiles').glob('*.json')):
        put(path, 'repo/' + path.relative_to(REPO).as_posix())
    # Preserve executable bits and exact file hashes in the internal manifest.
    allowlist = args.out / 'allowlist.json'
    allowlist.write_text(json.dumps(files, indent=2) + '\n')
    archive = args.out.resolve() / 'deployment.tar.gz'
    result = pack(stage, files, archive, 'pilot-deployment', 'prepared-assets')
    result.update(created_at=datetime.now(timezone.utc).isoformat(),
        repository_head=subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
        source_note='Per-member bytes include local changes; git HEAD alone is not this code bundle identity.',
        cloud_instances_started=False)
    (args.out / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
