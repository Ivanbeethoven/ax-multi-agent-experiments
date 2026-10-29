#!/usr/bin/env python3
"""Generate the five-RQ task list, never the obsolete four-RQ Cartesian grid."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random

METHODS = ['native', 'native-overlay', 'native-overlay-pack', 'juicefs']


def groups():
    # Independent studies: do not multiply axes belonging to different questions.
    return [
        ('RQ1', 'everyday', METHODS, {'trace': ['seven-trace-set']}, ['effective-fuse-controls', 's3-path']),
        ('RQ1', 'modified-parent-fork', METHODS, {'children': [1,16,64], 'child_write_mib': [1]}, ['seal-modified-parent', 'fork-isolation', 'remote-durability']),
        ('RQ1', 'retention', METHODS, {'states': [1000,10000]}, ['seal-modified-parent', 'retained-resource-accounting']),
        ('RQ2', 'metadata-offload', ['native-overlay','native-overlay-pack','juicefs'],
         {'files':[32768,1048576], 'bytes':[1024], 'consumers':[1,16], 'query':['lookup-stat','readdir'], 'cache':['cold','warm']},
         ['same-frozen-parent', 'db-counters', 'cache-evidence']),
        ('RQ2', 'writable-neighbor', ['native-overlay','native-overlay-pack','juicefs'],
         {'background':['none','sustainable','overload'], 'files':[1048576], 'consumers':[16]},
         ['same-frozen-parent', 'open-loop-arrivals', 'db-counters']),
        ('RQ2', 'build-amortization', ['native-overlay','native-overlay-pack'],
         {'reuse':[1,4,16,64]}, ['freeze-build-upload-publish-costs', 'same-work']),
        ('RQ2', 'version-update', ['native-overlay','native-overlay-pack'],
         {'updated_percent':[0.1,1,10], 'reuse_per_version':[4,64]}, ['version-update', 'freeze-build-upload-publish-costs']),
        ('RQ3', 'client-granularity', METHODS, {'g_mib':[1,4,8,16], 'pattern':['sparse4k','sequential','fixed-coverage-write']},
         ['fixed-backend-layout', 'layered-byte-counters']),
        ('RQ3', 'backend-layout', ['common-s3-generator'], {'B_bytes':[None,262144,1048576,4194304]},
         ['identical-objects-and-requests', 'persisted-layout-probe', 'layered-byte-counters']),
        ('RQ3', 'retrieval-strategy', ['native-overlay-pack'], {'strategy':['baseline','candidate']},
         ['fixed-layout', 'retrieval-control', 'layered-byte-counters']),
        ('RQ4', 'migration', ['native-overlay','native-overlay-pack','juicefs-reopen'],
         {'migrations':[0,1,4], 'pending_mib':[0,16,128], 'target_cache':['cold','warm'], 'workspaces':[16], 'source_workers':[4], 'target_workers':[4]},
         ['cross-host-identity', 'save-open', 'remote-durability']),
        ('RQ4', 'handoff-faults', ['native-overlay','native-overlay-pack'],
         {'fault':['before-ack','after-ack','late-old-writer','target-interruption'], 'injections':[20]}, ['cross-host-identity','writer-fencing']),
        ('RQ5', 'trace-batch', METHODS, {'workers':[4,16], 'concurrency':[1,4,16], 'tasks':[56]},
         ['seven-trace-digests', 'rust-performance-replay', 'independent-worker-mounts']),
        ('RQ5', 'live-agent', METHODS, {'tasks':[2], 'action':['continue','fork','migrate']},
         ['agent-session-api', 'task-grader', 'model-budget']),
    ]


def build(repetitions=3, seed=42):
    cells = []
    for rq, group, methods, axes, requirements in groups():
        for values in itertools.product(*axes.values()):
            params = dict(zip(axes, values))
            pair = hashlib.sha256(json.dumps([rq,group,params], sort_keys=True).encode()).hexdigest()[:12]
            for batch in range(1, repetitions+1):
                order = methods.copy(); random.Random(f'{seed}:{pair}:{batch}').shuffle(order)
                for method in order:
                    # JuiceFS g is its own block size, not an assertion about object length.
                    cells.append({'id': f'{pair}-b{batch}-{method}', 'rq': rq, 'group': group,
                         'paired_batch': f'{pair}-b{batch}', 'method': method, 'params': params,
                         'requires': requirements, 'status': 'not_run', 'execution_enabled': False})
    return {'protocol': 'FINAL-PLAN-20261028', 'seed': seed, 'independent_batches': repetitions,
            'total_replay_timeout_s': None, 'cells': cells,
            'note': 'Task list is not authorization or runtime qualification. Each paired batch needs fresh state.'}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seed',type=int,default=42); p.add_argument('--smoke',action='store_true')
    a=p.parse_args(); result=build(1 if a.smoke else 3,a.seed)
    result['smoke_only']=a.smoke
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(f'{len(result["cells"])} cells; execution remains disabled until per-RQ qualification')
