#!/usr/bin/env python3
"""Save elapsed cloud cost from actual creation receipts and a dated live quote.

This is a prorated estimate, not a settled Aliyun bill. No cloud API or mutation.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path


def ref(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def timestamp(value):
    result=datetime.fromisoformat(value)
    if result.tzinfo is None:raise ValueError('Explicit UTC offset required')
    return result


def events(path,known,at):
    if path is None:return {}
    document=json.loads(path.read_text())
    if document.get('schema')!='ax-pilot-cost-events-v1' or document.get('reviewed') is not True:
        raise ValueError('Reviewed cost event list required')
    grouped={}
    for event in document['events']:
        resource=event['resource_id'];when=timestamp(event['at'])
        if resource not in known or when>at:raise ValueError('Unknown resource or future cost event')
        proof_file=Path(event['evidence']['path'])
        if ref(proof_file)!=event['evidence']:raise ValueError('Cost event evidence changed')
        proof=json.loads(proof_file.read_text())
        if proof.get('passed') is not True or proof.get('resource_id')!=resource or proof.get('at')!=event['at']:
            raise ValueError('Cost event evidence identity/time differs')
        if event['kind']=='rate-change':
            rate=event['quoted_hourly_cny']
            if (type(rate) not in (int,float) or not math.isfinite(rate) or rate<0 or
                    proof.get('configuration_verified') is not True or proof.get('quoted_hourly_cny')!=rate or
                    not proof.get('quote')):
                raise ValueError('Verified actual configuration and dated new quote required')
            quote_file=Path(proof['quote']['path'])
            if ref(quote_file)!=proof['quote']:raise ValueError('New quote changed')
        elif event['kind']=='release-confirmed':
            if proof.get('released') is not True or proof.get('all_owned_disks_absent') is not True:
                raise ValueError('Actual absence of the resource and every owned disk required')
        else:raise ValueError('Unknown cost event kind')
        grouped.setdefault(resource,[]).append(event)
    for resource,rows in grouped.items():
        rows.sort(key=lambda row:timestamp(row['at']))
        if len({row['at'] for row in rows})!=len(rows):raise ValueError('Duplicate cost event time')
        if any(row['kind']=='release-confirmed' for row in rows[:-1]):
            raise ValueError('No cost changes after confirmed release')
    return grouped


def intervals(start,at,rate,rows):
    if start>at or type(rate) not in (int,float) or not math.isfinite(rate) or rate<0:
        raise ValueError('Invalid initial elapsed time or rate')
    result=[];cursor=start;released=False
    for event in rows:
        end=timestamp(event['at'])
        if end<cursor:raise ValueError('Cost event precedes resource creation')
        hours=(end-cursor).total_seconds()/3600
        result.append(dict(start=cursor.isoformat(),through=end.isoformat(),hours=hours,
            quoted_hourly_cny=rate,estimated_cny=hours*rate,end_event=event))
        cursor=end
        if event['kind']=='release-confirmed':released=True;rate=0
        else:rate=event['quoted_hourly_cny']
    if not released:
        hours=(at-cursor).total_seconds()/3600
        result.append(dict(start=cursor.isoformat(),through=at.isoformat(),hours=hours,
            quoted_hourly_cny=rate,estimated_cny=hours*rate))
    return result,rate,released,cursor if released else at


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--quote',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--control-disk-hourly',type=float,default=.105)
    p.add_argument('--events',type=Path,help='Verified dated resize/rate changes and confirmed releases; requests alone never end charging')
    a=p.parse_args();at=datetime.now(timezone.utc);quote=json.loads(a.quote.read_text());rows=[]
    rates={r['role']:r['hourly_trade_price'] for r in quote['roles']}
    created=[(receipt,json.loads(receipt.read_text())) for receipt in sorted(a.ledger.glob('*.created.json'))]
    created=[(receipt,s) for receipt,s in created if not s.get('preexisting')]
    known={s.get('disk_id') or s['instance_id'] for _,s in created}
    changes=events(a.events,known,at)
    for receipt,s in created:
        role=s.get('role') or ('coordinator-disk' if receipt.name=='coordinator-disk.created.json' else None)
        rate=a.control_disk_hourly if role=='coordinator-disk' else rates[role]
        resource=s.get('disk_id') or s['instance_id'];start=timestamp(s['at'])
        segments,active_rate,released,end=intervals(start,at,rate,changes.get(resource,[]))
        rows.append(dict(role=role,resource_id=resource,
            start=s['at'],through=end.isoformat(),hours=(end-start).total_seconds()/3600,
            quoted_hourly_cny=rate,active_quoted_hourly_cny=active_rate,released=released,
            estimated_cny=sum(segment['estimated_cny'] for segment in segments),segments=segments,creation=ref(receipt)))
    result=dict(at=at.isoformat(),currency='CNY',quote=ref(a.quote),resources=rows,
        estimated_new_resources_cny=sum(r['estimated_cny'] for r in rows),
        active_quoted_hourly_cny=sum(r['active_quoted_hourly_cny'] for r in rows),hard_budget_cap=False,
        excluded=['Reused jump compute and its existing system disk/EIP','Network traffic','OSS requests/storage','Billing granularity and adjustments'],
        control_disk_rate_source='Dated pilot ESSD PL0 quote: 100 GiB at CNY0.00105/GiB/hour',
        scope='Prorated estimate from actual creation times. Counts created resources through this timestamp until release is independently confirmed; not a final bill.')
    if a.events:result['events']=ref(a.events)
    with a.out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:result[k] for k in ['at','estimated_new_resources_cny','active_quoted_hourly_cny']}))


if __name__=='__main__':main()
