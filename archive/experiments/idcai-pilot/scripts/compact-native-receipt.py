"""Keep native C4 checks, replace large file-hash maps, and bind the untouched raw receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def compact(value, path='$', trees=None):
    trees = [] if trees is None else trees
    if isinstance(value, dict):
        if len(value) >= 1000 and all(isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v)
                                      for v in value.values()):
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
            record = {'kind': 'file-sha256-map', 'entries': len(value),
                      'canonical_json_sha256': hashlib.sha256(encoded).hexdigest()}
            trees.append({'field': path, **record})
            return {'omitted_tree': record}
        return {key: compact(child, path+'.'+key, trees) for key, child in value.items()}
    if isinstance(value, list):
        return [compact(child, f'{path}[{i}]', trees) for i, child in enumerate(value)]
    return value


def summarize(source):
    raw = json.loads(source.read_text())
    trees = []
    small = compact(raw, trees=trees)
    return {'schema': 'ax-native-receipt-summary-v1',
            'source': {'path': str(source), 'bytes': source.stat().st_size, 'sha256': file_sha(source)},
            'scope': 'Original status, timings and checks retained; large path-to-SHA256 maps replaced by count and canonical JSON SHA256. Raw receipt is required for full tree review.',
            'tree_hash_encoding': 'UTF-8 JSON; sort_keys=true; separators=comma,colon; ensure_ascii=true',
            'compactor_sha256': file_sha(Path(__file__)), 'trees': trees, 'receipt': small}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.source)
    encoded = json.dumps(result, indent=2).encode()+b'\n'
    if len(encoded) > 1024**2:
        raise ValueError('Summary still exceeds 1 MiB; inspect unmapped fields before publishing')
    with args.out.open('xb') as stream:
        stream.write(encoded)
    print(json.dumps({'path': str(args.out), 'bytes': len(encoded), 'raw_sha256': result['source']['sha256'],
                      'summarized_trees': len(result['trees'])}))


if __name__ == '__main__':
    main()
