#!/usr/bin/env python3
"""Read and verify a fresh A-pool Packed C3 boot boundary; never admit a cell."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID

CELL = '16f4ac389d2a2da6-r1-packed'
ROLES = ('client', 'metadata', 'objects')
PILOT = Path(__file__).resolve().parent.parent
BASE = PILOT/'evidence/preparation/parallel-hardware-20261111-01/hardware-qualification-02'
BASE_PINS = {
    'client': '20b62a11d155c59d6abf3bb0b26d9f64234bd27e9c557447aa476bf695f33651',
    'metadata': '122e570ef3ea8192409d5ab879252b7c2c1a256b40266f06baac5dad72d9625d',
    'objects': '277e3ff412b8b4d22f8b3329614aa094031458ec8ae0b9bb32b9d76b90257f92',
    'result': 'fcdfe806fb7b5d77710ae89b0359590c6ec540454750cb19c1697b072f1ebbce',
}
ROOTS = {'client': ('/srv/ax-client',), 'metadata': ('/srv/ax-metadata',),
         'objects': tuple(f'/srv/ax-objects-{i}' for i in range(4))}
SLOT = {'cpu.max': '200000 100000', 'memory.max': '8589934592',
        'memory.swap.max': '0', 'pids.max': '4096', 'cgroup.procs': ''}
FLAGS = ('RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE', 'RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED',
         'RUSTFS_EC_READ_QUANTUM_ENABLE', 'RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED')
REDIS_KEYS = ('appendonly', 'appendfsync', 'maxmemory', 'maxmemory-policy', 'save', 'databases', 'dir', 'bind')
SKEW = timedelta(seconds=2)
WINDOW = timedelta(seconds=900)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return Path(path).read_text(encoding='utf-8').strip()


def command(*argv):
    return subprocess.check_output(argv, text=True, timeout=25).strip()


def block_device(path):
    stat = Path(path).stat()
    return f'{os.major(stat.st_dev)}:{os.minor(stat.st_dev)}'


def identity(path):
    stat = Path(path).stat()
    return {'device': block_device(path), 'inode': stat.st_ino}


def instance_id():
    # Only the instance ID is read; the metadata token is never retained.
    base = 'http://100.100.100.200/latest/'
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(base+'api/token', method='PUT',
            headers={'X-aliyun-ecs-metadata-token-ttl-seconds': '60'}), timeout=3) as response:
        token = response.read().decode().strip()
    with opener.open(Request(base+'meta-data/instance-id',
            headers={'X-aliyun-ecs-metadata-token': token}), timeout=3) as response:
        return response.read().decode().strip()


def process(pid, boot_id):
    fields = read(f'/proc/{pid}/stat').rsplit(')', 1)[1].split()
    require(fields[0] not in ('Z', 'X'), 'Service is no longer live')
    return {'boot_id': boot_id, 'pid': pid, 'start_ticks': int(fields[19])}


def data_probe(path):
    fields = command('findmnt', '-n', '--mountpoint', path, '-o', 'TARGET,SOURCE,FSTYPE,MAJ:MIN,UUID,OPTIONS').split()
    require(len(fields) == 6, 'Exact data mount required: '+path)
    target, source, fs, device, uuid, options = fields
    stat = os.statvfs(path)
    return {'path': target, 'source': source, 'fstype': fs, 'device': device, 'stat_device': block_device(path),
            'uuid': uuid, 'options': options.split(','), 'total_bytes': stat.f_blocks*stat.f_frsize,
            'available_bytes': stat.f_bavail*stat.f_frsize, 'total_inodes': stat.f_files, 'available_inodes': stat.f_favail}


def info(text):
    return dict(line.split(':', 1) for line in text.splitlines() if ':' in line and not line.startswith('#'))


def redis_probe(boot_id, ip):
    cli = ('redis-cli', '-h', ip, '--raw')
    unit = ('systemctl', 'show', 'ax-pilot-redis.service')
    pid = int(command(*unit, '-p', 'MainPID', '--value'))
    before = process(pid, boot_id)
    settings = {}
    for key in REDIS_KEYS:
        row = json.loads(command('redis-cli', '-h', ip, '--json', 'CONFIG', 'GET', key))
        settings[key] = row[key] if isinstance(row, dict) else row[1]
    server = info(command(*cli, 'INFO', 'SERVER'))
    memory = info(command(*cli, 'INFO', 'MEMORY'))
    persistence = info(command(*cli, 'INFO', 'PERSISTENCE'))
    cg = Path('/sys/fs/cgroup')/command(*unit, '-p', 'ControlGroup', '--value').lstrip('/')
    return {'systemd_pid': int(command(*unit, '-p', 'MainPID', '--value')), 'process': before,
            'process_after': process(pid, boot_id), 'info_pid': int(server['process_id']), 'version': server['redis_version'],
            'binary_sha256': command('sha256sum', f'/proc/{pid}/exe').split()[0],
            'active': command('systemctl', 'is-active', 'ax-pilot-redis.service'),
            'io_accounting': command(*unit, '-p', 'IOAccounting', '--value'), 'settings': settings,
            'cgroup': {key: read(cg/key) for key in ('cpu.max', 'memory.max', 'memory.swap.max')},
            'memory': {key: int(memory[key]) for key in ('used_memory', 'used_memory_rss', 'maxmemory')},
            'persistence': {key: persistence[key] for key in ('aof_enabled', 'loading', 'aof_last_write_status',
                           'aof_rewrite_in_progress', 'aof_current_size')}}


def object_probe(boot_id):
    name = 'ax-pilot-20261111-objects-01-rustfs-1'
    selectors = {'id': '.Id', 'image_id': '.Image', 'image_reference': '.Config.Image', 'pid': '.State.Pid',
                 'running': '.State.Running', 'restarts': '.RestartCount', 'mounts': '.Mounts',
                 'nano_cpus': '.HostConfig.NanoCpus', 'memory_bytes': '.HostConfig.Memory',
                 'memory_swap_bytes': '.HostConfig.MemorySwap'}
    fmt = '{'+','.join(json.dumps(key)+':{{json '+value+'}}' for key, value in selectors.items())+'}'
    state = json.loads(command('docker', 'inspect', '--format', fmt, name))
    state['process'] = process(state['pid'], boot_id)
    tests = ['(eq . '+json.dumps(key+'='+v)+')' for key in FLAGS for v in ('true', 'false')]
    select = '{{range .Config.Env}}{{if or '+' '.join(tests)+'}}{{println .}}{{end}}{{end}}'
    state['flags'] = dict(line.split('=', 1) for line in command('docker', 'inspect', '--format', select, state['id']).splitlines())
    state['source_commit'] = command('docker', 'image', 'inspect', '--format',
                                      '{{index .Config.Labels "org.opencontainers.image.revision"}}', state['image_id'])
    state['binary_sha256'] = command('docker', 'exec', state['id'], 'sha256sum', '/usr/bin/rustfs').split()[0]
    state['data_dirs'] = {str(p): identity(p) for p in (Path(root)/'rustfs-data' for root in ROOTS['objects'])}
    state['bind_dirs'] = {f'/srv/ax-rustfs-data-{i}': identity(f'/srv/ax-rustfs-data-{i}') for i in range(4)}
    state['bind_mounts'] = [command('findmnt', '-n', '--mountpoint', p, '-o', 'TARGET') for p in state['bind_dirs']]
    state['process_after'] = process(int(command('docker', 'inspect', '--format', '{{.State.Pid}}', state['id'])), boot_id)
    return state


def probe(role):
    at = datetime.now(timezone.utc).isoformat()
    boot = read('/proc/sys/kernel/random/boot_id')
    addresses = json.loads(command('ip', '-json', '-4', 'address', 'show'))
    result = dict(schema='packed-c3-native-boot-v1', role=role, read_only=True, started_at=at,
                  instance_id=instance_id(), hostname=socket.gethostname(), boot_id=boot,
                  boot_started_unix=int(re.search(r'^btime ([0-9]+)$', read('/proc/stat'), re.M)[1]),
                  ntp=command('timedatectl', 'show', '-p', 'NTPSynchronized', '--value'),
                  kernel=command('uname', '-r'), cpu_models=sorted(set(re.findall(r'^model name\s*:\s*(.+)$', read('/proc/cpuinfo'), re.M))),
                  affinity_cpu_count=len(os.sched_getaffinity(0)),
                  memory_total_bytes=int(re.search(r'^MemTotal:\s+([0-9]+)', read('/proc/meminfo'), re.M)[1])*1024,
                  private_addresses=addresses,
                  block_devices=json.loads(command('lsblk', '--json', '--bytes', '--paths', '--output',
                                            'PATH,TYPE,SERIAL,SIZE,FSTYPE,UUID,MAJ:MIN,MOUNTPOINTS')),
                  mounts=[data_probe(path) for path in ROOTS[role]])
    if role == 'client':
        slot = Path('/sys/fs/cgroup/ax-slot-1')
        result['slot'] = {key: read(slot/key) for key in SLOT}
        result['slot']['io_stat_readable'] = bool(read(slot/'io.stat') or (slot/'io.stat').is_file())
    if role == 'metadata':
        result['redis'] = redis_probe(boot, '10.20.2.162')
    if role == 'objects':
        result['rustfs'] = object_probe(boot)
    require(read('/proc/sys/kernel/random/boot_id') == boot, 'Host rebooted during native probe')
    result['at'] = datetime.now(timezone.utc).isoformat()
    return result


def stamp(value):
    dt = datetime.fromisoformat(value)
    require(dt.utcoffset() is not None, 'Offset-aware timestamp required')
    return dt.astimezone(timezone.utc)


def pin(path, sha):
    path = Path(path)
    require(path.resolve(strict=True) == path and path.is_file(), 'Canonical absolute input file required: '+str(path))
    require(isinstance(sha, str) and re.fullmatch(r'[a-f0-9]{64}', sha), 'Valid SHA-256 required')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == sha, 'Input SHA-256 mismatch: '+str(path))
    return json.loads(raw)


def read_ref(ref):
    return pin(ref['path'], ref['sha256'])


def historical():
    result = pin(BASE/'result.json', BASE_PINS['result'])
    require(result['passed'] is True, 'Original hardware result differs')
    return ({role: pin(BASE/f'pool-a-{role}.json', BASE_PINS[role]) for role in ROLES},
            result['details']['pool-a'])


def keyed(rows, key, wanted=None):
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows) and (wanted is None or set(result) == set(wanted)),
            'Missing, duplicate or extra '+key)
    return result


def global_addresses(observation):
    return {a['local'] for interface in observation['private_addresses'] for a in interface['addr_info']
            if a['scope'] == 'global' and interface['ifname'] != 'docker0'}


def same_service(new, old, boot, keys, pid):
    require(all(new[key] == old[key] for key in keys), 'Frozen service build/configuration changed')
    p = new['process']
    require(p == new['process_after'] and p['boot_id'] == boot and type(p['pid']) is int
            and p['pid'] == pid > 0 and type(p['start_ticks']) is int and p['start_ticks'] > 0,
            'Missing, stale or inconsistent current service process identity')


def verify_role(role, old, details, now, ecs, disks, boundary, checked):
    shape = details['api']
    old_ip = f"10.20.2.{dict(client=161,metadata=162,objects=163)[role]}"
    require(ecs['Status'] == 'Running' and ecs['InstanceId'] == old['instance_id'] == details['instance_id']
            and ecs['HostName'] == old['hostname'] and all(ecs[k] == shape[k] for k in ('InstanceType', 'Cpu', 'Memory', 'ZoneId'))
            and ecs['VpcAttributes']['VpcId'] == shape['vpc']
            and old_ip in ecs['VpcAttributes']['PrivateIpAddress']['IpAddress']
            and not ecs['PublicIpAddress']['IpAddress'] and not ecs.get('EipAddress', {}).get('IpAddress'),
            'ECS instance, hardware, private network or public exposure differs: '+role)
    require(now['schema'] == 'packed-c3-native-boot-v1' and now['read_only'] is True and now['role'] == role
            and now['instance_id'] == old['instance_id'] and now['hostname'] == old['hostname']
            and old_ip in global_addresses(old) and old_ip in global_addresses(now)
            and now['kernel'] == old['kernel'] and now['cpu_models'] == old['cpu_models']
            and now['affinity_cpu_count'] == old['affinity_cpu_count'] and now['memory_total_bytes'] >= ecs['Memory']*1024**2*.90,
            'Native host identity or frozen hardware differs: '+role)
    start, end = stamp(now['started_at']), stamp(now['at'])
    boot_time = datetime.fromtimestamp(now['boot_started_unix'], timezone.utc)
    require(str(UUID(now['boot_id'])) == now['boot_id'] and now['boot_id'] != old['boot_id'] and now['ntp'] == 'yes'
            and boundary-SKEW <= boot_time <= start <= end <= checked+SKEW and checked-start <= WINDOW,
            'Old boot, clock skew or stale readback: '+role)
    current = keyed(now['mounts'], 'path', ROOTS[role])
    old_devices = old['block_devices']['blockdevices']
    native_devices = now['block_devices']['blockdevices']
    disk_ids = []
    device_ids = []
    uuid_ids = []
    for path, mounted in current.items():
        previous = [d for d in old_devices if d['type'] == 'disk' and path in (d['mountpoints'] or [])]
        require(len(previous) == 1 and previous[0]['fstype'] == 'ext4', 'Missing original disk identity: '+path)
        serial = previous[0]['serial'].strip()
        disk_id = 'd-'+serial
        matches = [d for d in native_devices if d['type'] == 'disk' and (d['serial'] or '').strip() == serial]
        require(len(matches) == 1, 'Missing or duplicate native disk serial: '+path)
        native = matches[0]
        api = disks[disk_id]
        require(api['InstanceId'] == ecs['InstanceId'] and api['Type'] == 'data' and api['Status'] == 'In_use'
                and api['ZoneId'] == shape['ZoneId'] and (not api.get('SerialNumber') or api['SerialNumber'] == serial)
                and native['size'] == previous[0]['size'] == api['Size']*1024**3
                and native['fstype'] == mounted['fstype'] == 'ext4' and not native.get('children')
                and path in (native['mountpoints'] or []) and native['path'] == mounted['source']
                and native['maj:min'] == mounted['device'] == mounted['stat_device']
                and str(UUID(native['uuid'])) == native['uuid'] == mounted['uuid']
                and {'rw', 'noatime'} <= set(mounted['options']) and 'ro' not in mounted['options'],
                'ECS attachment or native source/UUID/options differ: '+path)
        require(all(type(mounted['available_'+q]) is int and type(mounted['total_'+q]) is int
                    and 0 <= mounted['available_'+q] <= mounted['total_'+q] and mounted['total_'+q] > 0
                    for q in ('bytes', 'inodes')), 'Invalid native available/total capacity: '+path)
        disk_ids.append(disk_id)
        device_ids.append(mounted['device'])
        uuid_ids.append(mounted['uuid'])
    require(set(disk_ids) == set(details['data_disk_ids']) and len(set(disk_ids)) == len(set(device_ids)) == len(set(uuid_ids)) == len(current)
            and sorted([disks[k][field] for field in ('Category', 'Size', 'PerformanceLevel')] for k in disk_ids) == shape['data_disks'],
            'Frozen data disk membership or allocation shape differs: '+role)
    result = dict(instance_id=ecs['InstanceId'], old_boot_id=old['boot_id'], boot_id=now['boot_id'],
                  disks={k: current[k] for k in sorted(current)})
    if role == 'client':
        require(now['slot'] == dict(SLOT,io_stat_readable=True), 'Original client slot limits or idle state differ')
    if role == 'metadata':
        service = now['redis']
        same_service(service, old['redis'], now['boot_id'], ('version', 'binary_sha256', 'io_accounting', 'cgroup'), service['systemd_pid'])
        expected = {**old['redis']['settings'], 'dir': '/srv/ax-metadata/redis', 'bind': '127.0.0.1 '+old_ip}
        health = service['persistence']
        require(service['active'] == 'active' and service['info_pid'] == service['systemd_pid']
                and service['settings'] == expected and service['memory']['maxmemory'] == int(expected['maxmemory'])
                and health['aof_enabled'] == '1' and health['loading'] == health['aof_rewrite_in_progress'] == '0'
                and health['aof_last_write_status'] == 'ok' and int(health['aof_current_size']) >= 0,
                'Redis active process, AOF or bounded-memory configuration differs')
        result.update(process=service['process'], redis_memory=service['memory'], redis_persistence=health)
    if role == 'objects':
        service = now['rustfs']
        same_service(service, old['rustfs'], now['boot_id'], ('id', 'image_id', 'image_reference', 'source_commit',
                     'binary_sha256', 'nano_cpus', 'memory_bytes', 'memory_swap_bytes', 'flags'), service['pid'])
        mounts = keyed([m for m in service['mounts'] if m['Destination'].startswith('/data/rustfs')], 'Destination',
                       [f'/data/rustfs{i}' for i in range(4)])
        require(service['running'] is True and service['restarts'] == 0
                and sorted(service['bind_mounts']) == sorted(service['bind_dirs']), 'Original object service or mounts not live')
        for i, root in enumerate(ROOTS['objects']):
            bind, data = f'/srv/ax-rustfs-data-{i}', root+'/rustfs-data'
            m = mounts[f'/data/rustfs{i}']
            require(m['Type'] == 'bind' and m['RW'] is True and m['Source'] == bind
                    and service['bind_dirs'][bind] == service['data_dirs'][data]
                    and service['data_dirs'][data]['device'] == current[root]['device']
                    and service['data_dirs'][data]['inode'] > 0,
                    'RustFS container bind is not on its original disk/directory: '+root)
        result.update(process=service['process'], rustfs_container_id=service['id'], rustfs_image=service['image_id'])
    return result


def verify(request, checked=None):
    checked = checked or datetime.now(timezone.utc)
    require(set(request) == {'schema','cell_id','wake_requested_at','previous_stopped','instances','disks','native'}
            and request['schema'] == 'packed-c3-reboot-evidence-request-v1' and request['cell_id'] == CELL,
            'Exact Packed C3 evidence request required; no admission fields accepted')
    old, details = historical()
    boundary = stamp(request['wake_requested_at'])
    previous = read_ref(request['previous_stopped'])
    inst_ids = {d['instance_id'] for d in details.values()}
    disk_ids = {disk for d in details.values() for disk in d['data_disk_ids']}
    require(previous['RequestId'] and stamp(previous['at']) <= boundary <= checked+SKEW, 'Invalid StopCharging/wake order')
    stopped = keyed(previous['instances'], 'InstanceId')
    require(all(stopped[i]['Status'] == 'Stopped' and stopped[i]['StoppedMode'] == 'StopCharging' for i in inst_ids),
            'Previous A-pool StopCharging evidence missing')
    api = {}
    for key, plural, singular, wanted in (('instances', 'Instances', 'Instance', inst_ids), ('disks', 'Disks', 'Disk', disk_ids)):
        ref = request[key]
        captured = stamp(ref['at'])
        doc = read_ref(ref)
        require(doc['RequestId'] and boundary-SKEW <= captured <= checked+SKEW and checked-captured <= WINDOW,
                'Stale or unbound ECS readback: '+key)
        api[key] = keyed(doc[plural][singular], singular+'Id', wanted)
        require(doc.get('TotalCount', len(wanted)) == len(wanted), 'Truncated ECS readback: '+key)
    require(set(request['native']) == set(ROLES), 'All A roles require current native readback')
    result = {}
    for role in ROLES:
        native_ref = request['native'][role]
        native = read_ref(native_ref)
        sent, received = stamp(native_ref['sent_at']), stamp(native_ref['received_at'])
        require(boundary-SKEW <= sent <= received <= checked+SKEW
                and sent-SKEW <= stamp(native['started_at']) <= stamp(native['at']) <= received+SKEW,
                'Native readback falls outside coordinator clock bounds: '+role)
        inst = api['instances'][details[role]['instance_id']]
        result[role] = verify_role(role, old[role], details[role], native, inst, api['disks'], boundary, checked)
    require(len({node['boot_id'] for node in result.values()}) == len(ROLES), 'Duplicate current native boot IDs')
    return dict(schema='packed-c3-reboot-evidence-check-v1', evidence_consistent=True,
                verified_at=checked.isoformat(), cell_id=CELL, roles=result, historical_sha256=BASE_PINS,
                input_pins={k:request[k] for k in ('previous_stopped', 'instances', 'disks', 'native')},
                admission=False, formal_acceptance=False,
                scope='Read-only source and identity check. Not fresh capacity approval, cold proof, runtime binding, cross-pool equivalence or a lease.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    capture = actions.add_parser('probe')
    capture.add_argument('role', choices=ROLES)
    check = actions.add_parser('verify')
    check.add_argument('--request', type=Path, required=True)
    check.add_argument('--sha256', required=True)
    args = parser.parse_args()
    try:
        result = probe(args.role) if args.action == 'probe' else verify(pin(args.request,args.sha256))
        if args.action == 'verify':
            result['request'] = {'path': str(args.request), 'sha256': args.sha256}
        print(json.dumps(result, sort_keys=True, indent=2))
    except (ValueError, KeyError, TypeError, IndexError, OSError, OverflowError, subprocess.SubprocessError) as error:
        print('Evidence rejected: '+str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
