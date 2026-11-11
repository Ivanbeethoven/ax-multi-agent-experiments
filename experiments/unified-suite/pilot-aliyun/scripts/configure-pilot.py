#!/usr/bin/env python3
"""Prepare one phase's actual suite config, or a clearly non-executable draft."""
import argparse
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.suite_catalog import template, select_pilot_files
from bench.suite_plan import build
from bench.runtime.pilot_pools import inventories,resource_ids,alias


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['c123','c1','c2','c3','fork','handoff'], required=True)
    parser.add_argument('--files', type=int, choices=[100000,1000000],
                        help='Explicit file count for a new C3/fork pilot; legacy defaults remain one million')
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--inventory', type=Path, help='Actual phase inventory with roles, instance IDs and disk IDs')
    parser.add_argument('--build-manifest', type=Path)
    parser.add_argument('--input-manifest', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--handoff-task-id',help='Actual fixed session input used by this handoff batch')
    args = parser.parse_args()
    actual = json.loads(args.inventory.read_text()) if args.inventory else None
    multiple = actual is not None and len(inventories(actual))>1
    if args.files is not None and args.phase not in ('c3','fork') and not (multiple and args.phase=='c123'):
        parser.error('files requires a separate C3 or fork phase; never rewrite a running C1/C2 batch')
    if args.handoff_task_id and (args.phase!='handoff' or not re.fullmatch('[a-z0-9][a-z0-9-]{2,63}',args.handoff_task_id)):
        parser.error('handoff-task-id requires handoff phase and a simple fixed input ID')
    if any((args.inventory, args.build_manifest, args.input_manifest)) and not all((args.inventory, args.build_manifest, args.input_manifest)):
        parser.error('Provide all three actual inventory/build/input documents, or none for a draft')
    config = template()
    config['campaign_id'] = args.campaign
    config['placement'] = json.loads((REPO/'experiments/unified-suite/pilot-aliyun/scope.json').read_text())['placement']
    if multiple:
        if {p['pool_id'] for p in inventories(actual)}!={'pool-a','pool-b'}:
            raise ValueError('Default parallel pilot requires explicit pool-a and pool-b inventories')
        config['placement']={'mode':'baseline-parallel','product_pools':{'brewfs':['pool-a'],'juicefs':['pool-b']}}
    config['metadata_backend'] = 'redis'
    config['cases'] = [c for c in config['cases'] if (c['suite'] in ('C1','C2','C3') if args.phase=='c123'
                       else c['suite']==args.phase.upper() if args.phase in ('c1','c2','c3') else c['workload']==args.phase)]
    if args.files is not None:
        select_pilot_files(config, args.files)
    if args.handoff_task_id:
        for case in config['cases']:case['params']['trace']=args.handoff_task_id
    roles = ['client','metadata','objects'] + (['target'] if args.phase=='handoff' else [])
    prototype = config['clusters'][0]
    prototype.update(metadata_backend='redis', agent_slots=2 if args.phase=='handoff' else 1,
                supported_suites=['C1','C2','C3'] if args.phase=='c123' else [args.phase.upper()] if args.phase in ('c1','c2','c3') else ['C4'],
                observer_ids=['pool-a-'+role for role in roles])
    if args.inventory:
        clusters=[]
        for inventory in inventories(actual):
            if set(inventory['roles']) != set(roles):
                raise ValueError('Phase inventory must contain exactly its actual roles')
            pool=deepcopy(prototype);pool_id=inventory['pool_id']
            pool.update(id=pool_id,resource_ids=resource_ids(inventory),inventory_sha256=digest(args.inventory),
                        observer_ids=[alias(pool_id,role) for role in roles])
            clusters.append(pool)
        if not multiple and clusters[0]['id']!='pool-a':
            raise ValueError('Legacy single-pool placement requires pool-a')
        config['clusters']=clusters
        config['build_manifest_sha256'] = digest(args.build_manifest)
        config['input_manifest_sha256'] = digest(args.input_manifest)
    else:
        prototype['resource_ids'] = ['host:unresolved-'+role for role in roles]
        config['clusters']=[prototype]
    plan = build(config)
    expected = {'c123':19, 'c1':10, 'c2':5, 'c3':4, 'fork':5, 'handoff':4}[args.phase]
    if len(plan['cells']) != expected:
        raise ValueError('Unexpected pilot matrix; inspect catalog before changing scope')
    args.out.mkdir(parents=True, exist_ok=False)
    for name, value in [('config.json',config),('plan.json',plan)]:
        (args.out/name).write_text(json.dumps(value, indent=2)+'\n')
    print(json.dumps(dict(phase=args.phase, cells=len(plan['cells']),
                          provenance_bound=plan['provenance_bound'], observers=[o for p in config['clusters'] for o in p['observer_ids']],
                          note='Drafts cannot be claimed. Actual provenance binding does not replace per-cell qualification.')))


if __name__ == '__main__':
    main()
