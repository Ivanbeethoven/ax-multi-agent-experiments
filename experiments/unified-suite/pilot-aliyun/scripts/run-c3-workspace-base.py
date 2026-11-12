#!/usr/bin/env python3
"""Reserve pool A and run one bounded current-engine base check; never starts ECS."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time


CONTROL = Path('/srv/ax-pilot-control')
CANONICAL = CONTROL/'parallel-control-01'
sys.path.insert(0,str(CONTROL/'code-12'))
from bench.runtime.pilot_pools import active_conflicts, resource_guard

IMAGE = 'sha256:942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec'
REVISION = 'f4ed202508f2153afb7956bae6f7799b60eccd00'
FLAGS = ['-F',str(CANONICAL/'ssh.conf'),'-oBatchMode=yes','-oConnectTimeout=5']


def run(args):
    out = Path(__file__).resolve().parent
    if out.parent != CONTROL/'preparation' or not out.name.startswith('c3-current-base-'):
        raise ValueError('Dedicated coordinator preparation directory required')
    tag = out.name
    if not 0 <= args.workers <= 32 or not 60 <= args.verify_seconds <= 900:
        raise ValueError('Bounded verifier workers and duration required')
    worker = out/'qualify-c3-workspace-base.py'
    script = worker.read_bytes()
    if hashlib.sha256(script).hexdigest()!=args.worker_sha256:
        raise ValueError('Staged worker script digest mismatch')
    plan = json.loads((CANONICAL/'suite/plan.json').read_text())
    cell = next(c for c in plan['cells'] if c['pool']=='pool-a' and c['workload']=='smallfiles')
    controls = sorted(p.parent for p in CONTROL.glob('*/state.sqlite'))
    if len(controls)<5 or CANONICAL not in controls:
        raise ValueError('Existing coordinator control set missing')

    def ssh(role, command, **kw):
        return subprocess.run(['ssh',*FLAGS,'pool-a-'+role,command],check=True,**kw)

    def setup(role):
        deadline = time.monotonic()+130
        while time.monotonic()<deadline:
            try:
                ssh(role,'true',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
                break
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
                time.sleep(3)
        else:
            raise RuntimeError(role+' did not become reachable')
        with (out/(role+'-restore.log')).open('xb') as stream:
            restore = f'python3 /srv/ax-receipts/a-pool-up/a-pool-up.py {role} --out /srv/ax-receipts/a-pool-up/{role}-{tag}'
            ssh(role,'timeout --kill-after=8s 105s '+restore,stdout=stream,stderr=subprocess.STDOUT,timeout=120)

    with ExitStack() as stack:
        roots=set()
        for ctl in controls:
            locks=(ctl/'runner-locks').resolve(strict=True)
            if locks not in roots:
                stack.enter_context(resource_guard(ctl,cell,plan)); roots.add(locks)
        if controls!=sorted(p.parent for p in CONTROL.glob('*/state.sqlite')) or any(active_conflicts(c,cell,plan) for c in controls):
            raise ValueError('Pool A or a shared physical resource has an active lease')
        with (out/'locks-held.json').open('x') as stream:
            json.dump(dict(pool='pool-a',controls=list(map(str,controls)),locks=sorted(map(str,roots))),stream)
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(setup,('objects','metadata','client')))
        image = ssh('objects', "docker inspect -f '{{.Image}} {{.State.Running}}' ax-pilot-20261111-objects-01-rustfs-1; "
            "docker image inspect -f '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' "+IMAGE,
            capture_output=True,text=True,timeout=12).stdout.splitlines()
        if image!=[IMAGE+' true',REVISION]:
            raise ValueError('Actual running RustFS image/revision differs')
        (out/'rustfs-image.txt').write_text('\n'.join(image)+'\n')
        slot_spec = CANONICAL/'hardware-inputs-01/data/pool-a-client-spec.json'
        slot_bytes = slot_spec.read_bytes()
        if hashlib.sha256(slot_bytes).hexdigest()!='3592411238e1af9eb21980c78d61394c24ea33f74d6452babd335a497d0b5d55':
            raise ValueError('Exact original slot spec differs')
        remote = '/srv/ax-receipts/'+tag
        ssh('client','mkdir -m 700 '+remote,timeout=12)
        def upload(name, data):
            ssh('client','set -C; cat > '+remote+'/'+name,input=data,timeout=12)
        upload(worker.name,script)
        upload('slot.json',slot_bytes)
        ssh('client','test -d /sys/fs/cgroup/ax-slot-1 || python3 /srv/ax-client/code-12/experiments/unified-suite/'
            f'pilot-aliyun/scripts/configure-node.py slot --spec {remote}/slot.json --out {remote}/slot-setup',timeout=15)
        cmd = ['timeout','--kill-after=25s',str(args.verify_seconds+105)+'s','python3','-B',remote+'/'+worker.name,
               '--tag',tag,'--database',str(args.database),'--workers',str(args.workers),
               '--verify-seconds',str(args.verify_seconds)]
        try:
            with (out/'worker.log').open('xb') as stream:
                ssh('client',shlex.join(cmd),stdout=stream,stderr=subprocess.STDOUT,timeout=args.verify_seconds+140)
        finally:
            for name in ('intent.json','init.stdout','init.stderr','ready.json','mount.log','mount-exit.json','verify.log',
                         'verify/result.json','result.json'):
                source = '/srv/ax-client/preparation/'+tag+'/'+name
                proc = subprocess.run(['ssh',*FLAGS,'pool-a-client','test -f '+source+' && cat '+source],
                                      capture_output=True,timeout=10)
                if proc.returncode==0:
                    with (out/('worker-'+name.replace('/','-'))).open('xb') as stream: stream.write(proc.stdout)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker-sha256',required=True)
    p.add_argument('--database',type=int,required=True)
    p.add_argument('--workers',type=int,default=0)
    p.add_argument('--verify-seconds',type=int,default=480)
    run(p.parse_args())
