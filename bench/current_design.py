"""Check the current C1-C4 design and count planned cells; never execute it."""
import argparse
import json
from pathlib import Path

SCHEMA = 'ax-c1-c4-design-v2'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate(config):
    require(config.get('schema') == SCHEMA, 'current design schema required')
    require(config.get('status') == 'design-only' and config.get('execution_enabled') is False,
            'this checker cannot enable execution or certify adapters')
    require(bool(config.get('execution_blockers')), 'preserve explicit execution blockers')
    controls = config['controls']
    require(controls['max_agents'] == 32, 'this design is capped at 32 Agents')
    require(controls['max_active_workload_slots_total'] == 32 and controls['max_independent_pools'] == 2,
            'two pools share a global limit of 32 active workload slots')
    require(controls['metadata_backend'] == 'redis', 'TiKV requires a separate campaign')
    require(controls['placement'] == 'one-pool-sequential-arms', 'default paired single pool required')
    require(controls['paired_repetitions'] == 3 and controls['paired_repetitions_at_32'] == 5,
            'formal repetitions are 3, or 5 at 32 Agents')
    schedule = config['scheduling']
    require(schedule['assignment_unit'] == 'whole-paired-block'
            and schedule['all_arms_same_pool'] is True and schedule['one_active_cell_per_pool'] is True,
            'assign whole paired blocks; do not split their arms across pools')
    require(schedule['prepare_and_measure_same_resource'] == 'serial'
            and schedule['shared_network_bulk_transfer'] == 'outside-all-affected-measurement-windows',
            'prepare and bulk transfer must not interfere with measurements')
    require(schedule['c1_active_slots'] == 1
            and schedule['handoff_live_agents'] == 'source-stops-before-target-starts',
            'count fio as one slot and migration targets as replacements')
    for component in ('brewfs', 'rustfs'):
        require(config['versions'][component]['revision_policy'] == 'branch-latest-per-campaign',
                'freeze versions over all paired repeats and scaling points')
    expected = {'brewfs-basic': 'flat-v1', 'brewfs-full': 'workspace-v1', 'juicefs': 'juicefs'}
    require({k: v['engine'] for k, v in config['arms'].items()} == expected,
            'current routes are flat-v1, workspace-v1 and JuiceFS only')
    keys = ('overlay', 'packed_metadata', 'dynamic_block')
    for name, flags in [('brewfs-basic', (False, False, True)), ('brewfs-full', (True, True, True))]:
        require(tuple(config['arms'][name].get(k) for k in keys) == flags,
                'basic is not all-off; full requires all three designs')
    require(config['arms']['brewfs-full']['lower'] == 'packed-v3-wire005', 'Packed-v3 lower required')
    ablations = {a['id']: a for a in config['ablations']}
    desired = {'overlay-off': (False, True, True), 'packed-off': (True, False, True),
               'dynamic-off': (True, True, False), 'all-off': (False, False, False)}
    require(set(ablations) == set(desired), 'four declared ablations required')
    for name, flags in desired.items():
        row = ablations[name]
        require(row['engine'] == 'workspace-v1', 'format swap is not a single-design ablation')
        require(tuple(row['switches'].get(k) for k in keys) == flags, 'incorrect ablation switches')
        require(bool(row['requires']) and 'required' in row['status'], 'missing ablation prerequisites')
    work = config['workloads']
    require(set(work) == {'C1', 'C2', 'C3', 'C4-fork', 'C4-handoff'}, 'C1-C4 coverage required')
    for suite, row in work.items():
        for name in row['arms']:
            base = name.removesuffix('-stay').removesuffix('-move')
            require(base in expected, 'unknown system arm in ' + suite)
            require(base != 'brewfs-basic' or suite in ('C1', 'C2'),
                    'Flat must not be scheduled outside C1/C2')
        agents = row.get('agents', [])
        for n in agents if isinstance(agents, list) else [agents]:
            require(type(n) is int and 1 <= n <= 32, 'invalid Agent count')
    require(config['arms']['brewfs-basic']['role'] == 'c1-c2-system-control',
            'Flat is only a system control for C1/C2')
    require(work['C3']['arms'] == ['brewfs-full', 'juicefs'],
            'C3 uses shared lower and JuiceFS clone; no Flat copy arm')
    fork = work['C4-fork']
    require(fork['arms'] == ['brewfs-full', 'juicefs'] and
            fork.get('creation_methods') == {'brewfs-full': 'native-fork', 'juicefs': 'native-clone'}
            and set(fork) == {'arms', 'agents', 'files', 'file_bytes', 'fixture',
                              'creation', 'source', 'creation_methods', 'required_evidence', 'mutations'}
            and fork['source'] == 'exact-immutable-revision' and fork['creation'] == 'burst',
            'C4 derivation is exclusively BrewFS native fork versus JuiceFS native clone')
    require(set(fork['required_evidence']) ==
            {'same-nonempty-parent-content', 'brewfs-child-inherits-packed-lower', 'child-first-correct-read',
             'parent-sibling-isolation', 'full-cold-readback-after-drain'},
            'Qualify a populated lower before a C4 timed run; a successful command alone is insufficient')
    overlay = ablations['overlay-off']
    require(overlay.get('workloads') == ['C2-runtime']
            and overlay.get('protocol') == 'warm-lower-runtime-only'
            and set(overlay['requires']) == {'same-engine-overlay-toggle', 'identical-payload-and-metadata-layout',
                                            'verified-warm-lower-before-timing'},
            'Overlay off is an independent warm-lower runtime comparison, never a fork arm')
    runtime = work['C2'].get('overlay_runtime', {})
    require(runtime.get('arms') == ['brewfs-full', 'overlay-off']
            and runtime.get('working_set') == 'all-lower-metadata-and-data-ranges-touched-by-timed-ops'
            and type(runtime.get('warmup_passes_per_arm')) is int and runtime['warmup_passes_per_arm'] == 2
            and runtime.get('warmup_scope') == ['metadata', 'data'] and runtime.get('warmup_read_only') is True
            and runtime.get('verify') == 'same-mount-native-lower-range-deltas-on-final-pass'
            and runtime.get('userspace_path_required') is True and runtime.get('cold_probe_confirms_counters') is True
            and all(type(runtime.get(k)) is int and runtime[k] == 0 for k in
                    ('max_lower_backend_range_requests', 'max_lower_backend_read_bytes'))
            and runtime.get('cache_policy') == 'same-budget-no-remount-or-reset-after-warmup'
            and runtime.get('cache_overflow') == 'freeze-a-smaller-warm-trace-separately'
            and runtime.get('prepare_and_warmup_outside_timer') is True
            and runtime.get('timing') == 'same-operations-immediately-after-arm-warmup'
            and runtime.get('drain_reported_separately') is True,
            'Warm the same lower working set per arm; verify caches before timing and report drain separately')
    require(work['C2']['scale_agents'] == [8, 16, 32], 'C2 scale must be 8/16/32')
    require(sum(work['C2']['mixed_32'].values()) == 32, 'mixed trace assignment must total 32')
    require(all(type(n) is int and n > 0 for n in work['C2']['mixed_32'].values()), 'invalid mix')
    require(work['C2']['replay'] == 'complete' and work['C2']['pacing'] == 'off'
            and work['C2']['simulate_llm'] is False, 'complete trace without inserted model waits required')
    trace_ids = set(config['inputs']['callback_counts'])
    require(set(work['C2']['single_agent_traces']) == trace_ids
            and set(work['C2']['mixed_32']) == trace_ids
            and work['C2']['scale_trace'] in trace_ids
            and work['C2']['staggered']['trace'] in trace_ids, 'trace catalog mismatch')
    for key in ('C3', 'C4-fork'):
        require(work[key]['files'] == 1000000 and work[key]['file_bytes'] == 4096,
                'formal smallfile inputs are one million 4 KiB files')
        require(work[key]['agents'] == [1, 8, 16, 32], 'formal workspace scale must be 1/8/16/32')
    handoff = work['C4-handoff']
    require(handoff['agents'] == [8, 16, 32] and handoff['moved_agents'] == [2, 4, 8],
            'handoff scale/move counts differ')
    require(handoff['restore_method'] == 'shared-workspace-db', 'workspace-local state required')
    pilot = config['pilot']
    require(pilot['agents'] == 1 and pilot['repetitions'] == 1
            and pilot['C3']['files'] == pilot['C4-fork']['files'] == 100000,
            'pilot is 1 Agent, 100k files, one repetition')
    res = config['resources']
    require(res['rustfs']['nodes'] == 4 and res['rustfs']['data_disks_per_node'] == 4,
            'four hosts with four independent data disks each required')
    require(res['slots_per_client'] == 8 and res['coordinator']['public_ips'] == 1,
            'eight client slots and one public ingress required')
    require(res['agent']['cpu'] * res['slots_per_client'] <= res['client']['vcpu'] / 2
            and res['agent']['memory_bytes'] * res['slots_per_client']
            <= res['client']['memory_gib'] * 1024**3 / 2, 'host/observer reserve is missing')
    require(res['disk_free_fraction_min'] >= 0.2, 'reserve at least 20% per disk')
    layout = config['service_settings']['rustfs']
    require(len(layout['enabled']) == 4 and set(layout['enabled']) == set(layout['disabled'])
            and set(layout['enabled'].values()) == {'true'}
            and set(layout['disabled'].values()) == {'false'}, 'complete on/off service profiles required')
    require(config['versions']['same_binary_across_ablations'] is True, 'same binary required')
    require(config['observation']['interval_seconds'] == 5
            and config['observation']['max_gap_seconds'] == 30, 'observation cadence mismatch')
    return config


