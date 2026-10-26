"""Lossless local archives for OSS: preserve links/modes, scan keys, hash each file."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time


class CheckedReader:
    def __init__(self, stream, secrets):
        self.stream, self.secrets = stream, secrets
        self.tail = b''
        self.keep = max(map(len, secrets), default=1) - 1
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size=-1):
        data = self.stream.read(size)
        combined = self.tail + data
        if any(secret in combined for secret in self.secrets):
            raise RuntimeError('Known credential found; archive must not be uploaded')
        self.tail = combined[-self.keep:] if self.keep else b''
        self.digest.update(data)
        self.size += len(data)
        return data


def archive(source, output, secrets, metadata_only=False):
    name = 'batch-metadata' if metadata_only else source.name
    target = output / (name + '.tar.gz')
    if target.exists() or target.with_suffix('.json').exists():
        raise FileExistsError(target)
    partial = output / (name + '.tar.gz.partial')
    inventory = output / (name + '.files.jsonl.gz')
    started = time.monotonic()
    count = total = 0
    selections = ([p for p in sorted(source.iterdir()) if p.name not in {'django', 'sklearn', 'sympy'}]
                  if metadata_only else [source])
    paths = []
    for selected in selections:
        paths.append(selected)
        if selected.is_dir() and not selected.is_symlink():
            for current, dirs, files in os.walk(selected, followlinks=False):
                dirs.sort(); files.sort()
                paths.extend(Path(current) / n for n in dirs + files)
    with partial.open('xb') as compressed, gzip.open(inventory, 'xt', encoding='utf-8', compresslevel=1) as records:
        compressor = subprocess.Popen(['pigz', '-1', '-p', '4', '-c'], stdin=subprocess.PIPE, stdout=compressed)
        try:
            with tarfile.open(fileobj=compressor.stdin, mode='w|', format=tarfile.PAX_FORMAT) as tar:
                for path in paths:
                    if os.path.ismount(path):
                        raise RuntimeError('Refusing to archive a mounted filesystem: ' + str(path))
                    arcname = str(Path(source.name) / path.relative_to(source))
                    info = tar.gettarinfo(str(path), arcname=arcname)
                    row = {'path': arcname, 'type': info.type.decode(), 'mode': info.mode,
                           'bytes': info.size, 'linkname': info.linkname}
                    if any(secret in json.dumps(row).encode() for secret in secrets):
                        raise RuntimeError('Known credential in archive metadata')
                    if info.isfile():
                        with path.open('rb') as raw:
                            checked = CheckedReader(raw, secrets)
                            tar.addfile(info, checked)
                            if checked.size != info.size:
                                raise RuntimeError('Source changed size: ' + str(path))
                            row['sha256'] = checked.digest.hexdigest()
                            total += checked.size
                    else:
                        tar.addfile(info)
                    records.write(json.dumps(row) + '\n')
                    count += 1
                    if count % 10000 == 0:
                        (output / (name + '.progress.json')).write_text(json.dumps({'entries': count, 'source_bytes': total}))
            compressor.stdin.close()
            if compressor.wait() != 0:
                raise RuntimeError('Compression failed')
        except BaseException:
            compressor.kill(); compressor.wait()
            raise
    subprocess.run(['pigz', '-t', str(partial)], check=True)
    partial.rename(target)
    files = []
    for path in (target, inventory):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        files.append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest})
    result = {'source': str(source), 'entries': count, 'source_bytes': total, 'files': files,
              'known_credential_scan_passed': True, 'gzip_integrity_passed': True,
              'sampling': False, 'redactions': 0, 'links_and_modes_preserved': True,
              'elapsed_s': time.monotonic() - started}
    (output / (name + '.manifest.json')).write_text(json.dumps(result, indent=2))
    print(json.dumps({'archive': name, 'files': files, 'entries': count}), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--credential-config', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.credential_config.read_text(encoding='utf-8-sig'))
    secrets = [provider.get('options', {}).get('apiKey', '') for provider in config.get('provider', {}).values()]
    secrets = [value.encode() for value in secrets if isinstance(value, str) and value and not value.startswith('{')]
    batch = Path('/home/hxy/ax-full-trace/more-tasks-20261023')
    jobs = [(batch / name, False) for name in ('django', 'sklearn', 'sympy')]
    jobs += [(Path('/home/hxy/ax-full-trace/pytest-full-02'), False), (batch, True)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(archive, source, args.output, secrets, metadata) for source, metadata in jobs]
        results = [future.result() for future in futures]
    (args.output / 'manifest.json').write_text(json.dumps({'format': 'ax-oss-full-trace-archive-v1', 'archives': results}, indent=2))
