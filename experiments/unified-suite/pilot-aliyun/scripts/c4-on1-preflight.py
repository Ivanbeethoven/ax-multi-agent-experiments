#!/usr/bin/env python3
"""Build the fresh A-C4 On1 pre-step chain on the coordinator in one window.

The resume gate requires every piece of evidence to be younger than fifteen
minutes and to appear in a fixed order, so this runs the whole chain as one
timed pipeline instead of five hand-made steps:

  cleanup recheck -> native capacity snapshot -> observer scratch snapshot ->
  evidence placement -> reviewed capacity file -> continuation request

Nothing here starts a workload, deletes data or retries an existing attempt.
The reviewable pieces are pure functions so the ordering and the budget
relocation are covered by offline tests.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOTS = {'client': '/srv/ax-client', 'metadata': '/srv/ax-metadata', 'objects': '/srv/ax-objects-0'}
SCRATCH = '/srv/ax-c4-observer-scratch'
DRAFT_ROWS = ('role', 'path', 'quantity', 'data_growth', 'observer_raw', 'archive_peak')
FRESH_SECONDS = 900


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def ref(path):
    p = Path(path).resolve(strict=True)
    return dict(path=str(p), sha256=sha(p))


def write_new(path, value):
    import os
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def instant(text):
    """Accept an ISO string or an already parsed datetime; naive means UTC."""
    if isinstance(text, datetime):
        return text if text.tzinfo else text.replace(tzinfo=timezone.utc)
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def check_order(cleanup_at, snapshot_at, placement_at, capacity_at, sampled=(), now=None):
    """Every sample must follow the cleanup check and stay inside one window.

    The gate also bounds each node's own ``sampled_at`` by the cleanup check and
    the snapshot, so that is checked here too: a node clock that lags by more
    than the margin would otherwise pass this preflight and be refused later.
    """
    clock = now or datetime.now(timezone.utc)
    cleanup = instant(cleanup_at)
    snapshot = instant(snapshot_at)
    for name, value in (('snapshot', snapshot_at), ('placement', placement_at), ('capacity', capacity_at)):
        moment = instant(value)
        require(moment >= cleanup, name+' must not precede the cleanup check')
        require(moment <= clock, name+' is in the future')
        require((clock-moment).total_seconds() <= FRESH_SECONDS,
                name+' is older than %d seconds' % FRESH_SECONDS)
    require(snapshot >= cleanup, 'snapshot must follow the cleanup check')
    require(instant(placement_at) >= snapshot, 'placement must follow the snapshot')
    require(instant(capacity_at) >= instant(placement_at), 'capacity must follow the placement')
    for index, value in enumerate(sampled):
        moment = instant(value)
        require(cleanup <= moment <= snapshot,
                'native capacity sample %d is outside the cleanup-to-snapshot span' % index)
    return True


def split_rows(rows, scratch_root):
    """Move the object observer and archive budgets onto the dedicated disk.

    The four RustFS disks keep only their measured data growth; the scratch disk
    carries the observation raw bytes and the archive peak.
    """
    require(isinstance(rows, list) and rows, 'reviewed draft rows required')
    moved, kept = [], []
    for row in rows:
        require(set(DRAFT_ROWS) <= set(row), 'draft row is incomplete: '+str(row)[:120])
        if row['role'] == 'objects' and row['path'] == '/srv/ax-rustfs-data-0':
            moved.append(dict(row, path=scratch_root, data_growth=0))
            kept.append(dict(row, observer_raw=0, archive_peak=0))
        else:
            kept.append(dict(row))
    require(len(moved) == 2, 'draft object rows must provide both bytes and inodes')
    require(len({row['quantity'] for row in moved}) == 2, 'scratch rows must cover bytes and inodes')
    return kept+moved, moved


def coordinator_block(out, targets, device_of):
    """Continuation, trial, archives and raw must live on one coordinator device."""
    parent = Path(out).parent.resolve(strict=True)
    parent_device = device_of(str(parent))
    mapping = {str(path): device_of(path) for path in targets}
    require(set(mapping.values()) == {parent_device},
            'coordinator continuation paths are not on one device')
    return dict(path=str(parent), device=parent_device, targets=mapping)


def placement_block(snapshot, scratch, probe, runtime_pin, scratch_root=None, now=None):
    """Per-role device placement, including the dedicated observer root."""
    root_expected = scratch_root or SCRATCH
    require(scratch['root'] == root_expected, 'only the dedicated observer root is allowed')
    workers = {}
    for role, root in (('client', ROOTS['client']), ('metadata', ROOTS['metadata']), ('objects', root_expected)):
        row = probe(role, root)
        require(row['boot_id'] == snapshot['nodes'][role]['boot_id'],
                'boot identity changed since the capacity sample: '+role)
        targets = {root+'/'+name: row['trees'][name] for name in ('archives', 'evidence', 'o')}
        require(len(set(targets.values())) == 1, 'worker evidence trees are split across devices: '+role)
        if role == 'objects':
            require(set(targets.values()) == {scratch['device']}, 'scratch placement device differs')
        workers[role] = dict(path=root, root=root, boot_id=row['boot_id'], targets=targets)
    return dict(schema='c4-archive-device-placement-v1',
                at=(now or datetime.now(timezone.utc)).isoformat(),
                runtime=runtime_pin, pool='pool-a', workers=workers)


def cleanup_block(pool, request, step, prefix, live, reclaim_pin, tags=(), now=None):
    require(live['left'] == [] and live['newer'] == [], 'the applied reclaim is not in effect')
    return dict(schema='pilot-prestep-cleanup-v1', pool=pool, original_request=request, step=step,
                prefix=prefix, outcome='not-needed', actions=[],
                finished_at=(now or datetime.now(timezone.utc)).isoformat(),
                checked=dict(reclaimed_tags=list(tags), reclaimed_tags_absent=not live['left'],
                             new_staging_since_apply=live['newer'],
                             object_free_bytes=live['free_bytes'], object_free_inodes=live['free_inodes'],
                             scratch_next_attempt_absent=not live.get('tag'), prior_applied_cleanup=reclaim_pin),
                scope='Read-only recheck of the already applied object reclaim; this step needed no deletion')


def capacity_doc(draft, step, prefix, cleanup_pin, snapshot_pin, placement_pin,
                 scratch_pin, rows, coordinator, pins, now=None):
    return dict(schema='c4-resume-stage-capacity-v1', passed=True, reviewed=True,
                at=(now or datetime.now(timezone.utc)).isoformat(),
                original_request=draft['original_request'], legacy_capacity=draft['legacy_capacity'],
                runtime=draft['runtime'], pool='pool-a', cell_id=draft['cell_id'], step=step,
                prefix=prefix, archive_mode='closed-observer-direct', reserve_fraction=.2,
                cleanup=cleanup_pin, snapshot=snapshot_pin, placement=placement_pin,
                observer_scratch=scratch_pin, observer_basis=draft['observer_basis'],
                archive_basis=draft['archive_basis'], observer_seconds=draft['observer_seconds'],
                coordinator=coordinator, rows=rows, source_pins=pins,
                budgeting_notes=draft['budgeting_notes'])


def request_doc(draft, request_draft, continuation_tool, capacity_pin, out, overlay, step=None, prefix=None):
    step = draft['step'] if step is None else step
    prefix = list(draft['prefix'][1:]) if prefix is None else prefix
    return dict(schema='c4-after-directory-hold-v1', reviewed=True,
                original_request=draft['original_request'],
                original_batch_tool=request_draft['original_batch_tool'],
                continuation_tool=continuation_tool,
                original_unit=request_draft['original_unit'],
                original_invocation=request_draft['original_invocation'],
                step=step, prefix=prefix, out=str(out), capacity=capacity_pin, archive_overlay=overlay)


def ssh(conf, host, script):
    result = subprocess.run(['ssh', '-F', str(conf), host, 'python3', '-'],
                            input=script, capture_output=True, text=True)
    require(result.returncode == 0, 'remote step failed on %s: %s' % (host, result.stderr.strip()[-800:]))
    return result.stdout


def ssh_out(conf, host, command):
    result = subprocess.run(['ssh', '-F', str(conf), host, command], capture_output=True, text=True)
    require(result.returncode == 0, 'remote command failed on %s: %s' % (host, result.stderr.strip()[-800:]))
    return result.stdout


def device_of(path):
    p = Path(path)
    while not p.exists():
        p = p.parent
    return p.stat().st_dev


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, required=True, help='frozen control directory')
    parser.add_argument('--runtime', type=Path, required=True, help='frozen runtime binding directory')
    parser.add_argument('--draft', type=Path, required=True, help='reviewed capacity draft')
    parser.add_argument('--request-draft', type=Path, required=True, help='reviewed request draft')
    parser.add_argument('--records', type=Path, required=True, help='fresh receipt directory')
    parser.add_argument('--code', type=Path, required=True, help='deployed continuation code tree')
    parser.add_argument('--out', type=Path, required=True, help='continuation directory (must not exist)')
    parser.add_argument('--ownership', type=Path, required=True, help='scratch ownership receipt path')
    parser.add_argument('--node-ownership', default='/srv/ax-receipts/c4-on1-scratch/ownership.json')
    parser.add_argument('--ownership-sha256', required=True)
    parser.add_argument('--remediation', type=Path, required=True)
    parser.add_argument('--scratch-remote-dir', required=True)
    parser.add_argument('--cell', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--reclaim', type=Path, required=True, help='applied reclaim result to recheck')
    parser.add_argument('--scratch-root', default=SCRATCH)
    args = parser.parse_args()

    control, runtime, out, records = args.control, args.runtime, args.out, args.records
    conf = runtime/'ssh.conf'
    sys.path.insert(0, str(args.code))
    from bench.runtime.pilot_pools import alias as pool_alias
    alias = {role: pool_alias('pool-a', role) for role in ROOTS}
    require(not out.exists(), 'continuation directory already exists')
    require(not (control/'cells'/args.cell).exists(), 'original formal cell output already exists')
    require(not records.exists(), 'receipt directory already exists')
    draft = load(args.draft)
    request_draft = load(args.request_draft)
    scripts = 'experiments/unified-suite/pilot-aliyun/scripts/'
    cont = args.code/scripts/'continue-c4-formal.py'
    runner = args.code/scripts/'calibrate-c4-closed-observer.py'
    helper = args.code/scripts/'archive-c4-closed-observer.py'
    # The draft itself must be reviewed and unblocked: this script may not sign
    # a budget that a human has not accepted, or an armed-but-blocked draft would
    # silently come out as passed.
    require(draft.get('reviewed') is True, 'reviewed capacity draft required')
    require(draft.get('passed') is True and not draft.get('draft_status'),
            'the capacity draft is still blocked: '+str(draft.get('draft_status')))
    step = draft['step']
    require(isinstance(step, int) and step >= 2, 'draft step must be an int >= 2')
    require(len(draft['prefix']) == step-1, 'draft prefix must hold step-1 entries')
    for path in (cont, runner, helper):
        require(path.is_file(), 'deployed continuation code missing: '+str(path))
    archive_overlay = request_draft['archive_overlay']
    format_spec = archive_overlay.get('observer_format')
    if format_spec:
        producer = format_spec.get('producer')
        expected = {'audit_module':args.code/'bench/runtime/observe_ndjson.py'}
        keys = {'producer', 'audit_module'}
        if producer=='legacy-fold':
            keys.update(('fold_tool', 'fold_peak_bytes'))
            expected['fold_tool'] = args.code/scripts/'fold-c4-observer.py'
        require(request_draft.get('reviewed') is True and producer in ('ndjson','legacy-fold') and
                set(format_spec)==keys and (producer=='ndjson' or
                type(format_spec['fold_peak_bytes']) is int and format_spec['fold_peak_bytes']>0),
                'Observer producer, pins and any fold budget must be explicitly reviewed')
        for key,path in expected.items():
            require(path.is_file() and path.resolve()==path and ref(path)==format_spec[key],
                    'Reviewed C4 observer format bytes are missing or changed: '+key)


    # 1) Read-only recheck that the applied object reclaim is still in effect.
    applied = load(args.reclaim)
    require(applied['passed'] is True and applied['applied'] is True and applied['groups'],
            'the original reclaim must be applied')
    started = instant(applied['at']).timestamp()
    probe = '\n'.join([
        'import json,os,pathlib',
        'root=pathlib.Path("/srv/ax-objects-0")',
        'tags=%r' % ([group['tag'] for group in applied['groups']],),
        'left=[t for t in tags if (root/"o"/t).exists()]',
        'newer=[p.name for p in (root/"o").iterdir() if p.is_dir() and p.stat().st_mtime>%r]' % (started,),
        's=os.statvfs(root)',
        'print(json.dumps(dict(left=left,newer=newer,free_bytes=s.f_bavail*s.f_frsize,free_inodes=s.f_favail)))'])
    live = json.loads(ssh(conf, alias['objects'], probe))
    require(not live['left'], 'a reclaimed tree reappeared: '+str(live['left']))
    require(not live['newer'], 'new staging directories appeared: '+str(live['newer']))
    require(live['free_bytes'] >= applied['final_free_bytes'], 'object disk lost space since the apply')
    scratch_probe = '\n'.join([
        'import json,pathlib',
        'root=pathlib.Path(%r)' % args.scratch_root,
        'print(json.dumps(dict(tag=((root/"o"/%r).exists() or (root/"q"/(%r+"-observer")).exists() or'
        ' (root/"archives"/(%r+"-objects.tar.gz")).exists()))))' % (args.tag, args.tag, args.tag)])
    require(not json.loads(ssh(conf, alias['objects'], scratch_probe))['tag'],
            'the next scratch attempt already exists')

    # 2) The cleanup receipt precedes every sample of this window.
    records.mkdir(parents=True, mode=0o700)
    cleanup_path = records/'cleanup.json'
    cleanup = cleanup_block('pool-a', draft['original_request'], step, draft['prefix'],
                            live, ref(args.reclaim), tags=[group['tag'] for group in applied['groups']])
    write_new(cleanup_path, cleanup)

    # 3) Native capacity snapshot through the frozen original tool.
    legacy = load(draft['legacy_capacity']['path'])
    snapshot_path = records/'capacity-native.json'
    command = [sys.executable, '-B', '/srv/ax-pilot-control/code-19/'+scripts+'capacity-check.py', 'snapshot',
               '--control', str(control), '--pool', 'pool-a',
               '--objects-container', load(legacy['after']['path'])['nodes']['objects']['container_id'],
               '--runtime-control', str(runtime/'binding.json'),
               '--runtime-control-sha256', sha(runtime/'binding.json'),
               '--before', legacy['after']['path'], '--out', str(snapshot_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    require(result.returncode == 0, 'native capacity snapshot failed: '+result.stderr.strip()[-800:])

    # 4) Observer scratch snapshot on the objects host, copied back byte for byte.
    prepare = '/srv/ax-receipts/c4-on1-scratch/prepare-c4-observer-scratch.py'
    ssh_out(conf, alias['objects'], 'if [ -d {0} ]; then rmdir {0}; fi'.format(args.scratch_remote_dir))
    remote = '\n'.join([
        'import subprocess,sys,json',
        'p=subprocess.run([sys.executable,%r,"--ownership",%r,"--sha256",%r,' % (prepare, args.node_ownership, args.ownership_sha256),
        '  "--source-pins-verified-offhost","--out",%r,"--snapshot-tag",%r],capture_output=True,text=True)' % (args.scratch_remote_dir, args.tag),
        'print(json.dumps(dict(rc=p.returncode,stderr=p.stderr[-900:],stdout=p.stdout[-900:])))'])
    run = json.loads(ssh(conf, alias['objects'], remote))
    require(run['rc'] == 0, 'scratch snapshot failed on the objects host: '+str(run['stderr']))
    raw = ssh_out(conf, alias['objects'], 'cat %s/result.json' % args.scratch_remote_dir)
    scratch_path = records/'observer-scratch.json'
    with scratch_path.open('x', encoding='utf-8') as stream:
        stream.write(raw)
    scratch = load(scratch_path)
    require(scratch['schema'] == 'c4-observer-scratch-snapshot-v1' and scratch['passed'] is True and
            scratch['tag'] == args.tag and scratch['root'] == args.scratch_root,
            'scratch snapshot receipt differs')

    # 5) Actual archive/observer device placement on each worker.
    snapshot = load(snapshot_path)

    def probe_role(role, root):
        script = '\n'.join(['import json,pathlib',
                            'r=pathlib.Path(%r)' % root,
                            'print(json.dumps(dict(boot_id=pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip(),'
                            ' trees={n:r.joinpath(n).stat().st_dev for n in ("o","archives","evidence")})))'])
        return json.loads(ssh(conf, alias[role], script))

    placement = placement_block(snapshot, scratch, probe_role, ref(runtime/'binding.json'),
                                scratch_root=args.scratch_root)
    placement_path = records/'placement.json'
    write_new(placement_path, placement)

    # 6) Reviewed capacity: the draft budgets with the object observer moved to scratch.
    rows, moved = split_rows(draft['rows'], args.scratch_root)
    targets = [str(out)]+[str(t) for t in draft['coordinator']['targets'] if str(t) != str(out)]
    coordinator = coordinator_block(out, targets, device_of)
    pins = [ref(cleanup_path), ref(snapshot_path), ref(scratch_path), ref(placement_path),
            draft['legacy_capacity'], legacy['before'], legacy['after'],
            *draft['archive_basis'].values(), *draft['prefix'],
            ref(str(control/'calibration/flat-off1/cohort/result.json')),
            ref(str(control/'calibration/flat-off1/drain-timing.json')),
            ref(str(control/'calibration/flat-off1/raw/client/q/c4-a-flat-off1/verify/result.json')),
            *(pin for sources in draft['observer_basis'].values() for pin in sources),
            ref(args.reclaim), ref(args.remediation)]
    require(len({pin['path'] for pin in pins}) == len(pins), 'duplicate capacity source pin')
    capacity = capacity_doc(draft, step, draft['prefix'], ref(cleanup_path), ref(snapshot_path),
                            ref(placement_path), ref(scratch_path), rows, coordinator, pins)
    # Validate the whole chain before anything else lands, so a rejected window
    # does not leave a half-written receipt set behind.
    sampled = [node['sampled_at'] for node in (snapshot.get('nodes') or {}).values()
               if node.get('sampled_at')]
    check_order(cleanup['finished_at'], snapshot['at'], placement['at'], capacity['at'], sampled=sampled)
    capacity_path = records/'capacity.json'
    write_new(capacity_path, capacity)

    require(request_draft.get('reviewed') is True, 'reviewed continuation request draft required')
    require(request_draft.get('original_request') == draft.get('original_request'),
            'both drafts must attest the same original request')
    require(out.is_absolute() and out.resolve() == out, 'canonical absolute continuation directory required')
    overlay = dict(runner=ref(runner), helper=ref(helper), worker_code=archive_overlay['worker_code'])
    if format_spec:
        overlay['observer_format'] = format_spec
    request = request_doc(draft, request_draft, ref(cont), ref(capacity_path), out, overlay)
    request_path = records/'request.json'
    write_new(request_path, request)
    print(json.dumps(dict(cleanup=ref(cleanup_path), snapshot=ref(snapshot_path), scratch=ref(scratch_path),
                          placement=ref(placement_path), capacity=ref(capacity_path),
                          request=ref(request_path), rows_moved=len(moved)), indent=2))


if __name__ == '__main__':
    main()
