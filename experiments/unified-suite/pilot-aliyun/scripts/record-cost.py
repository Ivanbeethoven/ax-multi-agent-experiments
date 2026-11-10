#!/usr/bin/env python3
"""Save elapsed cloud cost from actual creation receipts and a dated live quote.

This is a prorated estimate, not a settled Aliyun bill. No cloud API or mutation.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path


def ref(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--quote',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--control-disk-hourly',type=float,default=.105)
    a=p.parse_args();at=datetime.now(timezone.utc);quote=json.loads(a.quote.read_text());rows=[]
    rates={r['role']:r['hourly_trade_price'] for r in quote['roles']}
    for receipt in sorted(a.ledger.glob('*.created.json')):
        s=json.loads(receipt.read_text())
        if s.get('preexisting'):continue
        role=s.get('role') or ('coordinator-disk' if receipt.name=='coordinator-disk.created.json' else None)
        rate=a.control_disk_hourly if role=='coordinator-disk' else rates[role]
        start=datetime.fromisoformat(s['at']);hours=(at-start).total_seconds()/3600
        if hours<0 or rate<0:raise ValueError('Invalid elapsed time or rate')
        # A deletion request is not proof that charging has stopped.
        rows.append(dict(role=role,resource_id=s.get('disk_id') or s['instance_id'],
            start=s['at'],through=at.isoformat(),hours=hours,quoted_hourly_cny=rate,
            estimated_cny=hours*rate,creation=ref(receipt)))
    result=dict(at=at.isoformat(),currency='CNY',quote=ref(a.quote),resources=rows,
        estimated_new_resources_cny=sum(r['estimated_cny'] for r in rows),
        active_quoted_hourly_cny=sum(r['quoted_hourly_cny'] for r in rows),hard_budget_cap=False,
        excluded=['Reused jump compute and its existing system disk/EIP','Network traffic','OSS requests/storage','Billing granularity and adjustments'],
        control_disk_rate_source='Dated pilot ESSD PL0 quote: 100 GiB at CNY0.00105/GiB/hour',
        scope='Prorated estimate from actual creation times. Counts created resources through this timestamp until release is independently confirmed; not a final bill.')
    with a.out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:result[k] for k in ['at','estimated_new_resources_cny','active_quoted_hourly_cny']}))


if __name__=='__main__':main()
