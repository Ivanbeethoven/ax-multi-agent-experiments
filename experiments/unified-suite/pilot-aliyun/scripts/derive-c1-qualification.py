#!/usr/bin/env python3
"""Reuse verified C1 bases and hardware, with a separate exact workload contract.

No measurement, calibration acceptance, cell claim or automatic next workload.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,write_new


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--case',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists() or a.out.resolve().is_relative_to(a.source.resolve()) or a.source.resolve().is_relative_to(a.out.resolve()):
        raise ValueError('New disjoint output required')
    plan=load(a.plan)
    cell=next(c for c in plan['cells'] if c['case']==a.case)
    if cell['workload']!='fio':raise ValueError('C1 only')
    hardware=load(a.source/'hardware.json')
    if not hardware['passed']:raise ValueError('Hardware is not qualified')
    arms=[arm for arm in ('native','juicefs') if (a.source/arm).is_dir()]
    if not arms:raise ValueError('No qualified flat route found')
    for arm in arms:
        assigned=next(c for c in plan['cells'] if c['case']==a.case and c['arm_id']==arm)
        if assigned['pool']!=hardware.get('pool','pool-a'):raise ValueError('Source hardware belongs to another assigned pool')
    # Keep every original receipt intact. Only the new workload contract differs;
    # it will require its own three explicit calibration pairs before admission.
    shutil.copytree(a.source,a.out)
    for arm in arms:
        folder=a.out/arm
        for name in ('input.json','filesystem.json'):
            if not load(folder/name)['passed']:raise ValueError('Source route is not qualified')
        path=folder/'workload.json';original=load(path)
        original_ref=dict(path=str(a.source/arm/'workload.json'),sha256=digest(a.source/arm/'workload.json'))
        path.rename(folder/'source-workload.json')
        original.update(params=cell['params'],scope='One exact C1 '+a.case+' workload; separate observer calibration required',
                        source_contract=original_ref)
        write_new(path,original)
    write_new(a.out/'derived.json',dict(passed=True,case=a.case,plan_sha256=digest(a.plan),
              source=str(a.source),scope='Shared verified bases and hardware; no calibration or performance acceptance'))
    print(json.dumps(dict(passed=True,case=a.case,out=str(a.out))))


if __name__=='__main__':main()
