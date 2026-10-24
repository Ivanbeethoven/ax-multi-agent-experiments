#!/usr/bin/env python3
"""Make an immutable task list, without launching containers or model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import random

CASES = ('django', 'sklearn', 'sympy', 'pytest-full-02')
SYSTEMS = ('juicefs', 'brewfs', 'brewfs-no-shared', 'brewfs-no-packed',
           'brewfs-no-shared-no-packed', 'brewfs-full-copy', 'brewfs-fixed-layout', 'brewfs-copy-restore')


def build(systems, profile):
    if not systems or len(systems) != len(set(systems)):
        raise ValueError('Supply distinct, already-qualified system labels')
    if any(not s or not all(c.isalnum() or c in '-_' for c in s) for s in systems):
        raise ValueError('System labels must be letters, numbers, hyphens or underscores')
    if any(s not in SYSTEMS for s in systems):
        raise ValueError('This campaign compares BrewFS with JuiceFS only')
    if profile not in ('smoke', 'main', 'scale'):
        raise ValueError('Unknown profile')
    # Work remains constant across concurrency levels: no extra model cases implied.
    if profile == 'scale':
        # Four traces per worker: weak scaling, not a fixed-work speedup.
        grid = [(workers, workers * 4, workers * 4) for workers in (4, 16, 64, 128)]
    else:
        tasks = 4 if profile == 'smoke' else 64
        levels = [1, 4] if profile == 'smoke' else [1, 4, 16, 64]
        grid = [(2 if profile == 'smoke' else 4, c, tasks) for c in levels]
    cells = [(repeat, workers, c, tasks, system)
             for repeat in range(1, 4) for workers, c, tasks in grid for system in systems]
    random.Random(20261024).shuffle(cells)
    if profile == 'scale':
        # Qualify smaller allocations first; randomize systems/repeats within a size.
        cells.sort(key=lambda item: item[1])
    batches = []
    for order, (repeat, workers, c, tasks, system) in enumerate(cells):
        run_id = f'{profile}-{system}-w{workers}-c{c}-r{repeat}'
        batches.append({'run_id': run_id, 'order': order, 'system': system,
                        'worker_count': workers, 'concurrency': c, 'repeat': repeat,
                        'timeout_s': 1800, 'batch_timeout_s': ((tasks + c - 1) // c) * 1800 + 300,
                        'comparison': 'four_traces_per_worker_weak_scaling' if profile == 'scale' else 'fixed_work',
                        'mode': 'as_fast_as_possible_strict_verification',
                        'cache': 'prepared_and_verified_backend_unknown',
                        'tasks': [{'task_id': f'{run_id}-t{i:03d}', 'trace': CASES[i % 4],
                                   'copy_index': i // 4,
                                   'worker_slot': (i // 4 + (0 if profile == 'scale' else i % 4) + repeat - 1) % workers,
                                   'status': 'planned'} for i in range(tasks)]})
    return {'schema_version': 1, 'profile': profile, 'execution_enabled': False,
            'unique_traces': 4, 'tasks_per_batch': None if profile == 'scale' else tasks, 'seed': 20261024,
            'worker_assignment': 'slots fixed; map slots to actual allocations before execution',
            'batches': batches}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--systems', nargs='+', choices=SYSTEMS, default=['juicefs'])
    p.add_argument('--profile', choices=['smoke', 'main', 'scale'], default='smoke')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    plan = build(a.systems, a.profile)
    body = json.dumps(plan, ensure_ascii=False, indent=2) + '\n'
    with a.out.open('x', encoding='utf-8') as stream:
        stream.write(body)
    print(json.dumps({'batches': len(plan['batches']), 'tasks': sum(len(b['tasks']) for b in plan['batches']),
                      'sha256': hashlib.sha256(body.encode()).hexdigest()}))
