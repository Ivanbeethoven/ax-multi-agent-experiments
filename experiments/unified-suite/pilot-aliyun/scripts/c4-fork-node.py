#!/usr/bin/env python3
"""C4 leaf operations on one already qualified base; no mounts or services."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(REPO),str(REPO/'bench'),str(REPO/'scripts'),str(Path(__file__).parent)]
from bench.runtime.common import digest,load,pinned,write_new,spec
from bench.runtime.prepare import AGENT_BUDGET
from bench.runtime.fork_work import WORK_TAG,logical_work,validate_result
from bench.fs_step import first_read,mutate
from bench.suite_plan import check_plan
from bench.suite_support import review
from full_trace_common import tree
from pilot_io import fsync_import


def ref(path):return dict(path=str(path),sha256=digest(path))


def binding(asset,cell_id,child_name):
    value=load(Path(asset)/'asset.json');plan=check_plan(load(pinned(value['plan'])))
    cell=next(c for c in plan['cells'] if c['id']==cell_id)
    expected='native' if value['product']=='brewfs' else 'juicefs'
    engine='flat-v1' if value['product']=='brewfs' else 'juicefs'
    if (cell['workload']!='fork' or cell['arm_id']!=expected or cell['arm']['engine']!=engine or
            cell['arm']['product']!=value['product'] or cell['pool']!=value['pool'] or
            cell['params']['agents']!=1 or cell['params']['children']!=1):
        raise ValueError('Actual one-child flat/JuiceFS C4 base required')
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{2,95}',child_name) or child_name=='base':
        raise ValueError('Unique child path required; fixed payload tag is separate')
    return value,cell,Path(value['mount_point'])/'base',Path(value['mount_point'])/child_name


def checked_preparation(reference,asset,cell):
    value=load(pinned(reference))
    if (value.get('passed') is not True or value.get('purpose')!='asset-preparation' or
            value.get('cell_id')!=cell['id'] or value.get('files')!=cell['params']['files'] or
            value.get('bytes')!=cell['params']['files']*4096 or value.get('asset')!=ref(Path(asset)/'asset.json') or
            value.get('work_tag')!=WORK_TAG or len(value.get('normal_exits',[]))!=2):
        raise ValueError('Original full base preparation and normal closure required')
    for original in value['normal_exits']:
        exit_=load(pinned(original))
        if not exit_['passed'] or exit_['exit_code']!=0 or not exit_['mount_absent']:raise ValueError('Base mount not normally closed')
    oracle=load(pinned(value['oracle']))
    if oracle['tag']!=WORK_TAG or oracle['files']!=cell['params']['files']:raise ValueError('Independent fixed-tag oracle required')
    return value,oracle


def measure(request,output):
    s=spec(request,'c4-fork-leaf-v1');out=Path(output)
    value,cell,base,child=binding(s['asset'],s['cell_id'],s['child_name'])
    prepared,oracle=checked_preparation(s['preparation'],s['asset'],cell)
    binary=pinned(value['filesystem_binary'])
    if child.exists() or child.is_symlink() or not base.is_dir():raise ValueError('Closed base and fresh child required')
    if out.exists() or out.is_symlink() or out.resolve().is_relative_to(Path(value['mount_point'])):
        raise ValueError('New evidence outside tested filesystem required')
    out.mkdir(parents=True)
    method='full-independent-copy' if value['product']=='brewfs' else 'native-clone'
    argv=([str(binary),'clone','-p',str(base),str(child)] if method=='native-clone' else
          ['/usr/bin/cp','-a','--reflink=never','--',str(base),str(child)])
    write_new(out/'create-command.json',dict(argv=argv,automatic_retry=False))
    clock={'create_start_ns':time.monotonic_ns()}
    with (out/'create.stdout').open('xb') as stdout,(out/'create.stderr').open('xb') as stderr:
        process=subprocess.Popen(argv,stdout=stdout,stderr=stderr)
        from bench.runtime.common import identity
        write_new(out/'create-process.json',dict(pid=process.pid,identity=identity(process.pid)));code=process.wait()
    clock['create_end_ns']=time.monotonic_ns()
    write_new(out/'create-exit.json',dict(exit_code=code))
    if code:raise RuntimeError('Original native creation failed; no retry')
    clock['first_read_start_ns']=time.monotonic_ns()
    first=first_read(child,oracle['first_read']['path'],oracle['first_read']['sha256'])
    # This end follows the successful content check. It shares the exact clock
    # origin with create_start, including process/lookup/first-use work between.
    clock['first_read_verified_ns']=time.monotonic_ns()
    write_new(out/'first-read.json',first)
    files=fsync_import(child)
    fd=os.open(child.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    if files!=cell['params']['files']:raise ValueError('Complete created child fsync required')
    clock['fsync_end_ns']=time.monotonic_ns()
    changed=mutate(child,WORK_TAG);clock['mutation_end_ns']=time.monotonic_ns()
    write_new(out/'mutation.json',changed);write_new(out/'clock.json',clock)
    result=dict(schema='c4-fork-work-v1',completed=True,passed=True,**logical_work(files,method),
        cell_id=cell['id'],child_name=s['child_name'],clock=ref(out/'clock.json'),
        create_command_seconds=(clock['create_end_ns']-clock['create_start_ns'])/1e9,
        create_to_first_read_seconds=(clock['first_read_verified_ns']-clock['create_start_ns'])/1e9,
        first_read_seconds=first['seconds'],
        creation_fsync_seconds=(clock['fsync_end_ns']-clock['first_read_verified_ns'])/1e9,
        mutation_fsync_seconds=(clock['mutation_end_ns']-clock['fsync_end_ns'])/1e9,
        wall_seconds=(clock['mutation_end_ns']-clock['create_start_ns'])/1e9,
        source_preparation=s['preparation'],automatic_retry=False)
    validate_result(result,clock,cell['params']);write_new(out/'result.json',result)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['draft','worker','measure','verify'])
    p.add_argument('--asset',type=Path);p.add_argument('--cell');p.add_argument('--child-name')
    p.add_argument('--preparation');p.add_argument('--preparation-sha256')
    p.add_argument('--out',type=Path,required=True);p.add_argument('--request');p.add_argument('--sha256')
    p.add_argument('--mount-spec');p.add_argument('--mount-run');p.add_argument('--draft')
    p.add_argument('--purpose',choices=['experiment','observer-calibration-on','observer-calibration-off'],default='experiment')
    a=p.parse_args()
    if a.action=='measure':measure(dict(path=a.request,sha256=a.sha256),a.out);return
    value,cell,base,child=binding(a.asset,a.cell,a.child_name)
    prepared,oracle=checked_preparation(dict(path=a.preparation,sha256=a.preparation_sha256),a.asset,cell)
    if a.action=='draft':
        if a.out.exists() or child.exists() or child.is_symlink():raise ValueError('New draft and child required')
        a.out.mkdir(parents=True)
        write_new(a.out/'result.json',dict(passed=True,cell_id=cell['id'],preparation=ref(a.preparation),
            child_name=a.child_name,work_tag=WORK_TAG,files=cell['params']['files'],oracle=prepared['oracle'],
            tested_filesystem_mutated=False))
    elif a.action=='verify':
        if a.out.exists():raise ValueError('Fresh complete verification evidence required')
        a.out.mkdir(parents=True)
        original=load(pinned(prepared['source_tree']));expected=load(pinned(oracle['expected_tree']));start=time.monotonic_ns()
        parent_ok=tree(base)==original;child_ok=tree(child)==expected
        end=time.monotonic_ns()
        write_new(a.out/'parent.json',dict(passed=parent_ok,cell_id=cell['id'],expected=prepared['source_tree']))
        write_new(a.out/'result.json',dict(passed=parent_ok and child_ok,cell_id=cell['id'],
            files=cell['params']['files'],child_name=a.child_name,work_tag=WORK_TAG,expected_child=oracle['expected_tree'],
            parent_unchanged=parent_ok,full_cold_child=child_ok,start_ns=start,end_ns=end,cold_verification_seconds=(end-start)/1e9))
        if not parent_ok or not child_ok:raise ValueError('Full original parent/child isolation failed')
    else:
        if a.out.exists():raise ValueError('New worker evidence required')
        a.out.mkdir(parents=True)
        mount_ref=ref(a.mount_spec);mount=spec(mount_ref,'experiment-mount-v1')
        if mount['suite_binding']['cell_id']!=cell['id'] or mount['purpose']!=a.purpose:
            raise ValueError('Same original cell and mount purpose required')
        leaf=dict(schema='c4-fork-leaf-v1',reviewed=True,asset=str(a.asset),cell_id=cell['id'],
            child_name=a.child_name,preparation=ref(a.preparation))
        write_new(a.out/'leaf.json',leaf)
        path=a.out/'measure';method='native-clone' if value['product']=='juicefs' else 'full-independent-copy'
        argv=[sys.executable,str(Path(__file__).resolve()),'measure','--request',str(a.out/'leaf.json'),
              '--sha256',digest(a.out/'leaf.json'),'--out',str(path)]
        step=dict(schema='experiment-step-v1',reviewed=True,cell_id=cell['id'],stage='fork-native-work',
            argv=argv,cwd=str(REPO),executable_sha256=digest(sys.executable),
            inputs=[ref(__file__),ref(a.out/'leaf.json'),ref(a.preparation),value['plan'],
                    ref(a.asset/'asset.json'),prepared['source_tree'],oracle['expected_tree']],inherit_environment=[])
        write_new(a.out/'step.reviewed.json',step)
        worker=dict(schema='experiment-worker-v1',reviewed=True,worker_id='a01',purpose=a.purpose,
            kind='fork',budget=AGENT_BUDGET,requires_mount=True,
            mount=dict(spec=mount_ref,directory=a.mount_run),
            jobs=[dict(id='fork-0001',workload_id='fork-100k-fixed-child',step=ref(a.out/'step.reviewed.json'),
                result=dict(path=str(path/'result.json'),equals={k:v for k,v in dict(completed=True,passed=True,
                    **logical_work(cell['params']['files'],method)).items() if k in
                    ('completed','passed','children','files','file_bytes','work_tag','method')}))])
        write_new(a.out/'worker.reviewed.json',worker)
    print(json.dumps(dict(passed=True,out=str(a.out))))


if __name__=='__main__':main()
