#!/usr/bin/env python3
"""Hold the coordinator's existing B locks while an isolated scratch run is active."""
import fcntl
import hashlib
import json
import sqlite3
import sys
from contextlib import ExitStack
from pathlib import Path

ROOT = Path('/srv/ax-pilot-control')
CANONICAL = ROOT/'parallel-control-01'


def topology():
    inventory = json.loads((CANONICAL/'provenance/inventory.json').read_bytes())
    roles = next(x['roles'] for x in inventory['pools'] if x['pool_id'] == 'pool-b')
    resources = {'pool:pool-b'} | {
        key for role in roles.values() for key in
        ['host:'+role['instance_id'], *('disk:'+d for d in role['data_disk_ids'])]
    }
    dbs = sorted(ROOT.glob('*/state.sqlite'))
    locks = sorted({(x.parent/'runner-locks').resolve(strict=True) for x in dbs})
    if len(dbs) < 5 or len(locks) < 3 or any(not d.is_relative_to(ROOT) for d in locks):
        raise ValueError('Missing original coordinator controls or locks')
    return roles, resources, dbs, locks


def conflicts(dbs, resources):
    result = []
    for db in dbs:
        with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as sql:
            for cell, pool in sql.execute('SELECT cell_id,pool FROM cell_leases'):
                ids = {r[0] for r in sql.execute('SELECT id FROM cell_resources WHERE cell_id=?',(cell,))}
                if pool == 'pool-b' or ids & resources:
                    result.append([db.parent.name,cell,pool])
    return result


def main():
    roles, ids, dbs, locks = topology()
    with ExitStack() as stack:
        for lock in locks:
            for key in sorted(ids):
                name = hashlib.sha256(key.encode()).hexdigest()+'.lock'
                stream = stack.enter_context((lock/name).open('a+'))
                fcntl.flock(stream, fcntl.LOCK_EX|fcntl.LOCK_NB)
        if topology() != (roles,ids,dbs,locks) or conflicts(dbs, ids):
            raise ValueError('B resource lease or coordinator topology conflict')
        print(json.dumps({'passed':True,'roles':roles,'ledgers':list(map(str,dbs)),
                          'lock_directories':list(map(str,locks))}),flush=True)
        if sys.stdin.readline() != 'release\n':
            raise ValueError('Controller disconnected before explicit release')
        if topology() != (roles,ids,dbs,locks) or conflicts(dbs, ids):
            raise ValueError('B resource topology or lease changed while locked')
        print('released',flush=True)


if __name__ == '__main__':
    main()
