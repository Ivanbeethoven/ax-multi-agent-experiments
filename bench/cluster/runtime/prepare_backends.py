"""Start the two independent four-node RustFS services before measurements."""
import json
from pathlib import Path
import time

import cluster_lib as cl
import rustfs_layout_tools as layout


def prepare():
    cl.select_rustfs_backend('dynamic')
    dynamic = cl.RUSTFS_BACKENDS['dynamic']
    for host in dynamic['nodes']:
        cl.run_on_host(host, ['docker', 'start', dynamic['container']])
    nodes = layout.discover_rustfs_nodes()
    official = cl.RUSTFS_BACKENDS['official']
    for host in dynamic['nodes']:
        settings = nodes[host]
        source = """
import json,subprocess,sys
from pathlib import Path
spec=json.loads(sys.stdin.read())
p=Path(spec['compose_files']);config=json.loads(p.read_text())
service=config['services']['rustfs'];env=service['environment']
if isinstance(env,list):env=dict(item.split('=',1) for item in env)
names=['RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE','RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED',
       'RUSTFS_EC_READ_QUANTUM_ENABLE','RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED']
changed=any(env.get(name)!='true' for name in names)
for name in names:env[name]='true'
service['environment']=env
if changed:
 p.write_text(json.dumps(config,indent=2))
 subprocess.run(['docker','compose','-f',str(p),'up','-d','--no-deps','--force-recreate','rustfs'],check=True)
subprocess.run(['docker','start',spec['otel_container']],check=True,capture_output=True)
dropin=Path('/etc/systemd/system')/(spec['unit']+'.d')
dropin.mkdir(parents=True,exist_ok=True)
(dropin/'resources.conf').write_text('[Service]\\nCPUQuota='+str(spec['cpu_quota'])+'%\\nMemoryMax='+str(spec['memory_gib'])+'G\\nMemorySwapMax=0\\n')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','start',spec['unit']],check=True)
version=subprocess.check_output([spec['process_executable'],'--version'],text=True)
if spec['version'] not in version:raise RuntimeError('Official RustFS executable version differs')
print(json.dumps({'official_version':version.strip()}))
"""
        command = ['python3', '-c', source]
        result = cl.run_on_host(host, command, input_bytes=json.dumps({**settings, **official, 'cpu_quota': 800 if host == 'node-store-4' else 1600, 'memory_gib': 24 if host == 'node-store-4' else 32}).encode(), timeout=180)
        record = cl.RUN_ROOT / 'versions' / (host + '-official-rustfs.json')
        record.write_text(json.dumps(json.loads(result.stdout.decode().strip().splitlines()[-1]), indent=2)+'\n')
    for attempt in range(60):
        try:
            for backend in cl.RUSTFS_BACKENDS.values():
                for endpoint in backend['endpoints']:
                    cl.make_s3_client(endpoint).list_buckets()
            break
        except Exception:
            if attempt == 59:
                raise
            time.sleep(2)
    (cl.RUN_ROOT / 'versions/rustfs-backends.json').write_text(json.dumps(cl.RUSTFS_BACKENDS, indent=2)+'\n')


if __name__ == '__main__':
    prepare()
