#!/usr/bin/env python3
"""One predeclared C4 on/off trial. The outer batch retains canonical locks."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
loader=importlib.util.spec_from_file_location('c4_trial_runner',Path(__file__).with_name('run-c4.py'))
runner=importlib.util.module_from_spec(loader);loader.loader.exec_module(runner)
from bench.runtime.common import load,pinned,write_new,digest
from bench.runtime.pilot_pools import fleet_node


def ref(path):return dict(path=str(path),sha256=digest(path))


def run(request):
    s=load(pinned(request))
    if s.get('reviewed') is not True or s.get('purpose') not in ('observer-calibration-on','observer-calibration-off'):
        raise ValueError('Reviewed explicit on/off C4 trial required')
    out=Path(s['out']);out.mkdir(parents=True,exist_ok=False)
    write_new(out/'request.json',s);c=runner.Fork(out/'request.json')
    c.check_sources()
    old=s['collector_name'];old_out=s['collector_out']
    running=c.remote('objects',['docker','inspect','--format','{{.State.Running}}',old]).strip()=='true'
    tool=fleet_node(c.fleet,c.pool,'objects')['repo']+'/experiments/unified-suite/pilot-aliyun/scripts/metric-collector.py'
    if c.purpose.endswith('-off') and running:
        c.remote('objects',['python3',tool,'stop','--name',old,'--out',old_out])
    elif c.purpose.endswith('-on') and not running:
        new=s['new_collector_name'];new_out=s['new_collector_out']
        if new==old:raise ValueError('A stopped collector is never restarted')
        c.remote('objects',['python3',tool,'start','--image-receipt','/srv/ax-objects-0/assets/provenance/otel-collector.json',
            '--name',new,'--out',new_out]);s.update(collector_name=new,collector_out=new_out)
    write_new(out/'context.json',s);c=runner.Fork(out/'context.json')
    for stage in c.cell['stages']:c.execute(stage)
    q=out/'raw/client/q'/c.tag
    write_new(out/'finished.json',dict(passed=True,pool=c.pool,purpose=c.purpose,
        cohort=ref(out/'cohort/result.json'),client_verify=ref(q/'verify/result.json'),
        parent_verify=ref(q/'verify/parent.json'),native_exit=ref(q/'exit.json'),verify_exit=ref(q/'verify-exit.json'),
        closure=ref(out/'closure.json'),work=ref(out/'work-check.json'),archives=ref(out/'archives.json'),
        collector_name=s['collector_name'],collector_out=s['collector_out'],automatic_retry=False))
    return load(out/'finished.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    a=p.parse_args();print(json.dumps(run(dict(path=a.request,sha256=a.sha256))))
