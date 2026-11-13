"""Read closed C1 receipts; retain failed cases and report native fio metrics separately."""
import hashlib
import json
from pathlib import Path
import re
import sys

CASES = [(mode, product) for mode in ('read','write','randread','randwrite','randrw')
         for product in ('brewfs','juicefs') if (mode,product) != ('randrw','juicefs')]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def load(path):
    return json.loads(path.read_text()) if path.is_file() else None


def io_metrics(job, kind):
    value = job.get(kind,{})
    latency = value.get('lat_ns' if kind=='sync' else 'clat_ns',{})
    return {**{key:value.get(key) for key in ('io_bytes','total_ios','iops','bw_bytes','runtime')},
            'latency_mean_ns':latency.get('mean'), 'latency_percentiles_ns':latency.get('percentile',{})}


def summarize(root):
    rows=[]
    for mode,product in CASES:
        folder=root/f'c1-{mode}-{product}-qualification'
        source=folder/'trial.json'
        trial=load(source)
        if trial is None:
            rows.append(dict(mode=mode,product=product,finished=False,passed=False));continue
        if trial['mode']!=mode or trial['product']!=product or trial['seed']!=9173:
            raise ValueError('Unexpected C1 identity or seed')
        references={}
        for relative in ('trial.json','measure/result.json','measure/fio.json','verify/result.json',
                         'verify/fio.json','verify/exit.json','verify/stderr.log'):
            path=folder/relative
            if path.is_file(): references[relative]={'sha256':sha(path),'bytes':path.stat().st_size}
        measure=load(folder/'measure/result.json') or {}
        verify=load(folder/'verify/result.json') or {}
        exit_info=load(folder/'verify/exit.json') or {}
        cold=bool(verify.get('passed') and exit_info.get('exit_code')==0 and
                  any(e['stage']=='verify-unmount' and e['passed'] for e in trial['events']))
        drains={}
        for phase in ('prepare','measure','verify'):
            proof=load(folder/f'{phase}-drain/result.json') or {}
            zeros=proof.get('zero_samples',[])
            drains[phase]=bool(proof.get('passed') and len(zeros)>=2 and
                               zeros[-1]['at_ns']-zeros[-2]['at_ns']>=1_000_000_000 and
                               all(z['native_gauges'] and all(v==0 for v in z['native_gauges'].values()) for z in zeros[-2:]))
        measure_ok=bool(measure.get('passed') and measure.get('completed') and
                        measure.get('application_bytes')==67108864 and measure.get('jobs')==1)
        if trial['passed'] and not (cold and measure_ok and all(drains.values())):
            raise ValueError('Passing trial lacks complete fio, cold verification or native debt proof')
        row=dict(mode=mode,product=product,finished=True,passed=trial['passed'],
                 eligible_for_formal_performance=False,cold_verified=cold,native_zero_debt=drains,
                 error=trial.get('error'),detail=trial.get('detail'),
                 verify_exit_code=exit_info.get('exit_code'),
                 measure_wrapper_seconds=measure.get('wrapper_wall_seconds'),
                 measure_application_bytes=measure.get('application_bytes'),
                 fio_params=trial.get('fio_params'),source=str(folder),references=references)
        if not trial['passed']:
            path=folder/'verify/stderr.log'
            row['verify_stderr_tail']=path.read_text(errors='replace')[-4000:] if path.is_file() else None
        jobs=measure.get('native_jobs',[])
        if len(jobs)==1:
            row['fio_job_runtime_ms']=jobs[0].get('job_runtime')
            row['fio']={kind:io_metrics(jobs[0],kind) for kind in ('read','write','sync')}
        rows.append(row)
    return dict(run=root.name,schema='ax-idcai-c1-summary-v1',formal_sample=False,
                expected_cases=len(CASES),finished_cases=sum(r['finished'] for r in rows),
                passed_cases=sum(r['passed'] for r in rows),all_passed=all(r['passed'] for r in rows),
                metric_scope='Native fio application bytes, IOPS, bandwidth and completion latency; sync latency is separate. Measurement timing excludes later drain/cold verification. Failed cold checks do not qualify.',
                cases=rows)


if __name__=='__main__':
    root=Path(sys.argv[1])
    if not root.is_dir() or not re.fullmatch(r'ax-four-[0-9a-f]{12}',root.name):
        raise ValueError('Existing IDCAI run directory required')
    print(json.dumps(summarize(root),indent=2))
