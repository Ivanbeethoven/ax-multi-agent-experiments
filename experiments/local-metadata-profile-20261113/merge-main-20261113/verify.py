#!/usr/bin/env python3
"""Verify the merge evidence, the A355 run, and its recorded source/binary identity."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    root = Path(__file__).resolve().parent
    manifest = root / 'SHA256SUMS'
    seen = set()
    for line in manifest.read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = (root / name).resolve()
        assert path.is_relative_to(root) and path not in seen
        assert sha(path) == expected, name
        seen.add(path)
    assert seen == {p.resolve() for p in root.rglob('*') if p.is_file() and p != manifest}
    sys.dont_write_bytecode = True
    verifier = root.parent.parent / 'hot-lower-20261112/verify_results.py'
    spec = importlib.util.spec_from_file_location('verify_hot_lower', verifier)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.verify(root / 'evidence/a355/smoke-01')
    assert result['cells'] == 12 and result['files_verified'] == 134
    run = json.loads((root / 'evidence/a355/smoke-01/run.json').read_text())
    provenance = json.loads((root / 'provenance.json').read_text())
    assert run['build_profile'] == provenance['build']['profile'] == 'debug'
    assert run['binary_sha256'] == provenance['build']['binary_sha256']
    assert run['producer_sha256'] == provenance['build']['producer_sha256']
    assert run['source_commit'] == provenance['build']['base_commit']
    assert run['source_patch_sha256'] == provenance['build']['patch_sha256'] == sha(root / 'evidence/build/source.patch')
    summary = json.loads((root / 'evidence/a355/summary.json').read_text())
    assert sum(c['pread_operations'] for c in summary['cells_checked']) == 168
    assert sum(c['metadata_edits'] for c in summary['cells_checked']) == 192
    assert all(c['remote_remount_verified'] for c in summary['cells_checked'])
    assert all(not any(c[key].values()) for c in summary['cells_checked']
               for key in ('hot_rustfs_delta', 'metadata_rustfs_delta'))
    for name in ('cli-check.exit', 'ax-final-check.exit', 'redis/exit.txt', 'a355/smoke-01.exit'):
        assert (root / 'evidence' / name).read_text().strip() == '0'
    assert (root / 'evidence/failed-wsl-smoke.exit').read_text().strip() == '1'
    print(json.dumps(dict(passed=True, archive_files=len(seen), a355=result,
                          source=provenance['checked_commit'], performance_claim=False)))


if __name__ == '__main__':
    main()
