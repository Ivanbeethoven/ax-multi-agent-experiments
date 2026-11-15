#!/usr/bin/env python3
"""Archive one of five exact c123 C1 cohorts after a reviewed Pool A handoff.

The request must pin the staged draft, current canonical plan and SSH config,
three original host identity receipts, a fresh quiescence receipt, the worker
tool, this runner, and both resident coordinator services. Neither pool may
run work during archival. No test launches, deployment, retries or cleanup.
"""
import argparse
import base64
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import sqlite3
import stat
import subprocess
import sys
import tarfile

sys.dont_write_bytecode = True
PILOT = Path('/srv/ax-pilot-control')
CANONICAL = PILOT/'parallel-control-01'
ORIGINAL = PILOT/'c123-control-01'
PREP = PILOT/'preparation'
DRAFT_DIR = PREP/'c123-old-c1-calibration-draft-01'
ROLE_ROOTS = {'client': '/srv/ax-client', 'metadata': '/srv/ax-metadata', 'objects': '/srv/ax-objects-0'}
MANAGER_FIELDS = ('LoadState', 'InvocationID', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'Result', 'ExecMainStatus')
TERMINAL = {('active', 'exited'), ('inactive', 'dead'), ('failed', 'failed')}
POOLS = {'pool-a', 'pool-b'}
PILOT_UNIT = re.compile(r'ax-pilot-[a-z0-9-]+\.service')
ASSETS = 'ax-pilot-assets.service'
RELAY = 'ax-pilot-c4-relay-01.service'
RESIDENT_UNITS = {ASSETS, RELAY}
CGROUP_ROOT = Path('/sys/fs/cgroup')
SERVICE_FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'ControlGroup')
RESIDENT_FIELDS = (*SERVICE_FIELDS, 'InvocationID', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'ExecStart')
GIB = 1024 ** 3
MIB = 1024 ** 2
RUN = re.compile(r'c123-old-c1-retention-[a-z0-9-]{2,45}')
SSH_OPTIONS = ('-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
               '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc)


def encode(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()


def read(path):
    return json.loads(path.read_bytes())


def ref(path):
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_file(), f'Invalid pinned file: {path}')
    with path.open('rb') as stream:
        return {'path': str(path), 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}


def pin(value, expected=None):
    path = Path(value['path'])
    require((expected is None or path == expected) and ref(path) == value, f'Changed or misplaced input: {path}')
    return path


def write(path, value):
    with path.open('xb') as stream:
        stream.write(encode(value)+b'\n')


def new_paths(request_path, out):
    require(request_path.parent.parent == PREP and RUN.fullmatch(request_path.parent.name) and
            request_path.name == 'request.reviewed.json' and out == request_path.parent/'run',
            'New owned coordinator retention request and output required')
    package = out.parent/'calibrations.tar.gz'
    for path in (out, package):
        require(path.resolve() == path and not path.exists() and not path.is_symlink(), f'Existing or unsafe output: {path}')
    return package


def load_input(request, runner_path):
    s = read(pin(request))
    require(s['schema'] == 'c123-old-c1-calibration-retention-request-v1' and s['reviewed'] is True and
            s['new_measurements'] is False and s['automatic_retry'] is False, 'Reviewed retention-only request required')
    pin(s['runner'], runner_path)
    draft_path = pin(s['draft'], DRAFT_DIR/'draft.json')
    draft = read(draft_path)
    validation = read(pin(s['draft_validation'], DRAFT_DIR/'validation.json'))
    adapter_path = pin(s['draft_adapter'], DRAFT_DIR/'adapter.py')
    require(draft['schema'] == 'c123-old-c1-calibration-retention-draft-v1' and
            draft['coordinator_receipts_verified'] is True and draft['approved_for_execution'] is False and
            draft['workers_contacted'] is False and draft['worker_raw_verified'] is False and
            validation['schema'] == 'c123-old-c1-calibration-draft-validation-v1' and validation['passed'] is True and
            validation['draft_sha256'] == s['draft']['sha256'] and validation['source_sha256'] == s['draft_adapter']['sha256'],
            'Exact previously verified draft required')
    selected = [c for c in draft['cells'] if c['cell_id'] == s['cell_id']]
    require(len(draft['cells']) == 5 and len(selected) == 1 and len(selected[0]['trials']) == 6,
            'Select one exact old C1 cohort')
    retainer = pin(draft['node_retainer'])
    repo = retainer.parents[4]
    require(repo.parent == PILOT and re.fullmatch(r'code-[0-9]{2}', repo.name), 'Expected original frozen retention runtime')
    for item in draft['coordinator_retainer_dependencies']:
        pin(item)
    plan = read(pin(s['canonical_plan'], CANONICAL/'suite/plan.json'))
    old_plan = read(pin(draft['plan'], ORIGINAL/'suite/plan.json'))
    old = next(c for c in old_plan['cells'] if c['id'] == s['cell_id'])
    cell = next(c for c in plan['cells'] if c['id'] == s['lock_cell'])
    require(old['pool'] == cell['pool'] == 'pool-a' and set(old['resource_ids']) == set(cell['resource_ids']),
            'Canonical A lock must cover the exact original physical resources')
    ssh = pin(s['ssh_config'], CANONICAL/'ssh.conf')
    tool = pin(s['node_tool'], Path(request['path']).parent/'node.py')
    adapter = runpy.run_path(str(adapter_path))
    node = runpy.run_path(str(tool))
    runtime = {name: ref(repo/name)['sha256'] for name in sorted(node['RUNTIME_FILES'])}
    require(retainer == repo/node['TOOL'], 'Unexpected frozen helper path')
    return s, draft, selected[0], repo, plan, cell, ssh, tool, adapter, node, runtime


def identities(s, cell, fleet):
    require(set(s['worker_identities']) == set(ROLE_ROOTS), 'All three original role identities are required')
    result = {}
    for role, root in ROLE_ROOTS.items():
        nodes = [n for n in fleet['nodes'] if n['id'] == 'pool-a-'+role]
        require(len(nodes) == 1, 'Wrong canonical role inventory')
        node = nodes[0]
        path = pin(s['worker_identities'][role])
        require(path.is_relative_to(PILOT) and path.name == f'pool-a-{role}.json', 'Wrong original role probe')
        proof = read(path)
        mounts = [m for m in proof['data_mounts'] if m['path'] == root]
        require(node['host_key_verified'] is True and node['owned_by_campaign'] is True and node['cluster_id'] == 'pool-a' and
                node['work_root'] == root and node['ssh_alias'] == node['id'] and
                proof['read_only'] is True and not proof['errors'] and proof['instance_id'] == node['host_id'] and
                'host:'+proof['instance_id'] in cell['resource_ids'] and proof['hostname'] == node['hostname'] and
                re.fullmatch(r'[a-f0-9-]{36}', proof['boot_id']) and len(mounts) == 1 and
                mounts[0]['is_mount'] is True and type(mounts[0]['device']) is int and mounts[0]['device'] > 0,
                'Original role ownership, boot or device evidence differs')
        require(all('disk:'+d in cell['resource_ids'] for d in node['data_disk_ids']), 'Original role disk is not locked')
        result[role] = {'hostname': proof['hostname'], 'boot_id': proof['boot_id'], 'data_device': mounts[0]['device']}
    return result


def validate_quiescence(proof, cell, clock, fresh=True):
    require(proof['schema'] == 'c123-pool-a-retention-quiescence-v1' and proof['passed'] is True and
            proof['reviewed'] is True and proof['pool'] == 'pool-a' and
            set(proof['resource_ids']) == set(cell['resource_ids']) and
            all(proof[k] is True for k in ('no_active_pool_lease', 'agent_slot_empty', 'all_workload_processes_exited',
                                           'all_mounts_absent')) and
            all(type(proof[k]) is int and proof[k] == 0 for k in ('pending_upload_bytes', 'pending_upload_operations')),
            'Actual reviewed A quiescence and zero debt required')
    observed = datetime.fromisoformat(proof['observed_at'])
    require(observed.tzinfo is not None and 0 <= (clock-observed).total_seconds() and
            (not fresh or (clock-observed).total_seconds() <= 900), 'A quiescence evidence is stale or future-dated')
    require(re.fullmatch(r'ax-pilot-pa-[a-z0-9-]+\.service', proof['unit']) and
            re.fullmatch(r'[a-f0-9]{32}', proof['manager']['InvocationID']) and
            int(proof['manager']['InvocationID'], 16) != 0, 'Exact latest A invocation required')
    manager = proof['manager']
    require(set(manager) == set(MANAGER_FIELDS) and manager['LoadState'] == 'loaded' and
            manager['MainPID'] == manager['ControlPID'] == '0' and
            (manager['ActiveState'], manager['SubState']) in TERMINAL, 'A manager boundary is not terminal')


def listed_units(output):
    units = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        columns = line.split()
        require(len(columns) >= 4 and PILOT_UNIT.fullmatch(columns[0]) and columns[0] not in units,
                'Unrecognized pilot service in manager output')
        units[columns[0]] = tuple(columns[2:4])
    return units


def show_unit(unit, fields):
    out = subprocess.check_output(['systemctl', 'show', unit, '--all', *['-p'+f for f in fields]], text=True)
    entries = [line.split('=', 1) for line in out.splitlines()]
    require(all(len(pair) == 2 for pair in entries) and len(entries) == len(fields) and
            {pair[0] for pair in entries} == set(fields), 'Incomplete manager evidence: '+unit)
    return dict(entries)


def require_empty_cgroup(unit, group):
    if not group:
        return  # The manager reports no associated cgroup for this unit.
    require(re.fullmatch(r'/[A-Za-z0-9_.@/-]+', group) and Path(group).name == unit,
            'Unexpected workload cgroup: '+unit)
    path = CGROUP_ROOT/group.lstrip('/')
    require(path.is_relative_to(CGROUP_ROOT) and path.resolve() == path, 'Unsafe workload cgroup: '+unit)
    try:
        events = (path/'cgroup.events').read_text().splitlines()
    except FileNotFoundError:
        require(not path.exists(), 'Missing workload cgroup evidence: '+unit)
        return  # An empty cgroup may disappear between the systemd and proc checks.
    require([row for row in events if row.startswith('populated ')] == ['populated 0'],
            'Workload cgroup is populated: '+unit)


def command_identity(raw):
    # Exclude the status, timestamps and PID appended by systemd to ExecStart.
    start = re.fullmatch(r'\{ path=(\S+) ; argv\[\]=(.*?) ; ignore_errors=no ; .* \}', raw)
    require(start is not None and raw.count('argv[]=') == 1, 'Expected one direct resident ExecStart')
    executable, args = start.groups()
    argv = shlex.split(args)
    require(bool(argv) and argv[0] == executable, 'Resident executable and argv differ')
    return argv, hashlib.sha256(encode(argv)).hexdigest()


def check_resident(unit, reviewed, state):
    require(set(reviewed) == {'invocation_id', 'fragment', 'drop_ins', 'exec_start_sha256', 'script'},
            'Exact resident service pins required: '+unit)
    invocation = reviewed['invocation_id']
    require(state['LoadState'] == 'loaded' and (state['ActiveState'], state['SubState']) == ('active', 'running') and
            state['MainPID'].isdigit() and int(state['MainPID']) > 0 and state['ControlPID'] == '0' and
            state['NeedDaemonReload'] == 'no' and state['InvocationID'] == invocation and
            re.fullmatch(r'[a-f0-9]{32}', invocation) and int(invocation, 16) != 0,
            'Resident service invocation or loaded definition changed: '+unit)
    pin(reviewed['fragment'], Path(state['FragmentPath']))
    drop_ins = shlex.split(state['DropInPaths'])
    require(drop_ins == [entry['path'] for entry in reviewed['drop_ins']], 'Resident drop-in set changed: '+unit)
    for entry in reviewed['drop_ins']:
        pin(entry)
    argv, sha = command_identity(state['ExecStart'])
    require(sha == reviewed['exec_start_sha256'], 'Resident command changed: '+unit)
    if unit == ASSETS:
        require(reviewed['script'] is None and argv == ['/usr/bin/python3', '-m', 'http.server', '18080',
                '--bind', '10.20.0.10', '--directory', str(PILOT/'assets')], 'Unexpected immutable assets command')
    else:
        script = pin(reviewed['script'])
        require(script.is_relative_to(PILOT) and script.name == 'session-relay.py' and
                argv[0] in {'/usr/bin/python3', '/usr/bin/python3.12'}, 'Unexpected C4 relay script')
        args = iter(argv[1:])
        require(next((arg for arg in args if arg not in {'-B', '-u', '-I'}), None) == str(script),
                'Resident relay does not start the pinned script')
    return {'invocation_id': invocation, 'main_pid': int(state['MainPID']), 'exec_start_sha256': sha}


def services_idle(s):
    residents = s.get('resident_units', {})
    require(isinstance(residents, dict) and set(residents) == RESIDENT_UNITS and not s.get('allow_live_b_units'),
            'Only exact asset and C4 relay resident exceptions may be reviewed')
    units = listed_units(subprocess.check_output(['systemctl', 'list-units', '--all', '--plain', '--full',
        '--no-legend', '--no-pager', '--type=service', 'ax-pilot-*.service'], text=True))
    active = [unit for unit, state in units.items() if unit not in RESIDENT_UNITS and state not in TERMINAL]
    require(not active, 'Pilot service still active: '+', '.join(active))
    require(RESIDENT_UNITS <= units.keys(), 'Reviewed resident services missing from manager inventory')
    result = {}
    for unit, state in sorted(units.items()):
        fields = RESIDENT_FIELDS if unit in RESIDENT_UNITS else SERVICE_FIELDS
        current = show_unit(unit, fields)
        require((current['ActiveState'], current['SubState']) == state, 'Pilot service changed during check: '+unit)
        if unit in RESIDENT_UNITS:
            result[unit] = check_resident(unit, residents[unit], current)
        else:
            require(current['MainPID'] == current['ControlPID'] == '0', 'Pilot service has a live PID: '+unit)
            require_empty_cgroup(unit, current['ControlGroup'])
    jobs = subprocess.check_output(['systemctl', 'list-jobs', '--plain', '--full', '--no-legend', '--no-pager'], text=True)
    if jobs.strip() == 'No jobs running.':
        return result
    for line in jobs.splitlines():
        if line.strip():
            columns = line.split()
            require(len(columns) >= 4 and columns[0].isdigit(), 'Unrecognized manager job output')
            require(not PILOT_UNIT.fullmatch(columns[1]), 'Pending pilot manager job: '+columns[1])
    return result


def lock_resources(plan):
    clusters = plan['config']['clusters']
    require(len(clusters) == len(POOLS) and {c['id'] for c in clusters} == POOLS and
            {c['pool'] for c in plan['cells']} == POOLS, 'Canonical two-pool plan required')
    resources = {'pool:'+pool for pool in POOLS}
    for row in (*clusters, *plan['cells']):
        ids = row['resource_ids']
        require(isinstance(ids, list) and ids and all(isinstance(x, str) and x for x in ids),
                'Missing canonical physical resource IDs')
        resources.update(ids)
    return sorted(resources)


@contextmanager
def two_pool_guard(directory, resources):
    """Use the frozen runtime's exact canonical lock keys and flock mode."""
    import fcntl
    require(directory.resolve(strict=True) == directory and directory.is_dir(), 'Canonical locks were redirected')
    with ExitStack() as stack:
        for resource in resources:
            key = hashlib.sha256(resource.encode()).hexdigest()+'.lock'
            fd = os.open(directory/key, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            stack.callback(os.close, fd)
            require(stat.S_ISREG(os.fstat(fd).st_mode), 'Unsafe canonical lock file: '+key)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('Another preparation or cell owns physical resource: '+resource) from error
        yield


def require_no_leases(s):
    controls = {Path(x) for x in s['lease_controls']}
    require(controls == {p.parent for p in PILOT.glob('*/state.sqlite')} and {CANONICAL, ORIGINAL} <= controls,
            'Current coordinator state inventory differs from review')
    for path in sorted(controls):
        database = path/'state.sqlite'
        require(path.resolve(strict=True) == path and database.resolve(strict=True) == database and database.is_file(),
                'Unexpected coordinator lease state path')
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
            leases = db.execute('SELECT pool, cell_id FROM cell_leases ORDER BY pool, cell_id').fetchall()
        require(not leases, 'Pilot resources are leased in '+str(path)+': '+str(leases))
    return sorted(map(str, controls))


def boundary(s, cell, fresh=True):
    path = pin(s['quiescence'])
    require(path.is_relative_to(PREP), 'Quiescence receipt must live under owned preparation')
    proof = read(path)
    validate_quiescence(proof, cell, now(), fresh)
    require(len({entry['path'] for entry in proof['source_pins']}) >= 2,
            'Distinct actual close and live-check source receipts are required')
    for entry in proof['source_pins']:
        require(pin(entry).is_relative_to(PILOT), 'Foreign quiescence evidence')
    controls = require_no_leases(s)
    manager = show_unit(proof['unit'], MANAGER_FIELDS)
    require(manager == proof['manager'], 'Latest A service invocation or terminal state changed')
    residents = services_idle(s)
    return {'manager': manager, 'reviewed_quiescence': s['quiescence'], 'lease_controls': controls,
            'resident_units': residents}


def disk_room(path, bytes_needed, inodes_needed):
    vfs = os.statvfs(path)
    available = vfs.f_bavail * vfs.f_frsize
    require(available >= bytes_needed and vfs.f_favail >= inodes_needed, 'Coordinator archive capacity is insufficient')
    return {'free_bytes': available, 'free_inodes': vfs.f_favail, 'required_free_bytes': bytes_needed}


def dispatch(ssh, role, tool_bytes, envelope, directory):
    target = 'pool-a-'+role
    serialized = base64.b64encode(encode(envelope)).decode()
    require(len(serialized) <= 90000, 'Worker request exceeds bounded SSH command size')
    args = ['ssh', '-F', str(ssh), *SSH_OPTIONS, target,
            shlex.join(['/usr/bin/python3', '-B', '-', '--dispatch', serialized])]
    with (directory/'stdout.json').open('xb') as stdout, (directory/'stderr.log').open('xb') as stderr:
        call = subprocess.run(args, input=tool_bytes, stdout=stdout, stderr=stderr, check=False)
    require(call.returncode == 0, f'Worker {role} {envelope["phase"]} failed; inspect {directory}')
    result = read(directory/'stdout.json')
    require(result['passed'] is True and result['phase'] == envelope['phase'] and result['role'] == role,
            'Worker returned a foreign or failed receipt')
    return result


def coordinator_manifest(selected, old_root):
    result = {}
    for trial in selected['trials']:
        for name, item in trial['coordinator_files'].items():
            require(name not in result, 'Duplicate coordinator file across trials')
            result[name] = item
        for entry in trial['contracts'].values():
            p = pin(entry)
            result[p.relative_to(old_root).as_posix()] = {'bytes': p.stat().st_size, 'sha256': entry['sha256']}
    closing = read(pin(selected['closing']))
    entries = [selected['closing'], selected['overhead'], closing['sample'],
               *(v['evidence'] for v in closing['closure'].values())]
    for entry in entries:
        p = pin(entry)
        result[p.relative_to(old_root).as_posix()] = {'bytes': p.stat().st_size, 'sha256': entry['sha256']}
    return result


def bind_packed_manifest(archive, expected, manifest_name):
    with tarfile.open(archive, 'r:gz') as package:
        item = package.next()
        require(item is not None and item.name == manifest_name and item.isfile() and item.size <= 16*MIB,
                'Bounded archive manifest required')
        manifest = json.load(package.extractfile(item))
        require(manifest['files'] == expected, 'Archived controller bytes differ from the frozen draft')


def collect(ssh, role, result, preflight, directory, archive_check, cell, configs):
    require(result['identity'] == preflight['identity'] and result['sources'] == preflight['sources'] and
            result['scratch'] == preflight['scratch'] and result['raw_staging_created'] is False and
            len(result['archives']) == len(configs), 'Worker receipt differs from approved capacity and source')
    retained = []
    for node, cfg, source in zip(result['archives'], configs, result['sources'], strict=True):
        tag = cfg['tag']; archive = node['archive']
        expected = Path(result['scratch'])/(tag+'.tar.gz')
        require(node['passed'] is True and node['tag'] == tag and node['mode'] == cfg['mode'] and node['role'] == role and
                archive['archive'] == str(expected) and archive['kind'] == f'calibration-{tag}-{role}' and
                archive['cell_id'] == cell and 0 < archive['bytes'] <= source['bound_bytes'], 'Foreign worker archive')
        dest = directory/(tag+'.tar.gz')
        require(not dest.exists() and not dest.is_symlink(), 'New coordinator download path required')
        args = ['scp', '-F', str(ssh), *SSH_OPTIONS, 'pool-a-'+role+':'+str(expected), str(dest)]
        subprocess.run(args, check=True)
        checked = archive_check.verify(dest, archive['sha256'])
        require(checked['bytes'] == archive['bytes'] and checked['cell_id'] == cell and checked['kind'] == archive['kind'] and
                checked['file_members'] == source['files'], 'Independent coordinator archive verification differs')
        write(directory/(tag+'.check.json'), checked)
        retained.append(ref(dest))
    return retained


def run(request, out, runner):
    package = new_paths(Path(request['path']), out)
    s, draft, selected, repo, plan, cell, ssh, tool, adapter, node, runtime = load_input(request, runner)
    sys.path.insert(0, str(repo))
    from bench import archive_check
    fleet = read(pin(s['fleet'], CANONICAL/'fleet.json'))
    identity = identities(s, cell, fleet)
    resources = lock_resources(plan)
    boundary(s, cell)  # Do not contend with a still-running handoff for its next lock.
    worker_started = False
    with two_pool_guard(CANONICAL/'runner-locks', resources):
        handoff = dict(boundary(s, cell), locked_resources=resources)
        rebuilt = adapter['build'](ORIGINAL, Path(draft['node_retainer']['path']))
        require(hashlib.sha256(encode(rebuilt)).hexdigest() == s['draft']['sha256'], 'Frozen original source changed after draft')
        controller = coordinator_manifest(selected, ORIGINAL)
        ctrl_bound = node['archive_bound'](sum(v['bytes'] for v in controller.values()), len(controller))
        disk_room(out.parent, GIB * 2 + 2 * ctrl_bound, 128)
        out.mkdir(mode=0o700, exist_ok=False)
        write(out/'started.json', dict(request=request, draft=s['draft'], cell_id=s['cell_id'], handoff=handoff,
                   new_measurements=False, automatic_retry=False))
        configs = {role: [t['node_pack'][role] for t in selected['trials'] if role in t['node_pack']] for role in ROLE_ROOTS}
        envelopes = {role: dict(schema='c123-role-retention-dispatch-v1', reviewed=True, role=role, run=out.parent.name,
                     identity=identity[role], retention_code_dir=repo.name, runtime_files=runtime,
                     retainer_sha256=draft['node_retainer']['sha256'], configs=configs[role], cell_id=s['cell_id'])
                     for role in ROLE_ROOTS}
        try:
            preflight = {}
            for role, envelope in envelopes.items():
                boundary(s, cell, fresh=False)
                work = out/(role+'-preflight'); work.mkdir()
                preflight[role] = dispatch(ssh, role, tool.read_bytes(), dict(envelope, phase='preflight'), work)
                require(preflight[role]['bytes_written'] == 0 and preflight[role]['scratch'] == '/var/tmp/'+out.parent.name and
                        [v['tag'] for v in preflight[role]['sources']] == [v['tag'] for v in configs[role]],
                        'Read-only worker inventory did not match exact trials')
            total = ctrl_bound + sum(p['bound_bytes'] for r in preflight.values() for p in r['sources'])
            space = disk_room(out, 2 * GIB + 2 * total + 64 * MIB, 128)
            write(out/'capacity.json', dict(space, raw_stage_copies=False, planned_nested_bound_bytes=total))
            local_archive = out/'controller.tar.gz'
            for name, expected in controller.items():
                p = ORIGINAL/name
                require(ref(p)['sha256'] == expected['sha256'] and p.stat().st_size == expected['bytes'], 'Changed controller evidence')
            ctl = archive_check.pack(ORIGINAL, sorted(controller), local_archive, s['cell_id'], 'original-calibration-coordinator')
            bind_packed_manifest(local_archive, controller, archive_check.MANIFEST)
            write(out/'controller.check.json', ctl)
            retained = {}
            for role, envelope in envelopes.items():
                boundary(s, cell, fresh=False)
                work = out/role; work.mkdir()
                worker_started = True
                r = dispatch(ssh, role, tool.read_bytes(), dict(envelope, phase='pack', preflight=preflight[role]), work)
                retained[role] = collect(ssh, role, r, preflight[role], work, archive_check, s['cell_id'], configs[role])
            require(sum(map(len, retained.values())) == 12, 'All original six trials and their role archives are required')
            write(out/'members.json', dict(cell_id=s['cell_id'], controller=ref(local_archive), roles=retained,
                  closure=s['quiescence'], independent_oss_verification_required=True, new_measurements=False))
            boundary(s, cell, fresh=False)
            members = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())
            checked = archive_check.pack(out, members, package, s['cell_id'], 'original-c123-six-calibration-trials')
            boundary(s, cell, fresh=False)
            write(out/'result.json', dict(passed=True, archive=checked, worker_archives=12, source_trial_count=6,
                  new_measurements=False, automatic_retry=False, scratch_preserved=True,
                  independent_oss_verification_required=True))
            print(json.dumps(checked))
        except Exception as error:
            write(out/'failed.json', dict(passed=False, error=str(error), new_measurements=False, automatic_retry=False,
                  remote_pack_attempted=worker_started, recheck_worker_quiescence_before_next_activity=worker_started))
            raise


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--request-sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--apply-retention', action='store_true', required=True)
    a = parser.parse_args()
    run(dict(path=str(a.request), sha256=a.request_sha256), a.out, Path(__file__).resolve())
