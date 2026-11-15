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
import stat
import subprocess
import sys
import time

def runtime_root(argv):
    value=next((word.split('=',1)[1] for word in argv if word.startswith('--node-tool=')),None)
    if value is None and '--node-tool' in argv:
        index=argv.index('--node-tool')+1
        if index<len(argv):value=argv[index]
    return Path(value).resolve(strict=True).parents[4] if value else Path(__file__).resolve().parents[4]


REPO=runtime_root(sys.argv[1:])
sys.path.insert(0,str(REPO));sys.path.insert(0,str(REPO/'scripts'))
from bench.runtime.common import digest,load,write_new,pinned
from full_trace_common import tree


def ref(path):return dict(path=str(path),sha256=digest(path))


def initial_hardlinks(root):
    groups={};counts={}
    for path in root.rglob('*'):
        value=path.lstat()
        if stat.S_ISREG(value.st_mode):
            key=(value.st_dev,value.st_ino)
            groups.setdefault(key,[]).append(path.relative_to(root).as_posix());counts[key]=value.st_nlink
    if any(counts[key]!=len(names) for key,names in groups.items()):
        raise ValueError('Initial hardlink group leaves the complete input root')
    return sorted(sorted(names) for names in groups.values() if len(names)>1)


def preflight_case(case):
    """Reject a damaged local source before any asset, output or mount exists."""
    begin=time.monotonic();expected=load(case/'initial-tree.json')
    if tree(case/'initial')!=expected:
        raise ValueError('Initial source tree differs; restore a new case before asset preparation')
    links=initial_hardlinks(case/'initial')
    return dict(passed=True,initial_tree=ref(case/'initial-tree.json'),entries=len(expected),initial_hardlink_groups=links,
                wall_seconds=time.monotonic()-begin,scope='Read-only source paths, bytes, modes and link targets before asset creation')


