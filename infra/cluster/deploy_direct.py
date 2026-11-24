"""Prepare the direct-network campaign from the coordinator using shared SSH connections."""
import argparse
import io
import json
from pathlib import Path
import sys
import tarfile
import time

sys.path.insert(0, "/srv/ax-cluster/direct/scripts")
import cluster_lib as cl

BASE = Path('/srv/ax-cluster/direct')
OBJECTS = ['node-store-1', 'node-store-2', 'node-store-3', 'node-store-4']


def remote_python(host, script, argument=None):
    command = ['python3', '-c', script]
    if argument is not None:
        command.append(json.dumps(argument))
    return cl.run_on_host(host, command, timeout=180, log_cmd=False)


def restrict_service(host, port):
    remote_python(host, '''
import json,subprocess,sys
spec=json.loads(sys.argv[1]); chain='AXDIRECT'
if subprocess.run(['iptables','-n','-L',chain],capture_output=True).returncode:
    subprocess.run(['iptables','-N',chain],check=True)
for address in spec['allow']:
    rule=['-s',address,'-j','ACCEPT']
    if subprocess.run(['iptables','-C',chain,*rule],capture_output=True).returncode:
        subprocess.run(['iptables','-I',chain,'1',*rule],check=True)
if subprocess.run(['iptables','-C',chain,'-j','REJECT'],capture_output=True).returncode:
    subprocess.run(['iptables','-A',chain,'-j','REJECT'],check=True)
rule=['-d',spec['ip'],'-p','tcp','--dport',str(spec['port']),'-j',chain]
if subprocess.run(['iptables','-C','INPUT',*rule],capture_output=True).returncode:
    subprocess.run(['iptables','-I','INPUT','1',*rule],check=True)
''', {'ip':cl.HOST_BY_NAME[host]['ip'],'port':port,'allow':[h['ip'] for h in cl.HOSTS]})


def distribute():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for folder in ('scripts', 'repo'):
            archive.add(BASE / folder, arcname=folder, filter=lambda t: None if '__pycache__' in t.name else t)
    payload = buffer.getvalue()
    for host in cl.ALL_HOST_NAMES:
        if host != 'node-coord':
            cl.run_on_host(host, ['python3', '-c',
                "import io,sys,tarfile; from pathlib import Path; p=Path('/srv/ax-cluster/direct'); p.mkdir(parents=True,exist_ok=True); tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()),mode='r:').extractall(p,filter='data')"], input_bytes=payload, log_cmd=False)
        remote_python(host, '''
import os,sys,json
from pathlib import Path
root=Path('/srv/ax-cluster/direct')
for d in ('bin','runtime','services','deployment'):
    (root/d).mkdir(parents=True,exist_ok=True)
run=Path('/srv/ax-cluster/runs/direct/run-001')
for d in ('hosts','mounts','cache','C1','C2','C3','C4','plan'):
    (run/d).mkdir(parents=True,exist_ok=True)
for p in Path('/srv/ax-cluster/releases/bin').iterdir():
    if p.is_file() and p.name not in ('graftfs','packed_snapshot_fixture','ax-graftfs-admin'):
        target=root/'bin'/p.name
        if not target.exists(): target.symlink_to(p)
print(json.dumps({'host':sys.argv[1],'ready':True}))
''', host)
        print(json.dumps({'host': host, 'runtime_distributed': True}), flush=True)


