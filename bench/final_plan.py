#!/usr/bin/env python3
"""Generate the base qualification/mechanism plan; scale campaigns use scale_plan.py."""
import argparse
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = 'FINAL-PLAN-20261029-FOUR-CLUSTERS'
GENERAL_METHODS = ['native-overlay', 'juicefs']
ABLATION_METHODS = ['native', 'native-overlay']
STAGES = ['G', 'A', 'P', 'D', 'F']
POOLS = {'G':'product-comparison', 'A':'brewfs-ablation', 'P':'ml-readonly', 'D':'object-layout', 'F':'fork-clone'}
CLUSTERS = {'C1': ['G', 'A'], 'C2': ['P'], 'C3': ['D'], 'C4': ['F', 'M']}
POOLS['M'] = 'agent-node-handoff'
TRACE_CATALOG = ROOT/'scenarios/k8s-containers/archives.json'
TRACE_ARCHIVES = {r['case']: r for r in json.loads(TRACE_CATALOG.read_text(encoding='utf-8'))['archives']}
TRACE_CASES = list(TRACE_ARCHIVES)
DEFERRED = [
    '64/128-agent replay/live, 8M/32M readonly corpus, large fork and fleet handoff are planned separately in bench/scale_plan.py',
    'Mid-tool/process-memory migration and network-partition takeover remain outside the clean-boundary handoff protocol',
    'EC 4+2 and client block-size changes require a separate study',
]


def layout_controls():
    matrix = json.loads((ROOT/'experiments/retest-2026-10-29/layout-matrix.json').read_text())
    controls = {'s3-default': {'profile': 'disabled', 'requested_block_bytes': None,
                              'flags': matrix['profiles']['disabled']}}
    for profile in ('block-only', 'enabled'):
        for block in matrix['supported_ec_block_bytes']:
            controls[f's3-{profile}-{block}'] = {
                'profile': profile, 'requested_block_bytes': block,
                'flags': matrix['profiles'][profile],
                'frame_noop_control_ec2_plus2': profile == 'enabled' and block == 1048576,
            }
    return controls


