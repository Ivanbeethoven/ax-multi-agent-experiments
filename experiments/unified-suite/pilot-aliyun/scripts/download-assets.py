#!/usr/bin/env python3
"""Copy one role's verified assets from the sole coordinator over private HTTP."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
from urllib.request import build_opener, ProxyHandler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role', choices=['client', 'target', 'metadata', 'objects'], required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.destination.exists() or not a.destination.is_absolute() or a.out.exists():
        raise ValueError('New destination and result path required')
    base = 'http://10.20.0.10:18080/'
    opener = build_opener(ProxyHandler({}))
    with opener.open(base + '.suite-manifest.json', timeout=30) as response:
        raw = response.read(16 * 1024**2 + 1)
    if len(raw) > 16 * 1024**2 or hashlib.sha256(raw).hexdigest() != a.manifest_sha256:
        raise ValueError('Private asset manifest SHA256 differs')
    manifest = json.loads(raw)
    if manifest['kind'] != 'prepared-assets' or manifest['deferred']:
        raise ValueError('Complete prepared-assets manifest required')
    selected = {}
    for name, ref in manifest['files'].items():
        path = PurePosixPath(name)
        if name.startswith('/') or '..' in path.parts or str(path) != name or '\\' in name or ':' in name:
            raise ValueError('Unsafe asset member name')
        if name.startswith(('repo/', 'provenance/')) or \
                a.role in ('client', 'target') and name.startswith(('bin/', 'runtime/')) or \
                a.role == 'objects' and name == 'images/otel-collector-image.tar':
            if a.role in ('client', 'target') and name == 'bin/rustfs':
                continue
            selected[name] = ref
    if not selected:
        raise ValueError('Empty role assets')
    a.destination.mkdir(parents=True, exist_ok=False)
    for name, ref in selected.items():
        target = a.destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.part')
        count, checksum = 0, hashlib.sha256()
        with opener.open(base + name, timeout=120) as response, temporary.open('xb') as stream:
            while chunk := response.read(1024**2):
                count += len(chunk)
                if count > ref['bytes']:
                    raise ValueError('Asset body exceeds frozen size')
                checksum.update(chunk); stream.write(chunk)
        if count != ref['bytes'] or checksum.hexdigest() != ref['sha256']:
            raise ValueError('Private asset content differs: ' + name)
        temporary.rename(target)
        target.chmod(0o755 if name.startswith('bin/') else 0o644)
    (a.destination / '.role-manifest.json').write_text(json.dumps(selected, indent=2) + '\n')
    result = dict(at=datetime.now(timezone.utc).isoformat(), passed=True, role=a.role,
        manifest_sha256=a.manifest_sha256, members=len(selected), bytes=sum(x['bytes'] for x in selected.values()),
        destination=str(a.destination), source=base, automatic_retry=False)
    with a.out.open('x') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
