#!/usr/bin/env python3
"""Resolve a non-executable fresh C3 qualification draft from retained receipts."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

HERE = Path(__file__).resolve().parent
PREP = HERE.parent / 'evidence/preparation'
PREFIX = 'experiments/unified-suite/pilot-aliyun/scripts/'
OLD = '/srv/ax-pilot-control/parallel-control-01'
CLIENT = '/srv/ax-client'
CELL = '16f4ac389d2a2da6-r1-packed'
DB = '<REVIEWED_EMPTY_REDIS_DATABASE>'


def require(ok, detail):
    if not ok:
        raise ValueError(detail)


def draft(tag, prep=PREP):
    require(re.fullmatch(r'c3-reboot-[0-9]{8}t[0-9]{6}z', tag), 'Use c3-reboot-YYYYMMDDtHHMMSSz')
    sources = {}

    def read(name, expected=None):
        path = prep / name
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        require(not expected or sha == expected, 'Retained bytes differ: ' + name)
        sources[name] = sha
        return json.loads(data)

    ctl = 'parallel-control-01/'
    copy = read(ctl + 'copied-files.json', 'c4f2b0969081603f094d3576e7a8185d31e9a053d8b09900856f3f36e71ed138')
    copied = {row['file']: row['sha256'] for row in copy['files']}
    plan, inv, inputs, code = [read(ctl + name, copied[name]) for name in
                              ('plan.json', 'inventory.json', 'input-manifest.json', 'manifest.json')]
    cell = next(row for row in plan['cells'] if row['id'] == CELL)
    require(cell['pool'] == 'pool-a' and cell['arm_id'] == 'packed' and cell['params']['files'] == 100000
            and len(inv['pools']) == 2 and inputs and plan['config']['input_manifest_sha256'] == copied['input-manifest.json']
            and all(row['inventory_sha256'] == copied['inventory.json'] for row in plan['config']['clusters']),
            'Frozen plan, inputs or two-pool inventory differ')
    build_sha = plan['config']['build_manifest_sha256']
    build = read('brewfs-ca07159-build/build-manifest.json', build_sha)
    bins = {item['file']: item['sha256'] for item in build['files']}
    root = 'parallel-c3-packed-base-and-acl-01/'
    archive = read(root + 'pins.json', '30a476acb3768fa846b774e59a106c18c0fecd7d018489068fefa4bbdd5ad0e7')
    archived = {row['file']: row['sha256'] for row in archive['files']}
    old = {name: read(root + 'raw/' + name + '.json', archived['raw/' + name + '.json'])
           for name in ('base-started', 'private-started', 'private-debt', 'cold02-request', 'new-helper-install')}
    base, child, cold = (old[key] for key in ('base-started', 'private-started', 'cold02-request'))
    require(base['cell_id'] == child['cell_id'] == cold['cell_id'] == CELL and child['node_tool'] == base['node_tool']
            and child['plan']['sha256'] == copied['plan.json'] and cold['helper'] == old['new-helper-install']['entity_tool']
            and cold['argv'][1:3] == [cold['helper']['path'], 'cold-verify'], 'Wrong original qualification or tools')
    flags = dict(zip(cold['argv'][3::2], cold['argv'][4::2], strict=True))
    require(flags['--source'] == base['source'] and flags['--source-proof'] == base['source_proof']['path']
            and flags['--plan'] == child['plan']['path'], 'Original source/plan paths disagree')
    source = read('pool-a-next-01/collected-client/source-c3-verify-result.json', base['source_proof']['sha256'])
    require(source['passed'] is True and (source['files'], source['bytes_checked']) == (100000, 409600000),
            'Original 100k input verification differs')
    flat = read('c3-flat-native-01/request.json', 'cff7872236f4b473107ea18a47a8292bb591fc39a901a099934ab0b88e0c1b42')
    flat_pins = flat['node_files']
    require(flat_pins['inventory']['sha256'] == copied['inventory.json'] and flat_pins['binary']['sha256'] == bins['smallfiles']
            and old['private-debt']['probe']['sha256'] == bins['entity_metadata_probe'], 'Immutable binary/inventory pins disagree')
    tools = {name: dict(path=flags['--'+name], sha256=flags['--'+name+'-sha256']) for name in ('reader','verifier','oracle')}
    build_root = str(PurePosixPath(flags['--manifest']).parents[1])
    parent_dir, child_dir = CLIENT + '/preparation/' + tag + '-parent', CLIENT + '/qualification/' + tag + '-child'
    parent_asset, child_asset = CLIENT + '/a/' + tag + '-base', CLIENT + '/a/' + tag + '-private'
    parent_mount, child_mount = CLIENT + '/m/' + tag + '-parent', CLIENT + '/m/' + tag + '-child'
    new = '/srv/ax-pilot-control/' + tag

    def pin(path, sha):
        return dict(path=path, sha256=sha)

    def cmd(tool, *actions, **options):
        return ['python3', tool, *actions, *(item for key, value in options.items()
                  for item in ('--'+key.replace('_','-'), value))]

    producer = PREFIX + 'prepare-packed.py'
    worker = dict(plan=child['plan'], inventory=flat_pins['inventory'], source_proof=base['source_proof'],
                  node_tool=base['node_tool'], entity_tool=cold['helper'], build_manifest=pin(flags['--manifest'], build_sha),
                  prepare_tool=pin(CLIENT+'/code-12/'+producer, code[producer]), probe=old['private-debt']['probe'],
                  smallfiles=flat_pins['binary'], **tools)
    worker.update({name: pin(build_root+'/bin/'+name, bins[name]) for name in ('brewfs', 'packed_v3_snapshot_fixture')})
    shared = dict(plan=worker['plan']['path'], source=base['source'], source_proof=base['source_proof']['path'],
                  node_tool=worker['node_tool']['path'], node_tool_sha256=worker['node_tool']['sha256'])
    parents = cmd(worker['prepare_tool']['path'], asset=parent_asset, inventory=worker['inventory']['path'],
                  build_root=build_root, database=DB, mount_state=parent_mount, out=parent_dir, **shared)
    tools_flags = {key: value for name in (*tools, 'probe', 'smallfiles') for key, value in
                   ((name, worker[name]['path']), (name+'_sha256', worker[name]['sha256']))}
    children = cmd(worker['entity_tool']['path'], 'qualify', base_asset=parent_asset, asset=child_asset, cell=CELL,
                   manifest=worker['build_manifest']['path'], private_object_env=flags['--private-object-env'],
                   mount_state=child_mount, out=child_dir, **shared, **tools_flags)
    setup = PREFIX + 'setup-controller.py'
    provenance = {name: pin(OLD+'/provenance/'+name, sha) for name, sha in
                  (('inventory.json', copied['inventory.json']), ('build-manifest.json', build_sha),
                   ('input-manifest.json', copied['input-manifest.json']))}
    coordinator = pin('/srv/ax-pilot-control/code-12/'+setup, code[setup])
    return dict(schema='c3-packed-fresh-qualification-draft-v1', reviewed=False, execution_enabled=False,
                cell_id=CELL, plan_sha256=plan['plan_sha256'], new_control=new, historical_source_hashes=sources,
                worker_sha256_prechecks=worker, parent_argv=parents, child_argv=children,
                unused_worker_paths=[parent_asset, child_asset, parent_mount, child_mount, parent_dir, child_dir],
                provenance=provenance, setup_controller=coordinator, setup_argv=cmd(coordinator['path'],
                inventory=provenance['inventory.json']['path'], code_directory='code-12', out=new),
                suite_plan=pin(OLD+'/suite/plan.json', copied['plan.json']), canonical_lock=OLD+'/runner-locks',
                branch_lookups=[['git','ls-remote','--exit-code', row['repository'], 'refs/heads/'+row['branch']]
                                for row in build['sources']], expected_frozen_sources=build['sources'],
                requires_live=['all control DB leases and pool locks clear', 'current branch checks and worker bytes',
                               'both-pool fresh runtime and hardware; B objects root has 10 GiB minimum',
                               'current A boots, mounts and capacity; reserve a truly empty Redis DB',
                               'same-boot before samples and fresh base/child; post-run capacity and calibration'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    value = draft(args.attempt)
    with args.out.open('x', encoding='utf-8') as out:
        json.dump(value, out, indent=2)
        out.write('\n')
