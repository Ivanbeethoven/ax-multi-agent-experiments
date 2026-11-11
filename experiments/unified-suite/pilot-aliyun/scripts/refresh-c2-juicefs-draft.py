#!/usr/bin/env python3
"""Rebind the unstarted C2 recipe to the deployed C4 runner without touching workers."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('code', 'draft', 'fork-request', 'old-shared', 'old-cross', 'out'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--draft-sha256', required=True)
    p.add_argument('--fork-request-sha256', required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.code.resolve(strict=True)))
    from bench.runtime import pilot_context
    from bench.runtime.common import digest, load, pinned, write_new
    from bench.runtime.pilot_pools import cross_pool_proof

    def ref(path):
        return dict(path=str(Path(path).resolve(strict=True)), sha256=digest(path))

    old_pin = dict(path=str(a.draft), sha256=a.draft_sha256)
    fork_pin = dict(path=str(a.fork_request), sha256=a.fork_request_sha256)
    old = load(pinned(old_pin))
    fork = load(pinned(fork_pin))
    if old['schema'] != 'c2-juicefs-coordinator-draft-v1' or old['execution_enabled'] or old['reviewed']:
        raise ValueError('Unreviewed coordinator-only C2 recipe required')
    if fork.get('schema') != 'c4-fork-batch-v1' or fork.get('reviewed') is not True:
        raise ValueError('Actual reviewed C4 batch required')
    control = Path(old['plan']['path']).parents[1]
    plan = load(pinned(old['plan']))
    prior_runtime = pilot_context.select(control, old['runtime_control'])
    fork_control = Path(fork['control'])
    runtime = pilot_context.select(fork_control, fork['runtime_control'])
    new = pilot_context.validate(control, runtime)
    old_cross = cross_pool_proof(a.old_cross, plan, prior_runtime/'fleet.json')
    fork_plan = load(fork_control/'suite/plan.json')
    observed = cross_pool_proof(pinned(fork['cross_pool_qualification']), fork_plan, runtime/'fleet.json')
    if observed['original_observation'] != ref(a.old_cross):
        raise ValueError('C4 and C2 must share one original native hardware observation')
    if any(old_cross[k] != observed[k] for k in ('checks', 'details', 'at', 'cloud_inventory_sha256', 'pools')):
        raise ValueError('Runtime refresh may not change native hardware observations or placement')
    hardware = load(pinned(fork['hardware']))
    if digest(pinned(hardware['original_observation'])) != digest(a.old_shared/'hardware.json') or hardware['pool'] != old['pool']:
        raise ValueError('C4 hardware proof must be derived from the same B observations')
    copy_proof, prior_asset = (pinned(old['source_pins'][i]) for i in (1, 2))
    mount_args = old['commands']['client_qualification']
    value = lambda flag: mount_args[mount_args.index(flag) + 1]
    draft_module = runpy.run_path(str(pinned(old['source_pins'][0])))
    last = fork['trials'][-1]
    shared = a.out/'shared'
    (shared/'juicefs').mkdir(parents=True, exist_ok=False)
    for relative in ('rustfs-source.json', 'juicefs/metrics.json'):
        pin = next(x for x in old['source_pins'] if Path(x['path']) == a.old_shared/relative)
        with (shared/relative).open('xb') as stream:
            stream.write(pinned(pin).read_bytes())
    write_new(shared/'hardware.json', hardware)
    binding = a.out/'binding.json'
    write_new(binding, new)
    cross = a.out/'cross-pool.json'
    write_new(cross, dict(observed, plan_sha256=plan['plan_sha256'],
        source_pins=[*observed['source_pins'], ref(a.old_cross), ref(binding),
            fork['cross_pool_qualification'], old['plan']],
        derivation='Original C2 plan and native hardware observations, exact C4 code-only runtime refresh.'))
    cross_pool_proof(cross, plan, runtime/'fleet.json')
    draft_module['draft'](SimpleNamespace(control=control, code=a.code,
        copy_proof=copy_proof, prior_asset=prior_asset, shared_qualification=shared,
        cross_pool_qualification=cross, asset=Path(value('--asset')), route=Path(value('--out')),
        out=a.out/'recipe', database=old['candidate_database'],
        incoming_collector_name=last['new_collector_name'],
        incoming_collector_out=last['new_collector_out'],
        runtime_control=binding, runtime_control_sha256=digest(binding)))
    write_new(a.out/'result.json', dict(passed=True, execution_enabled=False, worker_contacted=False,
        cell_id=old['cell_id'], pool=old['pool'], recipe=ref(a.out/'recipe/draft.json'),
        old_recipe=old_pin, fork_request=fork_pin, runtime=ref(binding), cross_pool=ref(cross),
        hardware=ref(shared/'hardware.json'), at=datetime.now(timezone.utc).isoformat()))
    print(json.dumps(dict(result=ref(a.out/'result.json'), worker_contacted=False)))


if __name__ == '__main__':
    main()
