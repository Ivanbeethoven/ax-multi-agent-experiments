#!/usr/bin/env python3
"""Admit one explicit flat C1/C2/C3 cell from actual preparation and calibration."""
import argparse
import json
import re
from pathlib import Path,PurePosixPath
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,write_new,pinned
from bench.runtime import pilot_context as pilot_runtime
from bench.runtime.pilot_pools import pool_of,cross_pool_proof
from bench import suite_state as state


def ref(path):
    path=Path(path).resolve(strict=True)
    return dict(path=str(path),sha256=digest(path))


def asset_binary_proof(asset,product,build,folder):
    binary=asset.get('filesystem_binary')
    if binary is None:return None
    if product=='brewfs':
        matches=[item['sha256'] for item in build['files'] if item['file']=='brewfs']
        if len(matches)!=1 or binary['sha256']!=matches[0]:raise ValueError('Prepared filesystem binary differs from frozen build')
        return dict(product=product,binary=binary,source='frozen self-built manifest')
    if product!='juicefs':raise ValueError('Unknown filesystem product')
    expected='6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
    path=folder/'client-binary.json';proof=load(path)
    if (proof.get('schema')!='pilot-external-client-binary-v1' or proof.get('passed') is not True or
            proof.get('product')!='juicefs' or proof.get('binary')!=binary or binary['sha256']!=expected or
            not re.match(r'^juicefs version 1\.4\.2(?:\+|\s|$)',proof.get('version_output',''))):
        raise ValueError('Actual fixed JuiceFS 1.4.2 binary proof required')
    release=load(pinned(proof['release']))
    if (release.get('tag_name')!='v1.4.2' or release.get('draft') is not False or release.get('prerelease') is not False or
            release.get('html_url')!='https://github.com/juicedata/juicefs/releases/tag/v1.4.2'):
        raise ValueError('Pinned official JuiceFS release receipt differs')
    filesystem=load(folder/'filesystem.json')
    routes=[filesystem[key] for key in ('native_checks','complete_route','complete_base') if key in filesystem]
    route=proof['qualified_route']
    if route not in routes:raise ValueError('Binary proof belongs to another qualified route')
    qualified=load(pinned(route))
    if qualified.get('passed') is not True or qualified.get('binary_sha256',expected)!=expected:
        raise ValueError('Qualified route used another client binary')
    transfers=folder.parent/'transfers.json'
    originals=[item for item in load(transfers) if item.get('path')==route['path'] and item.get('sha256')==route['sha256']]
    if len(originals)!=1:raise ValueError('Unique original qualified-route transfer required')
    route_source=PurePosixPath(originals[0]['source']);command_source=PurePosixPath(proof['mount_command_source'])
    if (not route_source.is_absolute() or not command_source.is_absolute() or '..' in command_source.parts or
            not command_source.is_relative_to(route_source.parent) or command_source==route_source):
        raise ValueError('Original mount command is outside the qualified route')
    command=load(pinned(proof['mount_command']))
    argv=command if isinstance(command,list) else command.get('argv')
    if (not isinstance(argv,list) or len(argv)<2 or argv[:2]!=[binary['path'],'mount']):
        raise ValueError('Original qualified mount command used another filesystem executable')
    return dict(product=product,binary=binary,source='fixed external release',
        qualification=ref(path),release=proof['release'],qualified_route=route,
        mount_command=proof['mount_command'],mount_command_source=str(command_source),
        route_transfers=ref(transfers),version_output=proof['version_output'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control',type=Path,required=True)
    pilot_runtime.arguments(p)
    p.add_argument('--qualification',type=Path,required=True)
    p.add_argument('--overhead',type=Path,required=True)
    p.add_argument('--cell',required=True)
    p.add_argument('--node-tool',required=True)
    p.add_argument('--node-tool-sha256',required=True)
    p.add_argument('--observer-tool-name',default='make-observer-cloud-03.py')
    p.add_argument('--observer-tool-sha256',required=True)
    p.add_argument('--collector-name',required=True)
    p.add_argument('--latest-check',type=Path,help='Required explicit branch receipt for a new C2/C3 batch')
    p.add_argument('--cross-pool-qualification',type=Path)
    p.add_argument('--owner',default='pilot-operator')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    runtime_ref=pilot_runtime.reference(a);runtime=pilot_runtime.select(a.control,runtime_ref)
    plan=load(a.control/'suite/plan.json');cell=next(c for c in plan['cells'] if c['id']==a.cell)
    pool=pool_of(cell)
    if cell['workload'] not in ('fio','trace','smallfiles') or cell['arm_id'] not in ('native','juicefs'):
        raise ValueError('This adapter only admits flat C1/C2/C3 routes')
    is_trace=cell['workload']=='trace'
    is_small=cell['workload']=='smallfiles'
    if is_small and (cell['params']['agents']!=1 or cell['params']['workspaces']!=1):raise ValueError('One C3 workspace only')
    if (is_trace or is_small) and not a.latest_check:raise ValueError('Explicit latest-branch check required for C2/C3')
    q=a.qualification/cell['arm_id'];work=load(q/'workload.json');overhead=load(a.overhead)
    if work['params']!=cell['params']:
        raise ValueError('Calibration workload differs; provide a matching qualification')
    if not overhead['passed'] or overhead['metric_contract_sha256']!=digest(q/'metrics.json') or overhead['workload_contract_sha256']!=digest(q/'workload.json'):
        raise ValueError('Actual matching observation qualification required')
    if any(v>.05 for v in overhead['relative_changes'].values()):raise ValueError('Overhead exceeded protocol')
    for name in ('input.json','filesystem.json'):
        if load(q/name)['passed'] is not True:raise ValueError('Preparation failed: '+name)
    if load(a.qualification/'hardware.json')['passed'] is not True:raise ValueError('Hardware failed')
    if load(runtime/'allocation.json')['passed'] is not True:raise ValueError('Allocation failed')
    if plan['config']['build_manifest_sha256']!=digest(a.control/'provenance/build-manifest.json') or plan['config']['input_manifest_sha256']!=digest(a.control/'provenance/input-manifest.json'):
        raise ValueError('Frozen provenance differs')
    latest_path=a.latest_check or a.control/'provenance/latest-before-formal-c1-01.json'
    latest=load(latest_path)
    if latest['passed'] is not True or latest['build_manifest_sha256']!=plan['config']['build_manifest_sha256']:
        raise ValueError('Latest-branch check missing or changed')
    inventory=load(runtime/'fleet.json')
    if load(runtime/'allocation.json')['inventory_sha256']!=digest(runtime/'fleet.json'):
        raise ValueError('Actual fleet differs from allocation')
    assetdir=a.qualification/'assets'/cell['arm_id'];asset=load(assetdir/'asset.json')
    if asset['plan']['sha256']!=digest(a.control/'suite/plan.json'):
        raise ValueError('Asset was prepared under another plan')
    if asset.get('pool','pool-a')!=pool:raise ValueError('Asset belongs to another physical pool')
    binding=load(q/'binding.json') if (q/'binding.json').exists() else None
    if len(plan['config']['clusters'])>1 and binding is None:raise ValueError('New multi-pool cell needs an explicit prepared asset binding')
    if binding and (not Path(binding['asset']).is_absolute() or binding.get('pool',pool)!=pool):
        raise ValueError('Explicit asset path or pool differs')
    if binding and binding.get('asset_manifest'):
        if digest(pinned(binding['asset_manifest']))!=digest(assetdir/'asset.json'):raise ValueError('Asset binding differs from collected manifest')
    binary_provenance=asset_binary_proof(asset,cell['arm']['product'],load(a.control/'provenance/build-manifest.json'),q)
    config=asset['config']
    if cell['arm_id']=='native':
        cache=config['cache'];cache_ok=(cache['read_memory_bytes']==536870912 and cache['read_ssd_bytes']==0 and
            cache['write_memory_bytes']==536870912 and cache['writeback_mode']=='upload_before_commit')
    else:
        cache_ok=(config['cache_dir']=='memory' and config['cache_mib']==512 and config['buffer_mib']==512 and config['writeback'] is False)
    if not cache_ok:raise ValueError('Cache/durability differs')
    a.out.mkdir(parents=True,exist_ok=False)
    proof=dict(passed=True,plan_sha256=plan['plan_sha256'],cell_id=cell['id'],
               provenance=[ref(a.control/'provenance'/n) for n in ('build-manifest.json','input-manifest.json','inventory.json')],
               actual_allocation=ref(runtime/'allocation.json'),actual_asset=ref(assetdir/'asset.json'),
               binary_provenance=binary_provenance,
               cache_checked=cache_ok,latest_branch_check=ref(latest_path),
               source_roles=[n['id'] for n in inventory['nodes'] if n['cluster_id']==pool])
    write_new(a.out/'provenance-cache.json',proof)
    gates={
        'exact-build-input-inventory':a.out/'provenance-cache.json',
        'native-route-and-metadata':q/'filesystem.json',
        'effective-cache-and-durability':a.out/'provenance-cache.json',
        'host-capacity-and-maintenance':a.qualification/'hardware.json',
        'complete-telemetry-and-overhead':a.overhead}
    if is_trace:
        gates.update({'complete-trace-qualification':q/'filesystem.json','fresh-initial-tree':q/'input.json',
                      'repo-home-xdg-on-mount':q/'input.json'})
    if is_small:gates.update({'full-base-validation-once':q/'filesystem.json','client-cache-evidence':a.out/'provenance-cache.json'})
    if 'cross-pool-hardware-equivalence' in cell['required_gates']:
        if not a.cross_pool_qualification:raise ValueError('Explicit cross-pool equivalence evidence required')
        cross_pool_proof(a.cross_pool_qualification,plan,runtime/'fleet.json')
        gates['cross-pool-hardware-equivalence']=a.cross_pool_qualification
    if set(gates)!=set(cell['required_gates']):raise ValueError('Unexpected additional gate')
    qualification=dict(schema='suite-qualification-v1',plan_sha256=plan['plan_sha256'],cell_id=cell['id'],
                       checks=[dict(gate=g,evidence=ref(gates[g]),equals={'passed':True}) for g in cell['required_gates']])
    write_new(a.out/'qualification.json',qualification)
    if is_small:
        raw=a.qualification/'route';binding=load(q/'binding.json')
        prepared=q/'input.json';verified=raw/'base-check/result.json';cold=verified
        if any(load(p)['passed'] is not True for p in (prepared,verified)) or load(raw/'import/exit.json')['exit_code']!=0:
            raise ValueError('Full million-file import and original cold readback required')
        base_path=asset['mount_point']+'/base'
        timings=dict(import_seconds=load(raw/'import/exit.json')['wall_seconds'],
                     cold_full_verify=load(raw/'full-base-check/exit.json')['wall_seconds'])
    elif not is_trace:
        geometry='seq' if cell['params']['block_bytes']==1048576 else 'random'
        prepared=assetdir/('base-'+geometry+'-prepare/result.json')
        verified=assetdir/('base-'+geometry+'-verify/result.json')
        cold=assetdir/('cold-base-verify-'+str(cell['params']['block_bytes'])+'/result.json')
        for path in (prepared,verified,cold):
            value=load(path)
            if not value['passed'] or value['application_bytes']!=cell['params']['file_bytes_per_job']:
                raise ValueError('Exact full base preparation/CRC check required')
        base_path=asset['mount_point']+'/base-'+str(cell['params']['block_bytes'])
        timings={name:load(path)['wrapper_wall_seconds'] for name,path in [('prepare',prepared),('full_verify',verified),('cold_full_verify',cold)]}
    else:
        raw=a.qualification/'route';binding=load(q/'binding.json')
        prepared=q/'input.json';verified=raw/'strict-proof/verification.json';cold=raw/'cold-proof/verification.json'
        if any(load(p)['passed'] is not True for p in (prepared,verified,cold)) or load(raw/'import/exit.json')['exit_code']!=0:
            raise ValueError('Full initial import, strict qualification and original cold readback required')
        base_path=asset['mount_point']+'/base'
        timings=dict(import_seconds=load(raw/'import/exit.json')['wall_seconds'],
            strict_replay=load(raw/'strict/exit.json')['wall_seconds'],cold_readback=load(raw/'cold-readback/exit.json')['wall_seconds'])
    receipt=dict(schema='suite-asset-v1',plan_sha256=plan['plan_sha256'],asset_id=cell['asset_id'],
                 native_binding=dict(asset=ref(assetdir/'asset.json'),base_path=base_path,
                                     bucket=asset['bucket'],database=asset['database']),
                 timings_seconds=timings,
                 stages={name:dict(evidence=ref(path),equals={'passed':True}) for name,path in {
                     'source-verify':q/'input.json','import':prepared,'full-base-verify':verified,'cache-reset':cold}.items()})
    write_new(a.out/'asset.json',receipt)
    state.qualify(a.control/'state.sqlite',ref(a.out/'qualification.json'))
    with state.transaction(a.control/'state.sqlite') as db:
        exists=db.execute('SELECT 1 FROM assets WHERE id=?',(cell['asset_id'],)).fetchone() is not None
    if not exists:state.register_asset(a.control/'state.sqlite',ref(a.out/'asset.json'))
    lease=state.claim(a.control/'state.sqlite',pool,a.owner)
    write_new(a.out/'lease.json',lease)
    if lease['cell']!=cell or lease['already_claimed']:
        raise ValueError('Unexpected existing or different reservation; inspect without running')
    context=dict(control=str(a.control),out=str(a.out),cell_id=cell['id'],qualification=str(a.qualification),
                 overhead=ref(a.overhead),node_tool=dict(path=a.node_tool,sha256=a.node_tool_sha256),
                 observer_tool_name=a.observer_tool_name,observer_tool_sha256=a.observer_tool_sha256,
                 collector_name=a.collector_name,asset=binding['asset'] if binding else str(Path(asset['mount_point']).parent),
                 asset_manifest=ref(assetdir/'asset.json'))
    if runtime_ref:context['runtime_control']=runtime_ref
    if is_trace:context.update(asset=binding['asset'],trace=binding['trace'])
    if is_small:context.update(asset=binding['asset'],smallfiles=binding['smallfiles'])
    write_new(a.out/'context.json',context)
    print(json.dumps(dict(passed=True,cell_id=cell['id'],context=ref(a.out/'context.json'))))


if __name__=='__main__':main()
