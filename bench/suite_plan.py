"""Deterministic paired blocks across resource pools. No remote operations."""
import hashlib
import json
import re
from copy import deepcopy

from .suite_catalog import PROTOCOL, BACKEND

STAGES = ['observe-start', 'create', 'mount', 'measure', 'drain', 'unmount',
          'verify', 'observe-stop', 'archive']


def stages_for(case):
    if case['workload']!='smallfiles':return list(STAGES)
    # Each child really completes its original lifecycle before the next one;
    # a whole-cell wrapper must not hide four untracked/replayable loops.
    return ['observe-start', *[f'{stage}-w{n:02}'
        for n in range(1,case['params']['workspaces']+1)
        for stage in ('create','mount','measure','drain','unmount','verify')], 'observe-stop','archive']


def marks_for(case):
    marks=['measure-start','measure-end','drain-end','verify-end']
    if case['workload']!='smallfiles':return marks
    return [f'{mark}-w{n:02}' for n in range(1,case['params']['workspaces']+1) for mark in marks]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def ident(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}', value)


def sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)


def parallel(config):
    return config.get('placement',{}).get('mode','paired-pool')=='baseline-parallel'


def assigned_pools(config, case, name):
    placement=config['placement']
    ids=placement.get('arm_pools',{}).get(name)
    if ids is None:ids=placement.get('product_pools',{}).get(case['arms'][name]['product'])
    if not ids:raise ValueError('explicit pool assignment required for arm: '+name)
    by_id={p['id']:p for p in config['clusters']}
    eligible=[by_id[x] for x in ids if case['suite'] in by_id[x]['supported_suites']
              and by_id[x]['agent_slots']>=case['params']['agents']]
    if not eligible:raise ValueError('assigned pools lack capacity for arm: '+case['name']+'/'+name)
    return eligible


