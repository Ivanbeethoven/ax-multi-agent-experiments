#!/usr/bin/env python3
"""Prepare bounded random pauses with an exact total budget; never replay."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from experiment_io import digest, load, write_new

MAX_MS = 15000
ALGORITHM = 'sha256-feasible-allocation-shuffle-v1'


def allocate(count, total_ms, seed, agent_id):
    if type(count) is not int or count < 1 or type(total_ms) is not int or not 0 <= total_ms <= count * MAX_MS:
        raise ValueError('need 0 <= total_ms <= boundary_count * 15000')
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('seed must be a uint64')
    counter = 0
    def draw(low, high):
        nonlocal counter
        width = high - low + 1
        limit = 2**256 - 2**256 % width
        while True:
            raw = json.dumps([ALGORITHM, seed, agent_id, counter], separators=(',', ':'), ensure_ascii=True).encode()
            counter += 1
            value = int.from_bytes(hashlib.sha256(raw).digest(), 'big')
            if value < limit:
                return low + value % width
    remaining = total_ms
    values = []
    for i in range(count):
        low = max(0, remaining - (count-i-1)*MAX_MS)
        value = draw(low, min(MAX_MS, remaining))
        values.append(value)
        remaining -= value
    for i in range(count-1, 0, -1):
        j = draw(0, i)
        values[i], values[j] = values[j], values[i]
    return values


def prepare(events, boundary_file, total_ms, seed, agent_id, agent_count):
    if type(agent_count) is not int or agent_count < 2:
        raise ValueError('LLM simulation is only for an explicitly planned multi-agent replay')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', agent_id):
        raise ValueError('stable logical agent_id required; do not include baseline or host identity')
    events, boundary_file = Path(events), Path(boundary_file)
    source = load(boundary_file)
    if source.get('schema') != 'agent-boundaries-v1' or source.get('reviewed') is not True:
        raise ValueError('reviewed tool/turn boundary map required; no inferred filesystem-idle gaps')
    evidence = source.get('evidence', [])
    if not evidence:
        raise ValueError('boundary source evidence required')
    for item in evidence:
        p = (boundary_file.parent / item['path']).resolve()
        if not p.is_relative_to(boundary_file.parent.resolve()) or not p.is_file() or digest(p) != item['sha256']:
            raise ValueError('missing/changed boundary source evidence')
    boundaries = source.get('boundaries', [])
    ids = set(); previous = 1
    for b in boundaries:
        seq = b.get('before_seq')
        if type(seq) is not int or seq <= previous or b.get('kind') not in ('tool', 'turn'):
            raise ValueError('strictly increasing tool/turn boundaries after init required')
        if not isinstance(b.get('id'), str) or not b['id'] or b['id'] in ids:
            raise ValueError('unique boundary id required')
        if type(b.get('evidence_index')) is not int or not 0 <= b['evidence_index'] < len(evidence) or not b.get('evidence_locator'):
            raise ValueError('each boundary must reference its aligned source evidence')
        ids.add(b['id']); previous = seq
    waits = allocate(len(boundaries), total_ms, seed, agent_id)
    wanted = {b['before_seq'] for b in boundaries}; count = 0; sha = hashlib.sha256(); last = None
    with events.open('rb') as f:
        for raw in f:
            sha.update(raw); e = json.loads(raw); count += 1
            if type(e.get('seq')) is not int or e['seq'] != count or (count == 1 and e.get('op') != 'init'):
                raise ValueError('trace sequence/init invalid')
            if count in wanted and e.get('op') in ('init', 'destroy'):
                raise ValueError('cannot pause at lifecycle boundary')
            last = e.get('op')
    if count < 2 or last != 'destroy' or max(wanted) >= count or sha.hexdigest() != source.get('events_sha256'):
        raise ValueError('trace digest/end/boundary range mismatch')
    return {'schema':'trace-think-plan-v1', 'algorithm':ALGORITHM, 'simulate_llm':True,
            'events_sha256':sha.hexdigest(), 'events_operations':count,
            'boundary_map_sha256':digest(boundary_file), 'evidence':evidence,
            'agent_id':agent_id, 'agent_count':agent_count, 'seed':seed,
            'total_think_ms':total_ms, 'max_interval_ms':MAX_MS,
            'distribution':'bounded correlated allocation with exact sum; not independent uniform draws',
            'boundaries':[dict(b, delay_ms=wait) for b,wait in zip(boundaries, waits)],
            'execution_enabled':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--events', required=True); p.add_argument('--boundaries', required=True)
    p.add_argument('--total-think-seconds', type=Decimal, required=True)
    p.add_argument('--seed', type=int, required=True); p.add_argument('--agent-id', required=True)
    p.add_argument('--agent-count', type=int, required=True); p.add_argument('--output', required=True)
    a=p.parse_args(); ms=a.total_think_seconds*1000
    if not ms.is_finite() or ms < 0 or ms != ms.to_integral_value():
        p.error('total-think-seconds must be nonnegative with millisecond precision')
    write_new(a.output, prepare(a.events,a.boundaries,int(ms),a.seed,a.agent_id,a.agent_count))


if __name__ == '__main__': main()
