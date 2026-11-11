#!/usr/bin/env python3
"""Pin the next Pool A C4 flat-copy candidate using coordinator metadata only."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


CELL = 'c5e97774bcffa69a-r1-native'
C3_CELL = '16f4ac389d2a2da6-r1-native'
FILES = 100_000
BYTES = 409_600_000
TREE_SHA = '02aeda272edbab9512595f7b968d840a9cfdc0e8302ed036399bf4151f08bd13'
WORKER_INPUTS = Path('/srv/ax-client/inputs/pilot-100k-01')


def ref(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def load(path):
    return json.loads(path.read_text())


def build(root):
    canonical = root/'parallel-control-01'
    fork = root/'fork-control-01'
    old_plan = load(canonical/'suite/plan.json')
    plan_path = fork/'suite/plan.json'
    plan = load(plan_path)
    if ref(plan_path)['sha256'] != ref(canonical/'next/fork-100k-01/plan.json')['sha256']:
        raise ValueError('C4 plan differs from its canonical frozen source')
    cell = next(c for c in plan['cells'] if c['id'] == CELL)
    c3 = next(c for c in old_plan['cells'] if c['id'] == C3_CELL)
    if (cell['pool'], cell['suite'], cell['case'], cell['workload'], cell['arm_id'],
            cell['arm']['product'], cell['arm']['engine'], cell['params']['dataset'],
            cell['params']['children'], cell['params']['agents'], cell['params']['files'],
            cell['params']['file_bytes']) != ('pool-a', 'C4', 'fork-1', 'fork', 'native', 'brewfs',
            'flat-v1', 'fork-fixture-100k-4k', 1, 1, FILES, 4096):
        raise ValueError('Expected the original A C4 full copy only')
    if cell['asset_id'] == c3['asset_id'] or set(cell['resource_ids']) != set(c3['resource_ids']):
        raise ValueError('C4 requires its own asset on the exact same A hardware')
    source_dir = root/'preparation/pool-a-next-01/collected-client'
    closure_path = source_dir/'input-closure.json'
    fixture_path = source_dir/'source-c4-fixture.json'
    closure, fixture = load(closure_path), load(fixture_path)
    tree = dict(path=str(WORKER_INPUTS/'receipts/c4-fixture-tree.json'), sha256=TREE_SHA)
    if (not all(closure[k] is True for k in ('passed', 'sources_only', 'preparation_processes_exited')) or
            closure['c4_tree'] != tree or closure['c3_check']['sha256'] == TREE_SHA or
            (closure['files_per_input'], closure['bytes_per_input'], fixture['files'], fixture['bytes']) !=
            (FILES, BYTES, FILES, BYTES)):
        raise ValueError('Pool A original distinct C4 input proof differs')
    provenance = {name: ref(fork/'provenance'/name) for name in
                  ('build-manifest.json', 'input-manifest.json', 'inventory.json')}
    if (provenance['build-manifest.json']['sha256'] != plan['config']['build_manifest_sha256'] or
            provenance['input-manifest.json']['sha256'] != plan['config']['input_manifest_sha256'] or
            provenance['inventory.json']['sha256'] != next(c['inventory_sha256'] for c in
                plan['config']['clusters'] if c['id'] == 'pool-a')):
        raise ValueError('C4 provenance differs from the frozen plan')
    manifest = load(fork/'provenance/build-manifest.json')
    metrics = canonical/'qualification/pool-a-shared-01/native/metrics.json'
    if {item['observer_id'] for item in load(metrics)['bindings']} != {
            'pool-a-client', 'pool-a-metadata', 'pool-a-objects'}:
        raise ValueError('C4 must reuse the real A three-role metrics definitions')
    c2_input = canonical/'qualification/pool-a-c2-01/native/input.json'
    if load(c2_input).get('passed') is not True:
        raise ValueError('Verified prior A C2 staging evidence required')
    state_path = fork/'state.sqlite'
    with sqlite3.connect(state_path.as_uri()+'?mode=ro', uri=True) as db:
        status = db.execute('SELECT status FROM cells WHERE id=?', (CELL,)).fetchone()
        started = db.execute("SELECT 1 FROM stages WHERE cell_id=? AND status!='pending'", (CELL,)).fetchone()
    if status != ('pending',) or started:
        raise ValueError('Original C4 flat cell is not untouched')
    attempts = [dict(mode=mode, attempt='c4-a-flat-'+tag,
                     out=str(fork/'calibration'/('flat-'+tag)), verification=verify)
                for mode, tag, verify in [('off', 'off1', 'full-cold-v1'), ('on', 'on1', 'touched-cold-v1'),
                    ('on', 'on2', 'touched-cold-v1'), ('off', 'off2', 'touched-cold-v1'),
                    ('off', 'off3', 'touched-cold-v1'), ('on', 'on3', 'touched-cold-v1')]]
    return dict(schema='c4-native-flat-plan-draft-v1', reviewed=False, executable=False,
        workers_contacted=False, resources_created=0, cell_id=CELL, state_observed='pending',
        state_initialize=False, control=str(fork), canonical_control=str(canonical),
        canonical_lock_root=str(canonical/'runner-locks'), plan=ref(plan_path), provenance=provenance,
        source_proofs=dict(closure=ref(closure_path), fixture=ref(fixture_path)), source=dict(
            path=str(WORKER_INPUTS/'fork-source'), tree=tree, files=FILES, bytes=BYTES,
            fixture=dict(path=str(WORKER_INPUTS/'receipts/c4-fixture.json'), sha256=ref(fixture_path)['sha256']),
            input_closure=dict(path='/srv/ax-client/preparation/pool-a-next-01/input-closure.json',
                               sha256=ref(closure_path)['sha256'])),
        binary=dict(path='/srv/ax-client/build-ca07159-01/bin/brewfs', sha256=next(
            v['sha256'] for v in manifest['files'] if v['file'] == 'brewfs')),
        physical_resource_ids=cell['resource_ids'], required_handoff=dict(c3_cell_id=C3_CELL,
            closure_path=str(canonical/'cells'/(C3_CELL+'.close.json')), c2_staging=ref(c2_input)),
        metrics_contract=ref(metrics), preparation=dict(asset='/srv/ax-client/a/c4-fork-flat-base-01',
            client_out='/srv/ax-client/preparation/c4-fork-flat-base-01',
            controller_out=str(fork/'preparation/base-flat-01'), database=None, attempt='c4-a-flat-base-01'),
        method='full-independent-copy', copy_argv=['/usr/bin/cp', '-a', '--reflink=never', '--'],
        client_settings=cell['client_settings'], trials=attempts,
        formal=dict(attempt='c4-a-flat-formal-01', verification='full-cold-v1',
                    out=str(fork/'cells'/CELL)),
        pending=['Original A C3 successful formal close and exact worker/mount/collector handoff',
            'Fresh exact BrewFS/RustFS remote-tip receipt for this build',
            'Freeze and verify runtime with C4 archive-exit stabilization and touched-cold support; retain timed operation',
            'Fresh selected-pool boot/runtime and plan-bound hardware evidence after handoff',
            'Actual RustFS container/collector chain, empty owned DB, fresh paths and free inode/space checks',
            'Reviewed executable request, separate from this draft'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coordinator-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    root = args.coordinator_root.resolve(strict=True)
    if not output.is_relative_to(root/'preparation'):
        raise ValueError('Draft output must stay in the coordinator preparation directory')
    draft = build(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(draft, stream, indent=2)
        stream.write('\n')
    print(json.dumps(ref(output)))
