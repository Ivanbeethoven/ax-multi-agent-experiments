#!/usr/bin/env python3
"""Retain all six original calibration trials at an explicitly closed boundary.

No workload, mount, service stop, cleanup, model request or next-cell launch.
Run on the coordinator after the named one-cell service exits successfully.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys

NODE = (json.loads(base64.b64decode(sys.argv[sys.argv.index('--node-config')+1]))
        if '--node-config' in sys.argv else None)
REPO = Path(NODE['code']) if NODE else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench import archive_check, experiment_step
from bench.runtime.common import alive, digest, load, mounts, pinned, write_new
from bench.runtime.observe import audit, mount_lifecycle
if not NODE:
    from bench.runtime.pilot_pools import (pool_of, alias, cell_resources,
                                           active_conflicts, resource_guard)

ROOTS = dict(client='/srv/ax-client', metadata='/srv/ax-metadata', objects='/srv/ax-objects-0')


def file_list(root, directories):
    """Explicit evidence subtrees only; caches and dead IPC sockets are not data."""
    files = []; skipped = []
    for name in directories:
        archive_check.safe(name)
        directory = root/name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Missing or linked evidence directory: '+name)
        for parent, dirs, names in os.walk(directory, followlinks=False):
            dirs.sort(); names.sort()
            if 'cache' in dirs:
                skipped.append((Path(parent)/'cache').relative_to(root).as_posix())
                dirs.remove('cache')
            for item in dirs+names:
                path = Path(parent)/item
                relative = path.relative_to(root).as_posix()
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    raise ValueError('Linked evidence rejected: '+relative)
                if stat.S_ISREG(mode):
                    files.append(relative)
                elif not stat.S_ISDIR(mode):
                    if not stat.S_ISSOCK(mode):
                        raise ValueError('Unexpected special evidence file: '+relative)
                    skipped.append(relative)
    if not files or len(files) != len(set(files)):
        raise ValueError('Nonempty, nonoverlapping original evidence required')
    return sorted(files), sorted(skipped)


def stable_snapshot(root, files):
    return {name:dict(bytes=(root/name).stat().st_size, sha256=digest(root/name)) for name in files}


def assert_unchanged(root, directories, files, skipped, before):
    if file_list(root, directories) != (files, skipped) or stable_snapshot(root, files) != before:
        raise ValueError('Original evidence mutated during retention')


def pack_node_files(root, files, out, cell_id, kind):
    # The archive must be outside its packed root, even when retention uses the
    # same data disk as the original worker evidence.
    stage = out/'raw'
    stage.mkdir()
    for name in files:
        archive_check.safe(name)
        target = stage/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root/name, target)
    return archive_check.pack(stage, files, out/'raw.tar.gz', cell_id, kind)


def assert_closed_mount(directory):
    value = mount_lifecycle(dict(mount_directory=str(directory)))
    if not value or value['state'] != 'cleanly-stopped':
        raise ValueError('Original mount is not normally closed')
    process = load(directory/'process.json')
    if alive(process['supervisor']):
        raise ValueError('Original mount supervisor remains live')
    return dict(directory=str(directory), receipt=value, process=process)


def node_pack(s):
    root = Path(s['root']); tag = s['tag']; role = s['role']
    if role not in ROOTS or str(root) != ROOTS[role] or not re.fullmatch(r'cal-[a-z0-9-]{3,28}', tag):
        raise ValueError('Exact pilot role and original calibration tag required')
    out = root/'retained-calibrations'/s['bundle']/tag
    if not re.fullmatch(r'[a-z0-9-]{3,70}', s['bundle']):
        raise ValueError('Owned, simple retention bundle required')
    out.mkdir(parents=True, exist_ok=False)
    if role == 'client' and Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip():
        raise ValueError('Agent slot remains occupied; retention would disturb measurement')
    for entry in s['code_pins']:
        pinned(entry)
    directories = []; closed = []; audits = []
    if s['mode'] == 'on':
        observer = root/'o'/tag
        process = load(observer/'ready.json')['process']
        if alive(process):
            raise ValueError('Original observer remains live')
        audits.append(audit(observer, 30, s['marks']))
        if digest(root/'q'/(tag+'-observer')/'observer.json') != s['observer_sha256']:
            raise ValueError('Original observer spec changed')
        closed.append(process)
        directories += ['o/'+tag, 'q/'+tag+'-observer']
    debt = []
    if role == 'client':
        pinned(s['worker_spec'])
        for suffix in ('-p', '', '-v'):
            closed.append(assert_closed_mount(root/'m'/(tag+suffix)))
        # All three mounts use one asset mount point. Check the current table,
        # without treating a reused mount ID as the original process.
        point = load(root/'m'/tag/'ready.json')['mount']['point']
        if mounts(point):
            raise ValueError('Calibration mount point is occupied')
        for path in sorted((root/'j'/tag).rglob('process.json')):
            process = load(path)['process']
            if alive(process):
                raise ValueError('Original workload process remains live')
            closed.append(process)
        if load(root/'j'/tag/'finished.json')['passed'] is not True:
            raise ValueError('Original worker did not pass')
        receipt = load(root/'q'/tag/'debt/result.json')
        if receipt.get('passed') is not True or len(receipt['samples']) != 2:
            raise ValueError('Two original zero-debt observations required')
        for entry in receipt['samples']:
            sample = load(pinned(entry))
            if any(type(sample[k]) not in (int, float) or sample[k] != 0
                   for k in ('pending_upload_bytes', 'pending_upload_operations')):
                raise ValueError('Original upload debt is not numeric zero')
            debt.append(dict(evidence=entry, at=sample['at']))
        if (datetime.fromisoformat(debt[1]['at'])-datetime.fromisoformat(debt[0]['at'])).total_seconds() < 1:
            raise ValueError('Zero-debt observations are less than one second apart')
        directories += ['q/'+tag, 'm/'+tag+'-p', 'm/'+tag, 'm/'+tag+'-v', 'w/'+tag, 'j/'+tag]
    if not directories:
        raise ValueError('Off trials retain client evidence only')
    files, skipped = file_list(root, directories)
    before = stable_snapshot(root, files)
    checked = pack_node_files(root, files, out, s['cell_id'], 'calibration-'+tag+'-'+role)
    assert_unchanged(root, directories, files, skipped, before)
    result = dict(passed=True, pool=s.get('pool', 'pool-a'), role=role, tag=tag, mode=s['mode'], archive=checked,
                  processes_and_mounts=closed, observer_audits=audits, debt_samples=debt,
                  omitted_cache_and_dead_sockets=skipped,
                  scope='Closed original calibration evidence, not a new performance trial')
    write_new(out/'result.json', result)
    print(json.dumps(result))


def idle(control, unit, cell=None, plan=None):
    if not re.fullmatch(r'ax-pilot-[a-z0-9-]+', unit):
        raise ValueError('Explicit owned original one-cell unit required')
    raw = subprocess.check_output(['systemctl','show',unit+'.service',
        '-p','LoadState','-p','ActiveState','-p','SubState','-p','ExecMainStatus','-p','ExecMainCode',
        '-p','MainPID','-p','Result','-p','RemainAfterExit'], text=True)
    state = dict(line.split('=',1) for line in raw.splitlines())
    # A successful transient service can already have been garbage collected.
    # The pinned completed invocation and closed formal cell are checked by main.
    inactive = (state.get('ActiveState') == 'inactive' and state.get('SubState') == 'dead' and
        (state.get('LoadState') == 'not-found' or
         (state.get('LoadState') == 'loaded' and state.get('ExecMainStatus') == '0' and state.get('ExecMainCode') == '1')))
    # RemainAfterExit is used by some campaigns to preserve systemd evidence.
    # Only accept the exact no-process, normal-exit state.
    exited = all(state.get(key) == value for key, value in dict(LoadState='loaded',
        ActiveState='active', SubState='exited', RemainAfterExit='yes', MainPID='0',
        Result='success', ExecMainCode='1', ExecMainStatus='0').items())
    if not (inactive or exited):
        raise ValueError('Original one-cell service has not exited normally')
    if cell is None:
        # Keep the original conservative boundary for existing callers.
        with sqlite3.connect((control/'state.sqlite').as_uri()+'?mode=ro', uri=True) as db:
            if db.execute('SELECT COUNT(*) FROM cell_leases').fetchone()[0]:
                raise ValueError('A cell remains reserved')
    else:
        plan = plan or load(control/'suite/plan.json')
        matches = [candidate for candidate in plan['cells'] if candidate['id']==cell['id']]
        if len(matches)!=1 or matches[0]!=cell:
            raise ValueError('Retention cell differs from the frozen plan')
        if active_conflicts(control, cell, plan):
            raise ValueError('A cell remains reserved on this pool or shared resources')
        resources = set(cell_resources(plan, cell))
    for started in (control/'one-cell-runs').glob('*/started.json'):
        other = None
        if cell is not None:
            value = load(started)
            matches = [candidate for candidate in plan['cells']
                       if isinstance(value, dict) and candidate['id']==value.get('cell_id')]
            if len(matches)!=1:
                raise ValueError('Unknown cell in original started invocation: '+str(started))
            other = matches[0]
            if value.get('pool', pool_of(other))!=pool_of(other):
                raise ValueError('Original started invocation pool differs from the frozen plan')
        if any((started.parent/name).is_file() for name in ('result.json','failed.json')):
            continue
        if (cell is None or pool_of(other)==pool_of(cell) or
                resources.intersection(cell_resources(plan, other))):
            raise ValueError('Another original one-cell invocation remains unfinished')
    return state


def observer_roles(cohort, pool):
    result = {}
    for observer in cohort['observers']:
        role = observer['id'].removeprefix(pool+'-')
        if role not in ROOTS or observer['id']!=alias(pool, role) or role in result:
            raise ValueError('Original observer does not uniquely belong to the cell pool')
        result[role] = observer
    return result


def main():
    if NODE:
        node_pack(NODE); return
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('request','request-sha256','completed-sha256','unit','out'):
        p.add_argument('--'+name, required=True)
    a = p.parse_args()
    request_ref = dict(path=a.request, sha256=a.request_sha256)
    request = load(pinned(request_ref)); control = Path(request['control'])
    completed_ref = dict(path=str(Path(request['out'])/'result.json'), sha256=a.completed_sha256)
    completed = load(pinned(completed_ref))
    if request.get('reviewed') is not True or completed.get('passed') is not True or completed['cell_id'] != request['cell_id']:
        raise ValueError('Original completed cell and reviewed request required')
    if load(Path(request['out'])/'started.json')['request'] != request_ref:
        raise ValueError('Completed invocation used another request')
    plan = load(control/'suite/plan.json')
    cells = [cell for cell in plan['cells'] if cell['id']==request['cell_id']]
    if len(cells)!=1:
        raise ValueError('Exactly one original cell in the frozen plan required')
    cell = cells[0]
    with resource_guard(control, cell, plan):
        retain(a, request_ref, request, completed_ref, completed, control, cell, plan)


def retain(a, request_ref, request, completed_ref, completed, control, cell, plan):
    pool = pool_of(cell)
    idle_state = idle(control, a.unit, cell, plan)
    slot = subprocess.check_output(['ssh','-F',str(control/'ssh.conf'),alias(pool, 'client'),
        'cat /sys/fs/cgroup/ax-slot-1/cgroup.procs'], text=True)
    if slot.strip():
        raise ValueError('Agent slot remains occupied; do not start evidence hashing')
    closing = load(pinned(completed['closing']))
    if closing['outcome'] != 'complete' or closing['cell_id'] != request['cell_id']:
        raise ValueError('Original formal cell is not normally complete')
    retention = closing['retention']
    experiment_step.verify(retention['root'], retention['manifest']['path'], retention['manifest']['sha256'])
    trials = request['trials']
    if len(trials) != 6 or [t['mode'] for t in trials] != ['off','on','on','off','off','on']:
        raise ValueError('All six predeclared calibration trials required')
    if len({t['attempt'] for t in trials}) != 6 or len({t['out'] for t in trials}) != 6:
        raise ValueError('Original trials cannot be reused')
    out = Path(a.out)
    if (not out.is_absolute() or out.exists() or out.is_symlink() or
            out.parent != control/'retained-calibrations' or
            not re.fullmatch(r'[a-z0-9-]{3,70}', out.name)):
        raise ValueError('New owned output under control/retained-calibrations required')
    out.mkdir(parents=True)
    (out/'executed-source.py').write_bytes(Path(__file__).read_bytes())
    write_new(out/'started.json', dict(at=datetime.now(timezone.utc).isoformat(), request=request_ref,
        completed=completed_ref, original_unit=a.unit, idle=idle_state, automatic_retry=False))
    source = Path(__file__).read_bytes(); results = []
    ssh_config = control/'ssh.conf'
    for name, entry in request['tools'].items():
        target = out/'executed-tools'/(name+'.py'); target.parent.mkdir(exist_ok=True)
        shutil.copyfile(pinned(entry), target)
    for trial in trials:
        idle(control, a.unit, cell, plan)
        tag = trial['attempt']; original = Path(trial['out'])
        if original != control/'calibration'/tag:
            raise ValueError('Original trial lies outside calibration directory')
        end = load(original/'finished.json'); cohort = load(original/'cohort.json')
        for entry in end.values():
            if isinstance(entry, dict) and set(('path','sha256')).issubset(entry):
                pinned(entry)
        if end.get('passed') is not True or load(pinned(end['cohort'])).get('passed') is not True:
            raise ValueError('Only normally passed original trials may use this retainer')
        if load(original/'started.json')['cell_id'] != request['cell_id']:
            raise ValueError('Original calibration belongs to another cell')
        directory = out/tag; directory.mkdir()
        before_files, skipped = file_list(control, ['calibration/'+tag])
        before = stable_snapshot(control, before_files)
        for name in before_files:
            target = directory/'controller'/Path(name).relative_to('calibration/'+tag)
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(control/name, target)
        for key in ('inventory','metric_contract','workload_contract','allocation_qualification',
                    'hardware_qualification','input_qualification','filesystem_qualification'):
            entry = cohort[key]; original_ref = pinned(entry)
            target = directory/'contracts'/(key+'.json'); target.parent.mkdir(exist_ok=True)
            shutil.copyfile(original_ref, target)
        observers = observer_roles(cohort, pool)
        roles = ['client'] if trial['mode']=='off' else list(ROOTS)
        for role in roles:
            peer = cohort['workers'][0]['peer'] if role=='client' else observers[role]['peer']
            config = dict(code=peer['repo'], pool=pool, root=ROOTS[role], role=role, tag=tag, mode=trial['mode'],
                bundle=out.name, cell_id=request['cell_id'], marks=cell['observation_marks'],
                code_pins=peer['pins'],
                worker_spec=cohort['workers'][0]['spec'] if role=='client' else None,
                observer_sha256=observers[role]['spec']['sha256'] if role in observers else None)
            encoded = base64.b64encode(json.dumps(config).encode()).decode()
            argv = ['ssh','-F',str(ssh_config),alias(pool, role),
                    shlex.join(['python3','-','--node-config',encoded])]
            intent = directory/(role+'-intent.json'); write_new(intent, dict(argv=argv, automatic_retry=False))
            with (directory/(role+'-stdout')).open('xb') as stdout, (directory/(role+'-stderr')).open('xb') as stderr:
                code = subprocess.run(argv, input=source, stdout=stdout, stderr=stderr).returncode
            write_new(directory/(role+'-exit.json'), dict(exit_code=code))
            if code:
                raise RuntimeError('Retain original failure; inspect '+str(directory))
            result = json.loads((directory/(role+'-stdout')).read_text())
            archive = result['archive']; target = directory/(role+'.tar.gz')
            subprocess.run(['scp','-F',str(ssh_config),alias(pool, role)+':'+archive['archive'],str(target)], check=True)
            checked = archive_check.verify(target, archive['sha256'])
            write_new(directory/(role+'-archive-check.json'), checked)
        assert_unchanged(control, ['calibration/'+tag], before_files, skipped, before)
        results.append(dict(tag=tag, mode=trial['mode'], roles=roles, passed=True))
    for name, path in [('request.json', a.request), ('completed.json', completed_ref['path']),
                       ('plan.json', control/'suite/plan.json'), ('closing.json', completed['closing']['path'])]:
        shutil.copyfile(path, out/name)
    write_new(out/'result.json', dict(passed=True, at=datetime.now(timezone.utc).isoformat(),
        cell_id=request['cell_id'], trials=results, automatic_retry=False,
        independent_download_required=True, scope='Six complete calibration evidence sets, no new measurements'))
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())
    checked = archive_check.pack(out, files, out.parent/(out.name+'.tar.gz'), request['cell_id'], 'six-calibration-trials')
    write_new(out.parent/(out.name+'-archive-check.json'), checked)
    print(json.dumps(checked))


if __name__ == '__main__':
    main()