def groups():
    common = ['frozen-source-binary-image', 'resource-and-cache-accounting',
              'fresh-owned-state', 'content-and-persistence', 'complete-local-retention']
    def group(rq, name, stage, methods, axes, gates, runner, status):
        return dict(rq=rq, group=name, stage=stage, methods=methods, axes=axes,
                    requires=common+gates, runner=runner, runner_status=status)
    layouts = layout_controls()
    return [
        group('RQ5', 'G1-general-trace', 'G', GENERAL_METHODS,
              {'trace':TRACE_CASES, 'cache':['product-default']},
              ['full-trace-manifest-including-final-tree', 'reopen-content-check', 'actual-fuse-controls'],
              'tools/full-fuse-replay', 'single replay plus bench/trace_checks.py; full input download/verification and immutable lower preparation required'),
        group('RQ1', 'G2-warm-random-read', 'G', GENERAL_METHODS,
              {'files':[4], 'file_bytes':[536870912], 'request_bytes':[4096], 'cache':['same-files-W']},
              ['same-prefilled-files', 'full-coverage-and-convergence', 'measurement-cache-state-observed', 'fuse-and-db-counters'],
              'bench/warm_same_files.py', 'single-step fio job/check tools implemented; AI executes and reviews each pass; mounted W qualification pending'),
        group('RQ5', 'A1-overlay-trace-ablation', 'A', ABLATION_METHODS,
              {'trace':TRACE_CASES, 'cache':['product-default']},
              ['full-trace-manifest-including-final-tree', 'reopen-content-check', 'actual-fuse-controls'],
              'tools/full-fuse-replay', 'same-build flat/overlay; AI supervises each replay/check/crossover'),
        group('RQ1', 'A2-overlay-warm-ablation', 'A', ABLATION_METHODS,
              {'files':[4], 'file_bytes':[536870912], 'request_bytes':[4096], 'cache':['same-files-W']},
              ['same-prefilled-files', 'full-coverage-and-convergence', 'measurement-cache-state-observed', 'fuse-and-db-counters'],
              'bench/warm_same_files.py', 'same-build flat/overlay; stepwise fio preparation/check; mounted qualification pending'),
        group('RQ2', 'P2-ml-readonly-end-to-end', 'P', ['packed', 'native-tikv-flat-v1'],
              {'files':[1048576], 'readers':[1,16], 'workload':['discovery-stat','shuffled-read'],
               'cache':['client-cold','same-readset-warm']},
              ['packed-target-path-qualified', 'frozen-ml-agent-task-readset', 'source-file-inventory', 'all-content',
               's3-object-and-inline-inventory', 'total-client-plus-metadata-budget'],
              'bench/ml_import.py + tools/ml-corpus-scan', 'atomic import/scanner available; fixture publish and mount are separate native CLI steps; real ML task/readset and resource qualification required'),
        group('RQ2', 'P1-ml-metadata-placement', 'P', ['packed', 'native-tikv-packed-payload'],
              {'files':[1048576], 'readers':[1,16], 'workload':['discovery-stat','shuffled-read'],
               'cache':['client-cold','same-readset-warm']},
              ['packed-target-path-qualified', 'frozen-ml-agent-task-readset', 'identical-packed-payload', 'all-content',
               'metadata-db-counters', 'total-client-plus-metadata-budget'],
              'bench/reference/ml_readonly_matrix.py', '1024-file local qualification passed; formal task/cold/resource runner pending'),
        group('RQ3', 'D-fixed-object-layout', 'D', list(layouts),
              {'object_bytes':[8388731], 'request':['full-GET','random-4KiB-Range'],
               'cache':['verified-cold-start','same-object-warm']},
              ['identical-objects-and-requests', 'persisted-layout-probe', 'physical-EC2-plus2',
               'layered-byte-counters', 'observed-cold-or-warm-state'],
              'bench/s3_requests.py',
              'fixed single-step PUT/full-GET/Range tool available; serial request service time only; real-server layout/cold/counter qualification required'),
        group('RQ1', 'F-fork-clone', 'F', ['brewfs-fork', 'juicefs-clone', 'native-full-copy'],
              {'files':[100000], 'file_bytes':[4096], 'children':[1,8]},
              ['immutable-identical-source', 'fork-clone-content-and-isolation', 'first-durable-write',
               'capacity-and-inodes', 'prepare-and-retained-resource-accounting', 'verified-full-copy-path'],
              'bench/experiment_step.py + bench/fs_step.py',
              'single native create command and independent first-use/isolation tools available; real fork/clone semantics qualification required'),
        group('RQ4', 'M-agent-node-handoff', 'M',
              ['native-overlay-stay','native-overlay-move','juicefs-stay','juicefs-move'],
              {'task':['pytest-dev__pytest-10356','sympy__sympy-13878'], 'target_cache':['client-cold']},
              ['two-distinct-executor-nodes', 'shared-reachable-service-anchor', 'native-session-restore-qualified',
               'same-checkpoint-stay-move-pair', 'completed-tool-boundary', 'source-writer-release',
               'session-history-and-next-tool-identity', 'coordinator-monotonic-timeline'],
              'bench/session_check.py + bench/fs_step.py + bench/experiment_step.py',
              'history/file integrity and single-command receipts available; native export/import/resume and service lease release must qualify on actual nodes'),
    ]


