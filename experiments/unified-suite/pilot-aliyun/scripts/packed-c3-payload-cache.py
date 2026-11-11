#!/usr/bin/env python3
"""Draft a same-base C3 cache ablation and check actual effective cache counters."""

import argparse
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, pinned, write_new
from bench.suite_catalog import cases
from bench.suite_mount import matches
from bench.suite_plan import build, check_plan

ON = 512 * 2**20
CAPACITY = 'brewfs_entity_read_cache_capacity_bytes'
RETAINED = 'brewfs_entity_read_cache_retained_bytes'
ENTRIES = 'brewfs_entity_read_cache_entries'
EVICTIONS = 'brewfs_entity_read_cache_evictions_total'
CACHE = ('entity_lower_payload', 'entity_upper_payload')
HITS = tuple(f'brewfs_cache_hits_total{{cache="{label}"}}' for label in CACHE)
MISSES = tuple(f'brewfs_cache_misses_total{{cache="{label}"}}' for label in CACHE)
KINDS = ('upper', 'packed', 'large')
GETS = tuple(f'brewfs_object_get_total{{kind="{kind}"}}' for kind in KINDS)
BYTES = tuple(f'brewfs_object_get_bytes_total{{kind="{kind}"}}' for kind in KINDS)
COUNTERS = (*HITS, *MISSES, EVICTIONS, *GETS, *BYTES)
REQUIRED = {CAPACITY, RETAINED, ENTRIES, *COUNTERS}


def ref(path):
    return dict(path=str(Path(path).resolve(strict=True)), sha256=digest(path))


def draft(plan_path, cell_id, base_path, qualification_path, campaign, out):
    original = check_plan(load(plan_path))
    original_cell = next(c for c in original['cells'] if c['id'] == cell_id)
    params = original_cell['params']
    if (original_cell['workload'], original_cell['arm_id'], original_cell['pool'], params['files']) != ('smallfiles', 'packed', 'pool-a', 100000):
        raise ValueError('Original Pool A 100k Packed system cell required')
    if campaign == original['campaign_id']:
        raise ValueError('Use an independent campaign for the cache ablation')
    base_pin = ref(base_path)
    q = load(qualification_path)
    if (q.get('passed') is not True or q['cell_id'] != cell_id or q['immutable_base'] != base_pin or
            q['files'] != params['files'] or q['bytes_checked'] != params['files'] * params['file_bytes']):
        raise ValueError('Retained original full base qualification required')
    proofs = {name: q['proofs'][name] for name in ('source', 'full_base_check', 'base_normal_exit')}
    for pin in proofs.values():
        if load(pinned(pin)).get('passed') is not True:
            raise ValueError('Original base proof failed')
    base = load(base_path)
    config = base['config']
    if (base['plan']['sha256'] != digest(plan_path) or base['product'] != 'brewfs' or
            base['arm'] != 'packed' or base.get('pool', 'pool-a') != 'pool-a' or
            not config.get('packed_manifest_key') or config.get('entity_native_manifest_key') or
            config['meta']['backend'] != 'redis' or config['data']['backend'] != 's3'):
        raise ValueError('Original shared Packed Redis/S3 base required')
    matches(config, original_cell['client_settings'])
    policy = deepcopy(original['config'])
    case = next(c for c in cases('pilot', 'ablations') if c['name'] == 'payload-cache')
    case['params'] = deepcopy(params)
    policy.update(campaign_id=campaign, study='ablations', repetitions=1, cases=[case],
                  clusters=[c for c in policy['clusters'] if c['id'] == 'pool-a'],
                  placement=dict(mode='baseline-parallel', product_pools={'brewfs': ['pool-a']},
                                 arm_pools={name: ['pool-a'] for name in case['arms']}))
    new = build(policy)
    settings = {c['arm_id']: c['client_settings'] for c in new['cells']}
    on, off = (deepcopy(settings[name]) for name in ('cache-on', 'cache-off'))
    if on.pop('entity_read_cache_bytes') != ON or off.pop('entity_read_cache_bytes') != 0 or on != off:
        raise ValueError('Exactly one payload-cache setting may differ')
    if settings['cache-on'] != original_cell['client_settings'] or any(c['params'] != params for c in new['cells']):
        raise ValueError('Ablation changed the original settings or workload')
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'config.json', policy)
    write_new(out / 'plan.json', new)
    arms = [dict(cell_id=c['id'], arm=c['arm_id'], pool=c['pool'], config_patch={'entity_read_cache_bytes': c['client_settings']['entity_read_cache_bytes']},
                 mount_argv_fragment=['--entity-read-cache-bytes', str(c['client_settings']['entity_read_cache_bytes'])]) for c in new['cells']]
    write_new(out / 'route.draft.json', dict(reviewed=False, execution_enabled=False, measured_workload_started=False,
        original_system_plan=ref(plan_path), plan=ref(out / 'plan.json'), original_cell_id=cell_id,
        base_asset=base_pin, original_base_qualification=ref(qualification_path), reused_base_proofs=proofs,
        packed_manifest_key=config['packed_manifest_key'], redis_url=config['meta']['redis']['url'],
        s3_endpoint=config['data']['s3']['endpoint'], s3_bucket=config['data']['s3']['bucket'],
        build_manifest_sha256=policy['build_manifest_sha256'], input_manifest_sha256=policy['input_manifest_sha256'],
        filesystem_binary=base['filesystem_binary'], seed=policy['seed'], arms=arms,
        required_new_gates=['separate immutable-parent/child plan validation', 'native capacity and actual read-cache counters per arm',
                            'valid ≤5% observer calibration for each measured route', 'full formal cold child/parent readback'],
        reuses_failed_calibration=False, scope='Draft only; reuse original full immutable base proof, never a system sample or a formal child proof.'))


