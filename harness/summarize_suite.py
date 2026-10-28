#!/usr/bin/env python3
"""Summarize diagnostic observations without pooling different input/control settings."""
import argparse
from collections import Counter,defaultdict
import json
import math
from pathlib import Path
import re
import statistics


def summarize(roots):
    groups=defaultdict(list);statuses=Counter();runs=[]
    for root in roots:
        root=Path(root);result=json.loads((root/'result.json').read_text())
        statuses[result['status']]+=1
        runs.append({'root':str(root),'status':result['status'],'reason':result.get('reason'),
                     'batch_id':result.get('batch_id'),'runner_wall_seconds':result.get('runner_wall_seconds')})
        # Retain failed/interrupted batches above, never insert zeros into performance.
        if result['status']!='ok':continue
        for path in root.glob('*.json'):
            v=json.loads(path.read_text())
            if not isinstance(v,dict) or v.get('measurement_scope')!='application' or v.get('status')!='ok':continue
            if v.get('mode') in ('prepare','verify'):continue
            label=re.sub(r'-\d+$','',path.stem)
            key=json.dumps({'method':result['method'],'suite':result['mode'],'arm':label,
                'mode':v['mode'],'files':v['files'],'file_bytes':v['file_bytes'],'operations':v['operations'],
                'seed':v['seed'],'requested_controls':v['requested_controls']},sort_keys=True)
            groups[key].append({'source':str(path),**v})
    entries=[]
    for key,rows in sorted(groups.items()):
        stats={}
        for field in ['wall_seconds','iops','p95_us','p99_us','read_bytes','write_bytes']:
            values=[r[field] for r in rows if r.get(field) is not None]
            if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in values):
                raise ValueError('invalid measurement: '+field)
            stats[field]={'raw':values,'median':statistics.median(values) if values else None,
                          'min':min(values) if values else None,'max':max(values) if values else None}
        entries.append({**json.loads(key),'samples':len(rows),'statistics':stats,'sources':[r['source'] for r in rows]})
    return {'status_counts':dict(statuses),'runs':runs,'entries':entries,'formal_qualified':False,
            'scope':'diagnostic repeats only; no inference of independent replication or mechanism qualification'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('runs',type=Path,nargs='+');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=summarize(a.runs)
    with a.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False)
