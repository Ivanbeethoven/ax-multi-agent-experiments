#!/usr/bin/env python3
"""Take one raw native observer sample before a new Packed producer."""
import argparse
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime import observe
from bench.runtime.common import digest, identity, load, pinned

PATHS = {
    'client': ['/srv/ax-client'],
    'metadata': ['/srv/ax-metadata'],
    'objects': [f'/srv/ax-objects-{i}' for i in range(4)],
}
ENDPOINTS = {
    'filesystem': 'http://127.0.0.1:8910/metrics',
    'rustfs': 'http://127.0.0.1:18889/metrics',
    'collector': 'http://127.0.0.1:8888/metrics',
}
TARGETS = {'client': {'filesystem'}, 'metadata': {'redis'}, 'objects': {'rustfs', 'collector'}}
REDIS_CLI = Path('/usr/bin/redis-cli')
SLOT_PROCS = Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs')


def check_spec(spec, role):
    if (spec.get('schema') != 'experiment-observer-v1' or spec.get('role') != role
            or spec.get('pool') != 'pool-a' or spec.get('observer_id') != 'pool-a-' + role
            or spec.get('data_paths') != PATHS[role]):
        raise ValueError('Expected Pool A observer spec with exactly the original role disk paths')
    observe.validate(spec)
    processes = spec['processes']
    boot = identity(1)['boot_id']
    if any(p['boot_id'] != boot for p in processes):
        raise ValueError('Observer spec is from a different boot')
    if len(processes) != (2 if role == 'objects' else 1):
        raise ValueError('Expected process identities from make-observer.py')
    if role == 'client' and (processes[0]['pid'] != 1 or SLOT_PROCS.read_text().strip()):
        raise ValueError('Client must have a current PID 1 binding and an empty agent slot')
    if {t['id'] for t in spec['targets']} != TARGETS[role]:
        raise ValueError('Expected exactly the original local telemetry targets')
    for target in spec['targets']:
        name = target['id']
        if name == 'redis':
            step = load(pinned(target['step']))
            if (target['kind'] != 'command' or target['format'] != 'redis-info'
                    or step['argv'] != [str(REDIS_CLI.resolve()), 'INFO', 'ALL']
                    or step.get('inherit_environment') != []):
                raise ValueError('Only native local redis-cli INFO ALL is allowed')
        elif (target['kind'] != 'http' or target['format'] != 'prometheus'
              or target['url'] != ENDPOINTS[name] or target.get('bearer_file') or target.get('ca_file')):
            raise ValueError('Only the known local unauthenticated metrics endpoints are allowed')
        if name == 'filesystem':
            mount = Path(target['mount_directory'])
            if not mount.is_absolute() or mount.exists() or mount.is_symlink():
                raise ValueError('Bind the as-yet-unused producer supervisor directory')


def capture(role, spec_pin, out):
    spec = load(pinned(spec_pin))
    check_spec(spec, role)
    if out.exists() or out.is_symlink() or not out.is_absolute():
        raise ValueError('New absolute output directory required')
    sample = observe.snapshot(spec, out, 0)
    path = out / 'sample-000000' / 'sample.json'
    if not sample['passed']:
        raise ValueError('Native sample incomplete; retained original at ' + str(path))
    if role == 'metadata':
        raw = path.with_name('redis.txt').read_text()
        fields = dict(line.split(':', 1) for line in raw.splitlines() if ':' in line and not line.startswith('#'))
        if int(fields['process_id']) != sample['processes'][0]['pid']:
            raise ValueError('Redis INFO PID differs from the observed service process')
    return dict(sample=dict(path=str(path), sha256=digest(path)), observer_spec=spec_pin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=PATHS, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture(args.role, dict(path=str(args.spec), sha256=args.sha256), args.out)))


if __name__ == '__main__':
    main()
