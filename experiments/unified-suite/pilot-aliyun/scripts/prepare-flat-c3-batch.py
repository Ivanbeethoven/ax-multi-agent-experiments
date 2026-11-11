#!/usr/bin/env python3
"""Prepare exactly one C3 flat base on an idle, already deployed pool."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime import pilot_context
from bench.runtime.common import digest, load, pinned, write_new
from bench.runtime.pilot_pools import active_conflicts, alias, pool_of, resource_guard


def ref(path):
    return dict(path=str(path),sha256=digest(path))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    a=p.parse_args()
    req=dict(path=str(a.request),sha256=a.sha256)
    s=load(pinned(req))
    if s['schema']!='pilot-flat-c3-base-v1' or s['reviewed'] is not True:
        raise ValueError('Explicit reviewed C3 base request required')
    control=Path(s['control'])
    runtime=pilot_context.select(control,s['runtime'])
    plan=load(control/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['id']==s['cell_id'])
    pool=pool_of(cell)
    if cell['workload']!='smallfiles' or cell['arm_id'] not in ('native','juicefs') or cell['params']['files']!=100000:
        raise ValueError('A declared flat 100,000-file C3 condition is required')
    tools={name:pinned(pin) for name,pin in s['controller_tools'].items()}
    out=Path(s['out'])
    out.mkdir(parents=True,exist_ok=False)
    write_new(out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),request=req,cell=cell['id'],automatic_retry=False))
    ssh=['ssh','-F',str(runtime/'ssh.conf')]

    def remote(role,*argv):
        return [*ssh,alias(pool,role),shlex.join(list(map(str,argv)))]

    def command(name,argv):
        folder=out/name
        folder.mkdir()
        argv=list(map(str,argv))
        write_new(folder/'intent.json',dict(argv=argv,automatic_retry=False))
        with (folder/'stdout').open('xb') as stdout,(folder/'stderr').open('xb') as stderr:
            code=subprocess.run(argv,stdout=stdout,stderr=stderr).returncode
        write_new(folder/'exit.json',dict(exit_code=code))
        if code:
            raise RuntimeError('Inspect '+str(folder))
        return (folder/'stdout').read_text()

    try:
        with resource_guard(control,cell,plan):
            if any(active_conflicts(root,cell,plan) for root in [control,*map(Path,s['peer_controls'])]):
                raise ValueError('Physical pool lease remains')
            if command('agent-slot',remote('client','cat','/sys/fs/cgroup/ax-slot-1/cgroup.procs')).strip():
                raise ValueError('Agent slot not empty')
            if command('redis-db',remote('metadata','redis-cli','--raw','-n',s['database'],'DBSIZE')).strip()!='0':
                raise ValueError('Dedicated Redis database is not empty')
            tools_on_node=s['node_files']
            for name,pin in tools_on_node.items():
                actual=command('verify-'+name,remote('client','sha256sum',pin['path'])).split()[0]
                if actual!=pin['sha256']:
                    raise ValueError('Node source changed: '+name)
            command('latest',[sys.executable,tools['latest'],'--manifest',control/'provenance/build-manifest.json','--out',out/'latest.json'])
            runtime_args=['--runtime-control',s['runtime']['path'],'--runtime-control-sha256',s['runtime']['sha256']]
            snapshot=[sys.executable,tools['capacity'],'snapshot','--control',control,'--pool',pool,
                      '--objects-container',s['objects_container'],*runtime_args]
            command('capacity-before',[*snapshot,'--out',out/'before.json'])
            before=load(out/'before.json')
            for node in before['nodes'].values():
                if any(d['available_bytes']<d['total_bytes']*.2 or d['available_inodes']<d['total_inodes']*.2 for d in node['disks']):
                    raise ValueError('Existing disks lack the minimum reserve before import')
            n=tools_on_node
            worker=[n['prepare']['path'],'--product',cell['arm']['product'],'--plan',n['plan']['path'],
                '--asset',s['asset'],'--source',s['source'],'--source-proof',n['source_proof']['path'],
                '--node-tool',n['node']['path'],'--node-tool-sha256',n['node']['sha256'],
                '--database',s['database'],'--attempt',s['attempt'],'--out',s['route'],
                '--inventory',n['inventory']['path']]
            for name in ('filesystem-binary','verifier','copier'):
                if name in n:
                    worker+=['--'+name,n[name]['path'],'--'+name+'-sha256',n[name]['sha256']]
            command('import-cold-verify',remote('client','python3',*worker))
            command('capacity-after',[*snapshot,'--before',out/'before.json','--out',out/'after.json'])
            budget=out/'capacity.json'
            command('budget',[sys.executable,tools['capacity'],'budget','--before',out/'before.json','--after',
                out/'after.json','--extra-full-copies','7','--reserve','.2','--out',budget])
            collect=[sys.executable,tools['collect'],'--control',control,'--shared-qualification',s['shared_qualification'],
                '--capacity-check',budget,'--product',cell['arm']['product'],'--route',s['route'],'--asset',s['asset'],
                '--binary',n['binary']['path'],'--verifier',n['verifier']['path'],'--out',s['qualification'],*runtime_args]
            if 'copier' in n:
                collect+=['--copier',n['copier']['path']]
            command('collect',collect)
            write_new(out/'result.json',dict(passed=True,cell_id=cell['id'],capacity=ref(budget),qualification=s['qualification'],
                preparation_only=True,formal_started=False,automatic_retry=False))
    except Exception as error:
        write_new(out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False))
        raise


if __name__=='__main__':
    main()
