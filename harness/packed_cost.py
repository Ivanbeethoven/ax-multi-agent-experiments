#!/usr/bin/env python3
"""Same-unit additive resource accounting; never sum overlapping wall times."""
import argparse
import json
import math
from pathlib import Path


def account(data):
    if data['unit'] not in ('cpu_seconds','requests','transferred_bytes','retained_byte_seconds','currency'):
        raise ValueError('use an additive resource unit; makespan must be measured directly')
    if data['unit']=='currency' and not (data.get('price_date') and data.get('prices')):
        raise ValueError('currency requires explicit prices/date')
    result=[]
    if len(data['native']['Q']) != len(data['pack']['Q']): raise ValueError('different reuse counts')
    for method in ('native','pack'):
        arm=data[method]
        for x in [arm['F'],*arm['Q'],*arm['U'],*arm['G']]:
            if not isinstance(x,(int,float)) or not math.isfinite(x) or x<0:
                raise ValueError('missing/nonfinite/negative measured cost')
        if len(arm['Q']) != len(arm['U']) or len(arm['Q']) != len(arm['G']):
            raise ValueError('U/G are measured incremental costs at each reuse, not repeated totals')
    totals={m:data[m]['F'] for m in ('native','pack')}
    for i in range(len(data['native']['Q'])):
        for m in totals: totals[m]+=sum(data[m][k][i] for k in ('Q','U','G'))
        result.append({'K':i+1,**totals,'pack_minus_native':totals['pack']-totals['native']})
    return {'unit':data['unit'],'rows':result,
            'first_observed_crossover_K':next((r['K'] for r in result if r['pack_minus_native']<=0),None),
            'claim':'observed resource totals only; no extrapolated or permanent payback guarantee'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();result=account(json.loads(a.input.read_text()))
    with a.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False)