def prepare_services():
    inventory = {}
    for host in cl.ALL_HOST_NAMES:
        reply = remote_python(host, '''
import json,subprocess
from pathlib import Path
active=[]
for d in Path('/proc').iterdir():
    if not d.name.isdigit(): continue
    try:
        executable=(d/'exe').resolve().name
        if executable in ('fio','full-fuse-replay','c1-io-helper','c3-loader'):
            active.append({'pid':int(d.name),'executable':executable})
    except OSError: pass
mounts=Path('/proc/mounts').read_text().splitlines()
mounts=[m for m in mounts if 'fuse.graftfs' in m or 'fuse.juicefs' in m]
print(json.dumps({'active_workloads':active,'fuse_mounts':mounts}))
''')
        inventory[host] = json.loads(reply.stdout)
    (cl.RUN_ROOT/'preparation-inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
    if any(v['active_workloads'] or v['fuse_mounts'] for v in inventory.values()):
        raise RuntimeError('An existing workload or FUSE mount requires ownership review')
    for host in OBJECTS:
        restrict_service(host,9000)
    restrict_service('node-meta',16379)
    for host in cl.ALL_HOST_NAMES:
        remote_python(host, '''
import json,socket,subprocess
from pathlib import Path
unit='ax-cluster-collector-run001.service'
if subprocess.run(['systemctl','is-active','--quiet',unit]).returncode == 0:
    s=socket.socket(socket.AF_UNIX); s.settimeout(10)
    try:
        s.connect('/srv/ax-cluster/runtime/collector.sock'); s.sendall(b'{"action":"stop"}\\n'); s.recv(65536)
    finally: s.close()
    subprocess.run(['systemctl','stop',unit],check=True)
''')
    for index, host in enumerate(OBJECTS, 1):
        source = Path('/srv/ax-cluster/deploy/rustfs-deployment') / f'rustfs{index}' / 'compose.json'
        compose = json.loads(source.read_text())
        compose['name'] = 'ax-cluster-direct-run001'
        service = compose['services']['rustfs']
        service['environment']['RUSTFS_ADDRESS'] = cl.HOST_BY_NAME[host]['ip'] + ':9000'
        service['environment']['OTEL_RESOURCE_ATTRIBUTES'] = 'rustfs.cluster.id=ax-cluster-direct-run001'
        service['extra_hosts'] = {f'rustfs{i+1}': cl.HOST_BY_NAME[h]['ip'] for i,h in enumerate(OBJECTS)}
        for volume in service['volumes']:
            volume['source'] = str(BASE/'services'/('rustfs-data' if volume['target']=='/data/rustfs0' else 'rustfs-logs'))
        save = cl.RUN_ROOT/'deployment'/host/'compose.json'
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(json.dumps(compose, indent=2)+'\n')
        cl.sync_file_to_host(host, save, str(BASE/'deployment/compose.json'))
        remote_python(host, '''
import json,os,subprocess
from pathlib import Path
for name in ('rustfs-data','rustfs-logs'):
    p=Path('/srv/ax-cluster/direct/services')/name
    p.mkdir(parents=True,exist_ok=True)
    old=Path('/srv/ax-cluster')/name
    if old.exists():
        st=old.stat(); os.chown(p,st.st_uid,st.st_gid); os.chmod(p,st.st_mode & 0o777)
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
old='ax-cluster-c1-c3-run001-rustfs-1'
if old in names: subprocess.run(['docker','stop','--time','60',old],check=True)
subprocess.run(['docker','restart','ax-cluster-otelcol-run001'],check=True)
subprocess.run(['docker','compose','-f','/srv/ax-cluster/direct/deployment/compose.json','up','-d'],check=True)
''')
        print(json.dumps({'host':host,'direct_rustfs_started':True}), flush=True)
    remote_python('node-meta', '''
import subprocess
from pathlib import Path
root=Path('/srv/ax-cluster/direct/services/redis')
root.mkdir(parents=True,exist_ok=True)
pw=next(x.split('=',1)[1].strip() for x in Path('/srv/ax-cluster/private/redis.env').read_text().splitlines() if x.startswith('REDIS_PASSWORD='))
config=Path('/srv/ax-cluster/private/redis-direct.conf')
config.write_text('bind 127.0.0.1 175.168.3.55\\nport 16379\\nprotected-mode yes\\nrequirepass '+pw+'\\ndir '+str(root)+'\\nappendonly yes\\nappendfsync everysec\\nmaxmemory 48gb\\nmaxmemory-policy noeviction\\nsave ""\\n')
config.chmod(0o600)
subprocess.run(['systemctl','stop','ax-cluster-redis-run001.service'],check=True)
unit='ax-cluster-direct-redis.service'
if subprocess.run(['systemctl','is-active','--quiet',unit]).returncode:
    subprocess.run(['systemd-run','--unit='+unit,'--property=MemoryMax=64G','--property=MemorySwapMax=0','redis-server',str(config)],check=True)
''')
    deadline=time.monotonic()+120
    while True:
        try:
            for ep in cl.RUSTFS_ENDPOINTS: cl.make_s3_client(ep).list_buckets()
            if cl.redis_cli('PING').strip() != 'PONG': raise RuntimeError('Redis readiness')
            break
        except Exception:
            if time.monotonic()>deadline: raise
            time.sleep(2)
    endpoints={'redis':f'{cl.REDIS_HOST}:{cl.REDIS_PORT}','rustfs':cl.RUSTFS_ENDPOINTS,'interface':'eth0'}
    (cl.RUN_ROOT/'endpoints.json').write_text(json.dumps(endpoints,indent=2)+'\n')
    print(json.dumps({'services_ready':True,**endpoints}),flush=True)


def collectors():
    for host in cl.ALL_HOST_NAMES:
        active=cl.run_on_host(host,['systemctl','is-active','--quiet','ax-cluster-direct-collector.service'],check=False)
        if active.returncode==0:
            continue
        command=['systemd-run','--unit=ax-cluster-direct-collector.service','--property=MemoryMax=2G',
                 'python3',str(BASE/'scripts/host_collector.py'),'--host',host,
                 '--out-dir',str(cl.RUN_ROOT/'hosts'/host)]
        if host=='node-meta':command.append('--has-redis')
        if host in OBJECTS:command.append('--has-rustfs')
        cl.run_on_host(host,command)
    time.sleep(3)
    report={host:cl.collector_rpc(host,{'action':'flush'}) for host in cl.ALL_HOST_NAMES}
    (cl.RUN_ROOT/'collector-start.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'collectors_ready':list(report)}),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('action',choices=['distribute','services','collectors'])
    opts=ap.parse_args()
    {'distribute':distribute,'services':prepare_services,'collectors':collectors}[opts.action]()
