#!/usr/bin/env python3
"""Archive one stopped capture with exact bytes, links, permissions and a key scan."""
import argparse
import json
from pathlib import Path
import sys

RUNTIME = Path(__file__).resolve().parents[1] / 'assets/runtime'
sys.path.insert(0, str(RUNTIME / 'scripts'))
from archive_local_traces import archive

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--credential-config', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output.resolve()
    if output.is_relative_to(source):
        raise ValueError('Archive output must be outside captured source')
    config = json.loads(args.credential_config.read_text(encoding='utf-8-sig'))
    keys = [p.get('options', {}).get('apiKey', '') for p in config.get('provider', {}).values()]
    keys = [key.encode() for key in keys if isinstance(key, str) and key and not key.startswith('{')]
    if not keys:
        raise ValueError('No literal known keys supplied for credential scanning')
    output.mkdir(parents=True, exist_ok=False)
    result = archive(source, output, keys)
    (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
