#!/usr/bin/env python3
"""Calibration-only touched-file checks; never a full namespace cold proof."""
import argparse
import json
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import alive,digest,identity,load,mounts,pinned,write_new

def ref(p):return dict(path=str(p),sha256=digest(p))

def touched(path,files,workspace,count):
    lines=Path(path).read_text().splitlines()
    if not lines or lines[0]!=f'smallfiles-v1\t{files}\t{workspace}':raise ValueError('Original touched state header differs')
    rows=[];seen=set()
    for line in lines[1:]:
        fields=line.split('\t')
        if len(fields)!=2:raise ValueError('Invalid touched row')
        index,version=map(int,fields)
        if not 0<=index<files or version<=0 or index in seen:raise ValueError('Invalid or duplicate touched index/version')
        seen.add(index);rows.append((index,version))
    if len(rows)!=count:raise ValueError('All declared COW files must be checked')
    return rows

def relative(index):return Path('small')/f'd{index%1024:04}'/f'f{index:07}'

def properties(path):
    v=path.lstat()
    if not stat.S_ISREG(v.st_mode) or v.st_size!=4096:raise ValueError('Regular 4096-byte touched file required')
    return dict(mode=stat.S_IMODE(v.st_mode),uid=v.st_uid,gid=v.st_gid,nlink=v.st_nlink,bytes=v.st_size)

def status(a,asset,out):
    native=runpy.run_path(str(pinned(dict(path=str(a.entity_tool),sha256=a.entity_tool_sha256))))
    native['debt'](SimpleNamespace(asset=asset,plan=a.plan,cell=a.cell,manifest=a.manifest,probe=a.probe,
        probe_sha256=a.probe_sha256,state='Quiescent',out=out))
    samples=[load(pinned(p)) for p in load(out/'result.json')['samples']]
    if len(samples)!=2 or samples[0]['native_authority']!=samples[1]['native_authority']:raise ValueError('Unstable native authority')
    return samples[0]['native_authority']

def run(a):
    native=runpy.run_path(str(pinned(dict(path=str(a.entity_tool),sha256=a.entity_tool_sha256))))
    parent=load(a.base_asset/'asset.json');plan,cell=native['packed_cell'](parent,a.plan,a.cell)
    a.out.mkdir(parents=True,exist_ok=False)
    if a.action=='parent-snapshot':
        authority=status(a,a.base_asset,a.out/'native')
        write_new(a.out/'result.json',dict(passed=True,cell_id=a.cell,base=ref(a.base_asset/'asset.json'),
            native_authority=authority,native=ref(a.out/'native/result.json'),full_namespace_cold=False))
        return
    child=load(a.asset/'asset.json');native['packed_cell'](child,a.plan,a.cell)
    if child.get('immutable_parent')!=ref(a.base_asset/'asset.json'):raise ValueError('Another immutable parent')
    if a.action=='live-check':
        native['binary_pin'](a.smallfiles,a.smallfiles_sha256,a.manifest,'smallfiles')
        if not mounts(child['mount_point']):raise ValueError('Live verification requires the original mounted workspace')
        process=load(a.mount_run/'process.json')['process']
        if not alive(process):raise ValueError('Original FUSE process is absent')
        state_pin=ref(a.state_file);rows=touched(a.state_file,cell['params']['files'],1,cell['params']['cow_files'])
        attrs=[]
        for index,version in rows:
            path=relative(index);source=properties(a.source/path);actual=properties(Path(child['mount_point'])/path)
            if actual!=source:raise ValueError('Touched file attributes differ from original source: '+str(path))
            attrs.append(dict(path=path.as_posix(),index=index,version=version,source_properties=source,properties=actual))
        write_new(a.out/'properties.json',attrs)
        argv=[str(a.smallfiles),'verify-touched','--root',child['mount_point'],'--output',str(a.out/'bytes'),
              '--files',str(cell['params']['files']),'--state',str(a.state_file)]
        write_new(a.out/'command.json',dict(argv=argv,binary=ref(a.smallfiles),state=state_pin,full_namespace_cold=False))
        begin=time.monotonic()
        with (a.out/'stdout').open('xb') as so,(a.out/'stderr').open('xb') as se:
            p=subprocess.Popen(argv,stdout=so,stderr=se);write_new(a.out/'process.json',dict(process=identity(p.pid)));code=p.wait()
        write_new(a.out/'exit.json',dict(exit_code=code,wall_seconds=time.monotonic()-begin))
        if code:raise ValueError('Original verify-touched command failed')
        result=load(a.out/'bytes/result.json')
        if result.get('passed') is not True or result.get('completed') is not True or result.get('checked_files')!=len(rows):raise ValueError('Incomplete touched-byte verification')
        if ref(a.state_file)!=state_pin:raise ValueError('Touched state changed')
        write_new(a.out/'result.json',dict(passed=True,cell_id=a.cell,verification_mode='live-touched-v1',full_namespace_cold=False,
            checked_files=len(rows),checked_bytes=len(rows)*4096,state=state_pin,properties=ref(a.out/'properties.json'),
            bytes_check=ref(a.out/'bytes/result.json'),normal_check_exit=ref(a.out/'exit.json'),original_mount_process=process,
            namespace=child['config']['workspace'],volume=child['config']['entity_volume_id'],base=ref(a.base_asset/'asset.json')))
        return
    stopped=load(pinned(dict(path=str(a.normal_stop),sha256=a.normal_stop_sha256)))
    if not stopped['passed'] or stopped['exit_code']!=0 or not stopped['mount_absent'] or mounts(child['mount_point']):raise ValueError('Original mount has not normally stopped')
    if stopped['anchor']['workspace']!=child['config']['workspace'] or stopped['anchor']['namespace']!=child['config']['workspace_namespace']:raise ValueError('Stop belongs to another child')
    live=load(pinned(a.live_check));before=load(pinned(a.parent_before))
    if not live['passed'] or live['namespace']!=child['config']['workspace'] or not before['passed']:raise ValueError('Missing actual live or parent-before check')
    child_authority=status(a,a.asset,a.out/'child-status')
    parent_authority=status(a,a.base_asset,a.out/'parent-after')
    if before['base']!=ref(a.base_asset/'asset.json') or before['native_authority']!=parent_authority:raise ValueError('Immutable parent authority changed')
    for authority in (child_authority,parent_authority):
        if authority['lower']!=before['native_authority']['lower']:raise ValueError('Immutable lower digest/key changed')
    write_new(a.out/'result.json',dict(passed=True,cell_id=a.cell,verification_mode='live-touched-v1',full_namespace_cold=False,
        live_check=a.live_check,parent_before=a.parent_before,parent_after=ref(a.out/'parent-after/result.json'),
        child_status=ref(a.out/'child-status/result.json'),parent_authority_unchanged=True,
        namespace=child['config']['workspace'],volume=child['config']['entity_volume_id'],normal_exit=ref(a.normal_stop),
        scope='Calibration only: every touched file checked live; original child normal stop and native Quiescent status; parent authority/digest unchanged. No cold reader or complete namespace readback.'))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['parent-snapshot','live-check','finish'])
    for name in ('base-asset','asset','plan','manifest','entity-tool','probe','out'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('cell','entity-tool-sha256','probe-sha256'):p.add_argument('--'+name,required=True)
    for name in ('source','state-file','smallfiles','mount-run','normal-stop'):p.add_argument('--'+name,type=Path)
    for name in ('smallfiles-sha256','normal-stop-sha256'):p.add_argument('--'+name)
    for name in ('parent-before','live-check'):p.add_argument('--'+name,type=json.loads)
    run(p.parse_args())
if __name__=='__main__':main()
