#!/usr/bin/env python3
"""Extract small, shareable receipts and phase durations from a smoke matrix."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def summarize(root):
    rows=[]
    for cell in json.loads((root/'summary.json').read_text()):
        receipt_path=root/cell['receipt']
        receipt=json.loads(receipt_path.read_text())
        phases=[]
        for path in receipt_path.parent.rglob('stages.jsonl'):
            starts={}
            for line in path.read_text().splitlines():
                event=json.loads(line)
                if event['event']=='start':
                    starts[event['id']]=event
                elif event['event']=='end':
                    start=starts.pop(event['id'],{})
                    phases.append(dict(name=event['name'],seconds=event['elapsed_ns']/1e9,
                                       status=event['status'],error=event.get('error'),
                                       argv=start.get('details',{}).get('argv')))
            for event in starts.values():
                phases.append(dict(name=event['name'],seconds=None,status='unfinished'))
        evidence=receipt.get('evidence',{})
        arms=evidence.get('arms',{})
        issues=[dict(arm=name,phase=arm.get('phase'),error=arm.get('error'))
                for name,arm in arms.items() if isinstance(arm,dict) and arm.get('error')]
        rows.append(dict(name=cell['name'],status=receipt['status'],
            seconds=receipt.get('elapsed_ns',0)/1e9 if 'elapsed_ns' in receipt else None,
            reason=receipt.get('reason',evidence.get('error')),issues=issues,
            receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            phases=phases,cleanup={k:evidence[k] for k in ('mounts_absent','rustfs_removed',
                'redis_exited','scratch_removed','cleanup') if k in evidence}))
    return dict(formal_sample=False,counts=dict(Counter(row['status'] for row in rows)),cases=rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    report=summarize(args.root)
    with args.out.open('x') as stream:
        json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps(report['counts']))


if __name__=='__main__':
    main()
