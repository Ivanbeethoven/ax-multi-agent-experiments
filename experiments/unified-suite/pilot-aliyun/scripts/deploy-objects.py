#!/usr/bin/env python3
"""Start the attested RustFS build on four prepared, distinct pilot disks."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from urllib.request import ProxyHandler, build_opener


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!='ax-pilot-20261111-objects':
        raise ValueError('Owned object host required')
    mounts=[Path('/srv/ax-rustfs-data-'+str(i)) for i in range(4)]
    if not all(p.is_mount() and not list(p.iterdir()) for p in mounts) or len({p.stat().st_dev for p in mounts})!=4:
        raise ValueError('Four empty, distinct prepared block devices required')
    a.out.mkdir(parents=True,exist_ok=False)
    def run(argv,name):
        with (a.out/(name+'.log')).open('xb') as log:
            subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,check=True)
    daemon=Path('/etc/docker/daemon.json')
    if daemon.exists() or subprocess.check_output(['docker','ps','-q'],text=True).strip():
        raise ValueError('Configure a new Docker host with no running containers')
    daemon.write_text(json.dumps({'insecure-registries':['10.20.0.10:15000']})+'\n')
    run(['dockerd','--validate','--config-file',str(daemon)],'daemon-validate')
    run(['systemctl','restart','docker'],'docker-restart')
    proof=json.loads((a.assets/'provenance/rustfs-image.json').read_text())
    image='10.20.0.10:15000/ax-pilot/rustfs@'+proof['image_digest']
    run(['docker','pull',image],'pull')
    row=json.loads(subprocess.check_output(['docker','image','inspect',image]))[0]
    if row['Config']['Labels'].get('org.opencontainers.image.revision')!=proof['source_commit']:
        raise ValueError('Image source differs')
    actual=subprocess.check_output(['docker','run','--rm','--pull','never','--network','none','--entrypoint','sha256sum',image,'/usr/bin/rustfs'],text=True).split()[0]
    if actual!=proof['binary_sha256']:
        raise ValueError('Image binary differs')
    private=a.out/'private';private.mkdir(mode=0o700)
    env=private/'rustfs.env'
    with env.open('x') as f:
        os.chmod(env,0o600)
        f.write('RUSTFS_ACCESS_KEY=axpilot'+secrets.token_hex(10)+'\nRUSTFS_SECRET_KEY='+secrets.token_urlsafe(36)+'\n')
    spec=dict(mode='single-node-four-disk',image=image,
        observability=dict(cluster_id='ax-pilot-20261111-pool-a',meter_interval_seconds=1),
        experiment=dict(project_name='ax-pilot-20261111-objects-01',protocol_profile='unified-pilot-20261111',
            stage='C1-C4',cpus=6,memory_bytes=24*1024**3,layout_profile='enabled'),
        nodes=[dict(private_ip='10.20.2.163',data_mounts=list(map(str,mounts)),
            log_dir='/srv/ax-objects-0/rustfs-logs',credentials_file=str(env))])
    (a.out/'spec.json').write_text(json.dumps(spec,indent=2)+'\n')
    path=a.assets/'repo/infra/rustfs/generate_rustfs.py'
    loader=importlib.util.spec_from_file_location('pilot_rustfs_generator',path)
    module=importlib.util.module_from_spec(loader);loader.loader.exec_module(module)
    module.generate(spec,a.out/'deployment')
    compose=a.out/'deployment/rustfs1/compose.json'
    run(['docker','compose','-f',str(compose),'up','-d','--pull','never'],'start')
    cid=subprocess.check_output(['docker','compose','-f',str(compose),'ps','-q','rustfs'],text=True).strip()
    opener=build_opener(ProxyHandler({}))
    for i in range(120):
        try:
            with opener.open('http://10.20.2.163:9000/health',timeout=2) as r:
                if r.status==200:break
        except OSError:
            pass
        live=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
        if not live['State']['Running']:raise ValueError('RustFS exited; preserve logs')
        if i==119:raise ValueError('RustFS did not become ready; preserve it')
        time.sleep(1)
    live=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
    (a.out/'result.json').write_text(json.dumps(dict(passed=True,at=datetime.now(timezone.utc).isoformat(),
        container_id=cid,pid=live['State']['Pid'],image_reference=image,image_id=live['Image'],
        binary_sha256=actual,source_commit=proof['source_commit'],
        distinct_devices=[p.stat().st_dev for p in mounts],unsafe_disk_bypass=False,
        compose_sha256=hashlib.sha256(compose.read_bytes()).hexdigest(),scope='Service startup and source identity; performance qualification separate'),indent=2)+'\n')
    print((a.out/'result.json').read_text())


if __name__=='__main__':
    main()
