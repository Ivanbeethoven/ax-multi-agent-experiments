#!/usr/bin/env python3
"""Collect one completed full-trace route and write exact calibration contracts."""
import argparse
from pathlib import Path
import json
import shlex
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,write_new
from bench.runtime import pilot_context as pilot_runtime
from bench.runtime.pilot_pools import pool_of,alias,fleet_node


def ref(p):return dict(path=str(p),sha256=digest(p))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('control','shared-qualification','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--product',choices=['brewfs','juicefs'],required=True)
    pilot_runtime.arguments(p)
    p.add_argument('--cache-parent-fds',action='store_true')
    for name in ('route','asset','case','manifest','binary','verifier'):
        p.add_argument('--'+name,required=True,help='Actual absolute path on the client')
    a=p.parse_args();arm='native' if a.product=='brewfs' else 'juicefs'
    runtime=pilot_runtime.select(a.control,pilot_runtime.reference(a))
    plan=load(a.control/'suite/plan.json');cell=next(c for c in plan['cells'] if c['workload']=='trace' and c['arm_id']==arm)
    pool=pool_of(cell);client=fleet_node(load(runtime/'fleet.json'),pool,'client')
    if Path(a.case).name!=cell['params']['trace']:raise ValueError('Wrong complete trace')
    a.out.mkdir(parents=True,exist_ok=False);transfers=[]
    def remote(argv):
        return subprocess.check_output(['ssh','-F',str(runtime/'ssh.conf'),alias(pool,'client'),shlex.join(list(map(str,argv)))],text=True)
    def get(source,relative):
        target=a.out/relative;target.parent.mkdir(parents=True,exist_ok=True)
        wanted=remote(['sha256sum',source]).split()[0]
        subprocess.run(['scp','-F',str(runtime/'ssh.conf'),alias(pool,'client')+':'+source,str(target)],check=True)
        if digest(target)!=wanted:raise ValueError('Changed evidence transfer')
        transfers.append(dict(source=source,**ref(target)));return target
    raw=a.out/'route';raw.mkdir()
    for name in ('result.json','started.json','base-check.json','import/exit.json','import-sync.json',
                 'strict-proof/verification.json','strict-proof/replay.json','strict-proof/started.json','strict/exit.json','post-replay-sync.json',
                 'normal-stop.json','cold-stop.json','cold-proof/verification.json','cold-readback/exit.json'):
        get(a.route+'/'+name,'route/'+name)
    asset=get(a.asset+'/asset.json','assets/'+arm+'/asset.json')
    metrics=get(a.case+'/metrics.json','source/metrics.json')
    get(a.case+'/initial-tree.json','source/initial-tree.json')
    get(a.case+'/hardlink-check.json','source/hardlink-check.json')
    manifest=get(a.manifest,'source/manifest.json')
    sources={k:dict(path=value,sha256=remote(['sha256sum',value]).split()[0])
             for k,value in [('binary',a.binary),('verifier',a.verifier)]}
    # Recompute the full input inventory before any release barrier; never time it.
    script="import sys,json,pathlib;sys.path[:0]="+repr([client['repo'],client['repo']+'/bench'])+";from bench.trace_checks import inputs;print(json.dumps(inputs(pathlib.Path(CASE),pathlib.Path(MANIFEST),SHA)))"
    script=script.replace('CASE',repr(a.case)).replace('MANIFEST',repr(a.manifest)).replace('SHA',repr(digest(manifest)))
    checked=json.loads(remote(['python3','-c',script]));write_new(a.out/'source/input-check.json',checked)
    route=load(raw/'result.json');values=load(metrics);initial=load(a.out/'source/initial-tree.json')
    required=('repo','state','state/home','state/data','state/state','state/config','state/cache')
    if any(initial.get(k,{}).get('type')!='directory' for k in required):raise ValueError('Repository/HOME/XDG not in replay root')
    strict=load(raw/'strict-proof/verification.json');cold=load(raw/'cold-proof/verification.json')
    if not route['passed'] or route['cell_id']!=cell['id'] or route['asset']['sha256']!=digest(asset):raise ValueError('Actual qualified asset differs')
    if route['strict']['sha256']!=digest(raw/'strict-proof/verification.json') or route['cold_readback']['sha256']!=digest(raw/'cold-proof/verification.json'):
        raise ValueError('Qualified result points to different proofs')
    replay=load(raw/'strict-proof/replay.json');started=load(raw/'strict-proof/started.json')
    if replay['valid'] is not True or replay['mode'] not in ('strict','strict-portable-terminal-probe'):
        raise ValueError('Original byte-checking replay required')
    if started['binary_sha256']!=sources['binary']['sha256'] or started['events_sha256']!=load(manifest)['capture/events.jsonl']:
        raise ValueError('Qualification used a different replay executable or trace')
    for proof in (strict,cold):
        if not proof['passed'] or not proof.get('byte_validation',proof.get('strict') is True) or not proof['final_tree_equal'] or not proof['hardlink_topology_equal']:
            raise ValueError('Complete byte-checking route qualification required')
        for k,metric in [('callbacks','callbacks'),('actual_read_bytes','actual_read_bytes'),('actual_write_bytes','actual_write_bytes')]:
            if proof[k]!=values[metric]:raise ValueError('Qualification work differs')
        if proof.get('path_resolution','full-path')!=('parent-fd-cache' if a.cache_parent_fds else 'full-path'):
            raise ValueError('Qualify the same path policy used by the performance worker')
    for name in ('normal-stop.json','cold-stop.json'):
        s=load(raw/name)
        if not s['passed'] or s['exit_code']!=0 or not s['mount_absent']:raise ValueError('Original route is not normally closed')
    if load(asset)['plan']['sha256']!=digest(a.control/'suite/plan.json'):raise ValueError('Asset uses another frozen plan')
    if load(asset).get('pool','pool-a')!=pool:raise ValueError('Asset belongs to another physical pool')
    hardware=load(a.shared_qualification/'hardware.json')
    if not hardware['passed'] or hardware['inventory_sha256']!=digest(runtime/'fleet.json') or hardware.get('pool','pool-a')!=pool:raise ValueError('Actual matching hardware required')
    write_new(a.out/'hardware.json',hardware)
    folder=a.out/arm;folder.mkdir()
    binding=dict(asset=a.asset,asset_manifest=ref(asset),pool=pool,route=ref(raw/'result.json'),trace=dict(case=a.case,
        manifest=dict(path=a.manifest,sha256=digest(manifest)),cache_parent_fds=a.cache_parent_fds,**sources))
    write_new(folder/'binding.json',binding)
    proof=dict(passed=True,cell_id=cell['id'],complete_route=ref(raw/'result.json'),
        strict=ref(raw/'strict-proof/verification.json'),cold=ref(raw/'cold-proof/verification.json'),
        input_inventory=ref(a.out/'source/input-check.json'),repo_home_xdg=required,asset=ref(asset),
        scope='Full byte-checking replay, final tree/links/SQLite, original cold reopening and repository/HOME/XDG in the private child')
    write_new(folder/'input.json',proof);write_new(folder/'filesystem.json',proof)
    contract=load(a.shared_qualification/arm/'metrics.json')
    contract['notes']=['Complete trace replay; task p99 at N=1 is one task duration. Logical trace callbacks, actual FUSE operations and S3/physical I/O are distinct.']
    contract['source_pins'].append(ref(raw/'result.json'))
    write_new(folder/'metrics.json',contract)
    workload=dict(schema='experiment-workload-contract-v1',reviewed=True,binary_pins=[sources['binary']],
        input_pins=[ref(folder/'input.json'),ref(manifest),ref(metrics),ref(a.control/'suite/plan.json')],
        params=cell['params'],seed=plan['config']['seed'],path_resolution='parent-fd-cache' if a.cache_parent_fds else 'full-path',
        scope='One complete unpaced trace, same fixed callbacks/read/write bytes and path policy in every calibration and performance trial')
    write_new(folder/'workload.json',workload);write_new(a.out/'transfers.json',transfers)
    print(json.dumps(dict(passed=True,cell_id=cell['id'],qualification=str(a.out))))


if __name__=='__main__':main()
