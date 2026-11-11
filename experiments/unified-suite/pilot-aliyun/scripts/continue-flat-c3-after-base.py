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


def latest_receipt(pin, manifest):
    path=pinned(pin);receipt=load(path);build=load(manifest)
    when=datetime.fromisoformat(receipt['at'].replace('Z','+00:00'))
    age=(datetime.now(timezone.utc)-when).total_seconds()
    if receipt.get('passed') is not True or receipt.get('build_manifest_sha256')!=digest(manifest) or not -5<=age<=300:
        raise ValueError('An independently observed, current branch receipt for this exact build is required')
    rows=receipt['sources'];expected={s['component']:s for s in build['sources']}
    if len(rows)!=2 or {r['component'] for r in rows}!=set(expected) or set(expected)!={'brewfs','rustfs'}:
        raise ValueError('Both original product branches must be observed')
    for row in rows:
        source=expected[row['component']]
        if (row['repository'].removesuffix('.git')!=source['repository'].removesuffix('.git') or
                row['branch']!=source['branch'] or row['built_commit']!=source['source_commit'] or
                row['remote_commit']!=source['source_commit'] or row['lookup_exit_code']!=0 or row['matches'] is not True):
            raise ValueError('Independently observed branch tip differs from the exact build')
    return path


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
        manifest=control/'provenance/build-manifest.json'
        if s.get('independent_latest'):
            latest=latest_receipt(s['independent_latest'],manifest)
            prior=load(pinned(s['original_cloud_latest_failure']))
            if (prior.get('passed') is not False or prior['build_manifest_sha256']!=digest(manifest) or
                    any(r['remote_commit'] is not None and r['matches'] is not True for r in prior['sources'])):
                raise ValueError('Do not replace an observed divergent branch with an independent receipt')
            write_new(out/'latest-source.json',dict(passed=True,independent_receipt=s['independent_latest'],
                original_cloud_failure=s['original_cloud_latest_failure'],actual_build=ref(manifest)))
        else:
            latest=out/'latest.json'
            command(out,'latest',[sys.executable,tools['latest'],'--manifest',manifest,'--out',latest])
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
