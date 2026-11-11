#!/usr/bin/env python3
"""One read-only Pool B metadata diagnosis: which redis serves which database.

Starts only the metadata host, reads process/listener/INFO/keyspace facts, then
returns it to StopCharging. It never touches objects, never writes Redis and
never runs JuiceFS.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent

PROBE = r'''
import json, subprocess
def run(*a):
    p = subprocess.run(a, capture_output=True, text=True)
    return dict(rc=p.returncode, out=p.stdout.strip()[-4000:], err=p.stderr.strip()[-2000:])
res = {}
res['ps_redis'] = run('ps', '-eo', 'pid,etimes,cmd')
res['ss'] = run('ss', '-ltnpH')
units = run('systemctl', 'list-units', '--type=service', '--all', '--no-pager', '--no-legend')
res['units'] = dict(rc=units['rc'], out='\n'.join(l for l in units['out'].splitlines() if 'redis' in l.lower())[:3000], err=units['err'])
for host in ('127.0.0.1', '10.20.2.169'):
    key = 'host_' + host
    entry = {}
    entry['ping'] = run('redis-cli', '-h', host, 'PING')
    info = run('redis-cli', '-h', host, 'INFO', 'persistence')
    entry['persistence'] = dict((l.split(':', 1)[0], l.split(':', 1)[1]) for l in info['out'].splitlines() if ':' in l)
    entry['persistence_rc'] = info['rc']
    entry['config_appendonly'] = run('redis-cli', '-h', host, 'CONFIG', 'GET', 'appendonly')
    entry['dir'] = run('redis-cli', '-h', host, 'CONFIG', 'GET', 'dir')
    entry['dbsize'] = {str(db): run('redis-cli', '-h', host, '-n', str(db), 'DBSIZE') for db in (0, 60, 61, 62)}
    clients = run('redis-cli', '-h', host, 'CLIENT', 'LIST')
    entry['client_list'] = dict(rc=clients['rc'], head=clients['out'][:800], err=clients['err'])
    res[key] = entry
# Attempt the fix and keep the whole journal, so a failed start explains itself.
res['fix'] = {}
res['fix']['stop_foreign'] = run('systemctl', 'stop', 'redis-server')
res['fix']['start_original'] = run('systemctl', 'start', 'ax-pilot-redis.service')
run('sleep', '3')
res['fix']['active'] = run('systemctl', 'is-active', 'ax-pilot-redis.service')
res['fix']['status'] = run('systemctl', 'status', 'ax-pilot-redis.service', '--no-pager', '-l')
res['fix']['journal'] = run('journalctl', '-u', 'ax-pilot-redis.service', '--no-pager', '-n', '60')
res['fix']['data_dir'] = run('ls', '-ld', '/srv/ax-metadata/redis')
res['fix']['data_listing'] = dict(rc=0, out='\n'.join(run('ls', '-la', '/srv/ax-metadata/redis')['out'].splitlines()[:15]))
res['fix']['conf'] = run('cat', '/etc/redis/ax-pilot-20261111-b.conf')
res['fix']['listen'] = run('ss', '-ltnpH')
res['fix']['ping_private'] = run('redis-cli', '-h', '10.20.2.169', 'PING')
res['fix']['config_appendonly'] = run('redis-cli', '-h', '10.20.2.169', 'CONFIG', 'GET', 'appendonly')
print(json.dumps(res))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ssh-config', type=Path, required=True)
    parser.add_argument('--ssh-binary', required=True)
    parser.add_argument('--aliyun', required=True)
    parser.add_argument('--repo', type=Path, default=HERE.parents[3])
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('b_c1_gc_apply', HERE/'b-c1-gc-apply.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args.out.mkdir(parents=True, exist_ok=False)
    ssh = dict(binary=args.ssh_binary, config=str(args.ssh_config))
    cloud = module.cloud.Cloud(args.aliyun, args.out)
    report = {}
    with module.remote_locked(ssh):
        module.assert_client_stopped(cloud, 'before diagnosis')
        module.start_instance(cloud, 'metadata')
        module.wait_running(cloud, 'metadata', 900)
        module.assert_client_stopped(cloud, 'after starting metadata')
        try:
            # EC2 "Running" precedes sshd; wait for the transport, not the API.
            deadline = module.time.monotonic()+300
            while True:
                probe = subprocess.run(module.ssh_argv(ssh['binary'], ssh['config'], 'pool-b-metadata', ['true']),
                                       capture_output=True, text=True)
                if probe.returncode == 0:
                    break
                module.require(module.time.monotonic() < deadline,
                               'sshd never came up on metadata: '+probe.stderr[-300:])
                module.time.sleep(5)
            result = subprocess.run(module.ssh_argv(ssh['binary'], ssh['config'], 'pool-b-metadata', ['python3', '-']),
                                    input=PROBE, capture_output=True, text=True)
            module.require(result.returncode == 0, 'Probe failed: '+result.stderr[-800:])
            report = json.loads(result.stdout)
            (args.out/'probe.json').write_text(json.dumps(report, indent=2) + '\n')
        finally:
            module.stop_instance(cloud, 'metadata', 900)
            module.assert_client_stopped(cloud, 'after stopping metadata')
    print(json.dumps({k: report.get(k) for k in ('host_127.0.0.1', 'host_10.20.2.169')}, indent=2)[:6000])


if __name__ == '__main__':
    main()