def build(repetitions=3, seed=42, stages=None, include_diagnostics=False, cluster=None, include_migration=False):
    if repetitions not in (1,3):
        raise ValueError('one smoke batch or three paired batches required')
    if cluster is not None and (cluster not in CLUSTERS or stages is not None):
        raise ValueError('choose one cluster or stages, not both')
    allowed = STAGES + (['M'] if include_migration else [])
    selected = [s for s in CLUSTERS[cluster] if s in allowed] if cluster else allowed if stages is None else stages
    if not selected or len(selected) != len(set(selected)) or set(selected)-set(allowed):
        raise ValueError('unique stages from G/A/P/D/F required; M additionally requires include_migration')
    cells = []
    layouts = layout_controls()
    for g in groups():
        if g['stage'] not in selected:
            continue
        if g['group'] == 'P1-ml-metadata-placement' and not include_diagnostics:
            continue
        for values in itertools.product(*g['axes'].values()):
            params = dict(zip(g['axes'], values))
            pair = hashlib.sha256(json.dumps([PROTOCOL,g['group'],params], sort_keys=True).encode()).hexdigest()[:12]
            for batch in range(1, repetitions+1):
                order = list(g['methods'])
                if g['stage'] == 'D' and not include_diagnostics:
                    order = [m for m in order if layouts[m]['profile'] != 'block-only']
                random.Random(f'{seed}:{pair}:{batch}').shuffle(order)
                crossover = g['stage'] in ('G', 'A')
                for period in range(1, 3 if crossover else 2):
                    for position, method in enumerate(order,1):
                        cells.append({
                            'id':f'{pair}-b{batch}-{method}-p{period}', 'rq':g['rq'], 'group':g['group'], 'stage':g['stage'],
                            'paired_batch':f'{pair}-b{batch}',
                            'order_in_pair':(period-1)*len(order)+position, 'method':method,
                            'isolation_pool':POOLS[g['stage']],
                            'cluster_id':next(k for k,v in CLUSTERS.items() if g['stage'] in v),
                            'owner_agent':next(k.lower()+'-owner' for k,v in CLUSTERS.items() if g['stage'] in v),
                            'allocation_unit':('two-executors-shared-service' if g['stage']=='M' else
                                               'two-worker-crossover' if crossover else 'single-worker-complete-pair'),
                            'resource_roles':({'source_executor':1, 'target_executor':2, 'service_anchor':3}
                                              if g['stage']=='M' else None),
                            'parallel_period':period if crossover else None,
                            'worker_slot':(position if period==1 else 3-position) if crossover else 1,
                            'within_worker_comparison_required':g['stage']!='M',
                            'exclusive_backend_during_measurement':True,
                            'params':deepcopy(params), 'method_controls':deepcopy(layouts.get(method,{})),
                            'input_archive':({k: TRACE_ARCHIVES[params['trace']][k] for k in ('case','uri','sha256','bytes','root_dir')}
                                             if 'trace' in params else None),
                            'tier':('conditional-extension' if g['stage']=='M' else
                                    'conditional-diagnostic' if g['group']=='P1-ml-metadata-placement'
                                    or layouts.get(method,{}).get('profile')=='block-only' else 'primary'),
                            'requires':list(g['requires'])+(['matched-workers-and-complete-crossover'] if crossover else []),
                            'runner':g['runner'], 'runner_status':g['runner_status'],
                            'status':'not_run', 'execution_enabled':False,
                        })
    counts = {g['group']:sum(c['group']==g['group'] for c in cells)
              for g in groups() if any(c['group']==g['group'] for c in cells)}
    return {'protocol':PROTOCOL, 'seed':seed, 'paired_batches':repetitions,
            'cluster_id':cluster,
            'clusters':{k:{'stages':[s for s in v if s in selected], 'owner_agent':k.lower()+'-owner',
                           'prompt':'experiments/retest-2026-10-29/runbook/agents/'+k+'.prompt.md'} for k,v in CLUSTERS.items()
                        if any(s in selected for s in v)},
            'trace_catalog_sha256':hashlib.sha256(TRACE_CATALOG.read_bytes()).hexdigest(),
            'stages':[s for s in STAGES+['M'] if s in selected], 'group_counts':counts,
            'include_conditional_diagnostics':include_diagnostics,
            'include_migration_extension':include_migration,
            'prerequisites':{'P0':'qualify target packed path and inspect GET/cache amplification; not a performance score'},
            'scheduling':{'parallelism':'independent ready comparison/ablation pools; G/A baselines run on matched workers then swap',
                          'worker_unit':'G/A: complete two-worker two-period crossover; P/D/F: all methods sequential on one worker; M: fixed source/target executors and separate shared service',
                          'backend_scope':'G/A/P/D/F: independent worker-local services; M: one separate persistent service anchor per paired trial, shared only by its source/target executors',
                          'same_worker_concurrency':1,
                          'cross_stage_dependency':False,
                          'worker_count':None, 'provisioning_enabled':False,
                          'dispatcher_status':'AI supervises readiness, placement, each measurement and collection; no automatic dispatcher'},
            'total_replay_timeout_s':None, 'cells':cells, 'deferred':DEFERRED,
            'replication_scope':'three complete fresh-state paired blocks; G/A periods and fio epochs are nested, not independent samples; actual host identity recorded',
            'live_agent_score_claimed':False,
            'note':'Preparation only. Gates and runner_status are not runtime receipts; no resource actions.'}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--smoke',action='store_true')
    selection=p.add_mutually_exclusive_group()
    selection.add_argument('--stages',nargs='+',choices=STAGES+['M'])
    selection.add_argument('--cluster',choices=list(CLUSTERS),help='plan exactly one owner cluster; never provision')
    p.add_argument('--include-diagnostics',action='store_true',
                   help='also plan P1 metadata isolation and D block-only attribution; never auto-run')
    p.add_argument('--include-migration',action='store_true',help='plan conditional C4 live-session handoff; no provisioning or execution')
    a=p.parse_args()
    result=build(1 if a.smoke else 3,a.seed,a.stages,a.include_diagnostics,a.cluster,a.include_migration)
    result['smoke_only']=a.smoke
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'cells':len(result['cells']),'groups':result['group_counts'],'execution_enabled':False}))
