#!/usr/bin/env python3
"""Validate and summarize one C3 cohort receipt without modifying raw evidence."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import runpy


PHASES = (('serial-first-a', 1, 0), ('parallel-first-b', 32, 16),
          ('serial-repeat-b', 1, 16), ('parallel-repeat-a', 32, 0))
OPS = ('lstat', 'open', 'pread', 'close')
WORKER = Path(__file__).with_name('qualify-c3-workspace-base.py')


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def expected_path(index):
    return f'small/d{index % 1024:04d}/f{index:07d}'


def percentiles(values):
    ordered = sorted(values)
    n = len(ordered)
    return {f'p{p}_ns': ordered[(n*p+99)//100-1] if n else None for p in (50,95)}


def read_events(blob, full):
    rows = blob.splitlines(keepends=True)
    trailing = 0
    if rows and not rows[-1].endswith(b'\n'):
        need(not full, 'Complete attempt contains an unfinished last event')
        trailing = len(rows.pop())
    need(len(rows) <= 1160, 'Too many events for the fixed workload')
    return [json.loads(line) for line in rows], trailing


def reduce_events(rows, payload, indices):
    seen = []
    phase = None
    last = -1
    for row in rows:
        now = row['monotonic_ns']
        need(type(now) is int and now >= max(0,last), 'Event clock moved backwards')
        last = now
        kind = row['event']
        if kind == 'phase-start':
            need((phase is None or phase['terminal'] is not None) and len(seen) < len(PHASES), 'Overlapping or extra phase')
            name, workers, offset = PHASES[len(seen)]
            need((row['phase'],row['workers']) == (name,workers), 'Unexpected phase order or workers')
            phase = dict(name=name,workers=workers,start=now,terminal=None,
                         expected={expected_path(i): i for i in indices(offset)},state=defaultdict(dict),
                         verified=set(),timed=[],errors=[],pending={})
            seen.append(phase)
            continue
        need(phase and phase['terminal'] is None and row['phase'] == phase['name'], 'Event outside its active phase')
        if kind == 'phase-end':
            need(len(phase['verified'])==row['files']==32 and row['bytes']==32*4096
                 and not phase['pending'] and not phase['errors']
                 and type(row['elapsed_ns']) is int and 0 <= row['elapsed_ns'] <= now-phase['start'],
                 'Invalid completed phase counts or time')
            phase['terminal'] = row['elapsed_ns']
            continue
        path = row['path']
        need(path in phase['expected'] and path not in phase['verified'], 'Unexpected or repeated cohort path')
        state = phase['state'][path]
        if kind == 'verified':
            need(list(state)==list(OPS) and set(state.values())=={'end'} and row['bytes']==4096
                 and row['sha256']==sha(payload(phase['expected'][path])), 'Verification without four good calls or exact payload')
            phase['verified'].add(path)
            continue
        op = row['operation']
        need(op in OPS, 'Unknown operation')
        key = path,op
        if kind == 'start':
            order = OPS.index(op)
            need(op not in state and list(state)==list(OPS[:order]) and all(
                 state[prior]=='end' or (op=='close' and prior=='pread' and state[prior]=='error')
                 for prior in state), 'Misordered or repeated start')
            state[op] = 'start'
            phase['pending'][key] = now
            continue
        need(kind in ('end','error') and state.get(op)=='start' and key in phase['pending'],
             'Missing or repeated operation start')
        duration = row['elapsed_ns']
        need(type(duration) is int and 0 <= duration <= now-phase['pending'].pop(key), 'Invalid operation duration')
        state[op] = kind
        item = dict(path=path,operation=op,elapsed_ns=duration)
        if kind=='error':
            need(row['errno'] is None or type(row['errno']) is int, 'Invalid errno')
            phase['errors'].append(dict(item,errno=row['errno']))
        else:
            phase['timed'].append(item)
    return seen, last


def summarize_phase(phase, last):
    completed, errors = phase['timed'], phase['errors']
    pending = [dict(path=p,operation=o,observed_wait_lower_bound_ns=last-start)
               for (p,o),start in phase['pending'].items()]
    stats = {}
    for op in OPS:
        ns = [r['elapsed_ns'] for r in completed if r['operation']==op]
        stats[op] = dict(completed=len(ns),errored=sum(r['operation']==op for r in errors),
                         unfinished=sum(r['operation']==op for r in pending),sum_ns=sum(ns),**percentiles(ns))
    total = max(stats,key=lambda name:stats[name]['sum_ns'])
    return dict(phase=phase['name'],workers=phase['workers'],completed=phase['terminal'] is not None,
                elapsed_ns=phase['terminal'],verified_files=len(phase['verified']),
                verified_bytes=len(phase['verified'])*4096,operations=stats,errors=errors,
                dominant_completed_operation=total if completed else None,
                slowest_completed=max(completed,key=lambda x:x['elapsed_ns'],default=None),
                unfinished=sorted(pending,key=lambda x:-x['observed_wait_lower_bound_ns']))


def run(root):
    evidence = root/'receipt' if (root/'receipt').is_dir() else root
    pins = {}

    def read(name, location=evidence, optional=False):
        path = location/name
        if optional and not path.exists():
            return None
        blob = path.read_bytes()
        need(len(blob)<=1024**2, f'Oversize receipt: {name}')
        pins[name] = sha(blob)
        return blob

    stage = json.loads(read('stage.json',root))
    intent = json.loads(read('worker-intent.json'))
    ready = json.loads(read('worker-ready.json'))
    closed = json.loads(read('worker-mount-exit.json'))
    unit_rows = read('unit-result.txt',root).decode().splitlines()
    unit = dict(row.split('=',1) for row in unit_rows)
    need(len(unit)==len(unit_rows), 'Duplicate terminal unit field')
    native_blob = read('worker-cohort-result.json',optional=True)
    native = json.loads(native_blob) if native_blob is not None else None
    events = read('worker-cohort-events.ndjson')
    need(stage['tag']==intent['tag'] and re.fullmatch(r'c3-current-base-[0-9]{8}t[0-9]{6}z',intent['tag'])
         and stage['database']==intent['redis_db'] and stage['worker_sha256']==sha(WORKER.read_bytes())
         and stage['diagnostic_cohort'] is True and intent['diagnostic_cohort'] is True
         and stage['trace_packed_budget'] is True and intent['trace_packed_budget'] is True
         and stage['parallel_sha256'] is None and intent['formal_sample'] is False and intent['automatic_retry'] is False
         and stage['workers']==intent['verifier_workers']==32 and stage['verify_seconds']==intent['verify_timeout_seconds']==120
         and stage['packed_output_mib']*2**20==intent['packed_output_budget_bytes']==128*2**20
         and stage['packed_control_mib']*2**20==intent['packed_control_budget_bytes']==8*2**20,
         'Staging and fixed worker intent do not bind the same diagnostic')
    need(type(ready['pid']) is int and ready['pid'] > 0 and ready['mount']['id']>0
         and ready['mount']['type'].startswith('fuse'), 'No FUSE identity in ready receipt')
    need(closed==dict(unmount_exit=0,exit_code=0,mount_absent=True), 'Mount did not close normally')
    need(unit.get('MainPID')=='0' and unit.get('Result') in ('success','exit-code','timeout')
         and (unit['Result']=='success') == (unit.get('ExecMainStatus')=='0')
         and (native is not None) == (unit['Result']=='success'), 'No consistent terminal coordinator exit')
    rows, trailing = read_events(events,native is not None)
    bindings = runpy.run_path(str(WORKER))
    seen, last = reduce_events(rows,bindings['base_payload'],bindings['cohort_indices'])
    need(not seen or last-seen[0]['start'] <= 122*10**9, 'Events exceed verifier deadline plus shutdown tolerance')
    complete = len(seen)==4 and all(x['terminal'] is not None for x in seen)
    if native is not None:
        need(complete and native['passed'] is True and native['formal_sample'] is False
             and native['phases']==[dict(phase=x['name'],workers=x['workers'],files=32,bytes=131072,
                                       elapsed_ns=x['terminal']) for x in seen], 'Native result does not match raw events')
    return dict(schema='c3-packed-cohort-summary-v1',tag=intent['tag'],formal_sample=False,
                native_complete=native is not None,all_phase_events_complete=complete,
                unit=unit,mount=ready,raw_event_count=len(rows),ignored_partial_tail_bytes=trailing,
                phases=[summarize_phase(x,last) for x in seen],
                phases_never_started=[x[0] for x in PHASES[len(seen):]],source_sha256=pins,
                quantile_scope='Nearest rank over completed successful calls only; overlapping elapsed times are not makespan.',
                unfinished_scope='Lower bound from operation start to final observed event; receipt has no end timestamp for it.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    result=run(args.directory)
    with args.out.open('x') as stream:
        json.dump(result,stream,indent=2,sort_keys=True)
        stream.write('\n')
