#!/usr/bin/env python3
import json, pathlib, subprocess, urllib.request, time
BASE = pathlib.Path('/data/amp')
BASE.mkdir(exist_ok=True)
OUT = BASE / 'artifacts'
OUT.mkdir(exist_ok=True)
def cmd(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return {'argv': argv, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
result = {'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
for name, argv in {
    'version': ['juicefs', 'version'],
    'format_help': ['juicefs', 'format', '--help'],
    'mount_help': ['juicefs', 'mount', '--help'],
    'info_help': ['juicefs', 'info', '--help'],
    'mounts': ['findmnt', '--json'],
    'space': ['df', '-h', '/data'],
    'processes': ['pgrep', '-af', 'juicefs'],
    'redis_db2_size': ['redis-cli', '-p', '6380', '-n', '2', 'dbsize'],
}.items():
    result[name] = cmd(argv)
setting = json.loads(cmd(['redis-cli', '-p', '6380', '-n', '1', 'get', 'setting'])['stdout'])
result['format'] = {k: ('<redacted>' if any(s in k.lower() for s in ('secret', 'accesskey', 'token')) else v) for k, v in setting.items()}
raw = urllib.request.urlopen('http://127.0.0.1:9567/metrics', timeout=5).read().decode()
result['original_metrics'] = raw
(OUT / 'preflight.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: v for k,v in result.items() if k not in ('format_help','mount_help','info_help','mounts','original_metrics')}, indent=2))
for name in ('format_help','mount_help','info_help'):
    print(name)
    for line in result[name]['stdout'].splitlines():
        if any(s in line.lower() for s in ('block-size','writeback','flush','cache','metrics','prefetch','debug','compact','usage:','subdir')):
            print(line)
print('\n'.join(l for l in raw.splitlines() if 'object_request_data_bytes{' in l or ('object_request_durations_histogram_seconds_count{' in l) or ('compact' in l and not l.startswith('#'))))
