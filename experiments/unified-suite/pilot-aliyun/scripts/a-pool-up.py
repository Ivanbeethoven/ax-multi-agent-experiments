#!/usr/bin/env python3
"""Bring one stopped A-pool host back to its original mounted state.

Only the original data disks are mounted, by serial and filesystem UUID, and
that disk is never formatted. A stopped-then-started node loses its mounts, so
this script re-establishes them from the pinned table and records every step.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

OBJECTS = {
    'instance': 'i-bp1h9kaoya0predubdkn',
    'hostname': 'ax-pilot-20261111-objects',
    'disks': [
        ('bp1h9kaoya0predx6kvd', '/srv/ax-objects-0'),
        ('bp1h9kaoya0predx6kve', '/srv/ax-objects-1'),
        ('bp1h9kaoya0predx6kvf', '/srv/ax-objects-2'),
        ('bp1h9kaoya0predx6kvg', '/srv/ax-objects-3'),
    ],
    'scratch': ('bp16jaiu2fcmbij8mg7i', '/srv/ax-c4-observer-scratch'),
    'container': 'ax-pilot-20261111-objects-01-rustfs-1',
}
CLIENT = {
    'instance': 'i-bp1809mw7rlujtj9f705',
    'hostname': 'ax-pilot-20261111-client',
    'disks': [('bp1809mw7rlujtjc8epi', '/srv/ax-client')],
}

METADATA = {
    'instance': 'i-bp1gy3cfuj9jik0y4oc3',
    'hostname': 'ax-pilot-20261111-metadata',
    'disks': [('bp1gy3cfuj9jik0upt3k', '/srv/ax-metadata')],
    'redis_unit': 'ax-pilot-redis.service',
    'redis_conf': '/etc/redis/ax-pilot-20261111.conf',
    'redis_ip': '10.20.2.162',
}

# Injected as a module constant only so offline tests can point at a fake /proc.
PROC = Path('/proc')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def run(argv, codes=(0,)):
    result = subprocess.run(argv, capture_output=True, text=True)
    require(result.returncode in codes, 'failed: %s (%d): %s' % (argv, result.returncode, result.stderr[-400:]))
    return result.stdout


def blocks():
    return json.loads(run(['lsblk', '--json', '--bytes', '--paths', '--output',
                           'NAME,TYPE,SERIAL,SIZE,UUID,FSTYPE,MOUNTPOINTS']))['blockdevices']


def whole(serial):
    rows = [r for r in blocks() if (r.get('serial') or '').strip() == serial]
    require(len(rows) == 1, 'original whole disk not unique: '+serial)
    row = rows[0]
    require(row['type'] == 'disk' and not row.get('children'), 'wrong topology: '+serial)
    return row


def mount_disk(serial, target, actions):
    row = whole(serial)
    require(row.get('fstype') == 'ext4' and row.get('uuid'), 'original ext4 identity required: '+serial)
    target = Path(target)
    if target.is_mount():
        actions.append(dict(serial=serial, target=str(target), already_mounted=True))
        return row
    target.mkdir(exist_ok=True)
    run(['mount', '-t', 'ext4', '-o', 'rw,noatime', row['name'], str(target)])
    require(target.is_mount(), 'mount did not take: '+str(target))
    actions.append(dict(serial=serial, target=str(target), device=row['name'], uuid=row['uuid']))
    return row


def bring_objects(actions):
    for serial, target in OBJECTS['disks']:
        mount_disk(serial, target, actions)
    for index, (_serial, target) in enumerate(OBJECTS['disks']):
        data = Path(target)/'rustfs-data'
        bind = Path('/srv/ax-rustfs-data-'+str(index))
        require(data.is_dir(), 'original RustFS data dir missing: '+str(data))
        if not bind.is_mount():
            bind.mkdir(exist_ok=True)
            run(['mount', '--bind', str(data), str(bind)])
            actions.append(dict(bind=str(bind), source=str(data)))
    serial, target = OBJECTS['scratch']
    mount_disk(serial, target, actions)
    run(['docker', 'start', OBJECTS['container']])
    for _ in range(60):
        if run(['docker', 'inspect', '--format', '{{.State.Running}}', OBJECTS['container']]).strip() == 'true':
            actions.append(dict(container=OBJECTS['container'], started=True))
            return
        import time
        time.sleep(1)
    raise ValueError('rustfs container did not start')


def foreign_redis_pids(conf):
    found = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [p.decode() for p in (entry/'cmdline').read_bytes().split(b'\0') if p]
        except OSError:
            continue
        if argv and Path(argv[0]).name == 'redis-server' and argv[1:] != [conf]:
            found.append(dict(pid=int(entry.name), argv=argv))
    return found


def listening():
    return any(':6379' in line for line in run(['ss', '-H', '-ltn']).splitlines())


def effective(key):
    lines = run(['redis-cli', '-h', METADATA['redis_ip'], 'CONFIG', 'GET', key]).splitlines()
    require(len(lines) == 2 and lines[0] == key, 'unexpected CONFIG GET answer: '+key)
    return lines[1]


def bring_metadata(actions):
    mount_disk(METADATA['disks'][0][0], METADATA['disks'][0][1], actions)
    require(Path('/srv/ax-metadata/redis').is_dir(), 'original Redis data dir missing after mount')
    if listening() and run(['systemctl', 'is-active', METADATA['redis_unit']], codes=(0, 3)).strip() != 'active':
        # The image unit is enabled and would respawn a killed process; stop it
        # first, then any process that is not the original configuration.
        for name in ('redis-server.service', 'redis.service'):
            if run(['systemctl', 'is-active', name], codes=(0, 3)).strip() == 'active':
                run(['systemctl', 'stop', name])
                actions.append(dict(stopped_unit=name))
        for row in foreign_redis_pids(METADATA['redis_conf']):
            os.kill(row['pid'], 15)
            actions.append(dict(terminated=row))
        import time
        for _ in range(30):
            if not listening():
                break
            time.sleep(1)
        require(not listening(), 'port 6379 still held')
    if run(['systemctl', 'is-active', METADATA['redis_unit']], codes=(0, 3)).strip() != 'active':
        run(['systemctl', 'start', METADATA['redis_unit']])
        actions.append(dict(started_unit=METADATA['redis_unit']))
    for _ in range(60):
        try:
            values = {k: effective(k) for k in ('appendonly', 'databases', 'dir', 'bind')}
            require(values['appendonly'] == 'yes' and values['databases'] == '256'
                    and values['dir'] == '/srv/ax-metadata/redis'
                    and METADATA['redis_ip'] in values['bind'].split(),
                    'effective Redis configuration differs: '+str(values))
            actions.append(dict(effective=values))
            return
        except Exception as error:
            last = repr(error)
            import time
            time.sleep(1)
    raise ValueError('original Redis did not serve: '+last)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('objects', 'metadata', 'client'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    expected = OBJECTS if args.role == 'objects' else (METADATA if args.role == 'metadata' else CLIENT)
    require(os.geteuid() == 0 and os.uname().nodename == expected['hostname'], 'wrong A host')
    require(not args.out.exists(), 'fresh receipt directory required')
    args.out.mkdir(parents=True)
    actions = []
    if args.role == 'objects':
        bring_objects(actions)
    elif args.role == 'metadata':
        bring_metadata(actions)
    else:
        for serial, target in CLIENT['disks']:
            mount_disk(serial, target, actions)
    result = dict(schema='a-pool-bringup-v1', passed=True, role=args.role,
                  at=datetime.now(timezone.utc).isoformat(), instance=expected['instance'],
                  actions=actions, automatic_retry=False)
    with (args.out/'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result, indent=2)[:2000])


if __name__ == '__main__':
    main()
