#!/usr/bin/env python3
"""Check anonymous range access to every object in an OSS restoration receipt."""
import argparse
import datetime
import json
from pathlib import Path
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    restored = json.loads(args.receipt.read_text(encoding='utf-8-sig'))
    checks = []
    for obj in restored['objects']:
        url = ('https://brewfs-agent-traces-20261023-4ccdae73.'
               'oss-cn-hangzhou.aliyuncs.com/' + obj['oss_key'])
        request = urllib.request.Request(url, headers={'Range': 'bytes=0-0', 'Accept-Encoding': 'identity'})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 206:
                raise ValueError(f'Expected range response: {url}')
            size = int(response.headers['Content-Range'].rsplit('/', 1)[1])
            crc64 = response.headers['x-oss-hash-crc64ecma']
            if size != obj['bytes'] or crc64 != obj['remote_crc64'] or len(response.read(2)) != 1:
                raise ValueError(f'Object differs from verified restoration: {url}')
        checks.append(dict(url=url, status=206, bytes=size, crc64=crc64))
        print(f'{len(checks)}/{len(restored["objects"])} {obj["oss_key"]}', flush=True)
    receipt = dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   method='anonymous HTTPS Range bytes=0-0; total size and server CRC64 matched to restoration',
                   objects=checks)
    args.output.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
