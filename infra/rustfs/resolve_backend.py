#!/usr/bin/env python3
"""Follow the specified RustFS branch and record the source actually built."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPOSITORY = 'https://github.com/Ivanbeethoven/rustfs'
BRANCH = 'feat/dynamic-block-size-rebased'


def command(args):
    return subprocess.check_output(args, text=True).strip()


def clean_commit(path):
    if command(['git', '-C', str(path), 'status', '--porcelain']):
        raise ValueError('source tree has local changes; preserve them before updating')
    sha = command(['git', '-C', str(path), 'rev-parse', 'HEAD'])
    if not re.fullmatch('[0-9a-f]{40}', sha): raise ValueError('full source revision required')
    return sha


def latest_commit():
    rows = command(['git', 'ls-remote', REPOSITORY, 'refs/heads/' + BRANCH]).splitlines()
    if len(rows) != 1: raise ValueError('specified RustFS branch unavailable')
    fields = rows[0].split()
    if len(fields) != 2 or fields[1] != 'refs/heads/' + BRANCH or not re.fullmatch('[0-9a-f]{40}', fields[0]):
        raise ValueError('invalid branch response')
    return fields[0]


def update_source(source):
    """Fast-forward a clean, dedicated checkout; preserve divergent/local work."""
    current = clean_commit(source)
    if command(['git', '-C', str(source), 'branch', '--show-current']) != BRANCH:
        raise ValueError('use a dedicated checkout of ' + BRANCH)
    command(['git', '-C', str(source), 'fetch', '--no-tags', REPOSITORY, 'refs/heads/' + BRANCH])
    latest = command(['git', '-C', str(source), 'rev-parse', 'FETCH_HEAD'])
    command(['git', '-C', str(source), 'merge-base', '--is-ancestor', current, latest])
    command(['git', '-C', str(source), 'merge', '--ff-only', 'FETCH_HEAD'])
    if clean_commit(source) != latest: raise ValueError('checkout did not reach branch tip')
    return {'source_commit': latest, 'previous_commit': current, 'updated': latest != current}


def verify_image(source, brewfs_source, brewfs_binary, image_reference):
    latest = latest_commit()
    if clean_commit(source) != latest:
        raise ValueError('RustFS branch advanced: update source and build its latest version')
    image = json.loads(command(['docker', 'image', 'inspect', image_reference]))[0]
    if image.get('Config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != latest:
        raise ValueError('image was not built from the latest specified branch')
    digests = image.get('RepoDigests', [])
    if not digests: raise ValueError('record the built image registry digest')
    if '@sha256:' in image_reference and image_reference not in digests:
        raise ValueError('requested image digest is absent')
    reference = image_reference if '@sha256:' in image_reference else digests[0]
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', reference): raise ValueError('invalid image digest')
    with Path(brewfs_binary).open('rb') as stream:
        binary_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'source_commit': latest, 'built_from_commit': latest,
            'brewfs_client_sha': clean_commit(brewfs_source), 'brewfs_binary_sha256': binary_sha,
            'image_reference': reference, 'image_digest': reference.split('@', 1)[1], 'image_id': image['Id'],
            'runtime_verified': False,
            'build_provenance': 'latest branch, clean checkout and OCI revision checked; retain build logs separately'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    source = sub.add_parser('source', help='fast-forward the dedicated branch checkout before building')
    image = sub.add_parser('image', help='verify the built artifact against the current branch')
    for p in (source, image):
        p.add_argument('--rustfs-source', required=True); p.add_argument('--output', required=True)
    for name in ('brewfs-source', 'brewfs-binary', 'image'): image.add_argument('--'+name, required=True)
    args = parser.parse_args(argv); output = Path(args.output)
    if output.exists(): raise ValueError('use a new version receipt path')
    receipt = {'repository': REPOSITORY, 'branch': BRANCH, 'revision_policy': 'branch-latest',
               'resolved_at': datetime.now(timezone.utc).isoformat(), 'resolved_by': 'resolve_backend.py',
               'action': args.action}
    try:
        receipt.update(update_source(args.rustfs_source) if args.action == 'source' else
                       verify_image(args.rustfs_source, args.brewfs_source, args.brewfs_binary, args.image))
    except Exception as error:
        receipt['blocked_reason'] = str(error)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream: json.dump(receipt, stream, indent=2)
    return 1 if receipt.get('blocked_reason') else 0


if __name__ == '__main__': raise SystemExit(main())
