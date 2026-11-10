#!/usr/bin/env python3
"""Configure one owned pilot host after identity and new-disk preparation."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import socket
import subprocess
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['authorize-key', 'proxy', 'assets', 'object-paths', 'slot', 'redis'])
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--public-key', type=Path)
    p.add_argument('--private-ip')
    a = p.parse_args()
    if os.geteuid() != 0 or a.out.exists():
        raise ValueError('Root and a new output directory required')
    spec = json.loads(a.spec.read_text())
    loader = importlib.util.spec_from_file_location('pilot_provision', Path(__file__).with_name('provision-node.py'))
    provision = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(provision)
    if provision.instance_id() != spec['instance_id'] or socket.gethostname() != spec['hostname']:
        raise ValueError('This is not the host named in the deployment spec')
    a.out.mkdir(parents=True, exist_ok=False)
    result = dict(at=datetime.now(timezone.utc).isoformat(), action=a.action, instance_id=spec['instance_id'], passed=False)

    def run(argv, name):
        with (a.out / (name + '.log')).open('xb') as log:
            subprocess.run(argv, check=True, stdout=log, stderr=subprocess.STDOUT)

    def new(path, text, mode=0o644):
        with Path(path).open('x') as stream:
            os.chmod(path, mode)
            stream.write(text)

    def service(name, text):
        unit = '/etc/systemd/system/' + name + '.service'
        new(unit, text)
        run(['systemctl', 'daemon-reload'], 'systemd-reload')
        run(['systemctl', 'start', name], 'service-start')
        info = subprocess.check_output(['systemctl', 'show', name, '--property=MainPID,ActiveState,SubState'], text=True)
        (a.out / 'service-status.txt').write_text(info)
        if 'ActiveState=active' not in info:
            raise ValueError('Pilot service is not active')
        result['service'] = name

    if a.action == 'authorize-key':
        public = a.public_key.read_text().strip()
        if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+ ' + re.escape(spec['campaign']), public):
            raise ValueError('Expected this campaign public key, without authorized_keys options')
        path = Path('/root/.ssh/authorized_keys')
        if path.is_symlink():
            raise ValueError('Do not replace an authorized_keys symlink')
        old = path.read_text() if path.exists() else ''
        if public in old.splitlines():
            raise ValueError('Campaign key is already installed; inspect existing receipt')
        with path.open('a') as stream:
            stream.write(('\n' if old and not old.endswith('\n') else '') + public + '\n')
            stream.flush(); os.fsync(stream.fileno())
        path.chmod(0o600)
        result['existing_keys_retained'] = True
    elif a.action in ('proxy', 'assets'):
        if spec['role'] != 'coordinator' or a.private_ip != '10.20.0.10' or not os.path.ismount('/srv/ax-pilot-control'):
            raise ValueError('Coordinator private interface and independent control disk required')
        if a.action == 'proxy':
            # Squid was installed for this campaign; its default instance must
            # not compete with the dedicated private listener.
            run(['systemctl', 'stop', 'squid'], 'stop-new-default-squid')
            directory = Path('/srv/ax-pilot-control/services/proxy')
            directory.mkdir(parents=True, exist_ok=False)
            user = pwd.getpwnam('proxy'); os.chown(directory, user.pw_uid, user.pw_gid)
            config = '/etc/squid/ax-pilot-20261111.conf'
            new(config, '''http_port 10.20.0.10:3128
acl pilot src 10.20.0.0/16
acl safe_ports port 80 443
acl ssl_ports port 443
acl CONNECT method CONNECT
http_access deny !safe_ports
http_access deny CONNECT !ssl_ports
http_access allow pilot
http_access deny all
cache deny all
cache_mem 8 MB
pid_filename /run/ax-pilot-proxy/squid.pid
access_log stdio:/srv/ax-pilot-control/services/proxy/access.log
cache_log /srv/ax-pilot-control/services/proxy/cache.log
cache_store_log none
visible_hostname ax-pilot-private-proxy
''')
            service('ax-pilot-proxy', '''[Unit]
Description=AX pilot private package proxy
After=network-online.target
[Service]
User=proxy
Group=proxy
RuntimeDirectory=ax-pilot-proxy
ExecStart=/usr/sbin/squid -N -f /etc/squid/ax-pilot-20261111.conf
Restart=no
''')
        else:
            if not Path('/srv/ax-pilot-control/assets/.suite-manifest.json').is_file():
                raise ValueError('Install the verified bundle first')
            service('ax-pilot-assets', '''[Unit]
Description=AX pilot immutable prepared assets
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 -m http.server 18080 --bind 10.20.0.10 --directory /srv/ax-pilot-control/assets
Restart=no
''')
    elif a.action == 'slot':
        if spec['role'] not in ('client', 'target'):
            raise ValueError('Only executors have an Agent slot')
        # The slot's native io.stat exists only if its parent enables io.
        # Enable before creating the slot; absence is a deployment failure.
        # Ask systemd to retain the IO controller. A manual '+io' alone can
        # disappear when systemd reconciles its hierarchy during later spawns.
        subprocess.run(['systemctl', 'set-property', '--runtime', 'system.slice', 'IOAccounting=yes'], check=True)
        parent = Path('/sys/fs/cgroup')
        controllers = {'cpu', 'memory', 'pids', 'io'}
        if not controllers.issubset((parent/'cgroup.controllers').read_text().split()):
            raise ValueError('CPU, memory, pids and IO controllers required')
        (parent/'cgroup.subtree_control').write_text(' '.join('+'+c for c in sorted(controllers)))
        group = Path('/sys/fs/cgroup/ax-slot-1')
        group.mkdir(exist_ok=False)
        for name, value in [('cpu.max', '200000 100000'), ('memory.max', '8589934592'),
                ('memory.swap.max', '0'), ('pids.max', '4096')]:
            (group / name).write_text(value)
        actual = {name: (group / name).read_text().strip() for name in
            ('cpu.max', 'memory.max', 'memory.swap.max', 'pids.max', 'cgroup.procs')}
        if actual['cpu.max'] != '200000 100000' or actual['memory.max'] != '8589934592' or actual['memory.swap.max'] != '0':
            raise ValueError('Agent slot budget differs')
        result['cgroup'] = str(group); result['limits'] = actual
        result['io_stat'] = (group/'io.stat').read_text()
    elif a.action == 'object-paths':
        if spec['role'] != 'objects' or len(spec['data_disks']) != 4:
            raise ValueError('Four-disk objects host required')
        devices, targets = set(), []
        for index, disk in enumerate(spec['data_disks']):
            root = Path(disk['mount'])
            if root.is_symlink() or not os.path.ismount(root):
                raise ValueError('Original formatted data mount is absent')
            device = root.stat().st_dev
            if device == Path('/').stat().st_dev or device in devices:
                raise ValueError('Each data root must be on its own disk')
            devices.add(device)
            data = root / 'rustfs-data'; data.mkdir(exist_ok=False); os.chown(data, 10001, 10001)
            target = Path('/srv/ax-rustfs-data-' + str(index)); target.mkdir(exist_ok=False)
            run(['mount', '--bind', str(data), str(target)], 'bind-data-' + str(index))
            if target.stat().st_dev != device or target.stat().st_uid != 10001:
                raise ValueError('Bound RustFS data path differs')
            targets.append(str(target))
        logs = Path(spec['data_disks'][0]['mount']) / 'rustfs-logs'
        logs.mkdir(exist_ok=False); os.chown(logs, 10001, 10001)
        result.update(data_mounts=targets, log_dir=str(logs), distinct_data_devices=4,
            log_storage_note='Logs/observer share disk 0; their I/O is recorded and included in overhead checks.')
    else:
        if spec['role'] != 'metadata' or a.private_ip is None:
            raise ValueError('Metadata node private IP required')
        import ipaddress
        if not ipaddress.ip_address(a.private_ip).is_private:
            raise ValueError('Private Redis listener required')
        run(['systemctl', 'stop', 'redis-server'], 'stop-new-default-redis')
        root = Path('/srv/ax-metadata')
        if not os.path.ismount(root):
            raise ValueError('Redis data disk is absent')
        directory = root / 'redis'; directory.mkdir(exist_ok=False)
        (a.out / 'overcommit-before.txt').write_text(Path('/proc/sys/vm/overcommit_memory').read_text())
        run(['sysctl', '-w', 'vm.overcommit_memory=1'], 'overcommit')
        user = pwd.getpwnam('redis'); os.chown(directory, user.pw_uid, user.pw_gid)
        config = '/etc/redis/ax-pilot-20261111.conf'
        new(config, f'''bind 127.0.0.1 {a.private_ip}
protected-mode no
port 6379
daemonize no
supervised no
databases 256
dir {directory}
appendonly yes
appendfsync always
save ""
maxmemory 10737418240
maxmemory-policy noeviction
logfile ""
''')
        service('ax-pilot-redis', '''[Unit]
Description=AX pilot dedicated Redis metadata
After=network-online.target
[Service]
User=redis
Group=redis
ExecStart=/usr/bin/redis-server /etc/redis/ax-pilot-20261111.conf
IOAccounting=yes
CPUQuota=300%
MemoryMax=12884901888
MemorySwapMax=0
Restart=no
''')
        for _ in range(100):
            ready = subprocess.run(['redis-cli', 'PING'], capture_output=True, text=True)
            if ready.returncode == 0 and ready.stdout.strip() == 'PONG':
                break
            time.sleep(.1)
        else:
            raise ValueError('Original Redis service did not become ready; preserve it')
        run(['redis-cli', 'INFO', 'ALL'], 'native-info')
    result['passed'] = True
    (a.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
