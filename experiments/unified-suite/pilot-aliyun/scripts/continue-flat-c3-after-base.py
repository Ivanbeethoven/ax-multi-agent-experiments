#!/usr/bin/env python3
"""Run exactly one declared flat C3 cell after the original full base preparation."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest, load, pinned, write_new


def ref(path):
    return dict(path=str(path),sha256=digest(path))


def wait_base(unit,invocation,result_path,limit):
    end=time.monotonic()+limit
    while time.monotonic()<end:
        keys='InvocationID,ActiveState,SubState,ExecMainStatus,MainPID'
        raw=subprocess.check_output(['systemctl','show',unit,'-p',keys],text=True)
        state=dict(line.split('=',1) for line in raw.splitlines())
        if state['InvocationID']!=invocation:
            raise ValueError('The original base preparation invocation changed')
        if state['ActiveState']=='failed':
            raise ValueError('The original base preparation failed; preserve it')
        if state['SubState']=='exited' and state['ExecMainStatus']=='0' and state['MainPID']=='0':
            result=load(result_path)
            if result.get('passed') is not True or result.get('preparation_only') is not True or result['formal_started']:
                raise ValueError('Full base qualification is required')
            return result
        time.sleep(15)
    raise TimeoutError('Original preparation did not close in the declared wait window')


def command(out,name,argv):
    folder=out/name
    folder.mkdir()
    argv=list(map(str,argv))
    write_new(folder/'intent.json',dict(argv=argv,automatic_retry=False))
    with (folder/'stdout').open('xb') as stdout,(folder/'stderr').open('xb') as stderr:
        code=subprocess.run(argv,stdout=stdout,stderr=stderr).returncode
    write_new(folder/'exit.json',dict(exit_code=code))
    if code:
        raise RuntimeError('Inspect '+str(folder))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    a=p.parse_args()
    req=dict(path=str(a.request),sha256=a.sha256)
    s=load(pinned(req))
    if s['schema']!='pilot-flat-c3-after-base-v1' or s['reviewed'] is not True or not 0<s['wait_seconds']<=7200:
        raise ValueError('Explicit flat C3 continuation request required')
    base=load(pinned(s['base_request']))
    source=load(pinned(s['same_pool_previous_request']))
    control=Path(base['control'])
    out=Path(s['out'])
    out.mkdir(parents=True,exist_ok=False)
    write_new(out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),request=req,automatic_retry=False))
    try:
        tools={k:pinned(v) for k,v in s['tools'].items()}
        result_path=Path(base['out'])/'result.json'
        prepared=wait_base(s['unit'],s['invocation'],result_path,s['wait_seconds'])
        if prepared['cell_id']!=base['cell_id'] or prepared['qualification']!=base['qualification']:
            raise ValueError('Different prepared C3 base')
        capacity=load(pinned(prepared['capacity']))
        if capacity['passed'] is not True or capacity['extra_full_copies']!=7 or capacity['reserve_fraction']<.2:
            raise ValueError('Actual disk and Redis capacity forecast required')
        latest=out/'latest.json'
        command(out,'latest',[sys.executable,tools['latest'],'--manifest',control/'provenance/build-manifest.json','--out',latest])
        request=out/'run.request.json'
        argv=[sys.executable,tools['draft'],'--control',control,'--qualification',prepared['qualification'],'--cell',base['cell_id'],
              '--node-tool',base['node_files']['node']['path'],'--node-tool-sha256',base['node_files']['node']['sha256'],
              '--observer-tool-name',source['observer_tool']['name'],'--observer-tool-sha256',source['observer_tool']['sha256'],
              '--collector-name',source['final_collector_name'],'--collector-out',source['final_collector_out'],
              '--cross-pool-qualification',pinned(source['cross_pool_qualification']),
              '--runtime-control',base['runtime']['path'],'--runtime-control-sha256',base['runtime']['sha256'],
              '--latest-check',latest,'--attempt-suffix',s['attempt_suffix'],'--out',request]
        for name in ('calibrator','checker','admitter','runner'):
            argv+=['--'+name,tools[name]]
        command(out,'draft',argv)
        command(out,'run',[sys.executable,tools['entry'],'--request',request,'--sha256',digest(request)])
        write_new(out/'result.json',dict(passed=True,prepared=ref(result_path),run_request=ref(request),automatic_retry=False))
    except Exception as error:
        write_new(out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False))
        raise


if __name__=='__main__':
    main()