def validate(config):
    if config.get('schema') != PROTOCOL or not ident(config.get('campaign_id')):
        raise ValueError('versioned campaign and simple campaign ID required')
    if config.get('external_baselines') != ['juicefs']:
        raise ValueError('the external baseline is JuiceFS; BrewFS alternatives are internal controls')
    if config.get('backend_policy') != BACKEND:
        raise ValueError('use the dedicated branch-latest enabled RustFS policy; overrides belong to explicit ablation arms')
    if type(config.get('seed')) is not int or not 0<=config['seed']<2**64 or type(config.get('repetitions')) is not int or not 1 <= config['repetitions'] <= 12:
        raise ValueError('integer seed and 1..12 complete blocks required')
    if config.get('durability') != 'upload_before_commit+fsync+remote-drain':
        raise ValueError('do not mix writeback policy into these comparisons')
    if config.get('metadata_backend') not in ('redis', 'tikv'):
        raise ValueError('explicit shared metadata backend required')
    telemetry=config.get('telemetry',{})
    if telemetry!={'interval_seconds':5,'max_gap_seconds':30,'max_relative_overhead':0.05}:
        raise ValueError('freeze 5s sampling, <=30s gaps and <=5% observer overhead in this protocol')
    pools = config.get('clusters', [])
    if not pools or len({p['id'] for p in pools}) != len(pools):
        raise ValueError('unique nonempty resource pools required')
    for p in pools:
        if not ident(p['id']) or not p.get('hardware_class') or type(p.get('agent_slots')) is not int or p['agent_slots'] < 1:
            raise ValueError('pool identity, matching hardware class and slots required')
        if p.get('metadata_backend') != config['metadata_backend']:
            raise ValueError('pool metadata backend must match the paired campaign')
        resources = p.get('resource_ids', [])
        if not resources or len(resources) != len(set(resources)) or not all(isinstance(x, str) and ':' in x for x in resources):
            raise ValueError('explicit host/disk/service contention identities required')
        if not p.get('supported_suites') or not set(p['supported_suites']) <= {'C1','C2','C3','C4'}:
            raise ValueError('pool suite capabilities required')
        observers=p.get('observer_ids',[])
        if not observers or len(set(observers))!=len(observers) or not all(ident(o) for o in observers):
            raise ValueError('freeze complete host/client/backend observer identities before admission')
    rows = config.get('cases', [])
    if not rows or len({c['name'] for c in rows}) != len(rows):
        raise ValueError('unique nonempty case names required')
    for c in rows:
        if not ident(c['name']) or c['suite'] not in ('C1','C2','C3','C4'):
            raise ValueError('suite and case identity required')
        if c['workload'] not in ('fio','trace','smallfiles','fork','handoff') or len(c['arms']) < 2:
            raise ValueError('supported workload and at least two paired arms required')
        if type(c['params'].get('agents')) is not int or c['params']['agents'] < 1:
            raise ValueError('positive Agent/client count required')
        for name, arm in c['arms'].items():
            if not ident(name) or arm['product'] not in ('brewfs','juicefs'):
                raise ValueError('only BrewFS and JuiceFS routes are configured')
        if c['workload'] == 'smallfiles' and (c['params']['files'] != 1000000 or c['params']['file_bytes'] != 4096):
            raise ValueError('C3 remains exactly 1,000,000 x 4 KiB; local tool tests are separate')
        if c['workload']=='smallfiles':
            from .runtime.workloads import counts
            counts(c['params'])
            if type(c['params'].get('workspaces')) is not int or c['params']['workspaces']<1:
                raise ValueError('positive explicit workspace count required')
        if c['workload']=='fork' and (type(c['params'].get('children')) is not int or not 1<=c['params']['children']<=c['params']['agents']):
            raise ValueError('reserve one actual agent slot per concurrent child')
    placement=config.get('placement',{'mode':'paired-pool'})
    if not isinstance(placement,dict) or placement.get('mode') not in ('paired-pool','baseline-parallel'):
        raise ValueError('placement mode must be paired-pool or baseline-parallel')
    if set(placement)-{'mode','product_pools','arm_pools'}:
        raise ValueError('unknown placement setting')
    if not parallel(config) and set(placement)!={'mode'}:
        raise ValueError('pool mappings require baseline-parallel placement')
    if parallel(config):
        pool_ids={p['id'] for p in pools};arm_names={name for c in rows for name in c['arms']}
        for key,allowed in (('product_pools',{'brewfs','juicefs'}),('arm_pools',arm_names)):
            mappings=placement.get(key,{})
            if not isinstance(mappings,dict) or set(mappings)-allowed:raise ValueError('unknown '+key+' selector')
            for ids in mappings.values():
                if not isinstance(ids,list) or not ids or not all(isinstance(x,str) for x in ids) or len(set(ids))!=len(ids) or set(ids)-pool_ids:
                    raise ValueError('unique existing pool IDs required in '+key)
        for c in rows:
            assigned=[p for name in c['arms'] for p in assigned_pools(config,c,name)]
            if len({p['hardware_class'] for p in assigned})!=1:
                raise ValueError('cross-pool hardware must match for paired case: '+c['name'])
    return True


