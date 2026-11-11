#!/usr/bin/env python3
"""Check one frozen OpenCode input through an original shared filesystem.

Fresh source asset only. Optional resume makes a real model call after clean
reopening; it is not a cross-host result or a reusable unchanged checkpoint.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(REPO),str(REPO/'bench')]
from bench.runtime.common import digest,load,pinned,write_new
from bench.session_check import check_after


def ref(path):return dict(path=str(path),sha256=digest(path))


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','asset','checkpoint','source-tree','private-relay-config','node-tool','session-worker','opencode','out'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('checkpoint','source-tree','private-relay-config','node-tool','session-worker','opencode'):
        p.add_argument('--'+name+'-sha256',required=True)
    p.add_argument('--cell',required=True)
    p.add_argument('--database',type=int,required=True)
    p.add_argument('--attempt',required=True)
    p.add_argument('--resume',action='store_true',help='One real native resume and cold final-byte/history check; no retry')
    a=p.parse_args()
    if (not a.out.is_absolute() or not a.asset.is_absolute() or a.out.exists() or a.asset.exists() or
            not re.fullmatch('[a-z0-9-]{3,32}',a.attempt)):
        raise ValueError('New absolute asset/output and short dedicated attempt required')
    pins={name:dict(path=str(getattr(a,name)),sha256=getattr(a,name+'_sha256'))
        for name in ('checkpoint','source_tree','private_relay_config','node_tool','session_worker','opencode')}
    for pin in pins.values():pinned(pin)
    plan=load(a.plan);cell=next(c for c in plan['cells'] if c['id']==a.cell)
    if cell['workload']!='handoff' or cell['params']['trace']!='opencode-checkpoint-20261111':
        raise ValueError('Actual fixed OpenCode handoff input required')
    checkpoint=load(a.checkpoint)
    if checkpoint['opencode_binary_sha256']!=a.opencode_sha256:
        raise ValueError('Exact original OpenCode binary required')
    product=cell['arm']['product']
    if product=='brewfs' and cell['arm']['engine']!='workspace-native-v2':
        raise ValueError('Only the original native workspace or JuiceFS route')
    point=Path(checkpoint['canonical_workspace']).parent
    if point!=Path('/srv/ax-pilot-session'):raise ValueError('Keep the original canonical workspace path')
    a.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    write_new(a.out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),cell_id=a.cell,
        plan=ref(a.plan),pins=pins,real_resume=a.resume,automatic_retry=False,
        scope='Single-host shared-filesystem qualification; no cross-host/performance claim'))
    Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').write_text(str(__import__('os').getpid()))
    active=None;phase='asset-init'
    prep=module('fixed_session_input',Path(__file__).with_name('prepare-session-input.py'))
    worker=module('fixed_session_worker',a.session_worker)

    def helper(action,label,**fields):
        directory=a.out/label;directory.mkdir()
        argv=['python3',str(a.node_tool),action,'--asset',str(a.asset)]
        for name,value in fields.items():
            if value is True:argv+=['--'+name.replace('_','-')]
            elif value is not None:argv+=['--'+name.replace('_','-'),str(value)]
        write_new(directory/'command.json',dict(argv=argv,automatic_retry=False))
        with (directory/'stdout').open('xb') as out,(directory/'stderr').open('xb') as err:
            process=subprocess.Popen(argv,stdout=out,stderr=err)
            write_new(directory/'process.json',dict(pid=process.pid));code=process.wait()
        write_new(directory/'exit.json',dict(exit_code=code))
        if code:raise RuntimeError('Inspect original helper: '+str(directory))

    def mount(label):
        nonlocal active
        helper('draft-mount',label+'-draft',cell=a.cell,out=a.out/(label+'-config'))
        state=Path('/srv/ax-client/m')/(a.attempt+'-'+label)
        helper('start',label+'-start',spec=a.out/(label+'-config')/'mount.reviewed.json',out=state,join_slot=True)
        active=state
        return dict(spec=ref(a.out/(label+'-config')/'mount.reviewed.json'),directory=str(state))

    def stop(label):
        nonlocal active
        helper('debt',label+'-debt',cell=a.cell,out=a.out/(label+'-debt-proof'))
        helper('stop',label+'-stop',mount_run=active,out=a.out/(label+'-stop.json'))
        active=None

    try:
        helper('asset-init','asset-init',product=product,database=a.database,plan=a.plan,cell=a.cell,
            mount_point=point,out=a.out/'asset-init-proof')
        phase='install';source=mount('source')
        relay='http://10.20.0.10:18443/v1'
        request=dict(checkpoint_directory_receipt=pins['checkpoint'],source_tree=pins['source_tree'],
            private_relay_config=pins['private_relay_config'],session_worker=pins['session_worker'],
            binary=pins['opencode'],relay_url=relay,mount=source)
        write_new(a.out/'install-request.json',request)
        installed=prep.run(request,a.out/'installed')
        stop('source')
        phase='cold-native-export';cold=mount('cold')
        workspace=Path(checkpoint['canonical_workspace'])
        native=dict(action='export',binary=pins['opencode'],workspace=str(workspace),
            project=checkpoint['canonical_project'],config=str(workspace/'config/opencode.json'),
            state={name:str(workspace/'source'/name) for name in ('data','state','cache','config')},
            relay_url=relay,session_id=checkpoint['session_id'])
        exported=worker.run(native,a.out/'cold-native-export')
        validation=check_after(checkpoint['session_prefix'],exported,False)
        if digest(a.out/'cold-native-export/export.json')!=checkpoint['session_export_sha256']:
            raise ValueError('Original closed native database/history did not reopen unchanged')
        result=dict(passed=True,cell_id=a.cell,asset=ref(a.asset/'asset.json'),
            checkpoint=pins['checkpoint'],installed=ref(a.out/'installed/result.json'),
            original_cold_export=ref(a.out/'cold-native-export/export.json'),
            original_prefix_validation=validation,empty_home_each_phase=True,
            same_workspace_native_database=True,extra_session_import=False,real_resume=a.resume,
            model_calls_attempted=a.resume,automatic_retry=False,
            scope='Single-host original shared filesystem and native database; cross-host/timing qualification remains separate')
        if a.resume:
            phase='native-resume'
            resumed=worker.run(dict(native,action='resume',checkpoint=pins['checkpoint']),a.out/'native-resume')
            after=worker.run(native,a.out/'export-after-resume')
            result['resumed_prefix_validation']=check_after(checkpoint['session_prefix'],after,True)
            result['native_resume']=ref(a.out/'native-resume/result.json')
            result['state_sync']=prep.sync_tree(workspace)
            expected=prep.tree(workspace);write_new(a.out/'expected-final-tree.json',expected)
            stop('resumed')
            phase='cold-final-bytes';mount('final')
            if prep.tree(workspace)!=expected:raise ValueError('Full resumed workspace/state changed after cold reopening')
            if digest(workspace/'repo/final.txt')!=checkpoint['expected_final_sha256']:
                raise ValueError('Original cold final task bytes differ')
            result.update(cold_full_workspace_equal=True,cold_final_sha256=checkpoint['expected_final_sha256'])
            stop('final')
        else:stop('cold')
        write_new(a.out/'result.json',result);print(json.dumps(result))
    except BaseException as error:
        write_new(a.out/'failed.json',dict(passed=False,phase=phase,error_type=type(error).__name__,
            error=str(error),active_supervisor=str(active) if active else None,automatic_retry=False))
        if active:
            helper('stop','failure-normal-stop',mount_run=active,out=a.out/'failure-normal-stop.json')
        raise


if __name__=='__main__':main()
