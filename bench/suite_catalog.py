"""Historical unified-c1-c4-v1 catalog for frozen runs and explicit legacy templates.

New design: experiments/unified-suite/current/design.json (not executable yet).
Do not rename historical arms or reinterpret their engine identities.
"""
from copy import deepcopy

PROTOCOL = 'unified-c1-c4-v1'
BACKEND = {'repository': 'https://github.com/Ivanbeethoven/rustfs',
           'branch': 'feat/dynamic-block-size-rebased', 'revision_policy': 'branch-latest',
           'layout_profile': 'enabled', 'brewfs_hint': 'auto'}

# Versioned settings for NEW campaigns. Old frozen configs/plans are unchanged.
CLIENT_PROFILE = 'bounded-memory-v1'

# These are routes, not assertions that a selected build supports every route.
ARMS = {
    'juicefs': dict(product='juicefs', role='external-baseline', engine='juicefs',
                    delta='native-clone', packed=False, hint='none', payload_cache_bytes=536870912),
    'native': dict(product='brewfs', role='internal-control', engine='flat',
                   delta=False, packed=False, hint='auto', payload_cache_bytes=536870912),
    'native-workspace': dict(product='brewfs', role='internal-control', engine='native-workspace',
                             delta=True, packed=False, hint='auto', payload_cache_bytes=536870912),
    'entity-native': dict(product='brewfs', role='internal-control', engine='entity-workspace',
                         delta=True, packed=False, immutable_lower='native', hint='auto', payload_cache_bytes=0),
    'packed': dict(product='brewfs', role='candidate', engine='entity-workspace',
                   delta=True, packed=True, hint='auto', payload_cache_bytes=536870912),
}


def arm(name, **changes):
    return dict(deepcopy(ARMS[name]), **changes)


def case(suite, name, workload, params, arms, *, claim='system', gates=()):
    return dict(suite=suite, name=name, workload=workload, params=params,
                arms=arms, claim=claim, extra_gates=list(gates))


def cases(profile, study):
    if profile not in ('pilot', 'main') or study not in ('systems', 'ablations'):
        raise ValueError('profile pilot/main and study systems/ablations required')
    tiny = profile == 'pilot'
    return historical_cases(tiny, study)