def build(config):
    validate(config)
    split=parallel(config)
    pools = sorted(config['clusters'], key=lambda p: p['id'])
    rows, assets, blocks = [], {}, []
    for index, case in enumerate(config['cases']):
        eligible = [p for p in pools if case['suite'] in p['supported_suites'] and p['agent_slots'] >= case['params']['agents']]
        if not eligible:
            raise ValueError('no pool has capacity for complete paired case: '+case['name'])
        if not split and len({p['hardware_class'] for p in eligible}) != 1:
            raise ValueError('split heterogeneous hardware into separate campaigns: '+case['name'])
        condition = fingerprint([case, config['seed'], config['metadata_backend']])[:16]
        names = sorted(case['arms'])
        # Reproducible cyclic orders balance positions on a pool over a full
        # cycle. In parallel mode these are local queue positions, not barriers
        # or simultaneous-start promises across clusters.
        offset = int(fingerprint([condition, config['seed']])[:8], 16) % len(names)
        for repeat in range(config['repetitions']):
            paired_pool = eligible[(index + repeat) % len(eligible)]
            block = f'{condition}-r{repeat+1}'
            ordered = names[offset:] + names[:offset]
            shift = repeat % len(ordered); ordered = ordered[shift:] + ordered[:shift]
            ids = [];cell_pools={};resources=set()
            for order, name in enumerate(ordered):
                route = case['arms'][name]
                choices=assigned_pools(config,case,name) if split else [paired_pool]
                pool=choices[repeat%len(choices)]
                # One preparation per pool / dataset / representation/layout.
                # Cache toggles do not duplicate immutable import artifacts.
                formation = {k:v for k,v in route.items() if k not in ('payload_cache_bytes','role','handoff_mode')}
                dataset = case['params'].get('dataset', case['params'].get('trace', 'million-4k' if case['workload']=='smallfiles' else f"fio-{case['params'].get('file_bytes_per_job',0)}"))
                if case['workload']=='fio':
                    # Verify headers depend on block size, and each job needs a
                    # separate file. Never reuse a sequential fixture as 4K IO.
                    dataset += '-'+fingerprint({k:case['params'][k] for k in
                        ('file_bytes_per_job','block_bytes','numjobs')})[:12]
                asset = fingerprint([pool['id'], dataset, formation,
                                     config.get('input_manifest_sha256'), config.get('build_manifest_sha256')])[:24]
                assets.setdefault(asset, {'id':asset, 'pool':pool['id'], 'dataset':dataset,
                    'formation':formation, 'import_repetitions':1,
                    'required_stages':['source-verify','import','full-base-verify','cache-reset'],
                    'scope':'all preparation/validation/temporary bytes charged once per actual asset'})
                cell_id = block+'-'+name; ids.append(cell_id)
                cell_pools[cell_id]=pool['id'];resources.update(pool['resource_ids'])
                gates = ['exact-build-input-inventory', 'native-route-and-metadata',
                         'effective-cache-and-durability', 'host-capacity-and-maintenance',
                         'complete-telemetry-and-overhead', *case.get('extra_gates', [])]
                if split and len({p['id'] for n in case['arms'] for p in assigned_pools(config,case,n)})>1:
                    gates.append('cross-pool-hardware-equivalence')
                rows.append({'id':cell_id, 'paired_block':block, 'order':order, 'pool':pool['id'],
                    'suite':case['suite'], 'case':case['name'], 'workload':case['workload'],
                    'claim':case['claim'], 'params':deepcopy(case['params']), 'arm_id':name,
                    'arm':deepcopy(route), 'asset_id':asset, 'required_gates':gates,
                    'stages':stages_for(case), 'observation_marks':marks_for(case), 'execution_enabled':False})
                if split:rows[-1]['resource_ids']=list(pool['resource_ids'])
            if split:
                blocks.append({'id':block,'pool':None,'pools':sorted(set(cell_pools.values())),
                    'cell_pools':cell_pools,'cells':ids,'resource_ids':sorted(resources),'condition':condition})
            else:
                blocks.append({'id':block, 'pool':pool['id'], 'cells':ids,
                               'resource_ids':pool['resource_ids'], 'condition':condition})
    ready = all(sha(config.get(k)) for k in ('input_manifest_sha256','build_manifest_sha256')) and all(sha(p.get('inventory_sha256')) for p in pools)
    result = {'schema':PROTOCOL+'-plan', 'campaign_id':config['campaign_id'],
              'config_sha256':fingerprint(config), 'config':deepcopy(config),
              'provenance_bound':ready, 'execution_enabled':False,
              'blocks':blocks, 'cells':rows, 'assets':list(assets.values()),
              'counts':{'cells':len(rows),'paired_blocks':len(blocks),'onboarding_assets':len(assets)},
              'parallelism':'independent complete blocks; one active cell per pool; shared resource IDs serialize pools',
              'repeat_scope':'operation blocks; independent deployment repetitions require new campaign identities'}
    if split:
        result['parallelism']='baseline arms split across assigned pools; per-cell leases; one active cell per pool; shared resource IDs serialize conflicting cells'
    result['plan_sha256'] = fingerprint(result)
    return result


def check_plan(value):
    actual = deepcopy(value); claimed = actual.pop('plan_sha256', None)
    if fingerprint(actual) != claimed or value != build(value['config']):
        raise ValueError('plan changed; regenerate and qualify the new campaign')
    return value
