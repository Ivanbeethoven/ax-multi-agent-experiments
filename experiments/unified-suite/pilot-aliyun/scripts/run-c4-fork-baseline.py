#!/usr/bin/env python3
"""One actual C4 100k fork baseline, under the existing pool's resource locks.

The coordinator entry takes a reviewed request. Its private worker is sent over
SSH stdin while the coordinator retains the canonical host/disk/pool locks.
No other arm, model, retry, cloud provisioning or formal admission is included.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

FILES = 100000
BYTES = FILES * 4096
TREE_SHA = '02aeda272edbab9512595f7b968d840a9cfdc0e8302ed036399bf4151f08bd13'
CANONICAL_FS_STEP_SHA = 'd58b7b7198450e1728c10b84cb978c0b793e257ecafca21ee5ac2829cb4a9c79'
JUICEFS_SHA = '6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
RUNTIME_INPUTS = ('bench/fs_step.py',
    'experiments/unified-suite/pilot-aliyun/scripts/fork-oracle.py',
    'experiments/unified-suite/pilot-aliyun/scripts/pilot_io.py')
CLIENT = Path('/srv/ax-client')
SLOT = Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs')


def load(path): return json.loads(Path(path).read_text())


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ref(path): return dict(path=str(path), sha256=digest(path))


def pinned(value):
    path = Path(value['path'])
    if not path.is_absolute() or path.is_symlink() or digest(path) != value['sha256']:
        raise ValueError('Pinned absolute input changed')
    return path


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')


def process_identity(pid):
    try:
        raw = Path('/proc/'+str(pid)+'/stat').read_text()
        fields = raw[raw.rindex(')')+2:].split()
        return dict(pid=int(pid), start_ticks=int(fields[19]), state=fields[0],
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    except FileNotFoundError: return None


def release_probe(expected, mount_point):
    current = process_identity(expected['pid'])
    alive = current is not None and current['state'] != 'Z' and all(
        current[k] == expected[k] for k in ('pid', 'start_ticks', 'boot_id'))
    mounted = False
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split(' - ', 1)[0].split()[4])
        mounted |= point == mount_point
    slot_empty = not SLOT.read_text().strip()
    return dict(worker_identity=expected, worker_exited=not alive, mount_absent=not mounted,
        slot_empty=slot_empty, safe_for_release=not alive and not mounted and slot_empty)


def select_cell(plan, cell_id, pool, product):
    matches = [c for c in plan['cells'] if c['id'] == cell_id]
    if len(matches) != 1: raise ValueError('Exactly one explicit C4 cell required')
    cell = matches[0]
    expected = ('native', 'flat') if product == 'brewfs' else ('juicefs', 'juicefs')
    if product not in ('brewfs', 'juicefs') or (
            cell.get('suite'), cell.get('workload'), cell.get('pool'), cell.get('arm_id'),
            cell.get('arm', {}).get('product'), cell.get('arm', {}).get('engine')) != (
                'C4', 'fork', pool, expected[0], product, expected[1]):
        raise ValueError('Only actual C4 flat full copy or JuiceFS clone is supported')
    p = cell['params']
    if (p.get('files'), p.get('file_bytes'), p.get('agents'), p.get('children'),
            p.get('creation'), p.get('dataset')) != (
                FILES, 4096, 1, 1, 'burst', 'fork-fixture-100k-4k'):
        raise ValueError('Exact 100k / 4 KiB / one-child burst cell required')
    if plan.get('provenance_bound') is not True:
        raise ValueError('Actual build/input/inventory-bound plan required')
    return cell


def require_dispatch(cell):
    # This leaf also runs standalone against a pinned historical runtime.
    arm = cell.get('arm') or {}
    if (cell.get('suite'), cell.get('workload'), cell.get('arm_id'),
            arm.get('product'), arm.get('engine')) == ('C4', 'fork', 'native', 'brewfs', 'flat'):
        raise ValueError('Historical C4 BrewFS Flat full-copy dispatch is retired; existing evidence remains readable')


def creation_argv(product, binary, base, child):
    base, child = Path(base), Path(child)
    if not base.is_absolute() or base.parent != child.parent or base == child:
        raise ValueError('Distinct sibling base and child directories required')
    if product == 'juicefs': return [str(binary), 'clone', '-p', str(base), str(child)]
    if product == 'brewfs': return ['/usr/bin/cp', '-a', '--reflink=never', '--', str(base), str(child)]
    raise ValueError('Unsupported fork baseline')


def check_debt(directory, cell_id):
    result = load(Path(directory)/'result.json')
    if result.get('passed') is not True or result.get('cell_id') != cell_id or len(result.get('samples', [])) != 2:
        raise ValueError('Two original zero-debt samples required')
    samples = [load(pinned(value)) for value in result['samples']]
    for sample in samples:
        gauges = sample.get('native_gauges', {})
        if (sample.get('passed') is not True or sample.get('cell_id') != cell_id or not gauges or
                any(type(v) not in (int, float) or v != 0 for v in gauges.values()) or
                any(type(sample.get(k)) not in (int, float) or sample[k] != 0
                    for k in ('pending_upload_bytes', 'pending_upload_operations'))):
            raise ValueError('Original native upload gauges are not zero')
    if samples[1]['monotonic_ns']-samples[0]['monotonic_ns'] < 1000000000:
        raise ValueError('Zero-debt observations must be separated by at least one second')
    return ref(Path(directory)/'result.json')


def check_latest(value, build_sha):
    sources = value.get('sources', [])
    if (value.get('passed') is not True or value.get('build_manifest_sha256') != build_sha or
            {v.get('component') for v in sources} != {'brewfs', 'rustfs'} or
            any(v.get('matches') is not True or v.get('lookup_exit_code') != 0 or
                not v.get('built_commit') or v.get('built_commit') != v.get('remote_commit') for v in sources)):
        raise ValueError('Actual latest-branch check must match this frozen build manifest')


def runtime(path):
    path = Path(path).resolve(strict=True)
    sys.path[:0] = [str(path), str(path/'bench'), str(path/'scripts'),
                    str(path/'experiments/unified-suite/pilot-aliyun/scripts')]
    return path


def check_runtime(repo, pins):
    if set(pins) != set(RUNTIME_INPUTS) or pins['bench/fs_step.py'] != CANONICAL_FS_STEP_SHA:
        raise ValueError('Frozen canonical-mode fs_step and both C4 helper pins required; code12 is too old')
    for relative, sha in pins.items(): pinned(dict(path=str(Path(repo)/relative), sha256=sha))


def worker(s):
    """Only invoked by the locked coordinator; every result remains on the client."""
    tool = pinned(s['node_tool']); repo = runtime(tool.parents[4])
    check_runtime(repo, s['runtime_pins'])
    from bench.runtime.common import alive, mounts, cgroup_limits
    from bench.runtime.pilot_pools import endpoint_binding
    from bench.suite_plan import check_plan
    plan_path, inventory = pinned(s['plan']), pinned(s['inventory'])
    plan = check_plan(load(plan_path)); cell = select_cell(plan, s['cell_id'], s['pool'], s['product'])
    require_dispatch(cell)
    binding = endpoint_binding(plan, cell, inventory)
    if socket.gethostname() != binding['client']['hostname']:
        raise ValueError('Worker is not on the frozen client')
    binary = pinned(s['filesystem_binary'])
    tree_path, fixture, closure = pinned(s['source_tree']), pinned(s['fixture']), pinned(s['input_closure'])
    if digest(tree_path) != TREE_SHA or load(fixture).get('files') != FILES or load(fixture).get('bytes') != BYTES:
        raise ValueError('C4 fixture differs; C3 input is not interchangeable')
    closed = load(closure)
    if (closed.get('passed') is not True or closed.get('sources_only') is not True or
            closed.get('preparation_processes_exited') is not True or
            closed.get('c4_tree') != s['source_tree'] or closed.get('files_per_input') != FILES):
        raise ValueError('Original complete source preparation closure required')
    source, asset, out = (Path(s[k]) for k in ('source', 'asset', 'out'))
    for path, parent in ((asset, CLIENT/'a'), (out, CLIENT/'preparation')):
        if (path.parent != parent or path.exists() or path.is_symlink() or
                not re.fullmatch('[a-z0-9-]{3,70}', path.name)):
            raise ValueError('New owned absolute asset/output required')
    if (not source.is_absolute() or source.is_symlink() or not source.is_dir() or
            not source.resolve().is_relative_to(CLIENT/'inputs') or not 20 <= s['database'] < 256 or
            not re.fullmatch('[a-z0-9-]{3,32}', s['attempt'])):
        raise ValueError('Existing owned source, new DB and short attempt required')
    mode=s.get('mode','baseline')
    if mode not in ('baseline','prepare'):raise ValueError('Explicit baseline or once-only preparation required')
    states = [CLIENT/'m'/(s['attempt']+'-'+str(i)) for i in range(1, 3 if mode=='prepare' else 5)]
    if any(p.exists() or p.is_symlink() for p in states) or SLOT.read_text().strip():
        raise ValueError('Original attempt exists or Agent slot is occupied; never retry')
    for port in (8910, 9567):
        try: conn = socket.create_connection(('127.0.0.1', port), timeout=.5)
        except OSError: continue
        conn.close(); raise ValueError('Another native mount metrics endpoint is active')
    stat = os.statvfs(CLIENT)
    if stat.f_bavail*stat.f_frsize < 10*1024**3 or stat.f_favail < 1200000:
        raise ValueError('Insufficient client capacity/inodes')
    out.mkdir(parents=True, mode=0o700)
    write(out/'request.json', s)
    write(out/'started.json', dict(at=datetime.now(timezone.utc).isoformat(), source_tool=ref(__file__),
        source_lock=s['source_lock'], cell_id=cell['id'], automatic_retry=False, formal_admission=False))
    active = None; stopping = False

    def run(name, argv):
        directory = out/name; directory.mkdir(); argv = list(map(str, argv))
        write(directory/'command.json', dict(argv=argv, automatic_retry=False))
        begin = time.monotonic_ns()
        with (directory/'stdout').open('xb') as stdout, (directory/'stderr').open('xb') as stderr:
            proc = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
            write(directory/'process.json', dict(pid=proc.pid)); code = proc.wait()
        result = dict(exit_code=code, wall_seconds=(time.monotonic_ns()-begin)/1e9)
        write(directory/'exit.json', result)
        if code: raise RuntimeError('Inspect original failed stage: '+str(directory))
        return result

    def helper(action, name, **fields):
        argv = [sys.executable, tool, action, '--asset', asset]
        for key, value in fields.items():
            if value is True: argv += ['--'+key.replace('_', '-')]
            elif value is not None: argv += ['--'+key.replace('_', '-'), value]
        return run(name, argv)

    fs_step = repo/'bench/fs_step.py'

    def fs(action, name, root, **fields):
        target = out/(name+'.json')
        argv = [sys.executable, fs_step, action, '--root', root, '--output', target]
        for key, value in fields.items(): argv += ['--'+key.replace('_', '-'), value]
        result = run(name, argv)
        value = load(target)
        if value.get('passed') is False: raise ValueError('Filesystem verification failed')
        return result

    def flush(name, root):
        target = out/(name+'.json')
        result = run(name, [sys.executable, __file__, 'fsync-tree', '--root', root,
            '--runtime-repo', repo, '--out', target])
        if load(target).get('files') != FILES: raise ValueError('Incomplete fsync tree')
        return result

    def stop(index):
        nonlocal active, stopping
        stopping = True; target = out/('normal-stop-'+str(index)+'.json')
        helper('stop', 'stop-'+str(index), mount_run=active, out=target)
        value = load(target)
        if (value.get('passed') is not True or value.get('exit_code') != 0 or
                value.get('mount_absent') is not True or value != load(active/'stopped.json') or mounts(point)):
            raise ValueError('Original mount did not stop normally')
        process = load(active/'process.json'); deadline = time.monotonic()+5
        while alive(process['process']) or alive(process['supervisor']):
            if time.monotonic() >= deadline: raise ValueError('Original mount process remains live')
            time.sleep(.05)
        active = None; stopping = False

    def metrics(name):
        port = 8910 if s['product'] == 'brewfs' else 9567
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:'+str(port)+'/metrics', timeout=5) as response:
            raw = response.read(16*1024**2+1)
        if len(raw) > 16*1024**2: raise ValueError('Oversized metrics response')
        (out/(name+'.prom')).write_bytes(raw)

    try:
        # Source/oracle work is outside the creation timer and precedes tested output.
        fs('check', 'source-full-check', source, expected=tree_path, expected_sha256=TREE_SHA)
        oracle_tool = repo/'experiments/unified-suite/pilot-aliyun/scripts/fork-oracle.py'
        run('oracle', [sys.executable, oracle_tool, '--base-tree', tree_path, '--base-tree-sha256', TREE_SHA,
            '--fixture-proof', fixture, '--fixture-proof-sha256', s['fixture']['sha256'],
            '--files', FILES, '--tag', 'child-01', '--out', out/'oracle-result'])
        oracle = load(out/'oracle-result/result.json')
        check = run('database-empty', ['redis-cli', '-u', binding['metadata']+'/'+str(s['database']), 'DBSIZE'])
        if (out/'database-empty/stdout').read_text().strip() != '0': raise ValueError('DB not empty; never flush it')
        SLOT.write_text(str(os.getpid()))
        budget = cgroup_limits(dict(cpus=2.0, memory_bytes=8589934592, swap_bytes=0))
        write(out/'effective-budget.json', budget)
        helper('asset-init', 'asset-init', product=s['product'], database=s['database'], plan=plan_path,
            cell=cell['id'], inventory=inventory, filesystem_binary=binary,
            filesystem_binary_sha256=s['filesystem_binary']['sha256'], out=out/'asset-init-proof')
        actual = load(asset/'asset.json'); point = Path(actual['mount_point']); base = point/'base'; child = point/'child'
        if (actual['plan'] != s['plan'] or actual['filesystem_binary'] != s['filesystem_binary'] or
                actual.get('pool') != s['pool'] or actual['database'] != s['database']):
            raise ValueError('Actual asset binding differs')
        timings = {}
        for index, state in enumerate(states, 1):
            draft = out/('draft-'+str(index))
            helper('draft-mount', 'draft-step-'+str(index), cell=cell['id'], out=draft)
            active = state
            helper('start', 'start-'+str(index), spec=draft/'mount.reviewed.json', out=state, join_slot=True)
            if index == 1:
                argv = ([binary, 'sync', '--dirs', '--perms', '--links', str(source)+'/', str(base)+'/']
                    if s['product'] == 'juicefs' else ['/usr/bin/cp', '-a', '--reflink=never', '--', source, base])
                run('import', argv); flush('import-fsync', base)
                helper('debt', 'import-debt-step', cell=cell['id'], out=out/'import-debt')
                check_debt(out/'import-debt', cell['id'])
            elif index == 2:
                fs('check', 'cold-base-check', base, expected=tree_path, expected_sha256=TREE_SHA)
            elif index == 3:
                if child.exists() or child.is_symlink(): raise ValueError('Child already exists')
                metrics('before-create')
                timings['create_command_seconds'] = run('create', creation_argv(s['product'], binary, base, child))['wall_seconds']
                fs('first-read', 'first-read', child, file=oracle['first_read']['path'], sha256=oracle['first_read']['sha256'])
                timings['first_read_seconds'] = load(out/'first-read.json')['seconds']
                timings['creation_fsync_seconds'] = flush('creation-fsync', child)['wall_seconds']
                started = time.monotonic_ns()
                helper('debt', 'creation-debt-step', cell=cell['id'], out=out/'creation-debt')
                check_debt(out/'creation-debt', cell['id'])
                timings['creation_drain_seconds'] = (time.monotonic_ns()-started)/1e9
                metrics('after-create-drain')
                started = time.monotonic_ns()
                timings['mutate_fsync_seconds'] = fs('mutate', 'mutate', child, tag='child-01')['wall_seconds']
                helper('debt', 'mutation-debt-step', cell=cell['id'], out=out/'mutation-debt')
                check_debt(out/'mutation-debt', cell['id'])
                timings['mutation_and_drain_seconds'] = (time.monotonic_ns()-started)/1e9
                metrics('after-mutation-drain')
            else:
                started = time.monotonic_ns()
                fs('check', 'cold-parent-isolation', base, expected=tree_path, expected_sha256=TREE_SHA)
                fs('check', 'cold-child-check', child, expected=oracle['expected_tree']['path'],
                    expected_sha256=oracle['expected_tree']['sha256'])
                if sorted(p.name for p in point.iterdir()) != ['base', 'child']:
                    raise ValueError('Unexpected namespace outside original parent and child')
                timings['full_cold_verification_seconds'] = (time.monotonic_ns()-started)/1e9
            stop(index)
        if mode=='prepare':
            write(out/'result.json',dict(passed=True,cell_id=cell['id'],pool=s['pool'],product=s['product'],
                purpose='asset-preparation',files=FILES,bytes=BYTES,asset=ref(asset/'asset.json'),
                source_tree=s['source_tree'],fixture=s['fixture'],work_tag='child-01',
                source_check=ref(out/'source-full-check.json'),cold_base_check=ref(out/'cold-base-check.json'),
                oracle=ref(out/'oracle-result/result.json'),normal_exits=[ref(out/('normal-stop-'+str(i)+'.json')) for i in (1,2)],
                import_seconds=load(out/'import/exit.json')['wall_seconds'],
                import_fsync_seconds=load(out/'import-fsync/exit.json')['wall_seconds'],
                cold_verify_seconds=load(out/'cold-base-check/exit.json')['wall_seconds'],
                performance_accepted=False,formal_admission=False,automatic_retry=False))
            return
        write(out/'result.json', dict(passed=True, cell_id=cell['id'], pool=s['pool'], product=s['product'],work_tag='child-01',
            files=FILES, bytes=BYTES, children=1, method='full-independent-copy' if s['product']=='brewfs' else 'native-clone',
            asset=ref(asset/'asset.json'), source_tree=s['source_tree'], oracle=ref(out/'oracle-result/result.json'),
            timings=timings, native_metrics=[ref(out/(n+'.prom')) for n in
                ('before-create', 'after-create-drain', 'after-mutation-drain')],
            normal_exits=[ref(out/('normal-stop-'+str(i)+'.json')) for i in range(1, 5)],
            parent_unchanged=True, child_matches_independent_oracle=True, siblings=0,
            performance_accepted=False, formal_admission=False, automatic_retry=False,
            scope='Actual standalone C4 baseline and raw timings. Full C4 observer calibration/admission is separate; N=1 establishes no sibling concurrency or scaling.'))
    except Exception as error:
        write(out/'failed.json', dict(error=str(error), error_type=type(error).__name__,
            active_supervisor=str(active) if active else None, stop_already_attempted=stopping, automatic_retry=False))
        if active and not stopping and (active/'ready.json').is_file():
            try: stop('failure')
            except Exception as stop_error: write(out/'failure-stop-error.json', dict(error=str(stop_error)))
        raise


def validate_request(args):
    runtime(args.runtime_repo)
    from bench.runtime.pilot_pools import resource_ids, pool_inventory, cell_resources
    from bench.suite_plan import check_plan
    request_path = pinned(dict(path=str(args.request), sha256=args.sha256)); s = load(request_path)
    if s.get('schema') != 'c4-fork-baseline-request-v1' or s.get('reviewed') is not True:
        raise ValueError('Reviewed C4 baseline request required')
    if s['source_tool_sha256'] != digest(__file__): raise ValueError('Coordinator tool changed')
    control = Path(s['control']).resolve(strict=True)
    if (pinned(s['canonical_plan']) != control/'suite/plan.json' or not (control/'state.sqlite').is_file() or
            not (control/'runner-locks').is_dir()):
        raise ValueError('Existing canonical control plan/state/lock directory required; no separate empty lock root')
    plan_path = pinned(s['fork_plan']); plan = check_plan(load(plan_path))
    cell = select_cell(plan, s['cell_id'], s['pool'], s['product'])
    build = load(pinned(s['build_manifest']))
    if s['build_manifest']['sha256'] != plan['config']['build_manifest_sha256']:
        raise ValueError('Build manifest differs from the frozen C4 plan')
    check_latest(load(pinned(s['latest_check'])), plan['config']['build_manifest_sha256'])
    cloud = load(pinned(s['inventory']))
    wanted = set(resource_ids(pool_inventory(cloud, s['pool'])))
    old = check_plan(load(pinned(s['canonical_plan'])))
    old_cluster = next(p for p in old['config']['clusters'] if p['id'] == s['pool'])
    if wanted != set(cell_resources(plan, cell)) or wanted != set(old_cluster['resource_ids']):
        raise ValueError('Use the same actual host/disk IDs and canonical locks as the running campaign')
    cluster = next(p for p in plan['config']['clusters'] if p['id'] == s['pool'])
    if cluster['inventory_sha256'] != s['inventory']['sha256']:
        raise ValueError('Fork inventory differs from its frozen plan')
    w = dict(s['worker'])
    if (w['plan']['sha256'] != s['fork_plan']['sha256'] or w['inventory']['sha256'] != s['inventory']['sha256'] or
            w['tool']['sha256'] != s['source_tool_sha256']):
        raise ValueError('Client receives exactly the same plan/inventory/standalone tool bytes')
    if (set(w.get('runtime_pins', {})) != set(RUNTIME_INPUTS) or
            w['runtime_pins']['bench/fs_step.py'] != CANONICAL_FS_STEP_SHA):
        raise ValueError('Declare the new canonical-mode frozen C4 runtime, not code12')
    expected_binary = JUICEFS_SHA if s['product'] == 'juicefs' else next(
        item['sha256'] for item in build['files'] if item['file'] == 'brewfs')
    if w['filesystem_binary']['sha256'] != expected_binary:
        raise ValueError('Client binary differs from this batch build/fixed JuiceFS release')
    config = pinned(s['ssh_config']); out = Path(s['out'])
    if not out.is_absolute() or out.exists() or out.is_symlink(): raise ValueError('New coordinator output required')
    controls = [control] + [Path(p).resolve(strict=True) for p in s.get('other_lease_controls', [])]
    if len(set(controls)) != len(controls) or any(not (p/'state.sqlite').is_file() for p in controls):
        raise ValueError('Explicit distinct existing lease databases required')
    return dict(s=s,control=control,plan=plan,cell=cell,wanted=wanted,w=w,config=config,
                out=out,controls=controls,request_path=request_path)


def coordinator(args):
    from bench.runtime.pilot_pools import resource_guard
    context=validate_request(args)
    control,cell,plan=(context[k] for k in ('control','cell','plan'))
    require_dispatch(cell)
    with resource_guard(control, cell, plan):
        run_remote_locked(context)


def run_remote_locked(context):
    """Internal entry for a batch already holding the same canonical guard.

    This is intentionally no CLI/request flag: the batch calls this function
    from inside its own resource_guard, covering import through formal closure.
    """
    from bench.runtime.pilot_pools import active_conflicts,alias
    s,control,plan,cell,wanted,w,config,out,controls,request_path=(context[k] for k in
        ('s','control','plan','cell','wanted','w','config','out','controls','request_path'))
    if any(active_conflicts(p, cell, plan) for p in controls):
        raise ValueError('A lease still owns this pool or an intersecting resource; other pool leases may continue')
    _run_remote_locked(s,control,cell,wanted,w,config,out,request_path)


def _run_remote_locked(s,control,cell,wanted,w,config,out,request_path):
    require_dispatch(cell)
    from bench.runtime.pilot_pools import alias
    out.mkdir(parents=True, mode=0o700)
    write(out/'request.json', s)
    lock = dict(canonical_control=str(control), resource_ids=sorted(wanted), pool=s['pool'],
        at=datetime.now(timezone.utc).isoformat(), process=os.getpid(), request=ref(request_path))
    write(out/'locks-held.json', lock)
    w.update(pool=s['pool'], product=s['product'], cell_id=s['cell_id'], source_lock=ref(out/'locks-held.json'))
    # Check the remote standalone bytes before invoking it. No shell-built paths.
    peer = alias(s['pool'], 'client')
    actual = subprocess.check_output(['ssh', '-F', str(config), peer,
        shlex.join(['sha256sum', w['tool']['path']])], text=True).split()[0]
    if actual != w['tool']['sha256']: raise ValueError('Client tool bytes differ')
    argv = ['ssh', '-F', str(config), peer, shlex.join(['python3', w['tool']['path'], 'worker'])]
    write(out/'command.json', dict(argv=argv, stdin='reviewed non-secret worker request', automatic_retry=False))
    with (out/'stdout').open('xb') as stdout, (out/'stderr').open('xb') as stderr:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
        write(out/'process.json', dict(pid=proc.pid))
        proc.communicate((json.dumps(w)+'\n').encode())
    write(out/'exit.json', dict(exit_code=proc.returncode))
    # An SSH exit is not proof the original remote process stopped. Keep the
    # same locks while checking PID+boot+start-tick, mount and cgroup state.
    # Failed probes retry only this read-only check, never the workload.
    handshake = None
    for line in (out/'stdout').read_text().splitlines():
        try: candidate = json.loads(line)
        except ValueError: continue
        if candidate.get('source_lock') == w['source_lock'] and candidate.get('worker_identity'):
            handshake = candidate['worker_identity']; break
    if handshake is None:
        write(out/'unknown-remote-state.json', dict(automatic_retry=False,
            action='Keep this coordinator alive; inspect original SSH/remote process. No workload retry.'))
        # No process identity means the operator must inspect the original
        # session; releasing guards on a transport error would be unsafe.
        while True: time.sleep(5)
    probe_index = 0
    while True:
        probe_index += 1
        probe_argv = ['ssh', '-F', str(config), peer, shlex.join(['python3', w['tool']['path'],
            'release-probe', '--identity', json.dumps(handshake),
            '--mount-point', str(Path(w['asset'])/'mnt')])]
        probe = subprocess.run(probe_argv, capture_output=True)
        directory = out/('release-probe-'+str(probe_index)); directory.mkdir()
        (directory/'stdout').write_bytes(probe.stdout); (directory/'stderr').write_bytes(probe.stderr)
        write(directory/'exit.json', dict(exit_code=probe.returncode, argv=probe_argv))
        if probe.returncode == 0 and json.loads(probe.stdout).get('safe_for_release') is True: break
        time.sleep(5)
    if proc.returncode:
        write(out/'failed.json', dict(passed=False, remote_evidence=w['out'], automatic_retry=False,
            state='Inspect original worker and mounts before another use of this pool'))
        raise RuntimeError('Original worker failed; preserve evidence and inspect the pool')
    # Retrieve the final result while retaining the exact same resource guards.
    raw = subprocess.check_output(['ssh', '-F', str(config), peer,
        shlex.join(['cat', str(Path(w['out'])/'result.json')])])
    result = json.loads(raw)
    if result.get('passed') is not True or result.get('cell_id') != cell['id']:
        raise ValueError('Original complete worker result required')
    write(out/'worker-result.json', result)
    write(out/'result.json', dict(passed=True, cell_id=cell['id'], pool=s['pool'],
        remote_evidence=w['out'], lock=ref(out/'locks-held.json'), result=ref(out/'worker-result.json'),
        performance_accepted=False, formal_admission=False, scope='Standalone baseline; no suite cell marked complete'))


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('run')
    c.add_argument('--request', type=Path, required=True); c.add_argument('--sha256', required=True)
    c.add_argument('--runtime-repo', type=Path, required=True)
    sub.add_parser('worker')
    q = sub.add_parser('release-probe'); q.add_argument('--identity', required=True)
    q.add_argument('--mount-point', required=True)
    f = sub.add_parser('fsync-tree'); f.add_argument('--root', type=Path, required=True)
    f.add_argument('--out', type=Path, required=True); f.add_argument('--runtime-repo', type=Path, required=True)
    a = p.parse_args()
    if a.action == 'run': coordinator(a)
    elif a.action == 'worker':
        request = json.loads(sys.stdin.readline())
        print(json.dumps(dict(worker_identity=process_identity(os.getpid()), source_lock=request['source_lock'])), flush=True)
        worker(request)
    elif a.action == 'release-probe': print(json.dumps(release_probe(json.loads(a.identity), a.mount_point)))
    else:
        runtime(a.runtime_repo)
        from pilot_io import fsync_import
        if a.out.exists() or a.out.resolve().is_relative_to(a.root.resolve()):
            raise ValueError('New fsync evidence outside the tested tree required')
        count = fsync_import(a.root)
        fd = os.open(a.root.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        write(a.out, dict(files=count, fsync_each_file=True, fsync_parent_directory=True, workers=8))


if __name__ == '__main__': main()
