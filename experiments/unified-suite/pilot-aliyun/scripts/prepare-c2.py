#!/usr/bin/env python3
"""Qualify one actual C2 route with its complete trace and original cold readback.

Creates a new asset only; no formal performance cell, model call or retry.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO));sys.path.insert(0,str(REPO/'scripts'))
from bench.runtime.common import digest,load,write_new
from full_trace_common import tree


def ref(path):return dict(path=str(path),sha256=digest(path))


def sync_tree(root):
    # Record this extra barrier separately from replay timing. Verifying SQLite
    # uses copies; this only fsyncs existing files and directories.
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as f:os.fsync(f.fileno())
    for path in [*sorted((p for p in root.rglob('*') if p.is_dir() and not p.is_symlink()),reverse=True),root]:
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--product',choices=['brewfs','juicefs'],required=True)
    for name in ('asset','plan','case','node-tool','qualifier','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--database',type=int,required=True)
    p.add_argument('--node-tool-sha256',required=True)
    p.add_argument('--qualifier-sha256',required=True)
    p.add_argument('--attempt',default='qual-01')
    p.add_argument('--portable-terminal-probe',action='store_true')
    p.add_argument('--replay-binary',type=Path,default=Path('/srv/ax-client/assets/bin/full-fuse-replay'))
    a=p.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{3,32}',a.attempt):raise ValueError('Short explicit attempt required')
    if digest(a.node_tool)!=a.node_tool_sha256 or digest(a.qualifier)!=a.qualifier_sha256:
        raise ValueError('Preparation code changed')
    if a.asset.exists() or a.out.exists():raise ValueError('New asset and qualification output required')
    plan=load(a.plan);arm='native' if a.product=='brewfs' else 'juicefs'
    cell=next(c for c in plan['cells'] if c['workload']=='trace' and c['arm_id']==arm)
    a.out.mkdir(parents=True)
    write_new(a.out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),product=a.product,
              cell_id=cell['id'],case=str(a.case),node_tool=ref(a.node_tool),qualifier=ref(a.qualifier),
              events=ref(a.case/'capture/events.jsonl'),automatic_retry=False,purpose='full-route-qualification'))
    Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').write_text(str(os.getpid()))
    def run(name,argv):
        out=a.out/name;out.mkdir();begin=time.monotonic()
        write_new(out/'command.json',dict(argv=list(map(str,argv)),automatic_retry=False))
        with (out/'stdout').open('xb') as stdout,(out/'stderr').open('xb') as stderr:
            child=subprocess.Popen(list(map(str,argv)),stdout=stdout,stderr=stderr)
            write_new(out/'process.json',dict(pid=child.pid));code=child.wait()
        write_new(out/'exit.json',dict(exit_code=code,wall_seconds=time.monotonic()-begin))
        if code:raise RuntimeError('Inspect '+str(out))
    def helper(action,name,**fields):
        args=['python3',a.node_tool,action,'--asset',a.asset]
        for key,value in fields.items():
            if value is True:args+=['--'+key.replace('_','-')]
            elif value is not None:args+=['--'+key.replace('_','-'),str(value)]
        run(name,args)
    active=None;failure=None;stopped=[]
    try:
        helper('asset-init','asset-init',product=a.product,database=a.database,plan=a.plan,cell=cell['id'],out=a.out/'asset-init-proof')
        asset=load(a.asset/'asset.json');point=Path(asset['mount_point'])
        state=Path('/srv/ax-client/m')/('c2-'+arm+'-'+a.attempt)
        helper('draft-mount','draft',cell=cell['id'],out=a.out/'mount')
        helper('start','mount-start',spec=a.out/'mount/mount.reviewed.json',out=state,join_slot=True);active=state
        base=point/'base';child=point/'strict-01'
        if a.product=='juicefs':
            run('import',['/srv/ax-client/assets/bin/juicefs','sync','--dirs','--perms','--links',str(a.case/'initial')+'/',str(base)+'/'])
        else:run('import',['cp','-a','--reflink=never','--',a.case/'initial',base])
        begin=time.monotonic();sync_tree(base)
        write_new(a.out/'import-sync.json',dict(passed=True,wall_seconds=time.monotonic()-begin))
        actual=tree(base);expected=load(a.case/'initial-tree.json')
        if actual!=expected:
            write_new(a.out/'import-tree-differences.json',{k:dict(expected=expected.get(k),actual=actual.get(k))
                      for k in sorted(actual.keys()|expected.keys()) if actual.get(k)!=expected.get(k)})
            raise ValueError('Imported full initial tree differs')
        write_new(a.out/'base-check.json',dict(passed=True,expected=ref(a.case/'initial-tree.json'),root=str(base)))
        argv=(['/srv/ax-client/assets/bin/juicefs','clone','-p',base,child] if a.product=='juicefs'
              else ['cp','-a','--reflink=never','--',base,child])
        run('create-strict-child',argv);sync_tree(child)
        portability=['--portable-terminal-probe'] if a.portable_terminal_probe else []
        run('strict',['python3',a.qualifier,'--case',a.case,'--binary',a.replay_binary,
                      '--root',child,'--out',a.out/'strict-proof','--prepared-root',*portability])
        begin=time.monotonic();sync_tree(child)
        write_new(a.out/'post-replay-sync.json',dict(passed=True,wall_seconds=time.monotonic()-begin,
                  scope='Extra fsync of all final files after strict qualification; not part of replay timing'))
        helper('debt','debt',cell=cell['id'],out=a.out/'debt-proof')
        helper('stop','normal-stop',mount_run=active,out=a.out/'normal-stop.json');stopped.append(ref(a.out/'normal-stop.json'));active=None
        helper('draft-mount','cold-draft',cell=cell['id'],out=a.out/'cold-mount')
        cold=Path('/srv/ax-client/m')/('c2-'+arm+'-'+a.attempt+'-cold')
        helper('start','cold-start',spec=a.out/'cold-mount/mount.reviewed.json',out=cold,join_slot=True);active=cold
        run('cold-readback',['python3',a.qualifier,'--case',a.case,'--binary',a.replay_binary,
                 '--root',child,'--out',a.out/'cold-proof','--verify-only','--replay-result',a.out/'strict-proof/replay.json',*portability])
        if tree(base)!=load(a.case/'initial-tree.json'):raise ValueError('Parent changed during strict replay')
        helper('stop','cold-stop',mount_run=active,out=a.out/'cold-stop.json');stopped.append(ref(a.out/'cold-stop.json'));active=None
        write_new(a.out/'result.json',dict(passed=True,at=datetime.now(timezone.utc).isoformat(),product=a.product,
                  cell_id=cell['id'],strict=ref(a.out/'strict-proof/verification.json'),cold_readback=ref(a.out/'cold-proof/verification.json'),
                  base=ref(a.out/'base-check.json'),normal_exits=stopped,asset=ref(a.asset/'asset.json'),
                  scope='Complete strict trace on the actual filesystem, isolated initial child and original backend cold readback; no performance acceptance'))
        print(json.dumps(dict(passed=True,product=a.product)))
    except Exception as error:
        failure=error
        write_new(a.out/'failed.json',dict(passed=False,error_type=type(error).__name__,error=str(error),
                  at=datetime.now(timezone.utc).isoformat(),replay_retried=False,active_supervisor=str(active) if active else None))
    finally:
        if failure and active:
            # Every foreground command above has actually returned. The native
            # supervisor owns the live mount PID and waits for its normal drain.
            helper('stop','failure-normal-stop',mount_run=active,out=a.out/'failure-normal-stop.json')
    if failure:raise failure


if __name__=='__main__':main()
