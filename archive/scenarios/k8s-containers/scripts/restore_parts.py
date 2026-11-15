#!/usr/bin/env python3
"""Restore a losslessly split archive and verify every part and the whole file."""
import hashlib
import json
from pathlib import Path


def restore_parts(part_dir, target, entry):
    part_dir = Path(part_dir)
    target = Path(target)
    manifest_path = part_dir / 'PARTS.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FileNotFoundError(f'Missing regular parts manifest: {manifest_path}')
    parts_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if parts_manifest.get('original_bytes') != entry['bytes']:
        raise ValueError('Split manifest has the wrong original size')
    if parts_manifest.get('original_sha256') != entry['sha256']:
        raise ValueError('Split manifest has the wrong original SHA256')
    parts = parts_manifest.get('parts')
    if not isinstance(parts, list) or not parts:
        raise ValueError('Split manifest has no parts')
    names = [part.get('name') for part in parts]
    if any(not name or Path(name).name != name for name in names) or len(set(names)) != len(names):
        raise ValueError('Part names must be unique basenames')
    if target.exists() or target.is_symlink():
        raise FileExistsError(f'Refusing to replace existing archive: {target}')
    partial = target.with_name(target.name + '.partial')
    if partial.exists() or partial.is_symlink():
        raise FileExistsError(f'Inspect unfinished restore before retrying: {partial}')

    whole = hashlib.sha256()
    total = 0
    try:
        with partial.open('xb') as output:
            for part in parts:
                path = part_dir / part['name']
                if path.is_symlink() or not path.is_file():
                    raise FileNotFoundError(f'Missing regular archive part: {path}')
                if path.stat().st_size != part['bytes']:
                    raise ValueError(f'{path.name}: incorrect part size')
                digest = hashlib.sha256()
                size = 0
                with path.open('rb') as source:
                    for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
                        output.write(block)
                        digest.update(block)
                        whole.update(block)
                        size += len(block)
                        total += len(block)
                if digest.hexdigest() != part['sha256']:
                    raise ValueError(f'{path.name}: incorrect part SHA256')
                if size != part['bytes']:
                    raise ValueError(f'{path.name}: part changed while reading')
            output.flush()
    except Exception:
        # Keep the partial file for diagnosis; never replace it on a retry.
        raise

    if total != entry['bytes'] or whole.hexdigest() != entry['sha256']:
        raise ValueError('Restored archive size or SHA256 does not match the catalog')
    partial.rename(target)
    return {'case': entry['case'], 'bytes': total, 'sha256': whole.hexdigest(), 'verified': True}
