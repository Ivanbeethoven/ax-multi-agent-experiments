#!/usr/bin/env python3
"""Freeze a two-existing-client C4 state qualification recipe; never contact workers."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('runtime-repo', 'control', 'archive', 'checkpoint-receipt', 'opencode-release', 'out'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--source', choices=('pool-a', 'pool-b'), default='pool-b')
    p.add_argument('--candidate-database', type=int, required=True)
    a = p.parse_args()
    sys.path[:0] = [str(a.runtime_repo), str(a.runtime_repo/'bench')]
    from bench.runtime.common import digest, load, write_new
    from bench.runtime.pilot_pools import pool_inventory, resource_ids
    from bench.suite_catalog import cases
    from bench.suite_plan import build, check_plan

    def ref(path):
        return dict(path=str(path.resolve(strict=True)), sha256=digest(path))

    original = check_plan(load(a.control/'suite/plan.json'))
    inventory_path = a.control/'provenance/inventory.json'
    inventory = load(inventory_path)
    target = 'pool-a' if a.source == 'pool-b' else 'pool-b'
    source_pool, target_pool = [pool_inventory(inventory, pool) for pool in (a.source, target)]
    old = {c['id']:c for c in original['config']['clusters']}
    for pool in (source_pool, target_pool):
        if (old[pool['pool_id']]['inventory_sha256'] != digest(inventory_path) or
                set(old[pool['pool_id']]['resource_ids']) != set(resource_ids(pool))):
            raise ValueError('Original canonical inventory or physical lock identities differ')
    if set(resource_ids(source_pool)).intersection(resource_ids(target_pool)):
        raise ValueError('Existing client pools must not alias a physical resource')
    checkpoint, release = load(a.checkpoint_receipt), load(a.opencode_release)
    if (checkpoint['passed'] is not True or checkpoint['contains_active_relay_key'] is not False or
            checkpoint['contains_provider_master_key'] is not False or
            checkpoint['archive']['sha256'] != digest(a.archive) or
            checkpoint['archive']['bytes'] != a.archive.stat().st_size or
            checkpoint['opencode_sha256'] != release['opencode_sha256']):
        raise ValueError('One verified checkpoint and its original OpenCode binary required')
    if not 20 <= a.candidate_database < 256:
        raise ValueError('Explicit unused candidate DB in 20..255 required')
    role_pool = 'pool-c4-state'
    combined = dict(pool_id=role_pool, roles={**source_pool['roles'], 'target':target_pool['roles']['client']})
    binding = dict(pools=[combined])
    resources = resource_ids(combined)
    config = deepcopy(original['config'])
    case = next(c for c in cases('pilot', 'systems') if c['workload']=='handoff')
    case['name'] = 'opencode-state-reopen-qualification'
    case['params']['trace'] = 'opencode-checkpoint-20261111'
    # Retain the formal resume, writer and neighbor gates; this qualification
    # does not fulfill them and never initializes or writes a campaign DB.
    config.update(campaign_id='pilot-c4-state-reopen-01', placement={'mode':'paired-pool'}, cases=[case],
                  input_manifest_sha256=digest(a.checkpoint_receipt), clusters=[
                      dict(id=role_pool, hardware_class=old[a.source]['hardware_class'],
                           metadata_backend=config['metadata_backend'], agent_slots=2,
                           supported_suites=['C4'], resource_ids=resources,
                           observer_ids=[role_pool+'-'+r for r in combined['roles']],
                           inventory_sha256='0'*64)])
    a.out.mkdir(parents=True, exist_ok=False)
    write_new(a.out/'route-inventory.json', binding)
    config['clusters'][0]['inventory_sha256'] = digest(a.out/'route-inventory.json')
    plan = build(config)
    write_new(a.out/'plan.json', plan)
    cell = next(c for c in plan['cells'] if c['arm_id']=='juicefs-move')
    p = '/srv/ax-pilot-session'
    route = dict(schema='pilot-c4-state-reopen-draft-v1', execution_enabled=False, reviewed=False,
        cell_id=cell['id'], qualification_only=True, model_calls_allowed=0,
        source_pool=a.source, target_pool=target, source_ssh=a.source+'-client', target_ssh=target+'-client',
        source_host=combined['roles']['client'], target_host=combined['roles']['target'],
        workspace=p+'/workspace', project=p+'/workspace/repo', mount_point=p,
        canonical_control=str(a.control), original_plan=ref(a.control/'suite/plan.json'),
        plan=ref(a.out/'plan.json'), inventory=ref(a.out/'route-inventory.json'),
        original_inventory=ref(inventory_path),
        build_manifest=ref(a.control/'provenance/build-manifest.json'),
        ssh=ref(a.control/'ssh.conf'),
        original_lock_resources={n:old[n]['resource_ids'] for n in (a.source, target)},
        checkpoint_archive=ref(a.archive), checkpoint_receipt=ref(a.checkpoint_receipt),
        opencode_release=ref(a.opencode_release), candidate_database=a.candidate_database,
        endpoint_scope='Both mounts use source Redis/RustFS; target does not format and uses no target-pool credentials.',
        fence_scope='Clean old process/mount closure plus coordinator grant rejection; no JuiceFS-native crash fencing claim.',
        phase_order=['actual_handoffs_and_locks','check_hosts_slots_and_db','source_import_and_export',
                     'source_fsync_debt_and_normal_stop','reject_old_source_grant',
                     'target_new_mount_and_export_same_db','target_normal_stop'],
        claim='This is state readiness only. No model continuation, performance sample, or physical-host identity claim.')
    write_new(a.out/'draft.json', route)
    print(json.dumps(dict(draft=ref(a.out/'draft.json'), cell_id=cell['id'], worker_contacted=False)))


if __name__=='__main__':
    main()
