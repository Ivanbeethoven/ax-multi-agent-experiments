#!/usr/bin/env python3
"""Verify six original C4 trials, complete archives and three on/off pairs."""
import argparse
from pathlib import Path
import sys
REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import load,pinned,digest,write_new
from bench.runtime import c4_verify
from bench.runtime.observe import overhead,audit
from bench.archive_check import verify


def ref(path):return dict(path=str(path),sha256=digest(path))


def check(roots,metric,workload,out,verification_policy=c4_verify.FULL,recovered=None):
    if len(roots)!=6 or len(set(roots))!=6:raise ValueError('Six unique predeclared trials required')
    if verification_policy not in (c4_verify.FULL,c4_verify.TOUCHED):raise ValueError('Unknown C4 calibration verification policy')
    if recovered and verification_policy!=c4_verify.TOUCHED:raise ValueError('Recovered parent reference only for touched calibration')
    rows=[];verification=[];full=None;expected_pin=None
    for index,(directory,mode) in enumerate(zip(roots,('off','on','on','off','off','on'))):
        root=Path(directory);end=load(root/'finished.json');co=load(pinned(end['cohort']))
        if end['passed'] is not True or co['purpose']!='observer-calibration-'+mode:
            raise ValueError('Original trial order/purpose differs')
        for key in ('client_verify','parent_verify','native_exit','verify_exit','closure','work'):
            if load(pinned(end[key])).get('passed') is not True:raise ValueError('C4 trial did not verify or close')
        verified=load(pinned(end['client_verify']));parent=load(pinned(end['parent_verify']))
        observed_policy=verified.get('verification_policy',c4_verify.FULL)
        wanted_policy=(c4_verify.FULL if verification_policy==c4_verify.FULL or recovered and index==0 else
                       c4_verify.REFERENCE if index==0 else c4_verify.TOUCHED)
        if observed_policy!=wanted_policy:raise ValueError('C4 trial verification scope differs from predeclared policy')
        if wanted_policy==c4_verify.TOUCHED:
            if (verified['binding']!=full['binding'] or verified['full_reference']!=expected_pin or
                    verified['parent_identity']!=full['parent_identity'] or parent['parent_identity']!=full['parent_identity'] or
                    verified['parent_identity_unchanged'] is not True or verified['parent_matches'] is not True or
                    verified['child_matches'] is not True or not verified['touched_paths'] or
                    verified['full_cold_child'] is not False or verified['parent_unchanged'] is not False):
                raise ValueError('Touched C4 proof must retain original full-reference and narrower scope')
        elif not (verified.get('parent_unchanged') is True and verified.get('full_cold_child') is True):
            raise ValueError('Full C4 cold trial required')
        if wanted_policy==c4_verify.REFERENCE:
            full=verified
            c4_verify.reference_identity(full,full['binding'],None)
            expected_pin=dict(path='/srv/ax-client/q/'+full['child_name']+'/verify/result.json',sha256=end['client_verify']['sha256'])
        if recovered and index==0:
            expected_pin=recovered['remote'];full=load(pinned(recovered['local']))
            expected_full=dict(path='/srv/ax-client/q/'+verified['child_name']+'/verify/result.json',sha256=end['client_verify']['sha256'])
            if (expected_pin['sha256']!=recovered['local']['sha256'] or full.get('original_full')!=expected_full or
                    full.get('content_read')!=dict(files=0,bytes=0)):
                raise ValueError('Separate metadata reference must bind actual original full off proof')
            c4_verify.reference_identity(full,full['binding'],None,verified)
        verification.append(dict(policy=observed_policy,proof=end['client_verify'],
                                 cold_verification_seconds=verified['cold_verification_seconds']))
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
    write_new(out/'verification.json',dict(policy=verification_policy,trials=verification,recovered_reference=recovered,
        scope='Cold checks follow measure-end and do not enter makespan or P99. Formal C4 still requires full cold verification.'))
    result['verification']=ref(out/'verification.json')
    write_new(out/'result.json',result)
    if not result['passed']:raise ValueError('Original C4 observer overhead exceeded tolerance; no automatic rerun')
    return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('trials','metric','workload','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--verification-policy',default=c4_verify.FULL,choices=(c4_verify.FULL,c4_verify.TOUCHED))
    a=p.parse_args();check(load(a.trials),a.metric,a.workload,a.out,a.verification_policy)
