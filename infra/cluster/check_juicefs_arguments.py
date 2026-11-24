"""Check real JuiceFS launch arguments using a local-only backend on node-meta."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

REPO=Path('/srv/ax-cluster/direct/repo')
sys.path.insert(0,str(REPO))
from bench.cluster.mounts import render


def main():
    parent=Path('/srv/ax-cluster/direct/functional/juicefs-arguments')
    attempt=1
    while (parent/f'attempt-{attempt:02d}').exists():attempt+=1
    root=parent/f'attempt-{attempt:02d}'
    root.mkdir(parents=True)
    print(json.dumps({'directory':str(root)}),flush=True)
    redis_unit='ax-direct-local-redis.service'
    mount_unit='ax-direct-local-juicefs.service'
    binary='/srv/ax-cluster/direct/bin/juicefs'
    profiles=json.loads((REPO/'configs/mount-profiles.json').read_text())
    cluster=json.loads((REPO/'configs/cluster.json').read_text())
    request=dict(id='arguments',phase='measure',engine='juicefs',host='cluster-node-2',slot=40,
                 workspace='test',parent_workspace='none',purpose='argument-check',binary=binary,
                 cache_state='cold',windows=['measure'],root=str(root))
    record=render(request,profiles,cluster)
    mount=Path(record['mount_directory']);mount.mkdir(parents=True,exist_ok=True)
    (root/'objects').mkdir(exist_ok=True)
    manifests=root/'mounts.json';manifests.write_text(json.dumps({'mounts':[record]}))
    log=(root/'commands.log').open('a')

    def run(command):
        return subprocess.run(command,check=True,stdout=log,stderr=subprocess.STDOUT)

    try:
        run(['systemd-run','--unit='+redis_unit,'--property=MemoryMax=256M','redis-server',
             '--bind','127.0.0.1','--port','26379','--save','','--appendonly','no'])
        time.sleep(0.5)
        run([binary,'--no-agent','format','--storage=file','--bucket='+str(root/'objects'),
             'redis://127.0.0.1:26379/0','argument-check'])
        argv=[binary,'--no-agent','mount',*[f'--{key}={value}' for key,value in record['options'].items()],
              'redis://127.0.0.1:26379/0',str(mount)]
        run(['systemd-run','--unit='+mount_unit,'--slice=ax-clients.slice',
             '--property=MemoryMax=4G','--property=MemorySwapMax=0','--property=CPUQuota=200%',*argv])
        deadline=time.monotonic()+30
        while not os.path.ismount(mount):
            if time.monotonic()>deadline:raise RuntimeError('Local mount readiness timed out')
            time.sleep(0.2)
        relative=subprocess.check_output(['systemctl','show',mount_unit,'--property=ControlGroup','--value'],text=True).strip()
        group=Path('/sys/fs/cgroup')/relative.lstrip('/')
        pids=[int(pid) for pid in (group/'cgroup.procs').read_text().split()]
        worker=max(pid for pid in pids if Path(f'/proc/{pid}/exe').resolve()==Path(binary).resolve())
        raw=Path(f'/proc/{worker}/cmdline').read_bytes()
        (root/'worker-argv.json').write_text(json.dumps([s.decode(errors='replace') for s in raw.split(b'\0') if s],indent=2))
        unit=Path('/run/systemd/transient')/mount_unit
        (root/'systemd-unit.txt').write_text(unit.read_text())
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        metrics=opener.open('http://127.0.0.1:20040/metrics',timeout=3).read()
        (root/'metrics.txt').write_bytes(metrics)
        output=root/'audit.json';output.unlink(missing_ok=True)
        p=subprocess.run(['python3','-m','bench.cluster.audit_mount','--mounts',str(manifests),
                          '--id','arguments','--pid',str(worker),'--cgroup',str(group),
                          '--stats',str(root/'metrics.txt'),'--out',str(output)],cwd=REPO,capture_output=True,text=True)
        (root/'audit-output.txt').write_text(p.stdout+p.stderr)
        print(json.dumps({'returncode':p.returncode,'worker_argc':len(raw.split(b'\0')),
                          'result':p.stdout,'error':p.stderr}),flush=True)
        if p.returncode:raise RuntimeError('Launch argument check failed')
    finally:
        if os.path.ismount(mount):subprocess.run(['fusermount3','-u',str(mount)],stdout=log,stderr=log)
        subprocess.run(['systemctl','stop',mount_unit,redis_unit],stdout=log,stderr=log)
        log.close()


if __name__=='__main__':main()
