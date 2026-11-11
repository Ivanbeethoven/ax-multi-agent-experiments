#!/usr/bin/env python3
"""Combine existing pool cost receipts; charge every owned resource once."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path


def read(path):
    data=path.read_bytes()
    return json.loads(data),dict(path=str(path),sha256=hashlib.sha256(data).hexdigest())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool',type=Path,action='append',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    entries=[read(path) for path in a.pool]
    documents=[d for d,_ in entries]
    amounts=('estimated_new_resources_cny','active_quoted_hourly_cny')
    all_resources=[r for d in documents for r in d['resources']]
    ids=[r['resource_id'] for r in all_resources]
    if not ids or len(set(ids))!=len(ids):
        raise ValueError('Empty or overlapping cost ledgers')
    times=[datetime.fromisoformat(d['at']) for d in documents]
    if any(t.tzinfo is None for t in times) or (max(times)-min(times)).total_seconds()>60:
        raise ValueError('Use one recent time window for all receipts')
    excluded=documents[0]['excluded']
    if any(d['currency']!='CNY' or d['excluded']!=excluded for d in documents):
        raise ValueError('Different currency or scope')
    for d in documents:
        for value,key in zip(amounts,('estimated_cny','active_quoted_hourly_cny')):
            if abs(d[value]-sum(r[key] for r in d['resources']))>1e-7:
                raise ValueError('Pool total differs from its resource rows')
    result=dict(at=max(times).isoformat(),currency='CNY',owned_resources=len(ids),
        active_resources=sum(not r['released'] for r in all_resources),
        **{key:sum(d[key] for d in documents) for key in amounts},
        sources=[pin for _,pin in entries],excluded=excluded,hard_budget_cap=False,
        scope='Elapsed estimate from original creation receipts and dated quotes; not a settled bill.')
    with a.out.open('x',encoding='utf-8') as f:
        json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result))


if __name__=='__main__':main()