def cell_counts(config):
    """Count system cells only, excluding import, qualification and ablations."""
    validate(config)
    w = config['workloads']
    repeats = lambda n: config['controls']['paired_repetitions_at_32'] if n == 32 else config['controls']['paired_repetitions']
    counts = {'C1': len(w['C1']['modes']) * len(w['C1']['arms']) * repeats(1)}
    c2 = w['C2']
    counts['C2'] = len(c2['arms']) * (len(c2['single_agent_traces']) * repeats(1)
                   + sum(repeats(n) for n in c2['scale_agents'])
                   + repeats(sum(c2['mixed_32'].values())) + repeats(c2['staggered']['agents']))
    counts['C3'] = len(w['C3']['arms']) * len(w['C3']['hot_files']) * sum(repeats(n) for n in w['C3']['agents'])
    for key in ('C4-fork', 'C4-handoff'):
        counts[key] = len(w[key]['arms']) * sum(repeats(n) for n in w[key]['agents'])
    pilot = len(w['C1']['modes']) * len(w['C1']['arms']) + sum(len(w[k]['arms']) for k in w if k != 'C1')
    return {'formal_system_cells': counts, 'formal_total': sum(counts.values()), 'pilot_total': pilot}


def parallel_resources(config, cohorts):
    """Static budget for simultaneous (suite, Agents) pairs, excluding the shared coordinator.

    This checks counts only. It does not certify physical isolation or claim runtime locks.
    A handoff cohort includes its extra target host but not extra live Agent slots.
    """
    validate(config)
    require(1 <= len(cohorts) <= config['controls']['max_independent_pools'], 'one or two pools required')
    resources = config['resources']
    total = dict(active_slots=0, business_nodes=0, vcpu=0, memory_gib=0, data_gib=0)
    for suite, agents in cohorts:
        require(suite in config['workloads'], 'unknown workload')
        require(type(agents) is int and 1 <= agents <= config['controls']['max_agents'], 'invalid Agent count')
        if suite == 'C1':
            require(agents == 1, 'C1 fio jobs occupy one workload slot')
        targets = 0
        if suite == 'C4-handoff':
            row = config['workloads'][suite]
            moved = 1 if agents == 1 else dict(zip(row['agents'], row['moved_agents'])).get(agents)
            require(moved is not None, 'unplanned migration scale')
            targets = (moved + resources['target']['slots_per_node'] - 1) // resources['target']['slots_per_node']
        client_nodes = (agents + resources['slots_per_client'] - 1) // resources['slots_per_client'] + targets
        metadata = resources['redis']
        objects = resources['rustfs']
        total['active_slots'] += agents
        total['business_nodes'] += client_nodes + metadata['nodes'] + objects['nodes']
        total['vcpu'] += client_nodes * resources['client']['vcpu'] + metadata['nodes'] * metadata['vcpu'] + objects['nodes'] * objects['vcpu_per_node']
        total['memory_gib'] += client_nodes * resources['client']['memory_gib'] + metadata['nodes'] * metadata['memory_gib'] + objects['nodes'] * objects['memory_gib_per_node']
        total['data_gib'] += client_nodes * resources['client']['data_disk']['gib'] + metadata['nodes'] * metadata['data_disk']['gib'] + objects['nodes'] * objects['data_disks_per_node'] * objects['data_disk']['gib']
    require(total['active_slots'] <= config['controls']['max_active_workload_slots_total'],
            'parallel workloads exceed the global 32-slot budget')
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        result = cell_counts(config)
        result['resource_examples'] = {
            'one_pool_32': parallel_resources(config, [('C2', 32)]),
            'two_pools_16_plus_16': parallel_resources(config, [('C2', 16), ('C3', 16)]),
            'handoff_32': parallel_resources(config, [('C4-handoff', 32)])}
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(dict(result, design_consistent=True, execution_enabled=False,
                          execution_blockers=config['execution_blockers'],
                          readiness=config['readiness']), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