def historical_cases(tiny, study):
    if study == 'ablations':
        # Independent contrasts, not an unqualified 2^3 product. Same-engine
        # alternatives remain visible and blocked until native proof exists.
        return [
            case('C3', 'packed-format-native-control', 'smallfiles', small_params(tiny), {
                 'entity-native':arm('entity-native'), 'packed-cache-off':arm('packed',payload_cache_bytes=0)},
                 claim='lower-format-system-control',gates=['entity-native-lower-adapter','equal-source-view','equal-cache-policy']),
            case('C4', 'delta', 'fork', {'agents': 1 if tiny else 8, 'children': 1 if tiny else 8,
                 'dataset': 'fork-fixture-million-4k', 'creation': 'burst'}, {
                 'delta-on': arm('native-workspace'),
                 'delta-eager': arm('native-workspace', delta=False, materialization='eager-private-copy')},
                 claim='delta-only', gates=['same-engine-eager-materialization', 'equal-source-view']),
            case('C3', 'packed-metadata', 'smallfiles', small_params(tiny), {
                 'packed-on': arm('packed',payload_cache_bytes=0),
                 'packed-off': arm('entity-native')},
                 claim='packed-only', gates=['entity-native-lower-adapter', 'identical-payload-layout', 'equal-cache-policy']),
            case('C3', 'payload-cache', 'smallfiles', small_params(tiny), {
                 'cache-on': arm('packed'), 'cache-off': arm('packed', payload_cache_bytes=0)},
                 claim='payload-cache-only', gates=['effective-payload-cache-counters']),
            case('C1', 'client-object-size', 'fio', fio_params('randrw', tiny), {
                 f'g-{size}': arm('native', object_bytes=size, hint='1048576')
                 for size in (262144, 1048576, 4194304)}, claim='client-object-size-only',
                 gates=['fixed-effective-backend-B-q', 'same-fetch-cache-write-policy']),
            case('C1', 'backend-layout', 'fio', fio_params('randread', tiny), {
                 f'B-{size}': arm('native', object_bytes=4194304, hint=size)
                 for size in ('off', '65536', '262144', '1048576', '4194304')},
                 claim='backend-layout-only', gates=['actual-persisted-B-q', 'same-final-object-bytes', 'external-object-layout']),
            case('C1', 'checksum-quantum', 'fio', fio_params('randread', tiny), {
                 'block-only': arm('native', object_bytes=4194304, hint='1048576', layout_profile='block-only'),
                 'block-and-quantum': arm('native', object_bytes=4194304, hint='1048576', layout_profile='enabled')},
                 claim='checksum-quantum-only', gates=['fixed-B-distinct-q', 'service-switch-before-measurement']),
            case('C3', 'delta-packed-interaction', 'smallfiles', small_params(tiny), {
                 f'D{int(d)}P{int(p)}': arm('packed', delta=d, packed=p,payload_cache_bytes=0,
                     materialization='shared-lower' if d else 'eager-private-copy',
                     immutable_lower='packed' if p else 'native')
                 for d in (False, True) for p in (False, True)},
                 claim='same-engine-D-by-P', gates=['entity-native-lower-adapter',
                     'entity-eager-materialization', 'identical-payload-layout', 'all-four-cells-qualified']),
        ]
    rows = []
    for mode in ('read', 'write', 'randread', 'randwrite', 'randrw'):
        rows.append(case('C1', 'fio-'+mode, 'fio', fio_params(mode, tiny),
                         {k: arm(k) for k in ('native', 'juicefs')}))
    for trace in (['pytest-full-02'] if tiny else ['pytest-full-02', 'django', 'ripgrep-20261026', 'archive-io-20261026']):
        for agents in ([1] if tiny else [1, 4, 8]):
            rows.append(case('C2', f'{trace}-n{agents}', 'trace',
                {'agents': agents, 'trace': trace, 'tasks_per_agent': 1, 'pacing': 'off', 'replay': 'complete'},
                {k: arm(k) for k in ('native', 'native-workspace', 'entity-native', 'packed', 'juicefs')},
                gates=['complete-trace-qualification', 'fresh-initial-tree', 'repo-home-xdg-on-mount']))
    for hot in ([1024] if tiny else [1024, 16384]):
        params = small_params(tiny); params['hot_files'] = hot
        rows.append(case('C3', f'million-hot-{hot}', 'smallfiles', params,
                         {k: arm(k) for k in ('native', 'entity-native', 'packed', 'juicefs')},
                         gates=['full-base-validation-once', 'client-cache-evidence']))
    for children in ([1] if tiny else [1, 8, 32, 64]):
        rows.append(case('C4', f'fork-{children}', 'fork',
            {'agents': children, 'children': children, 'dataset': 'fork-fixture-million-4k', 'creation': 'burst'},
            {k: arm(k) for k in ('native', 'native-workspace', 'entity-native', 'packed', 'juicefs')},
            gates=['native-fork-or-copy-qualified', 'parent-sibling-isolation', 'capacity-for-eager-copy']))
    for agents in ([1] if tiny else [1, 8, 32]):
        rows.append(case('C4', f'handoff-{agents}', 'handoff',
            {'agents': agents, 'moved_agents': 1 if tiny else max(1, agents//2), 'trace': 'archive-io-20261026'},
            {f'{k}-{mode}': arm(k, handoff_mode=mode)
             for k in ('native-workspace', 'juicefs') for mode in ('stay', 'move')},
            gates=['native-session-resume', 'writer-fencing', 'different-executor-hosts', 'paired-checkpoint', 'neighbor-throughput']))
    return rows


def fio_params(mode, tiny):
    sequential = mode in ('read', 'write')
    return {'agents': 1, 'rw': mode, 'block_bytes': 1048576 if sequential else 4096,
            'file_bytes_per_job': 67108864 if tiny else 4294967296,
            'io_bytes_per_job': 67108864 if tiny else 4294967296 if sequential else 268435456,
            'numjobs': 1 if tiny else 8, 'ioengine': 'psync', 'iodepth': 1,
            'direct': False, 'fsync_every_write': True, 'rwmixread': 75,
            'cache_state': 'client-cold', 'time_based': False}


def small_params(tiny):
    return {'agents': 1, 'files': 1000000, 'file_bytes': 4096, 'directories': 1024,
            'hot_files': 1024, 'workers': 8, 'workspaces': 1 if tiny else 4,
            'read_ops': 8192 if tiny else 65536, 'cow_files': 256,
            'write_ops': 256 if tiny else 2048, 'mixed_ops': 1024 if tiny else 8192,
            'prewarm_passes': 2, 'mixed_read_fraction': 0.75, 'fsync_every_write': True}


def select_pilot_files(config, files):
    """Explicit size for a NEW pilot; the default catalog remains unchanged."""
    if config['profile'] != 'pilot' or type(files) is not int or files not in (100000, 1000000):
        raise ValueError('Explicit 100k or million-file pilot required')
    config['pilot_file_count'] = files
    for row in config['cases']:
        if row['workload'] == 'smallfiles':
            row['params']['files'] = files
            row['params']['dataset'] = 'smallfiles-100k-4k' if files == 100000 else 'million-4k'
            if files == 100000:
                row['name'] = row['name'].replace('million-', 'files-100000-')
            else:
                row['name'] = row['name'].replace('files-100000-', 'million-')
        elif row['workload'] == 'fork':
            row['params'].update(files=files, file_bytes=4096,
                dataset='fork-fixture-100k-4k' if files == 100000 else 'fork-fixture-million-4k')
    return config


def template(profile='pilot', study='systems'):
    rows=cases(profile,study)
    # Exact arm overrides can split internal native/ablation methods as well as
    # external products. Product mappings are the fallback for all other arms.
    overrides=({name:['pool-'+('a' if n%2==0 else 'b')]
        for c in rows for n,name in enumerate(sorted(c['arms']))}
        if study=='ablations' else {})
    return {'schema': PROTOCOL, 'campaign_id': 'unified-20261103', 'profile': profile,
            'study': study, 'seed': 9173, 'repetitions': 1 if profile == 'pilot' else 3,
            'client_profile': CLIENT_PROFILE,
            'placement':{'mode':'baseline-parallel','product_pools':{'brewfs':['pool-a'],'juicefs':['pool-b']},'arm_pools':overrides},
            'external_baselines': ['juicefs'], 'backend_policy': deepcopy(BACKEND),
            'cache_budget_bytes_per_client': 536870912, 'ssd_clean_cache_bytes': 0,
            'durability': 'upload_before_commit+fsync+remote-drain',
            'telemetry': {'interval_seconds':5,'max_gap_seconds':30,'max_relative_overhead':0.05},
            'metadata_backend': 'tikv', 'input_manifest_sha256': None, 'build_manifest_sha256': None,
            'clusters': [{'id': f'pool-{i}', 'hardware_class': 'matched-pool-v1',
                          'metadata_backend': 'tikv', 'agent_slots': 8 if profile=='pilot' else 64,
                          'supported_suites': ['C1', 'C2', 'C3', 'C4'],
                          'resource_ids': [f'host:pool-{i}-client', f'host:pool-{i}-target',
                                           f'host:pool-{i}-metadata', f'host:pool-{i}-objects'],
                          'observer_ids':[f'pool-{i}-client',f'pool-{i}-target',f'pool-{i}-metadata',f'pool-{i}-objects'],
                          'inventory_sha256': None} for i in ('a', 'b')],
            'cases': rows}
