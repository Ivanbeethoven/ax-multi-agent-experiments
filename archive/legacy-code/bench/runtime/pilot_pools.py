"""Explicit pilot placement. Legacy single-pool documents remain valid."""
import ipaddress
from contextlib import contextmanager,ExitStack
import hashlib
from pathlib import Path
import re
import sqlite3

from .common import load,digest,pinned

ROLES = ('client', 'metadata', 'objects', 'target')
ROOTS = {'client':'/srv/ax-client', 'target':'/srv/ax-target',
         'metadata':'/srv/ax-metadata', 'objects':'/srv/ax-objects-0'}


def pool_of(cell):
    value = cell['pool']
    if not isinstance(value, str) or not re.fullmatch(r'pool-[a-z0-9-]+', value):
        raise ValueError('Explicit cell pool required')
    return value


def alias(pool, role):
    if role not in ROLES:
        raise ValueError('Unknown pilot role: '+role)
    pool_of({'pool':pool})
    return pool+'-'+role


def inventories(document):
    values = document.get('pools', [document])
    if not isinstance(values, list) or not values:
        raise ValueError('Nonempty actual pilot pools required')
    seen = set()
    for value in values:
        pool = pool_of({'pool':value['pool_id']})
        if pool in seen:
            raise ValueError('Duplicate pilot pool')
        seen.add(pool)
        if set(value['roles']) not in ({'client','metadata','objects'}, {'client','metadata','objects','target'}):
            raise ValueError('Actual client, metadata, objects and optional target roles required')
        for role, node in value['roles'].items():
            if not node['instance_id'].startswith('i-') or not node['data_disk_ids'] or any(
                    not disk.startswith('d-') for disk in node['data_disk_ids']):
                raise ValueError('Actual instance and data disk IDs required')
            if not ipaddress.ip_address(node['private_ip']).is_private:
                raise ValueError('Explicit private role address required')
            if not node.get('hostname') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', node['hostname']):
                raise ValueError('Actual role hostname required')
    return values


def pool_inventory(document, pool):
    matches = [value for value in inventories(document) if value['pool_id']==pool]
    if len(matches)!=1:
        raise ValueError('Exactly one actual inventory for cell pool required: '+pool)
    return matches[0]


def resource_ids(inventory):
    result = []
    for role in ROLES:
        if role not in inventory['roles']:
            continue
        node = inventory['roles'][role]
        result += ['host:'+node['instance_id']] + ['disk:'+disk for disk in node['data_disk_ids']]
    if len(result)!=len(set(result)):
        raise ValueError('Distinct physical role resources required within a pool')
    return result


def endpoints(document, pool):
    roles = pool_inventory(document, pool)['roles']
    return {'metadata':'redis://'+roles['metadata']['private_ip']+':6379',
            'objects':'http://'+roles['objects']['private_ip']+':9000'}


def endpoint_binding(plan, cell, inventory_path=None):
    pool=pool_of(cell)
    if inventory_path is None:
        if pool!='pool-a' or len(plan['config']['clusters'])!=1:
            raise ValueError('A new multi-pool asset requires its explicit frozen cloud inventory')
        return {'metadata':'redis://10.20.2.162:6379','objects':'http://10.20.2.163:9000'}
    inventory_path=Path(inventory_path).resolve(strict=True)
    cluster=next(c for c in plan['config']['clusters'] if c['id']==pool)
    if digest(inventory_path)!=cluster['inventory_sha256']:
        raise ValueError('Asset inventory differs from its frozen pool plan')
    document=load(inventory_path)
    return dict(endpoints(document,pool),pool=pool,
                inventory=dict(path=str(inventory_path),sha256=digest(inventory_path)),
                client=pool_inventory(document,pool)['roles']['client'])


def cross_pool_proof(path, plan, fleet_path):
    """Validate the separately measured, pinned equivalence evidence."""
    proof=load(path)
    pools=sorted({pool_of(c) for c in plan['cells']})
    if (proof.get('schema')!='pilot-cross-pool-equivalence-v1' or proof.get('passed') is not True or
            proof.get('plan_sha256')!=plan['plan_sha256'] or
            proof.get('inventory_sha256')!=digest(fleet_path) or proof.get('pools')!=pools or
            not proof.get('checks') or not all(v is True for v in proof['checks'].values()) or
            not proof.get('source_pins')):
        raise ValueError('Actual plan-bound cross-pool hardware and calibration proof required')
    for pin in proof['source_pins']:pinned(pin)
    return proof


def fleet_node(fleet, pool, role):
    name = alias(pool, role)
    return next(node for node in fleet['nodes'] if node['id']==name)


def cell_resources(plan, cell):
    return cell.get('resource_ids', next(p['resource_ids'] for p in plan['config']['clusters'] if p['id']==pool_of(cell)))


def active_conflicts(control, cell, plan=None):
    """Read-only conflicts; leases in independent physical pools do not block."""
    control = Path(control)
    plan = plan or load(control/'suite/plan.json' if (control/'suite/plan.json').exists() else control/'plan.json')
    resources = cell_resources(plan, cell)
    with sqlite3.connect('file:'+str(control/'state.sqlite')+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT * FROM cell_leases').fetchall()
        result = []
        for lease in rows:
            occupied = {r[0] for r in db.execute('SELECT id FROM cell_resources WHERE cell_id=?', (lease['cell_id'],))}
            if lease['pool']==pool_of(cell) or occupied.intersection(resources):
                result.append(dict(lease))
        return result


@contextmanager
def resource_guard(control, cell, plan):
    """Keep the six calibration trials and formal cell exclusive per resource.

    These OS locks supplement SQLite formal leases. They never release a lease
    or make a previously started run eligible for an automatic retry.
    """
    import fcntl
    directory=Path(control)/'runner-locks';directory.mkdir(exist_ok=True)
    resources=['pool:'+pool_of(cell),*cell_resources(plan,cell)]
    with ExitStack() as stack:
        for resource in sorted(set(resources)):
            name=hashlib.sha256(resource.encode()).hexdigest()+'.lock'
            stream=stack.enter_context((directory/name).open('a+'))
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('Another preparation or cell owns physical resource: '+resource)
        yield
