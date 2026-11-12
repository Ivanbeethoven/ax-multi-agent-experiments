#!/usr/bin/env python3
"""Bind a live build to its clean source, then verify its completed outputs.

Capture may run before spawning Cargo (future wrappers) or while it is running.
The receipt explicitly records which occurred; it never invents a build start.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

SOURCES={'brewfs':('https://github.com/brewfs/brewfs.git','codex/packed-prefetch'),
         'rustfs':('https://github.com/Ivanbeethoven/rustfs.git','feat/dynamic-block-size-rebased')}
FILES={'brewfs':['brewfs','packed_snapshot_fixture'],'rustfs':['rustfs']}


def capture(argv):return subprocess.check_output(argv,text=True).strip()
def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(path,obj):
    with path.open('x') as f:json.dump(obj,f,indent=2);f.write('\n')


def source(root,component):
    path=root/'src'/component;url,branch=SOURCES[component]
    if capture(['git','-C',str(path),'status','--porcelain']):raise ValueError('dirty source')
    if (capture(['git','-C',str(path),'remote','get-url','origin']),capture(['git','-C',str(path),'branch','--show-current']))!=(url,branch):
        raise ValueError('wrong source branch')
    return dict(repository=url,branch=branch,source_commit=capture(['git','-C',str(path),'rev-parse','HEAD']),
                source_tree=capture(['git','-C',str(path),'rev-parse','HEAD^{tree}']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['capture','finish'])
    p.add_argument('--build-root',type=Path,required=True)
    p.add_argument('--component',choices=list(SOURCES),required=True)
    p.add_argument('--cargo-pid',type=int)
    a=p.parse_args();root=a.build_root.resolve(strict=True)
    attempt=Path((root/'receipts'/(a.component+'.last-attempt')).read_text().strip())
    if not attempt.is_relative_to(root/'logs'/a.component):raise ValueError('unexpected attempt directory')
    start=attempt/'attestation-start.json';end=attempt/'attestation-finished.json'
    rustc=Path.home()/'.cargo/bin/rustc'
    env=dict(__import__('os').environ,RUSTUP_TOOLCHAIN='stable')
    toolchain=subprocess.check_output([str(rustc),'-Vv'],env=env,text=True)
    current=source(root,a.component)
    if a.action=='capture':
        result=dict(at=datetime.now(timezone.utc).isoformat(),captured_ns=time.time_ns(),
            component=a.component,source=current,toolchain=toolchain,cargo_pid=a.cargo_pid,
            reviewed_build_command=(attempt/'build-command.txt').read_text().strip())
        if a.cargo_pid:
            proc=Path('/proc')/str(a.cargo_pid)
            if (proc/'cwd').resolve()!=root/'src'/a.component:raise ValueError('Cargo belongs to another checkout')
            argv=[x.decode() for x in (proc/'cmdline').read_bytes().split(b'\0') if x]
            if not argv[0].endswith('/cargo') or argv[1:4]!=['build','--locked','--release']:
                raise ValueError('unexpected build command')
            if a.component=='brewfs' and ('observability' not in ','.join(argv) or '--example' not in argv):
                raise ValueError('not the final observability/probe build')
            result.update(cargo_start_ticks=(proc/'stat').read_text().rsplit(')',1)[1].split()[19],
                actual_cargo_argv=argv,capture_scope='live build observed; this timestamp is not its start time')
        else:
            result.update(capture_scope='source/toolchain captured by build wrapper before starting Cargo')
        write(start,result)
    else:
        initial=json.loads(start.read_text())
        if initial['source']!=current or initial['toolchain']!=toolchain:raise ValueError('source/toolchain changed during build')
        if initial['cargo_pid']:
            proc=Path('/proc')/str(initial['cargo_pid'])
            if proc.exists() and (proc/'stat').read_text().rsplit(')',1)[1].split()[19]==initial['cargo_start_ticks']:
                raise ValueError('wait for the original Cargo process to exit')
        if 'Finished `release` profile' not in (attempt/'build.log').read_text():raise ValueError('build did not finish successfully')
        outputs=[]
        for name in FILES[a.component]:
            path=root/'bin'/name
            target=root/'targets'/a.component/'release'/('examples/'+name if name=='entity_metadata_probe' else name)
            if path.stat().st_mtime_ns<initial['captured_ns'] or digest(path)!=digest(target):
                raise ValueError('installed binary is stale or differs from compiled output: '+name)
            outputs.append(dict(file=name,bytes=path.stat().st_size,sha256=digest(path)))
        result=dict(at=datetime.now(timezone.utc).isoformat(),component=a.component,source=current,
            toolchain=toolchain,outputs=outputs,build_log_sha256=digest(attempt/'build.log'),
            build_command_sha256=digest(attempt/'build-command.txt'),start_attestation_sha256=digest(start),
            passed=True,capture_scope=initial['capture_scope'],note='Source/toolchain unchanged from capture; exact installed outputs checked.')
        if a.component=='brewfs':
            result['brewfs_library_sha256']=digest(root/'targets/brewfs/release/deps/libbrewfs.rlib')
        write(end,result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
