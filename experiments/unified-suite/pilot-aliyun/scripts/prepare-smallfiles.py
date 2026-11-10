#!/usr/bin/env python3
"""Import one complete million-file C3 base and verify its original cold reopen.

No workload cell, retry, downsampling or cloud resource creation.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,write_new
from pilot_io import fsync_import


def ref(p):return dict(path=str(p),sha256=digest(p))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--product',choices=['brewfs','juicefs'],required=True)
    for name in ('asset','plan','source','source-proof','node-tool','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--node-tool-sha256',required=True)
    p.add_argument('--database',type=int,required=True)
    p.add_argument('--attempt',required=True)
    a=p.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{3,32}',a.attempt):raise ValueError('Short explicit attempt required')
    if digest(a.node_tool)!=a.node_tool_sha256:raise ValueError('Helper changed')
    if a.asset.exists() or a.out.exists():raise ValueError('Fresh base and output required')
    plan=load(a.plan);arm='native' if a.product=='brewfs' else 'juicefs'
    cell=next(c for c in plan['cells'] if c['workload']=='smallfiles' and c['arm_id']==arm)
    source=load(a.source_proof)
    if source.get('passed') is not True or source.get('files')!=1000000 or source.get('bytes_checked')!=4096000000:
        raise ValueError('Full million-file source qualification required')
    a.out.mkdir(parents=True)
    write_new(a.out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),cell_id=cell['id'],
              source=str(a.source),source_proof=ref(a.source_proof),node_tool=ref(a.node_tool),
              purpose='asset-preparation',fsync_preparation_workers=8,automatic_retry=False))
    Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').write_text(str(os.getpid()))
    active=None;failure=None
    def run(name,argv):
        directory=a.out/name;directory.mkdir();begin=time.monotonic()
        write_new(directory/'command.json',dict(argv=list(map(str,argv)),automatic_retry=False))
        with (directory/'stdout').open('xb') as out,(directory/'stderr').open('xb') as err:
            child=subprocess.Popen(list(map(str,argv)),stdout=out,stderr=err)
            write_new(directory/'process.json',dict(pid=child.pid));code=child.wait()
        write_new(directory/'exit.json',dict(exit_code=code,wall_seconds=time.monotonic()-begin))
        if code:raise RuntimeError('Inspect '+str(directory))
    def helper(action,name,**fields):
        argv=['python3',a.node_tool,action,'--asset',a.asset]
        for k,v in fields.items():
            if v is True:argv+=['--'+k.replace('_','-')]
            elif v is not None:argv+=['--'+k.replace('_','-'),str(v)]
        run(name,argv)
    try:
        helper('asset-init','asset-init',product=a.product,database=a.database,plan=a.plan,cell=cell['id'],out=a.out/'asset-init-proof')
        point=Path(load(a.asset/'asset.json')['mount_point']);base=point/'base'
        helper('draft-mount','draft',cell=cell['id'],out=a.out/'mount')
        state=Path('/srv/ax-client/m')/a.attempt
        helper('start','mount-start',spec=a.out/'mount/mount.reviewed.json',out=state,join_slot=True);active=state
        argv=(['/srv/ax-client/assets/bin/juicefs','sync','--dirs','--perms','--links',str(a.source)+'/',str(base)+'/']
              if a.product=='juicefs' else ['cp','-a','--reflink=never','--',a.source,base])
        run('import',argv)
        begin=time.monotonic();files=fsync_import(base)
        write_new(a.out/'import-fsync.json',dict(passed=files==1000000,files=files,wall_seconds=time.monotonic()-begin,workers=8))
        if files!=1000000:raise ValueError('Imported file count differs')
        helper('debt','debt',cell=cell['id'],out=a.out/'debt-proof')
        helper('stop','normal-stop',mount_run=active,out=a.out/'normal-stop.json');active=None
        helper('draft-mount','cold-draft',cell=cell['id'],out=a.out/'cold-mount')
        cold=Path(str(state)+'-cold')
        helper('start','cold-start',spec=a.out/'cold-mount/mount.reviewed.json',out=cold,join_slot=True);active=cold
        run('full-base-check',['/srv/ax-client/assets/bin/smallfiles','verify-base','--root',base,
                             '--output',a.out/'base-check','--files','1000000'])
        checked=load(a.out/'base-check/result.json')
        if checked.get('passed') is not True or checked.get('files')!=1000000 or checked.get('bytes_checked')!=4096000000:
            raise ValueError('Full original cold base differs')
        helper('stop','cold-stop',mount_run=active,out=a.out/'cold-stop.json');active=None
        write_new(a.out/'result.json',dict(passed=True,cell_id=cell['id'],asset=ref(a.asset/'asset.json'),
                  source=ref(a.source_proof),full_cold_base_check=ref(a.out/'base-check/result.json'),
                  files=files,bytes_checked=checked['bytes_checked'],normal_exits=[ref(a.out/n) for n in ['normal-stop.json','cold-stop.json']],
                  scope='One full imported C3 base and original backend cold reopen; preparation cost, not a performance sample'))
    except Exception as error:
        failure=error
        write_new(a.out/'failed.json',dict(passed=False,error=str(error),active_supervisor=str(active) if active else None,automatic_retry=False))
    finally:
        if failure and active:helper('stop','failure-normal-stop',mount_run=active,out=a.out/'failure-normal-stop.json')
    if failure:raise failure


if __name__=='__main__':main()
