#!/usr/bin/env python3
"""Run one predeclared C1 formal retry after a successful Packed C3 handoff."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new


def ref(path):
    return dict(path=str(path),sha256=digest(path))


def wait_for_handoff(path, unit, deadline):
    while time.monotonic()<deadline:
        try:
            value=load(path)
        except (FileNotFoundError,json.JSONDecodeError):
            state=subprocess.check_output(['systemctl','show','--value','-p','ActiveState',unit],text=True).strip()
            if state=='failed':
                raise ValueError('Original Packed batch failed; inspect original artifacts before using the pool')
            time.sleep(10)
            continue
        if (value['passed'] is not True or value['state']!='complete' or value['pool']!='pool-a'
            or value['unit']!=unit or value['no_active_pool_lease'] is not True
            or set(value['original_processes_and_mounts'])!={'client','metadata','objects'}):
            raise ValueError('Expected actual successful handoff from the original Pool A batch')
        for role in value['original_processes_and_mounts'].values():
            if role['any_process_alive'] is not False or any(role.get(key) for key in ('mount_present','slot_occupied')):
                raise ValueError('Handoff reports residual process, mount or slot owner')
        closing=load(pinned(value['closing']))
        if closing['outcome']!='complete' or closing['cell_id']!=value['cell_id']:
            raise ValueError('Handoff and original closure differ')
        if load(pinned(value['full_cold']))['passed'] is not True or load(pinned(value['pipeline']))['passed'] is not True:
            raise ValueError('Original cold verification and pipeline must both pass')
        return value
    raise TimeoutError('No successful Pool A handoff within the explicit wait window')


def calibration_only_handoff(pin,unit):
    value=load(pinned(pin))
    if (value['passed'] is not True or value['pool']!='pool-a' or value['unit']!=unit
        or value['ready_for_next_workload'] is not True or value['no_active_pool_lease'] is not True
        or value['agent_slot_empty'] is not True or value['formal_packed_sample_accepted'] is not False
        or value['formal_packed_state']!='pending' or value['formal_stages_started']!=0
        or value['all_six_calibration_trials_clean'] is not True or len(value['trials'])!=6):
        raise ValueError('Actual calibration-only Pool A closure required')
    overhead=load(pinned(value['overhead']))
    if overhead['passed'] is not False:
        raise ValueError('Expected failed measurement-overhead gate, before the Packed formal stage')
    return value


def command(out,name,args):
    folder=out/name
    folder.mkdir()
    write_new(folder/'intent.json',dict(argv=[str(x) for x in args],automatic_retry=False))
    with (folder/'stdout').open('xb') as stdout,(folder/'stderr').open('xb') as stderr:
        code=subprocess.run([str(x) for x in args],stdout=stdout,stderr=stderr).returncode
    write_new(folder/'exit.json',dict(exit_code=code))
    if code:
        raise RuntimeError('Inspect '+str(folder))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    a=p.parse_args()
    s=load(pinned(dict(path=str(a.request),sha256=a.sha256)))
    if s['schema']!='pilot-c1-after-packed-v1' or s['reviewed'] is not True or not 0<s['wait_seconds']<=7200:
        raise ValueError('Reviewed one-shot request with a bounded wait required')
    out=Path(s['out'])
    out.mkdir(parents=True,exist_ok=False)
    write_new(out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),request=ref(a.request),automatic_retry=False))
    try:
        prepare=pinned(s['prepare'])
        original=load(prepare)
        control=Path(original['control'])
        handoff_path=Path(s['handoff'])
        reviewed=s.get('calibration_only_handoff')
        if reviewed is not None and str(handoff_path)!=reviewed['path']:
            raise ValueError('Different explicitly reviewed calibration handoff path')
        handoff=(calibration_only_handoff(reviewed,s['unit']) if reviewed is not None else
                 wait_for_handoff(handoff_path,s['unit'],time.monotonic()+s['wait_seconds']))
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(handoff['at'])).total_seconds()
        if handoff['cell_id']!=s['handoff_cell'] or not 0<=age<=(600 if reviewed else 120):
            raise ValueError('Handoff is stale or belongs to another cell')
        tool=pinned(s['rebind_tool'])
        command(out,'prepare',[sys.executable,tool,'prepare','--request',prepare])
        latest=out/'latest.json'
        command(out,'latest',[sys.executable,pinned(s['latest_tool']),'--manifest',
                              control/'provenance/build-manifest.json','--out',latest])
        request=out/'formal.request.json'
        write_new(request,dict(schema='rebound-flat-formal-v1',reviewed=True,control=str(control),
            out=str(control/'one-cell-runs'/load(pinned(original['original_request']))['cell_id']),
            prepared=ref(control/'prepared.json'),latest=ref(latest),handoff=ref(handoff_path),
            replacement_collector=dict(name=handoff['collector_name'],out=handoff['collector_out'])))
        command(out,'formal',[sys.executable,tool,'formal','--request',request,'--sha256',digest(request)])
        write_new(out/'result.json',dict(passed=True,formal_request=ref(request),handoff=ref(handoff_path),automatic_retry=False))
    except Exception as error:
        write_new(out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False))
        raise


if __name__=='__main__':
    main()
