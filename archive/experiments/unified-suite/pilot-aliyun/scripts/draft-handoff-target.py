#!/usr/bin/env python3
"""Draft one fresh C4 destination mount of the original shared filesystem.

Copies configuration only. Never formats a volume, creates a bucket, starts a
mount or copies workspace/session data. Run on the destination executor.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import stat
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(REPO),str(REPO/'bench')]
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.prepare import anchor,mount
from bench.suite_mount import cell_of,matches
from bench.suite_support import review


def ref(path):return dict(path=str(path),sha256=digest(path))


def draft(request,output):
    if request.get('reviewed') is not True:raise ValueError('Reviewed destination request required')
    plan=load(pinned(request['plan']));cell=cell_of(plan,request['cell_id'])
    if cell['workload']!='handoff':raise ValueError('C4 handoff cell required')
    asset=load(pinned(request['source_asset']))
    source=load(pinned(request['source_mount']))
    source_command=load(pinned(request['source_command']))
    binary=pinned(request['binary']);product=cell['arm']['product']
    if (source.get('schema')!='experiment-mount-v1' or source.get('reviewed') is not True or
            source_command.get('schema')!='experiment-step-v1' or source_command.get('reviewed') is not True or
            asset['product']!=product or source['filesystem']!=product or
            asset['plan']['sha256']!=request['plan']['sha256'] or
            source['suite_binding']['plan']['sha256']!=request['plan']['sha256'] or
            source['suite_binding']['cell_id']!=cell['id'] or
            source['command']['sha256']!=request['source_command']['sha256'] or
            source_command['executable_sha256']!=request['binary']['sha256']):
        raise ValueError('Keep the original plan, cell, product and binary bytes')
    config=deepcopy(asset['config']);matches(config,cell['client_settings'])
    if anchor(product,config)!=source['anchor']:
        raise ValueError('Original shared backend/workspace identity changed')
    if source['purpose']!=request['purpose']:raise ValueError('Same measurement purpose required')
    point=Path(source['mount_point']);output=Path(output)
    if (not point.is_absolute() or not output.is_absolute() or output.exists() or
            output.is_relative_to(point) or point.is_relative_to(output)):
        raise ValueError('New absolute output outside the original canonical mount required')
    environment=[];protected=None
    if product=='brewfs':
        if cell['arm']['engine']!='workspace-native-v2':raise ValueError('Original native workspace route required')
        protected=pinned(request['protected_environment'])
        if stat.S_IMODE(protected.stat().st_mode)!=0o600:
            raise ValueError('Private destination object credential file required')
        environment=['AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_REGION','AWS_EC2_METADATA_DISABLED']
        if set(load(protected))!=set(environment):raise ValueError('Exactly the original AWS credential environment fields required')
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    point.mkdir(parents=True,exist_ok=True)
    if point.is_symlink() or point.resolve()!=point:raise ValueError('Canonical destination mount directory required')
    if product=='brewfs':
        cache=output/'cache';cache.mkdir();config['cache']['cache_root']=str(cache)
    elif config['cache_dir']!='memory':
        cache=output/'cache';cache.mkdir();config['cache_dir']=str(cache)
    write_new(output/'request.json',request);write_new(output/'config.json',config)
    value=mount(ref(output/'config.json'),request['binary'],product,cell['pool'],str(point),str(REPO),
        output/'specs',environment,['fuse','fuse.brewfs','fuse.juicefs'],
        '127.0.0.1:'+('8910' if product=='brewfs' else '9567'),request['purpose'],
        cell['arm']['hint'] if product=='brewfs' else 'auto',plan['config']['metadata_backend'],
        dict(plan=request['plan'],cell_id=cell['id']))
    command=load(output/'specs/command.json')
    if protected:command['protected_environment']=request['protected_environment']
    write_new(output/'command.bound.json',command)
    write_new(output/'command.reviewed.json',review(output/'command.bound.json','pilot-operator',
        'Original shared anchor and binary; fresh destination mount, no format, no workspace copy. Credentials stay in the private environment file.'))
    value['command']=ref(output/'command.reviewed.json')
    if value['anchor']!=source['anchor']:raise ValueError('Destination must reopen exactly the original shared anchor')
    write_new(output/'mount.bound.json',value)
    write_new(output/'mount.reviewed.json',review(output/'mount.bound.json','pilot-operator',
        'New destination mount instance; source must stop and release before start. Hardware and observer qualification remain separate.'))
    result=dict(passed=True,mount=ref(output/'mount.reviewed.json'),command=ref(output/'command.reviewed.json'),
        anchor=value['anchor'],source_mount=request['source_mount'],binary=request['binary'],
        workspace_copy_bytes=0,mount_started=False,automatic_retry=False,
        scope='Destination spec preparation only; not route, resume, hardware or performance qualification')
    write_new(output/'result.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(draft(load(pinned(dict(path=str(a.request),sha256=a.sha256))),a.out)))
