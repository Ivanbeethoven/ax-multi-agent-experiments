#!/usr/bin/env python3
"""Collect one fully checked million-file base and exact C3 contracts; no run."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new


def ref(path):return dict(path=str(path),sha256=digest(path))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('control','shared-qualification','capacity-check','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--product',choices=['brewfs','juicefs'],required=True)
    for name in ('route','asset','binary','verifier'):p.add_argument('--'+name,required=True)
    p.add_argument('--copier')
    a=p.parse_args();arm='native' if a.product=='brewfs' else 'juicefs'
    plan=load(a.control/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['workload']=='smallfiles' and c['arm_id']==arm)
    if cell['params']['agents']!=1 or cell['params']['workspaces']!=1:raise ValueError('Exactly one complete C3 workspace required')
    capacity=load(a.capacity_check)
    if not capacity['passed'] or capacity['reserve_fraction']<.2:raise ValueError('Actual storage/RAM capacity with 20% reserve required')
    snapshot=load(pinned(capacity['after']))
    if snapshot['inventory']!=ref(a.control/'fleet.json'):raise ValueError('Capacity belongs to another actual inventory')
    if capacity.get('extra_full_copies',0)<7:
        raise ValueError('Budget six calibration children and one formal child')
    a.out.mkdir(parents=True,exist_ok=False);transfers=[]
    def remote(argv):
        return subprocess.check_output(['ssh','-F',str(a.control/'ssh.conf'),'pool-a-client',shlex.join(list(map(str,argv)))],text=True)
    def get(source,name):
        target=a.out/name;target.parent.mkdir(parents=True,exist_ok=True)
        wanted=remote(['sha256sum',source]).split()[0]
        subprocess.run(['scp','-F',str(a.control/'ssh.conf'),'pool-a-client:'+source,str(target)],check=True)
        if digest(target)!=wanted:raise ValueError('Transferred evidence changed')
        transfers.append(dict(source=source,**ref(target)));return target
    for name in ('result.json','started.json','optional-tools.json','import/exit.json','import-fsync.json',
                 'full-base-check/exit.json','base-check/result.json','normal-stop.json','cold-stop.json'):
        get(a.route+'/'+name,'route/'+name)
    asset=get(a.asset+'/asset.json','assets/'+arm+'/asset.json')
    sources={name:dict(path=path,sha256=remote(['sha256sum',path]).split()[0]) for name,path in
             [('binary',a.binary),('verifier',a.verifier)]+([('copier',a.copier)] if a.copier else [])}
    if a.binary!='/srv/ax-client/assets/bin/smallfiles':raise ValueError('Pin the actual worker binary path')
    raw=a.out/'route';route=load(raw/'result.json');base=load(raw/'base-check/result.json')
    if not route['passed'] or route['cell_id']!=cell['id'] or route['asset']['sha256']!=digest(asset):
        raise ValueError('Different qualified base')
    if route['full_cold_base_check']['sha256']!=digest(raw/'base-check/result.json'):
        raise ValueError('Original cold readback proof changed')
    if not base['passed'] or base['files']!=1000000 or base['bytes_checked']!=4096000000 or not base['full_namespace'] or not base['modes_owners_links']:
        raise ValueError('Every original file, name, byte, mode, owner and link must be checked')
    tools=load(raw/'optional-tools.json')
    if tools['verifier']!=sources['verifier'] or tools['copier']!=sources.get('copier'):
        raise ValueError('Qualification tools differ from requested tools')
    for name in ('normal-stop.json','cold-stop.json'):
        stopped=load(raw/name)
        if not stopped['passed'] or stopped['exit_code']!=0 or not stopped['mount_absent']:raise ValueError('Base is not normally closed')
    if load(raw/'import/exit.json')['exit_code']!=0 or not load(raw/'import-fsync.json')['passed']:
        raise ValueError('Complete durable import required')
    original_source=get(load(raw/'started.json')['source_proof']['path'],'source/proof.json')
    source=load(original_source)
    if digest(original_source)!=load(raw/'started.json')['source_proof']['sha256'] or not source['passed'] or source['files']!=1000000 or source['bytes_checked']!=4096000000:
        raise ValueError('Original full input proof changed')
    if load(asset)['plan']['sha256']!=digest(a.control/'suite/plan.json'):raise ValueError('Asset prepared under another plan')
    hardware=load(a.shared_qualification/'hardware.json')
    if not hardware['passed'] or hardware['inventory_sha256']!=digest(a.control/'fleet.json'):raise ValueError('Current matching hardware qualification required')
    write_new(a.out/'hardware.json',hardware)
    folder=a.out/arm;folder.mkdir()
    small=dict(binary=sources['binary'],verifier=sources['verifier'],copier=sources.get('copier'))
    write_new(folder/'binding.json',dict(asset=a.asset,route=ref(raw/'result.json'),smallfiles=small))
    proof=dict(passed=True,cell_id=cell['id'],complete_base=ref(raw/'result.json'),source=ref(original_source),
        cold_readback=ref(raw/'base-check/result.json'),capacity=ref(a.capacity_check),asset=ref(asset),
        scope='One million files, complete durable import and original cold namespace/byte/mode/owner/link checks; no performance acceptance')
    write_new(folder/'input.json',proof);write_new(folder/'filesystem.json',proof)
    metrics=load(a.shared_qualification/arm/'metrics.json');metrics['source_pins'].append(ref(raw/'result.json'))
    metrics['notes']=['Eight fixed-work phases, raw operation CSVs, cold full child/parent checks; trace/FUSE/object/disk I/O are separate quantities.']
    write_new(folder/'metrics.json',metrics)
    write_new(folder/'workload.json',dict(schema='experiment-workload-contract-v1',reviewed=True,
        binary_pins=[sources['binary']],input_pins=[ref(folder/'input.json'),ref(original_source),ref(a.control/'suite/plan.json')],
        params=cell['params'],seed=plan['config']['seed'],
        scope='One million-file workspace; unchanged native smallfiles workload, exact eight phases, all operation streams and bytes checked'))
    write_new(a.out/'transfers.json',transfers);print(json.dumps(dict(passed=True,cell_id=cell['id'],qualification=str(a.out))))


if __name__=='__main__':main()
