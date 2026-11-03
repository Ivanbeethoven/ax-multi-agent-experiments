"""Topology of existing SSH environments, independent of cloud and machine size."""
import re
from experiment_io import linux_path


def name(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', value)


def template(executors, services, hosts, cluster='C1'):
    aliases = executors + services + hosts
    if not name(cluster) or not executors:
        raise ValueError('cluster and at least one executor required')
    if len(set(aliases)) != len(aliases) or not all(name(a) for a in aliases):
        raise ValueError('unique simple SSH aliases required')
    nodes = []
    for role, members in [('executor', executors), ('service', services), ('observer', hosts)]:
        for alias in members:
            nodes.append({'id': alias, 'cluster_id': cluster, 'role': role,
                'kind': 'container' if role == 'executor' else 'host',
                'ssh_alias': alias, 'environment_id': None, 'host_id': None,
                'host_observer_node': alias if role != 'executor' else None,
                'host_key_verified': False, 'owned_by_campaign': False,
                'repo': '/opt/ax-experiments', 'python': '/usr/bin/python3',
                'work_root': '/srv/ax', 'data_paths': [],
                'requirements': {'max_agents': 1 if role == 'executor' else 0},
                'slots': [{'id': alias, 'container': None, 'cgroup': None}] if role == 'executor' else []})
    return {'schema': 'experiment-inventory-v3', 'provider': 'ssh',
            'campaign_id': None, 'execution_enabled': False, 'nodes': nodes,
            'scope': 'existing SSH environments; host_id groups shared capacity, not a physical-host proof'}


def slot_key(slot):
    return slot.get('container'), slot.get('cgroup')


def validate(value):
    if value.get('schema') != 'experiment-inventory-v3' or value.get('provider') != 'ssh' or value.get('execution_enabled') is not False:
        raise ValueError('SSH inventory must describe existing environments with execution disabled')
    if not name(value.get('campaign_id')):
        raise ValueError('fill campaign_id')
    nodes = value.get('nodes', [])
    if not nodes:
        raise ValueError('empty SSH fleet')
    for key in ('id', 'ssh_alias', 'environment_id'):
        vals = [n.get(key) for n in nodes]
        if any(not isinstance(v, str) or not v.strip() for v in vals) or len(set(vals)) != len(vals):
            raise ValueError('fill unique ' + key)
    by_id = {n['id']: n for n in nodes}
    for n in nodes:
        if not name(n['id']) or not name(n['ssh_alias']):
            raise ValueError('simple node IDs and SSH aliases required')
        if not name(n.get('cluster_id')):
            raise ValueError('explicit cluster required')
        if n.get('kind') not in ('host', 'container') or not n.get('host_id'):
            raise ValueError('record environment kind and shared host identity')
        if n.get('role') not in ('executor', 'service', 'observer'):
            raise ValueError('executor/service/observer role required')
        if n.get('host_key_verified') is not True or type(n.get('owned_by_campaign')) is not bool:
            raise ValueError('record verified host key and ownership')
        for key in ('repo', 'python', 'work_root'):
            linux_path(n[key])
        for d in n['data_paths']:
            linux_path(d['path'])
            if any(type(d.get(k)) is not int or d[k] <= 0 for k in ('min_free_bytes', 'min_free_inodes')):
                raise ValueError('positive data capacity/inode requirements required')
        if n['role'] != 'observer' and not n['data_paths']:
            raise ValueError('executor/service data paths required')
        slots = n['slots']; cap = n['requirements']['max_agents']
        if type(cap) is not int or cap != len(slots) or (n['role'] == 'executor') != (cap > 0):
            raise ValueError('executor capacity must match explicit slots')
        if len({s['id'] for s in slots}) != len(slots) or not all(name(s['id']) for s in slots):
            raise ValueError('unique slot IDs required')
        if len({slot_key(s) for s in slots}) != len(slots):
            raise ValueError('Agent isolation reused')
        for s in slots:
            container, group = slot_key(s)
            if container and group:
                raise ValueError('choose Docker exec or a delegated cgroup')
            if container and not re.fullmatch(r'ax-[a-z0-9-]+', container):
                raise ValueError('ax container name required')
            if group and not str(linux_path(group)).startswith('/sys/fs/cgroup/ax-'):
                raise ValueError('dedicated ax cgroup required')
            if n['kind'] == 'container' and (container or group):
                raise ValueError('SSH container uses its existing isolation directly')
            if not container and not group and len(slots) > 1:
                raise ValueError('direct SSH environment holds one Agent')
        observer = n.get('host_observer_node')
        if observer is not None:
            host = by_id.get(observer, {})
            if host.get('kind') != 'host' or host.get('host_id') != n['host_id'] or host.get('cluster_id') != n['cluster_id']:
                raise ValueError('host observer must belong to the same host and cluster')
    return {'passed': True, 'provider': 'ssh', 'nodes': len(nodes),
            'agent_slots': sum(len(n['slots']) for n in nodes),
            'host_groups': len({n['host_id'] for n in nodes}),
            'host_telemetry_complete': all(n.get('host_observer_node') for n in nodes),
            'execution_enabled': False, 'scope': 'static topology; native resource and workload qualification follows'}


def validate_workers(value, workers):
    """Use exact approved slots; aliases/ports alone do not establish isolation."""
    validate(value)
    nodes = {n['id']: n for n in value['nodes']}
    used = set()
    for w in workers:
        n = nodes[w['node_id']]; peer = w['peer']
        slot = next((s for s in n['slots'] if s['id'] == w.get('slot_id')), None)
        if slot is None or slot_key(slot) != slot_key(peer):
            raise ValueError('worker must use an allocated SSH slot')
        if peer.get('transport') != 'ssh' or peer.get('alias') != n['ssh_alias']:
            raise ValueError('worker SSH identity differs')
        if peer.get('repo') != n['repo'] or peer.get('python') != n['python']:
            raise ValueError('worker runtime path differs')
        key = n['environment_id'], slot_key(slot)
        if key in used:
            raise ValueError('Agent isolation reused')
        used.add(key)
    involved = {nodes[w['node_id']]['cluster_id'] for w in workers}
    if any(not n.get('host_observer_node') for n in nodes.values() if n['cluster_id'] in involved):
        raise ValueError('host telemetry mapping required for measured SSH cohorts')
