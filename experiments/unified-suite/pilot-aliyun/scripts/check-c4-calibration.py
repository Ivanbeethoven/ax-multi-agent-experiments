#!/usr/bin/env python3
"""Verify six original C4 trials, complete archives and three on/off pairs."""
import argparse
from pathlib import Path
import sys
REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import load,pinned,digest,write_new
from bench.runtime.observe import overhead,audit
from bench.archive_check import verify


def ref(path):return dict(path=str(path),sha256=digest(path))


def check(roots,metric,workload,out):
    if len(roots)!=6 or len(set(roots))!=6:raise ValueError('Six unique predeclared trials required')
    rows=[]
    for directory,mode in zip(roots,('off','on','on','off','off','on')):
        root=Path(directory);end=load(root/'finished.json');co=load(pinned(end['cohort']))
        if end['passed'] is not True or co['purpose']!='observer-calibration-'+mode:
            raise ValueError('Original trial order/purpose differs')
        for key in ('client_verify','parent_verify','native_exit','verify_exit','closure','work'):
            if load(pinned(end[key])).get('passed') is not True:raise ValueError('C4 trial did not verify or close')
        for archive in load(pinned(end['archives'])):verify(pinned(archive),archive['sha256'])
        if mode=='on':
            observers=load(root/'observers.json')
            if {o['id'] for o in observers}!={end['pool']+'-'+r for r in ('client','metadata','objects')}:
                raise ValueError('All three actual pool observers required')
            for o in observers:
                role=o['id'].removeprefix(end['pool']+'-');tag=Path(o['directory']).name
                audit(root/'raw'/role/'o'/tag,30,['measure-start','measure-end','drain-end','verify-end'])
        rows.append(end['cohort'])
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    plan=dict(schema='experiment-observer-overhead-plan-v1',reviewed=True,
        metric_contract_sha256=digest(metric),workload_contract_sha256=digest(workload),max_relative_change=.05,
        pairs=[dict(off=rows[i],on=rows[j]) for i,j in ((0,1),(3,2),(4,5))])
    write_new(out/'plan.json',plan);result=overhead(ref(out/'plan.json'))
    write_new(out/'result.json',result)
    if not result['passed']:raise ValueError('Original C4 observer overhead exceeded tolerance; no automatic rerun')
    return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('trials','metric','workload','out'):p.add_argument('--'+name,required=True)
    a=p.parse_args();check(load(a.trials),a.metric,a.workload,a.out)
