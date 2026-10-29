#!/usr/bin/env python3
"""Generate an unfilled topology or validate a filled one. No SSH/cloud calls."""
import argparse
import ipaddress
import re
from experiment_io import load, linux_path, write_new, digest

POOLS = {'C1-G': ('C1', 2), 'C1-A': ('C1', 2), 'C2-P': ('C2', 1),
         'C3-D': ('C3', 1), 'C4-F': ('C4', 1), 'C4-M': ('C4', 3)}


def template(provider, mode):
    ids = [f'n{i:02}' for i in range(1, 10 if mode == 'parallel' else 4)]
    pools = {'C1-G': ids[:2], 'C1-A': ids[2:4], 'C2-P': ids[4:5],
             'C3-D': ids[5:6], 'C4-F': ids[8:9], 'C4-M': ids[6:9]} if mode == 'parallel' else {
             k: ids[:v[1]] for k, v in POOLS.items()}
    return {'schema': 'experiment-inventory-v1', 'provider': provider, 'mode': mode,
            'campaign_id': None, 'execution_enabled': False,
            'nodes': [{'id': n, 'ssh_alias': None, 'private_ip': None, 'host_key_verified': False,
                       'hardware_class': None, 'resource_id': None,
                       'work_root': None, 'data_mounts': [], 'min_free_bytes_per_disk': 200 * 1024**3,
                       'min_free_inodes_per_disk': 1200000} for n in ids],
            'pools': pools, 'migration_roles_in_order': ['source_executor', 'target_executor', 'service_anchor']}


def validate(value):
    if value.get('schema') != 'experiment-inventory-v1' or value.get('provider') not in ('aliyun', 'provided'):
        raise ValueError('unknown inventory schema/provider')
    if value.get('mode') not in ('parallel', 'sequential'):
        raise ValueError('mode must be parallel or sequential')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,63}', value.get('campaign_id') or ''):
        raise ValueError('fill campaign_id')
    nodes = value['nodes']; by_id = {n['id']: n for n in nodes}
    if len(nodes) != len(by_id) or not nodes:
        raise ValueError('duplicate/empty node list')
    for key in ('ssh_alias', 'private_ip', 'resource_id'):
        vals = [n.get(key) for n in nodes]
        if any(not isinstance(v, str) or not v.strip() for v in vals) or len(set(vals)) != len(vals):
            raise ValueError('fill unique ' + key)
    for n in nodes:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', n['ssh_alias']):
            raise ValueError('use a dedicated SSH config alias')
        if not ipaddress.ip_address(n['private_ip']).is_private:
            raise ValueError('business traffic must use private addresses')
        if n.get('host_key_verified') is not True or not n.get('hardware_class'):
            raise ValueError('verify host key and hardware class')
        paths = [linux_path(p) for p in [n['work_root'], *n['data_mounts']]]
        if len(paths) not in (2, 5) or len(set(paths)) != len(paths):
            raise ValueError('one/four distinct data mounts and separate work root required')
        for i, p in enumerate(paths):
            if any(p in q.parents or q in p.parents for q in paths[i+1:]):
                raise ValueError('work root and data mounts must not overlap')
        for key in ('min_free_bytes_per_disk', 'min_free_inodes_per_disk'):
            if type(n.get(key)) is not int or n[key] <= 0:
                raise ValueError('positive capacity requirement required')
    if set(value['pools']) != set(POOLS):
        raise ValueError('all six experiment pools must be allocated')
    owners = {}
    for pool, (owner, count) in POOLS.items():
        members = value['pools'][pool]
        if len(members) != count or len(set(members)) != count or set(members)-by_id.keys():
            raise ValueError('wrong members for ' + pool)
        backend_nodes = members[2:] if pool == 'C4-M' else members
        if any(len(by_id[m]['data_mounts']) != 4 for m in backend_nodes):
            raise ValueError('RustFS hosts require four disks: ' + pool)
        if len({by_id[m]['hardware_class'] for m in members}) != 1:
            raise ValueError('match hardware within ' + pool)
        for m in members:
            owners.setdefault(m, set()).add(owner)
    if value['mode'] == 'parallel':
        if any(len(v) > 1 for v in owners.values()) or set(value['pools']['C1-G']) & set(value['pools']['C1-A']):
            raise ValueError('parallel resource pools overlap')
    if set(owners) != set(by_id):
        raise ValueError('unallocated nodes; keep the exact inventory minimal')
    return {'passed': True, 'nodes': len(nodes), 'provider': value['provider'], 'mode': value['mode'],
            'scope': 'static allocation only; host preflight, runtime limits and AI readiness still required',
            'execution_enabled': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    t = sub.add_parser('template'); t.add_argument('--provider', choices=['aliyun', 'provided'], required=True)
    t.add_argument('--mode', choices=['parallel', 'sequential'], default='parallel')
    t.add_argument('--output', required=True)
    v = sub.add_parser('check'); v.add_argument('--inventory', required=True); v.add_argument('--output', required=True)
    a = p.parse_args()
    if a.action == 'template':
        write_new(a.output, template(a.provider, a.mode))
    else:
        result = validate(load(a.inventory)); result['inventory_sha256'] = digest(a.inventory)
        write_new(a.output, result)
