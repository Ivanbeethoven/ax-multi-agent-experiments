"""Upload completed archives with the existing Aliyun CLI login; verify size and CRC64."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    destination = json.loads(args.destination.read_text(encoding='utf-8-sig'))
    args.output.mkdir(parents=True, exist_ok=True)
    cli = shutil.which('aliyun') or shutil.which('aliyun.exe')
    if not cli:
        raise RuntimeError('Aliyun CLI is required')
    prefix = f"oss://{destination['bucket']}/{destination['prefix']}"
    records = []
    done = set()

    def call(arguments, label):
        result = subprocess.run([cli, 'oss', *arguments], capture_output=True, encoding='utf-8', errors='replace')
        (args.output / (label + '.log')).write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'{label} failed; inspect its local log')
        return result.stdout

    def upload(path, expected=None, key=None):
        key = key or path.name
        if key in done:
            return
        size = path.stat().st_size
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if expected and (size != expected['bytes'] or digest != expected['sha256']):
            raise RuntimeError('Local archive changed after packaging: ' + path.name)
        label = key.replace('/', '_')
        local_crc = re.search(r'CRC64-ECMA\s*:\s*(\d+)', call(['hash', str(path), '--type', 'crc64'], label + '-hash'))
        if not local_crc:
            raise RuntimeError('Missing local CRC64')
        (args.output / 'progress.json').write_text(json.dumps({'uploading': key, 'bytes': size, 'verified_objects': len(records)}))
        call(['cp', str(path), prefix + key, '--endpoint', destination['endpoint'], '--acl', 'private',
              '--meta', 'x-oss-meta-sha256:' + digest, '--parallel', '4', '--part-size', '16777216',
              '--checkpoint-dir', str(args.output / 'checkpoints'), '--cli-non-interactive', '--yes'], label + '-upload')
        remote = call(['stat', prefix + key, '--endpoint', destination['endpoint']], label + '-stat')
        headers = {line.split(':', 1)[0].strip().lower(): line.split(':', 1)[1].strip()
                   for line in remote.splitlines() if ':' in line}
        if (headers.get('content-length') != str(size)
                or headers.get('x-oss-hash-crc64ecma') != local_crc.group(1)
                or headers.get('x-oss-meta-sha256') != digest):
            raise RuntimeError('Remote integrity check failed: ' + key)
        records.append({'key': key, 'uri': prefix + key, 'bytes': size, 'sha256': digest,
                        'crc64': local_crc.group(1), 'remote_size_crc64_and_sha256_metadata_match': True})
        done.add(key)
        (args.output / 'verified-objects.json').write_text(json.dumps(records, indent=2))
        print(json.dumps({'verified': key, 'bytes': size}), flush=True)

    deadline = time.monotonic() + 7200
    while True:
        for manifest in sorted(args.source.glob('*.manifest.json')):
            data = json.loads(manifest.read_text())
            if not data['known_credential_scan_passed'] or not data['gzip_integrity_passed']:
                raise RuntimeError('Archive not ready for upload')
            for item in data['files']:
                upload(args.source / item['name'], item)
            upload(manifest)
        if (args.source / 'manifest.json').exists():
            master = json.loads((args.source / 'manifest.json').read_text())
            expected_files = {item['name'] for archive in master['archives'] for item in archive['files']}
            if expected_files <= done:
                upload(args.source / 'manifest.json')
                break
        if time.monotonic() > deadline:
            raise TimeoutError('Archives not completed within two hours')
        time.sleep(5)
    for name in ('archive_local_traces.py', 'upload_trace_archives.py'):
        upload(Path(__file__).with_name(name), key='tools/' + name)
    readme = args.output / 'README.md'
    readme.write_text('# 文件系统记录云端备份\n\n'
        f"位置：`{prefix}`\n\n杭州地域，私有权限，并已开启阻止公共访问。本地原始数据保留。\n\n"
        '四道题分别保存为 tar.gz，另有 batch-metadata.tar.gz。包含初始文件、完整回调和写入内容、最终文件、回放副本、预检和结果；未抽样、未删改。\n\n'
        'manifest.json 列出各归档大小和 SHA256；每份 files.jsonl.gz 列出文件、权限、链接目标及内容摘要。上传时核对本地与远端 CRC64、大小和 SHA256 元数据。\n\n'
        '下载后先用 SHA256 核对归档，再在 Linux 的独立空目录中用 `tar -xzf 文件.tar.gz` 解压，保留权限和链接。\n\n'
        f'示例：`aliyun oss cp {prefix}django.tar.gz ./django.tar.gz --endpoint {destination["endpoint"]}`\n\n'
        'Django 达到运行上限；这不影响文件系统回放验收。原始结果中的超时、收尾异常和恢复说明均保留。\n', encoding='utf-8')
    upload(readme)
    complete = args.output / 'UPLOAD_COMPLETE.json'
    complete.write_text(json.dumps({'destination': destination, 'objects': records.copy(),
                                    'all_objects_verified': True, 'local_data_retained': True}, indent=2))
    upload(complete)
    print(json.dumps({'complete': True, 'prefix': prefix, 'objects': len(records),
                      'bytes': sum(record['bytes'] for record in records)}), flush=True)


if __name__ == '__main__':
    main()
