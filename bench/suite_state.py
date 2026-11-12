"""Atomic reservations on ONE controller's local disk; no expiring work leases.

SQLite coordinates AI operators, not filesystem data or benchmark timing. Do not
place this database on the filesystem under test or on a network share. A lost
controller leaves a claimed/started cell reserved; it never becomes pending.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3

from .c4_dispatch import retired_flat_fork, require_allowed
from .experiment_io import digest, load
from .suite_plan import check_plan, fingerprint, parallel


def utc():
    return datetime.now(timezone.utc).isoformat()


def evidence(ref):
    p = Path(ref['path'])
    if not p.is_absolute() or p.is_symlink() or digest(p) != ref['sha256']:
        raise ValueError('changed or non-absolute evidence')
    return load(p)


def reference(path):
    p = Path(path).resolve(strict=True)
    return {'path':str(p), 'sha256':digest(p)}


def predicates(value, expected):
    if not isinstance(expected, dict) or not expected:
        raise ValueError('explicit evidence predicates required')
    for key, wanted in expected.items():
        found = value
        for part in key.split('.'):
            found = found[part]
        if type(found) is not type(wanted) or found != wanted:
            raise ValueError('evidence predicate failed: '+key)


def initialize(path, plan):
    check_plan(plan)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600); os.close(fd)
    with sqlite3.connect(p) as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE campaign(plan TEXT NOT NULL);
        CREATE TABLE blocks(id TEXT PRIMARY KEY, pool TEXT, status TEXT, owner TEXT, token TEXT);
        CREATE TABLE cells(id TEXT PRIMARY KEY, block_id TEXT, position INTEGER, status TEXT, result TEXT);
        CREATE TABLE resources(id TEXT PRIMARY KEY, block_id TEXT);
        CREATE TABLE cell_leases(cell_id TEXT PRIMARY KEY, pool TEXT UNIQUE, owner TEXT, token TEXT);
        CREATE TABLE cell_resources(id TEXT PRIMARY KEY, cell_id TEXT);
        CREATE TABLE stages(cell_id TEXT, name TEXT, status TEXT, spec_sha TEXT, output TEXT,
                            evidence TEXT, PRIMARY KEY(cell_id,name));
        CREATE TABLE qualifications(cell_id TEXT PRIMARY KEY, receipt TEXT);
        CREATE TABLE assets(id TEXT PRIMARY KEY, receipt TEXT);
        CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, utc TEXT, kind TEXT, payload TEXT);
        ''')
        db.execute('INSERT INTO campaign VALUES (?)', (json.dumps(plan),))
        for b in plan['blocks']:
            db.execute('INSERT INTO blocks VALUES (?,?,?,NULL,NULL)', (b['id'],b['pool'],'pending'))
            for order, cell in enumerate(b['cells']):
                db.execute('INSERT INTO cells VALUES (?,?,?,?,NULL)', (cell,b['id'],order,'pending'))
        event(db, 'initialized', {'plan_sha256':plan['plan_sha256']})


@contextmanager
def transaction(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file(): raise ValueError('local regular controller database required')
    p = p.resolve(strict=True)
    db = sqlite3.connect(p, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback(); raise
    finally: db.close()


def event(db, kind, value):
    db.execute('INSERT INTO events(utc,kind,payload) VALUES (?,?,?)', (utc(),kind,json.dumps(value)))


def plan_of(db):
    return check_plan(json.loads(db.execute('SELECT plan FROM campaign').fetchone()[0]))


def qualify(path, ref):
    receipt = evidence(ref)
    with transaction(path) as db:
        plan = plan_of(db)
        cell = next(c for c in plan['cells'] if c['id'] == receipt['cell_id'])
        if receipt.get('schema') != 'suite-qualification-v1' or receipt.get('plan_sha256') != plan['plan_sha256']:
            raise ValueError('qualification belongs to a different frozen plan')
        checks = receipt.get('checks', [])
        if len({c['gate'] for c in checks}) != len(checks) or set(c['gate'] for c in checks) != set(cell['required_gates']):
            raise ValueError('all and only the required qualification gates must be evidenced')
        for c in checks: predicates(evidence(c['evidence']), c['equals'])
        db.execute('INSERT INTO qualifications VALUES (?,?)', (cell['id'],json.dumps(ref)))
        event(db, 'qualified', {'cell':cell['id'],'evidence':ref})


def register_asset(path, ref):
    value = evidence(ref)
    with transaction(path) as db:
        plan = plan_of(db)
        asset = next(a for a in plan['assets'] if a['id'] == value['asset_id'])
        if value.get('schema') != 'suite-asset-v1' or value.get('plan_sha256') != plan['plan_sha256']:
            raise ValueError('asset provenance mismatch')
        if set(value['stages']) != set(asset['required_stages']):
            raise ValueError('source/import/base validation/cache reset receipts required')
        for stage in value['stages'].values():
            predicates(evidence(stage['evidence']),stage['equals'])
        if not value.get('native_binding') or not value.get('timings_seconds'):
            raise ValueError('preserve actual base identity and preparation costs')
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in value['timings_seconds'].values()):
            raise ValueError('finite nonnegative actual preparation intervals required')
        db.execute('INSERT INTO assets VALUES (?,?)', (asset['id'],json.dumps(ref)))
        event(db, 'asset-registered', {'asset':asset['id'],'evidence':ref})


def claim(path, pool, owner):
    if not owner or not isinstance(owner, str): raise ValueError('operator identity required')
    with transaction(path) as db:
        plan = plan_of(db)
        if not plan['provenance_bound']:
            raise ValueError('bind build, input and actual inventory digests before initializing an executable campaign')
        if parallel(plan['config']):return claim_cell(db,plan,pool,owner)
        candidates = [b for b in plan['blocks'] if b['pool'] == pool]
        if not candidates: raise ValueError('unknown pool')
        occupied = db.execute("SELECT * FROM blocks WHERE pool=? AND status='claimed'", (pool,)).fetchone()
        if occupied:
            if occupied['owner'] != owner: raise ValueError('pool reserved by another operator; no automatic lease expiry')
            candidates = [b for b in candidates if b['id'] == occupied['id']]
        cells = {c['id']:c for c in plan['cells']}
        retirement_blocked = False
        for block in candidates:
            state = db.execute('SELECT * FROM blocks WHERE id=?',(block['id'],)).fetchone()
            if state['status'] == 'done': continue
            pending = db.execute("SELECT * FROM cells WHERE block_id=? AND status NOT IN ('complete','failed') ORDER BY position",(block['id'],)).fetchall()
            if not pending: continue
            cell_state = pending[0]
            cell = cells[cell_state['id']]
            if cell_state['status'] == 'claimed':
                return {'cell':cell,'owner':owner,'token':state['token'], 'already_claimed':True,
                        'instruction':'inspect original stage receipts; never re-execute a started stage'}
            # The original pair cannot start if any required arm is retired.
            if any(retired_flat_fork(cells[row['id']]) for row in pending):
                retirement_blocked = True
                continue
            # Qualify the whole block before consuming any comparison arm.
            if any(not db.execute('SELECT 1 FROM qualifications WHERE cell_id=?',(c,)).fetchone() for c in block['cells']):
                continue
            if any(not db.execute('SELECT 1 FROM assets WHERE id=?',(c['asset_id'],)).fetchone() for c in plan['cells'] if c['paired_block']==block['id']):
                continue
            if not occupied:
                if any(db.execute('SELECT 1 FROM resources WHERE id=?',(res,)).fetchone() for res in block['resource_ids']): continue
                token = secrets.token_hex(24)
                db.execute("UPDATE blocks SET status='claimed',owner=?,token=? WHERE id=?", (owner,token,block['id']))
                for res in block['resource_ids']: db.execute('INSERT INTO resources VALUES (?,?)',(res,block['id']))
            else: token = occupied['token']
            # Rehash referenced evidence at admission, not only registration.
            for cid in block['cells']:
                q = evidence(json.loads(db.execute('SELECT receipt FROM qualifications WHERE cell_id=?',(cid,)).fetchone()[0]))
                for item in q['checks']: predicates(evidence(item['evidence']),item['equals'])
            a = evidence(json.loads(db.execute('SELECT receipt FROM assets WHERE id=?',(cell['asset_id'],)).fetchone()[0]))
            for stage in a['stages'].values(): predicates(evidence(stage['evidence']),stage['equals'])
            db.execute("UPDATE cells SET status='claimed' WHERE id=?",(cell['id'],))
            event(db,'claimed',{'cell':cell['id'],'owner':owner,'pool':pool})
            return {'cell':cell,'owner':owner,'token':token,'already_claimed':False}
        reason = ('a pending retired C4 BrewFS Flat full-copy arm prevents the frozen pair from running'
                  if retirement_blocked else 'no qualified, prepared, unreserved complete block fits this pool')
        return {'cell':None,'reason':reason}


def claim_cell(db, plan, pool, owner):
    """A paired block groups results; only the admitted cell owns resources.

    Called inside the same BEGIN IMMEDIATE transaction as legacy claims. There
    is no remote/expiring lease and no wait for another baseline's qualification.
    """
    candidates=[c for c in plan['cells'] if c['pool']==pool]
    if not candidates:raise ValueError('unknown or unassigned pool')
    occupied=db.execute('SELECT * FROM cell_leases WHERE pool=?',(pool,)).fetchone()
    if occupied:
        if occupied['owner']!=owner:raise ValueError('pool reserved by another operator; no automatic lease expiry')
        cell=next(c for c in candidates if c['id']==occupied['cell_id'])
        return {'cell':cell,'owner':owner,'token':occupied['token'],'already_claimed':True,
                'instruction':'inspect original stage receipts; never re-execute a started stage'}
    retirement_blocked = False
    for cell in candidates:
        row=db.execute('SELECT status FROM cells WHERE id=?',(cell['id'],)).fetchone()
        if row[0]!='pending':continue
        if retired_flat_fork(cell):
            retirement_blocked = True
            continue
        qref=db.execute('SELECT receipt FROM qualifications WHERE cell_id=?',(cell['id'],)).fetchone()
        aref=db.execute('SELECT receipt FROM assets WHERE id=?',(cell['asset_id'],)).fetchone()
        if not qref or not aref:continue
        if any(db.execute('SELECT 1 FROM cell_resources WHERE id=?',(res,)).fetchone() for res in cell['resource_ids']):continue
        # Rehash before reserving; a rejected proof leaves no pool/resource lock.
        q=evidence(json.loads(qref[0]))
        for item in q['checks']:predicates(evidence(item['evidence']),item['equals'])
        a=evidence(json.loads(aref[0]))
        for stage in a['stages'].values():predicates(evidence(stage['evidence']),stage['equals'])
        token=secrets.token_hex(24)
        db.execute('INSERT INTO cell_leases VALUES (?,?,?,?)',(cell['id'],pool,owner,token))
        for res in cell['resource_ids']:db.execute('INSERT INTO cell_resources VALUES (?,?)',(res,cell['id']))
        db.execute("UPDATE cells SET status='claimed' WHERE id=?",(cell['id'],))
        db.execute("UPDATE blocks SET status='claimed' WHERE id=?",(cell['paired_block'],))
        event(db,'claimed',{'cell':cell['id'],'owner':owner,'pool':pool})
        return {'cell':cell,'owner':owner,'token':token,'already_claimed':False}
    reason = ('retired C4 BrewFS Flat full-copy cell is not eligible for a new lease' if retirement_blocked
              else 'no qualified, prepared, unreserved cell assigned to this pool')
    return {'cell':None,'reason':reason}


def admitted(db, lease):
    cell = lease['cell']
    plan=plan_of(db)
    if parallel(plan['config']):
        row=db.execute('SELECT * FROM cell_leases WHERE cell_id=?',(cell['id'],)).fetchone()
        active=row and row['pool']==cell['pool']
    else:
        row=db.execute('SELECT * FROM blocks WHERE id=?',(cell['paired_block'],)).fetchone()
        active=row and row['status']=='claimed'
    if not active or row['token']!=lease['token'] or row['owner']!=lease['owner']:
        raise ValueError('reservation identity differs')
    current = next(c for c in plan['cells'] if c['id']==cell['id'])
    if current != cell or db.execute('SELECT status FROM cells WHERE id=?',(cell['id'],)).fetchone()[0]!='claimed':
        raise ValueError('cell no longer reserved or changed')
    return cell


def run_stage(path, lease, stage, spec_ref, output):
    # Reuse the existing exact-one-step executor. Persist intent BEFORE spawn;
    # a crash between intent and spawn also remains unknown, not replayable.
    from .runtime.common import prepare
    from .experiment_step import execute
    command, _ = prepare(spec_ref['path'],spec_ref['sha256'])
    output = str(Path(output).absolute())
    with transaction(path) as db:
        cell = admitted(db,lease)
        require_allowed(cell, stage)
        if stage not in cell['stages'] or command['cell_id']!=cell['id'] or command['stage']!=stage:
            raise ValueError('command is not the reserved cell/stage')
        if Path(output).exists(): raise ValueError('new stage output required')
        for prior in cell['stages'][:cell['stages'].index(stage)]:
            row = db.execute('SELECT status FROM stages WHERE cell_id=? AND name=?',(cell['id'],prior)).fetchone()
            if not row or row[0]!='complete': raise ValueError('finish prior stage first: '+prior)
        db.execute('INSERT INTO stages VALUES (?,?,?,?,?,NULL)',(cell['id'],stage,'started',spec_ref['sha256'],output))
        event(db,'stage-intent',{'cell':cell['id'],'stage':stage,'spec':spec_ref,'output':output})
    code = execute(spec_ref['path'],spec_ref['sha256'],output)
    ref = reference(Path(output)/'finished.json')
    with transaction(path) as db:
        admitted(db,lease)
        db.execute('UPDATE stages SET status=?,evidence=? WHERE cell_id=? AND name=?',
                   ('complete' if code==0 else 'failed',json.dumps(ref),cell['id'],stage))
        event(db,'stage-terminal',{'cell':cell['id'],'stage':stage,'exit_code':code,'evidence':ref})
    return code


def finish(path, lease, ref):
    """Release only after retained evidence AND actual process/mount closure.

    Failed observation/correctness remains a failed sample; it is independent
    of evidence retention and safe resource release. No host deletion occurs.
    """
    result = evidence(ref)
    from .experiment_step import verify
    if result.get('schema') != 'suite-cell-close-v1' or result.get('outcome') not in ('complete','failed'):
        raise ValueError('explicit complete/failed closure required')
    required = {'processes':{'all_exited':True},'mounts':{'all_unmounted':True},
                'debt':{'pending_upload_bytes':0,'pending_upload_operations':0}}
    for key in ('processes','mounts','debt'):
        check = result['closure'][key]
        value=evidence(check['evidence'])
        if value.get('cell_id')!=result['cell_id']:raise ValueError('closure evidence belongs to another cell')
        predicates(value,required[key])
        predicates(value,check['equals'])
    retention = result['retention']
    verify(retention['root'],retention['manifest']['path'],retention['manifest']['sha256'])
    from .archive_check import verify as verify_archive
    if not result.get('archives'):raise ValueError('complete raw archives required before releasing reservation')
    for archive in result['archives']:
        checked=verify_archive(archive['path'],archive['sha256'])
        if checked['cell_id']!=result['cell_id']:raise ValueError('archive belongs to another cell')
    with transaction(path) as db:
        cell = admitted(db,lease)
        if result['cell_id'] != cell['id'] or result['plan_sha256']!=plan_of(db)['plan_sha256']:
            raise ValueError('closure identity mismatch')
        states = {r['name']:r['status'] for r in db.execute('SELECT * FROM stages WHERE cell_id=?',(cell['id'],))}
        if result['outcome']=='complete' and any(states.get(s)!='complete' for s in cell['stages']):
            raise ValueError('complete requires every original stage; interrupted cells close as failed')
        if result['outcome']=='failed' and not result.get('failure_reason'):
            raise ValueError('retain failure/interruption explanation')
        db.execute('UPDATE cells SET status=?,result=? WHERE id=?',(result['outcome'],json.dumps(ref),cell['id']))
        if parallel(plan_of(db)['config']):
            db.execute('DELETE FROM cell_resources WHERE cell_id=?',(cell['id'],))
            db.execute('DELETE FROM cell_leases WHERE cell_id=?',(cell['id'],))
        if not db.execute("SELECT 1 FROM cells WHERE block_id=? AND status NOT IN ('complete','failed')",(cell['paired_block'],)).fetchone():
            db.execute("UPDATE blocks SET status='done' WHERE id=?",(cell['paired_block'],))
            db.execute('DELETE FROM resources WHERE block_id=?',(cell['paired_block'],))
        event(db,'closed',{'cell':cell['id'],'outcome':result['outcome'],'evidence':ref})


def status(path):
    with transaction(path) as db:
        plan = plan_of(db)
        split=parallel(plan['config'])
        result={'plan_sha256':plan['plan_sha256'], 'campaign_id':plan['campaign_id'],
                'counts':plan['counts'], 'assets_ready':db.execute('SELECT count(*) FROM assets').fetchone()[0],
                'qualified_cells':db.execute('SELECT count(*) FROM qualifications').fetchone()[0],
                'blocks':[dict(r) for r in db.execute('SELECT id,pool,status,owner FROM blocks')],
                'cells':[dict(r) for r in db.execute('SELECT * FROM cells')],
                'stages':[dict(r) for r in db.execute('SELECT * FROM stages')],
                'reserved_resources':[dict(r) for r in db.execute('SELECT * FROM '+('cell_resources' if split else 'resources'))],
                'events':[dict(r) for r in db.execute('SELECT * FROM events ORDER BY sequence')],
                'automatic_retry':False, 'lease_expiry':None}
        if split:
            result['placement_mode']='baseline-parallel'
            result['active_leases']=[dict(r) for r in db.execute('SELECT cell_id,pool,owner FROM cell_leases ORDER BY pool')]
        return result