def values(path):
    observed = {}
    for line in path.read_text().splitlines():
        if line.startswith('#'):
            continue
        parts = line.split()
        if not parts or parts[0] not in REQUIRED:
            continue
        if len(parts) not in (2, 3) or parts[0] in observed:
            raise ValueError('Invalid or duplicate raw metric: ' + line)
        value = Decimal(parts[1])
        if not value.is_finite() or value < 0 or value != value.to_integral_value():
            raise ValueError('Expected nonnegative integer metric: ' + line)
        observed[parts[0]] = int(value)
    if missing := REQUIRED - observed.keys():
        raise ValueError('Original required metric absent: ' + ', '.join(sorted(missing)))
    return observed


def snapshot(observer, label):
    finished = load(observer / 'finished.json')
    marks = [m for m in finished['marks'] if m['label'] == label]
    if len(marks) != 1:
        raise ValueError('Exactly one original observer mark required: ' + label)
    mark = marks[0]
    directory = observer / f"sample-{mark['sample_index']:06}"
    sample_path = directory / 'sample.json'
    sample = load(sample_path)
    raw = directory / 'filesystem.txt'
    original = sample['files']['filesystem.txt']
    if (sample.get('passed') is not True or sample.get('errors') or sample['target_states']['filesystem']['state'] != 'live' or
            raw.stat().st_size != original['bytes'] or digest(raw) != original['sha256']):
        raise ValueError('Raw live filesystem sample differs from original: ' + label)
    return values(raw), dict(mark=mark, raw=ref(raw), sample=ref(sample_path))


def check(observer, before_label, after_label, budget, out):
    before, before_pin = snapshot(observer, before_label)
    after, after_pin = snapshot(observer, after_label)
    if before_pin['mark']['monotonic_ns'] >= after_pin['mark']['monotonic_ns']:
        raise ValueError('Original sample marks are not ordered')
    if budget not in (0, ON) or any(s[CAPACITY] != budget or s[RETAINED] > budget for s in (before, after)):
        raise ValueError('Measured cache capacity differs or retained bytes exceed it')
    deltas = {key: after[key] - before[key] for key in COUNTERS}
    if any(delta < 0 for delta in deltas.values()):
        raise ValueError('Measured counters reset between marks')
    if deltas[MISSES[0]] <= 0:
        raise ValueError('No observable lower payload miss in the measured workload')
    if budget == 0:
        if any(s[key] != 0 for s in (before, after) for key in (RETAINED, ENTRIES, EVICTIONS, *HITS)):
            raise ValueError('Disabled payload cache retains, hits or evicts entries')
    elif deltas[HITS[0]] <= 0 or after[RETAINED] <= 0 or after[ENTRIES] <= 0:
        raise ValueError('Enabled payload cache shows no lower hit or retained entries')
    if sum(deltas[key] for key in GETS) <= 0 or sum(deltas[key] for key in BYTES) <= 0:
        raise ValueError('No actual object reads are present in the measured window')
    write_new(out, dict(passed=True, capacity_bytes=budget, retained_bytes=[before[RETAINED], after[RETAINED]],
                       entries=[before[ENTRIES], after[ENTRIES]], before=before_pin, after=after_pin, deltas=deltas,
                       object_gets_total=sum(deltas[key] for key in GETS), object_get_body_bytes_total=sum(deltas[key] for key in BYTES),
                       range_requests=dict(observed=False, reason='Current client labels do not distinguish HTTP Range from non-Range GET'),
                       scope='Effective application payload cache and client object GET/body delta between original live marks; no physical disk or pure lower/upper attribution.'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    plan = sub.add_parser('draft')
    for name in ('plan', 'base-asset', 'qualification', 'out'):
        plan.add_argument('--' + name, type=Path, required=True)
    plan.add_argument('--cell', required=True)
    plan.add_argument('--campaign', required=True)
    metrics = sub.add_parser('check')
    metrics.add_argument('--observer', type=Path, required=True)
    metrics.add_argument('--out', type=Path, required=True)
    metrics.add_argument('--budget', type=int, required=True)
    metrics.add_argument('--before-mark', default='measurement-start')
    metrics.add_argument('--after-mark', default='application-finished')
    a = parser.parse_args()
    if a.action == 'draft':
        draft(a.plan, a.cell, a.base_asset, a.qualification, a.campaign, a.out)
    else:
        check(a.observer, a.before_mark, a.after_mark, a.budget, a.out)