def preflight_replay(binary,out,portable_terminal_probe=False,cache_parent_fds=False):
    """Exercise the requested CLI policy before allocating a backend or mount."""
    out.mkdir(parents=True)
    root=out/'root';root.mkdir();(root/'file').write_bytes(b'right')
    blobs=out/'blobs';blobs.mkdir()
    rows=[('init',[],None),('open',['/file',0],7),
          ('read',['/file',5,0,7],dict(len=5,sha256=hashlib.sha256(b'right').hexdigest())),
          ('release',['/file',7],0),('destroy',[],None)]
    trace=out/'events.jsonl'
    with trace.open('x') as stream:
        for index,(op,args,result) in enumerate(rows):
            stream.write(json.dumps(dict(seq=index+1,op=op,args=args,result=result,errno=0))+'\n')
    options=[]
    if portable_terminal_probe:options.append('--portable-terminal-probe')
    if cache_parent_fds:options.append('--cache-parent-fds')
    argv=list(map(str,[binary,trace,blobs,root,out/'replay.json',*options]))
    before=ref(binary);begin=time.monotonic()
    write_new(out/'command.json',dict(argv=argv,binary=before,automatic_retry=False,
              purpose='Five-byte CLI capability check on a local filesystem; no measured workload'))
    with (out/'stdout').open('xb') as stdout,(out/'stderr').open('xb') as stderr:
        process=subprocess.run(argv,stdout=stdout,stderr=stderr)
    write_new(out/'exit.json',dict(exit_code=process.returncode,wall_seconds=time.monotonic()-begin))
    if process.returncode:raise ValueError('Replay CLI capability check failed; inspect '+str(out))
    report=load(out/'replay.json')
    expected=dict(valid=True,completed=True,operations=5,actual_read_bytes=5,actual_write_bytes=0,remaining_handles=0,
                  mode='strict-portable-terminal-probe' if portable_terminal_probe else 'strict',
                  path_resolution='parent-fd-cache' if cache_parent_fds else 'full-path')
    if any(report.get(key)!=value for key,value in expected.items()) or ref(binary)!=before:
        raise ValueError('Replay capability report or pinned binary differs; inspect '+str(out))
    result=dict(passed=True,binary=before,report=ref(out/'replay.json'),trace=ref(trace),
                portable_terminal_probe=portable_terminal_probe,cache_parent_fds=cache_parent_fds)
    write_new(out/'verification.json',result)
    return result


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
    p.add_argument('--cache-parent-fds',action='store_true')
    p.add_argument('--replay-binary',type=Path,default=Path('/srv/ax-client/assets/bin/full-fuse-replay'))
    p.add_argument('--filesystem-binary',type=Path)
    p.add_argument('--filesystem-binary-sha256')
    p.add_argument('--inventory',type=Path)
    a=p.parse_args()
    if bool(a.filesystem_binary)!=bool(a.filesystem_binary_sha256):
        p.error('filesystem-binary requires its exact SHA256')
    if not re.fullmatch(r'[a-z0-9-]{3,32}',a.attempt):raise ValueError('Short explicit attempt required')
    if digest(a.node_tool)!=a.node_tool_sha256 or digest(a.qualifier)!=a.qualifier_sha256:
        raise ValueError('Preparation code changed')
    if a.asset.exists() or a.out.exists():raise ValueError('New asset and qualification output required')
    plan=load(a.plan);arm='native' if a.product=='brewfs' else 'juicefs'
    cell=next(c for c in plan['cells'] if c['workload']=='trace' and c['arm_id']==arm)
    if a.case.name!=cell['params']['trace']:raise ValueError('Exact declared trace case name required')
    source_check=preflight_case(a.case)
    replay_check=preflight_replay(a.replay_binary,a.out.with_name(a.out.name+'-replay-preflight'),
                                 a.portable_terminal_probe,a.cache_parent_fds)
    a.out.mkdir(parents=True)
    write_new(a.out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),product=a.product,
              cell_id=cell['id'],case=str(a.case),node_tool=ref(a.node_tool),qualifier=ref(a.qualifier),
              events=ref(a.case/'capture/events.jsonl'),source_preflight=source_check,replay_preflight=replay_check,
              automatic_retry=False,purpose='full-route-qualification'))
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
        helper('asset-init','asset-init',product=a.product,database=a.database,plan=a.plan,cell=cell['id'],out=a.out/'asset-init-proof',
               filesystem_binary=a.filesystem_binary,filesystem_binary_sha256=a.filesystem_binary_sha256,inventory=a.inventory)
        asset=load(a.asset/'asset.json');point=Path(asset['mount_point'])
        filesystem_binary=str(pinned(asset['filesystem_binary'])) if asset.get('filesystem_binary') else '/srv/ax-client/assets/bin/'+a.product
        state=Path('/srv/ax-client/m')/('c2-'+arm+'-'+a.attempt)
        helper('draft-mount','draft',cell=cell['id'],out=a.out/'mount')
        helper('start','mount-start',spec=a.out/'mount/mount.reviewed.json',out=state,join_slot=True);active=state
        base=point/'base';child=point/'strict-01'
        if a.product=='juicefs':
            run('import',[filesystem_binary,'sync','--dirs','--perms','--links',str(a.case/'initial')+'/',str(base)+'/'])
        else:run('import',['cp','-a','--reflink=never','--',a.case/'initial',base])
        begin=time.monotonic();sync_tree(base)
        write_new(a.out/'import-sync.json',dict(passed=True,wall_seconds=time.monotonic()-begin))
        actual=tree(base);expected=load(a.case/'initial-tree.json')
        if actual!=expected:
            write_new(a.out/'import-tree-differences.json',{k:dict(expected=expected.get(k),actual=actual.get(k))
                      for k in sorted(actual.keys()|expected.keys()) if actual.get(k)!=expected.get(k)})
            raise ValueError('Imported full initial tree differs')
        if initial_hardlinks(base)!=source_check['initial_hardlink_groups']:
            raise ValueError('Imported initial hardlink topology differs from source preflight')
        write_new(a.out/'base-check.json',dict(passed=True,expected=ref(a.case/'initial-tree.json'),root=str(base)))
        argv=([filesystem_binary,'clone','-p',base,child] if a.product=='juicefs'
              else ['cp','-a','--reflink=never','--',base,child])
        run('create-strict-child',argv);sync_tree(child)
        portability=['--portable-terminal-probe'] if a.portable_terminal_probe else []
        if a.cache_parent_fds:portability+=['--cache-parent-fds']
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
