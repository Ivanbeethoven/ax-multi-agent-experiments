#!/usr/bin/env python3
"""Restore only verified runtime assets; never reuse the old BrewFS binary."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def main():
    p = argparse.ArgumentParser()
    p.add_argument('archive', type=Path); p.add_argument('manifest', type=Path)
    p.add_argument('destination', type=Path)
    a = p.parse_args()
    meta = json.loads(a.manifest.read_text())
    assert digest(a.archive) == meta['bundle']['sha256'], 'bundle SHA mismatch'
    a.destination.mkdir(parents=True, exist_ok=False)
    wanted = {v['path']: v['sha256'] for k, v in meta['binaries'].items()
              if k in ('juicefs', 'full-fuse-replay', 'ax-fs-probe')}
    wanted.update({v['path']: v['digest'].split(':')[1] for v in meta['registry_image']['blobs']})
    wanted[meta['registry_image']['manifest_path']] = meta['image_digest'].split(':')[1]
    with tarfile.open(a.archive, 'r:gz') as tar:
        for member in tar:
            if member.name not in wanted:
                continue
            if not member.isfile() or Path(member.name).is_absolute() or '..' in Path(member.name).parts:
                raise ValueError('invalid asset member')
            out = a.destination / member.name
            out.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, out.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            assert digest(out) == wanted[member.name], 'asset SHA mismatch'
            if member.name.startswith('bin/'):
                out.chmod(0o755)
    assert all((a.destination / path).is_file() for path in wanted), 'missing asset'
    raw = json.loads((a.destination / meta['registry_image']['manifest_path']).read_text())
    config = raw['config']['digest'].split(':')[1]
    blob = a.destination / 'registry-image/blobs/sha256'
    image = json.loads((blob / config).read_text())
    assert image['config']['Labels']['org.opencontainers.image.revision'] == meta['rustfs_source']
    # docker load uses the same authenticated config and layer blobs. Record the
    # original registry manifest digest separately; a local tag is not a pin.
    load_manifest = [{'Config': config, 'RepoTags': ['ax-retest/rustfs:a87e8f94'],
                      'Layers': [v['digest'].split(':')[1] for v in raw['layers']]}]
    (a.destination / 'load-manifest.json').write_text(json.dumps(load_manifest))
    with tarfile.open(a.destination / 'docker-load.tar', 'w') as tar:
        tar.add(a.destination / 'load-manifest.json', arcname='manifest.json')
        for name in [config, *load_manifest[0]['Layers']]:
            tar.add(blob / name, arcname=name)
    receipt = {'rustfs_source': meta['rustfs_source'], 'original_registry_digest': meta['image_digest'],
               'expected_config_id': 'sha256:' + config, 'verified_files': wanted,
               'scope': 'restored exact old runtime; new BrewFS built separately'}
    (a.destination / 'restore-receipt.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps({'restored_files': len(wanted), 'config_id': 'sha256:' + config}))

if __name__ == '__main__':
    main()
