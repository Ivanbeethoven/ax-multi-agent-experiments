#!/usr/bin/env python3
"""Bind existing private ECS hosts and probe the pinned measurement runtime."""
import argparse
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.fleet import probe,reference
from bench.ssh_inventory import template,validate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    cloud=json.loads(a.inventory.read_text())
    roles=cloud['roles']
    if cloud['pool_id']!='pool-a' or set(roles) not in ({'client','metadata','objects'},{'client','metadata','objects','target'}):
        raise ValueError('Actual pilot roles required')
    executors=['pool-a-'+r for r in ('client','target') if r in roles]
    inv=template(executors,['pool-a-metadata','pool-a-objects'],[],cluster='pool-a')
    inv['campaign_id']='ax-pilot-20261111'
    roots={'client':'/srv/ax-client','target':'/srv/ax-target','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}
    for n in inv['nodes']:
        role=n['id'].removeprefix('pool-a-');c=roles[role];root=roots[role]
        n.update(kind='host',environment_id=c['instance_id'],host_id=c['instance_id'],host_observer_node=n['id'],
            host_key_verified=True,owned_by_campaign=True,repo=root+'/code-01',python='/usr/bin/python3',work_root=root,
            data_paths=[dict(path=root,min_free_bytes=10*1024**3,min_free_inodes=1200000)],
            data_disk_ids=c['data_disk_ids'],private_ip=c['private_ip'])
        if role in ('client','target'):n['slots'][0]['cgroup']='/sys/fs/cgroup/ax-slot-1'
    validate(inv)
    fleet=a.out/'fleet.json';fleet.write_text(json.dumps(inv,indent=2)+'\n')
    config=a.out/'ssh.conf'
    text='Host '+' '.join(n['ssh_alias'] for n in inv['nodes'])+'\n'
    text+='    User root\n    IdentityFile /srv/ax-pilot-control/ssh/id_ed25519\n    IdentitiesOnly yes\n    BatchMode yes\n    StrictHostKeyChecking yes\n    UserKnownHostsFile /srv/ax-pilot-control/ssh/known_hosts\n    GlobalKnownHostsFile none\n    ForwardAgent no\n    PermitLocalCommand no\n'
    for n in inv['nodes']:text+='Host '+n['ssh_alias']+'\n    HostName '+n['private_ip']+'\n'
    config.write_text(text);config.chmod(0o600)
    ssh=dict(executable=reference('/usr/bin/ssh'),config=reference(config),known_hosts=reference('/srv/ax-pilot-control/ssh/known_hosts'))
    results=[]
    for n in inv['nodes']:
        results.append(probe(fleet,n['id'],None,ssh,a.out/'probes'/n['id'],REPO,host_observer=True))
        if n['role']=='executor':
            results.append(probe(fleet,n['id'],n['slots'][0]['id'],ssh,a.out/'probes'/(n['id']+'-slot'),REPO))
    receipt=dict(passed=all(r['passed'] for r in results),inventory_sha256=reference(fleet)['sha256'],
        probes=[reference(p) for p in sorted((a.out/'probes').glob('*/probe.json'))],
        scope='Actual SSH, runtime bytes, FUSE capability, data capacity and cgroup limits; no performance acceptance')
    (a.out/'allocation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))
    if not receipt['passed']:raise ValueError('Fleet probe failed; inspect originals')


if __name__=='__main__':
    main()
