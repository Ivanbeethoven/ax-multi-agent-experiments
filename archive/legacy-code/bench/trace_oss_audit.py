#!/usr/bin/env python3
"""Read-only OSS trace inventory and sidecar audit. Never download full trace archives."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'scenarios/k8s-containers/archives.json'
PREFIX = 'full-fuse/20261023/'
SMALL_LIMIT = 64 * 1024 * 1024


def fetch(url, method='GET', limit=SMALL_LIMIT):
    with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=60) as r:
        headers = dict(r.headers.items())
        body = b'' if method == 'HEAD' else r.read(limit + 1)
        if len(body) > limit:
            raise ValueError('small-object download limit exceeded')
        return {k.lower(): v for k, v in headers.items()}, body


def list_bucket(base):
    objects, token = [], None
    while True:
        query = {'list-type': '2', 'max-keys': 1000}
        if token:
            query['continuation-token'] = token
        _, body = fetch(base + '?' + urllib.parse.urlencode(query))
        doc = ET.fromstring(body)
        for node in doc.iter():
            node.tag = node.tag.split('}')[-1]
        objects.extend({'key': n.findtext('Key'), 'bytes': int(n.findtext('Size'))}
                       for n in doc.findall('Contents'))
        if doc.findtext('IsTruncated') != 'true':
            return objects
        next_token = doc.findtext('NextContinuationToken')
        if not next_token or next_token == token:
            raise ValueError('missing/nonadvancing listing continuation')
        token = next_token


def checked_download(base, key, expected, out):
    _, data = fetch(base + urllib.parse.quote(key, safe='/'))
    actual = hashlib.sha256(data).hexdigest()
    if len(data) != expected['bytes'] or actual != expected['sha256']:
        raise ValueError('download size/SHA mismatch: ' + key)
    target = out / Path(key).name
    with target.open('xb') as f:
        f.write(data)
    return data


def member_json(data, member):
    # Read named regular members in memory; never extract archive paths to disk.
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        m = archive.getmember(member)
        if not m.isfile() or m.size > SMALL_LIMIT:
            raise ValueError('unexpected metadata member')
        return json.load(archive.extractfile(m))


def inspect_sidecar(data, root):
    required = ['capture/events.jsonl', 'initial-tree.json', 'final-tree.json',
                'metrics.json', 'result.json', 'verification.json', 'replay.json',
                'selection.json', 'agent-events.jsonl']
    found, counts = {}, {'entries': 0, 'initial_files': 0, 'initial_bytes': 0,
                          'write_blobs': 0, 'blob_bytes': 0}
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
        for line in f:
            row = json.loads(line)
            path = row['path']
            if path.startswith(root + '/'):
                path = path[len(root) + 1:]
            counts['entries'] += 1
            if path in required:
                found[path] = {k: row[k] for k in ('bytes', 'sha256') if k in row}
            if str(row.get('type')) in ('0', ''):
                for prefix, count, size in [('initial/', 'initial_files', 'initial_bytes'),
                                            ('capture/blobs/', 'write_blobs', 'blob_bytes')]:
                    if path.startswith(prefix):
                        counts[count] += 1
                        counts[size] += row['bytes']
    return {**counts, 'required_members': found,
            'missing_members': [p for p in required if p not in found],
            'scope': 'archive file list only; payload files not fetched or rehashed'}


def audit(out):
    catalog_bytes = CATALOG.read_bytes()
    catalog = json.loads(catalog_bytes)
    base = catalog['access']['public_base_url']
    objects = list_bucket(base)
    inventory = {o['key']: o['bytes'] for o in objects}
    _, body = fetch(base + PREFIX + 'manifest.json')
    old_manifest = json.loads(body)
    old_meta = next(f for a in old_manifest['archives'] for f in a['files']
                    if f['name'] == 'batch-metadata.tar.gz')
    old_data = checked_download(base, PREFIX + old_meta['name'], old_meta, out)
    new_meta = catalog['metadata'][0]
    new_key = urllib.parse.urlparse(new_meta['https_url']).path.lstrip('/')
    new_data = checked_download(base, new_key, new_meta, out)
    summaries = [member_json(old_data, 'more-tasks-20261023/summary.json'),
                 member_json(new_data, 'metadata/summary.json')]
    historical = {Path(row['directory']).name: row for s in summaries for row in s['runs']}
    cases = []
    for entry in catalog['archives']:
        key = urllib.parse.urlparse(entry['https_url']).path.lstrip('/')
        headers, _ = fetch(entry['https_url'], method='HEAD')
        if entry['batch'] == '20261023':
            _, data = fetch(base + key.replace('.tar.gz', '.manifest.json'))
            manifest = json.loads(data)
        else:
            manifest = member_json(new_data, 'metadata/cases/' + entry['root_dir'] + '/archive-manifest.json')
        side = next(f for f in manifest['files'] if f['name'].endswith('.files.jsonl.gz'))
        data = checked_download(base, key.rsplit('/', 1)[0] + '/' + side['name'], side, out)
        members = inspect_sidecar(data, entry['root_dir'])
        row = historical.get(entry['root_dir'], {})
        case = {'case': entry['case'], 'key': key, 'bytes': entry['bytes'], 'sha256': entry['sha256'],
                'list_size_matches': inventory.get(key) == entry['bytes'],
                'head_size_matches': int(headers.get('content-length', -1)) == entry['bytes'],
                'head_sha_metadata_matches': headers.get('x-oss-meta-sha256') == entry['sha256'],
                'sidecar_sha256_verified': side['sha256'], 'member_inventory': members,
                'full_archive_downloaded_this_audit': False,
                'historical_metrics': row.get('metrics'), 'historical_verification': row.get('verification'),
                'capture_exit': row.get('agent'), 'model_steps': row.get('model_steps')}
        cases.append(case)
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'bucket': catalog['bucket'],
            'catalog_sha256': hashlib.sha256(catalog_bytes).hexdigest(),
            'scope': 'entire trace bucket listing, HEAD of every catalog archive, SHA-verified metadata/sidecars; no cloud mutations',
            'objects': objects, 'object_count': len(objects), 'listing_complete': True,
            'uncatalogued_trace_archives': [o['key'] for o in objects if o['key'].endswith('.tar.gz')
                and o['key'] not in {c['key'] for c in cases}
                and o['key'] not in (PREFIX + old_meta['name'], new_key)],
            'metadata_downloads': [{'key': PREFIX+old_meta['name'], 'sha256': old_meta['sha256']},
                                   {'key': new_key, 'sha256': new_meta['sha256']}],
            'excluded_incomplete': summaries[1].get('incomplete', []), 'cases': cases,
            'archive_bytes': sum(c['bytes'] for c in cases),
            'passed': all(c['list_size_matches'] and c['head_size_matches'] and c['head_sha_metadata_matches']
                          and not c['member_inventory']['missing_members']
                          and c['member_inventory']['initial_files'] > 0
                          and c['member_inventory']['write_blobs'] > 0 for c in cases)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new local receipt/download directory')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = audit(args.output)
    (args.output/'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('object_count', 'archive_bytes', 'passed', 'uncatalogued_trace_archives')}))
    raise SystemExit(0 if result['passed'] else 1)
