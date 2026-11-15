"""Install the selected run configuration and start its metadata and collectors."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import cluster_lib as cl
sys.path.insert(0, str(cl.REPO_ROOT))


def remote_python(host, source):
    return cl.run_on_host(host, ['python3', '-'], input_bytes=source.encode(), timeout=600)


def setup(code_only=False):
    context = cl.REPO_ROOT / 'configs/runtime.json'
    for host in cl.ALL_HOST_NAMES:
        if host != "A366":
            cl.sync_file_to_host(host, Path("/srv/ax-cluster/direct/suite.tar.gz"), "/srv/ax-cluster/direct/suite.tar.gz")
            cl.run_on_host(host, ["python3", "-c", "import tarfile;tarfile.open('/srv/ax-cluster/direct/suite.tar.gz').extractall('/srv/ax-cluster/direct/repo',filter='data')"])
        cl.sync_file_to_host(host, context, '/srv/ax-cluster/direct/runtime/context.json')
        for source in sorted((cl.REPO_ROOT / 'bench/cluster/runtime').glob('*.py')):
            cl.sync_file_to_host(host, source, str(cl.SCRIPTS_DIR / source.name))
        cl.run_on_host(host, ['mkdir', '-p', str(cl.RUN_ROOT / 'hosts' / host)])

    if code_only:
        return
    from prepare_backends import prepare
    prepare()
    source = '''
import json,os,subprocess
from pathlib import Path
root=Path('/srv/ax-cluster/services/RUN_ID')
root.mkdir(parents=True,exist_ok=True)
secrets={}
for line in Path('/srv/ax-cluster/private/redis.env').read_text().splitlines():
 if '=' in line and not line.startswith('#'):
  key,value=line.split('=',1);secrets[key]=value
redis=root/'redis';redis.mkdir(exist_ok=True)
config=root/'redis.conf'
config.write_text('bind 127.0.0.1 175.168.3.55\\nport REDIS_PORT\\nprotected-mode yes\\nrequirepass '+secrets['REDIS_PASSWORD']+'\\ndir '+str(redis)+'\\nappendonly yes\\nappendfsync everysec\\nmaxmemory 48gb\\nmaxmemory-policy noeviction\\nsave ""\\n')
os.chmod(config,0o600)
tikv=root/'tikv.toml'
tikv.write_text('[storage]\\nreserve-space = "2GB"\\n[storage.block-cache]\\ncapacity = "8GB"\\n[raftstore]\\nstore-pool-size = 2\\napply-pool-size = 2\\n[readpool.unified]\\nmin-thread-count = 2\\nmax-thread-count = 8\\n[server]\\ngrpc-concurrency = 8\\n')
services={
 'redis':('/usr/bin/redis-server '+str(config),'64G','800%'),
 'pd':('/srv/ax-cluster/direct/bin/pd-server-v7.5.0 --name=ax-RUN_ID --data-dir='+str(root/'pd')+' --client-urls=http://175.168.3.55:2379 --advertise-client-urls=http://175.168.3.55:2379 --peer-urls=http://175.168.3.55:2380 --advertise-peer-urls=http://175.168.3.55:2380 --initial-cluster=ax-RUN_ID=http://175.168.3.55:2380','4G','200%'),
 'tikv':('/srv/ax-cluster/direct/bin/tikv-server-v7.5.0 --addr=175.168.3.55:20160 --advertise-addr=175.168.3.55:20160 --status-addr=0.0.0.0:20180 --pd=175.168.3.55:2379 --data-dir='+str(root/'tikv')+' --config='+str(tikv),'24G','800%')}
for name,(command,memory,cpu) in services.items():
 unit=Path('/etc/systemd/system')/('ax-RUN_ID-'+name+'.service')
 unit.write_text('[Unit]\\nAfter=network-online.target\\n[Service]\\nExecStart='+command+'\\nMemoryMax='+memory+'\\nMemorySwapMax=0\\nCPUQuota='+cpu+'\\nLimitNOFILE=1048576\\nRestart=on-failure\\nRestartSec=5\\n[Install]\\nWantedBy=multi-user.target\\n')
subprocess.run(['systemctl','daemon-reload'],check=True)
for name in services:
 subprocess.run(['systemctl','start','ax-RUN_ID-'+name],check=True)
'''.replace('RUN_ID', cl.RUN_ID).replace('REDIS_PORT', str(cl.REDIS_PORT))
    from infra.idcai.deploy_direct import restrict_service
    for port in (cl.REDIS_PORT, 2379, 2380, 20160, 20180):
        restrict_service('A355', port)
    remote_python('A355', source)
    # Select one replica before creating any data in this single-node TiKV test.
    time.sleep(5)
    cl.run_on_host('A355', ['curl', '-fsS', '-X', 'POST', '-H', 'Content-Type: application/json',
        '-d', '{"max-replicas":1}', cl.PD_ENDPOINT + '/pd/api/v1/config/replicate'])
    for host in cl.ALL_HOST_NAMES:
        try:
            cl.collector_rpc(host, {'action': 'stop'})
        except (OSError, RuntimeError):
            pass
        cl.run_on_host(host, ['systemctl', 'stop', 'ax-cluster-direct-collector.service'], check=False)
        flags = (' --has-redis --has-tikv' if host == 'A355' else '')
        if host in ('A672', 'A601', 'A635', 'A368'):
            flags += ' --has-rustfs'
        unit = ('[Unit]\nAfter=network-online.target\n[Service]\n'
                'ExecStart=/usr/bin/python3 /srv/ax-cluster/direct/scripts/host_collector.py '
                f'--host {host} --out-dir {cl.RUN_ROOT}/hosts/{host} --interval 5{flags}\n'
                'Restart=on-failure\nRestartSec=5\nMemoryMax=1G\n')
        remote_python(host, 'from pathlib import Path\nimport subprocess\n'
            f'Path("/etc/systemd/system/ax-cluster-direct-collector.service").write_text({unit!r})\n'
            'subprocess.run(["systemctl","daemon-reload"],check=True)\n'
            'subprocess.run(["systemctl","start","ax-cluster-direct-collector.service"],check=True)\n')
    time.sleep(3)
    records = {host: cl.collector_rpc(host, {'action': 'status'}) for host in cl.ALL_HOST_NAMES}
    (cl.RUN_ROOT / 'setup.json').write_text(json.dumps(records, indent=2) + '\n')
    print(json.dumps(records))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-only', action='store_true')
    setup(parser.parse_args().code_only)
