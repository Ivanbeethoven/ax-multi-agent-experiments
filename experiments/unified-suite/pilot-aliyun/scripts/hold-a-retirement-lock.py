#!/usr/bin/env python3
"""Hold the existing physical A locks during an explicitly owned retirement window."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import select
import sys
import time

ROOT = Path('/srv/ax-pilot-control')
CANON = ROOT/'parallel-control-01'
REQUIRED = {CANON, ROOT/'c123-control-01', ROOT/'fork-control-01'}


def need(value, reason):
    if not value:
        raise ValueError(reason)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--max-seconds', type=int, default=1200)
    a = p.parse_args()
    need(0 < a.max_seconds <= 3600 and a.repo.is_absolute()
         and a.repo.resolve(strict=True).is_relative_to(ROOT), 'Unowned runtime or deadline')
    sys.path.insert(0, str(a.repo))
    from bench.runtime.common import load
    from bench.runtime.pilot_pools import active_conflicts, resource_guard, cell_resources
    from bench.suite_plan import check_plan
    plan = check_plan(load(CANON/'suite/plan.json'))
    # Reserve every physical A resource from the original cluster. Never claim a cell.
    cluster = next(r for r in plan['config']['clusters'] if r['id'] == 'pool-a')
    cell = {'pool': 'pool-a', 'resource_ids': cluster['resource_ids']}
    need(bool(cell_resources(plan,cell)), 'Empty resource set')
    controls = lambda: sorted(x.parent for x in ROOT.glob('*/state.sqlite'))
    original = controls()
    need(REQUIRED <= set(original), 'Original control database set is incomplete')
    with ExitStack() as s:
        lock_roots = set()
        for ctl in original:
            lock = (ctl/'runner-locks').resolve(strict=True)
            if lock not in lock_roots:
                s.enter_context(resource_guard(ctl, cell, plan))
                lock_roots.add(lock)
        def prove():
            need(controls() == original and not any(active_conflicts(c,cell,plan) for c in original),
                 'Conflicting A lease or control membership changed')
            return {'held': True, 'pool': 'pool-a', 'controls': len(original), 'lock_roots': len(lock_roots)}
        print(json.dumps(prove()), flush=True)
        deadline = time.monotonic()+a.max_seconds
        while (left := deadline-time.monotonic()) > 0 and select.select([sys.stdin],[],[],left)[0]:
            command = sys.stdin.readline().strip()
            if not command or command == 'close':
                return
            need(command == 'check', 'Only check or close allowed')
            print(json.dumps(prove()), flush=True)
        raise TimeoutError('A retirement lock window expired')


if __name__ == '__main__':
    main()
