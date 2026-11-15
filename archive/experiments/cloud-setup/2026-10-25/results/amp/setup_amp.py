#!/usr/bin/env python3
"""Create isolated mounts under /data/amp; never remount the existing /mnt/jfs."""
import hashlib, json, pathlib, subprocess, time, urllib.request
BASE = pathlib.Path('/data/amp')
OUT = BASE / 'artifacts'
OUT.mkdir(parents=True, exist_ok=True)
def save(name, x):
    (OUT / name).write_text(json.dumps(x, indent=2) + '\n')
def cmd(argv, timeout=90, redact=False):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    safe = list(argv)
    for opt in ('--access-key', '--secret-key'):
        if opt in safe:
            safe[safe.index(opt)+1] = '<redacted>'
    r = dict(argv=safe, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    with (OUT / 'setup-commands.jsonl').open('a') as f:
        f.write(json.dumps(r) + '\n')
    if p.returncode:
        raise RuntimeError(r)
    return r
def mount(name, db, port, extra=()):
    path = BASE / name
    path.mkdir(exist_ok=False)
    argv = ['juicefs', '--debug', 'mount', '-d', '--no-syslog', '--no-usage-report', '--no-bgjob',
            '--backup-meta', '0', '--cache-dir', str(BASE / ('cache-' + name)), '--cache-size', '8192',
            '--buffer-size', '2048', '--log', str(OUT / (name + '-mount.log')),
            '--metrics', '127.0.0.1:' + str(port), *extra, 'redis://127.0.0.1:6380/' + str(db), str(path)]
    result = cmd(argv)
    cmd(['mountpoint', '-q', str(path)])
    return {'mount': str(path), 'db': db, 'port': port, 'argv': argv, 'result': result}
def main():
    result = {'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'mounts': {}}
    result['mounts']['default'] = mount('default', 1, 9571)
    result['mounts']['writeback'] = mount('writeback', 1, 9572, ['--writeback'])
    size = cmd(['redis-cli', '-p', '6380', '-n', '2', 'dbsize'])['stdout'].strip()
    if size != '0':
        raise RuntimeError('db2 is not empty; will not modify it')
    cmd(['juicefs', 'format', '--storage', 's3', '--bucket', 'http://127.0.0.1:9000/jfs-amp-64k-20261025',
         '--access-key', 'rustfsadmin', '--secret-key', 'rustfsadmin', '--block-size', '64K',
         '--compress', 'none', '--trash-days', '0', 'redis://127.0.0.1:6380/2', 'jfs-amp-64k-20261025'])
    result['mounts']['small'] = mount('small', 2, 9573)
    save('setup.json', result)
    src = OUT / 'source'
    src.mkdir(exist_ok=True)
    urls = ['pkg/vfs/writer.go', 'pkg/vfs/reader.go', 'pkg/vfs/vfs.go', 'pkg/chunk/cached_store.go',
            'pkg/meta/base.go', 'pkg/meta/redis.go', 'pkg/meta/slice.go', 'pkg/vfs/helpers.go']
    sources = []
    for path in urls:
        url = 'https://raw.githubusercontent.com/juicedata/juicefs/0b90c7d/' + path
        try:
            raw = urllib.request.urlopen(url, timeout=15).read()
            (src / path.replace('/', '__')).write_bytes(raw)
            sources.append({'url': url, 'file': path.replace('/', '__'), 'sha256': hashlib.sha256(raw).hexdigest()})
        except Exception as exc:
            sources.append({'url': url, 'error': repr(exc)})
    save('source-manifest.json', sources)
    print(json.dumps(result, indent=2), flush=True)
if __name__ == '__main__':
    main()
