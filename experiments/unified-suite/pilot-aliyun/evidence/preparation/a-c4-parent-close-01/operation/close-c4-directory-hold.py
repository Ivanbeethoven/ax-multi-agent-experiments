#!/usr/bin/env python3
"""Check one frozen C4 parent; --resume-parent sends only its pidfd SIGCONT.

Requires the original Off1 zombie with raw Linux wait status 0 and its closed,
fully verified evidence. The original parent alone reaps it, encounters the
existing empty trial-2 directory, and runs its own failure-release handler.
No repository imports, archive rehash, worker access, polling, or retries.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess

UNIT = 'ax-pilot-pa-c4-flat-reuse-01.service'
INVOCATION = 'd944eeaa8da34cc495ad8a993adade7f'
PARENT, PARENT_START = 161875, 6177165
CHILD, CHILD_START = 161969, 6177817
CONTROL = Path('/srv/ax-pilot-control/fork-control-01')
BATCH = CONTROL/'batch-a-flat-reuse-01'
OFF1 = CONTROL/'calibration/flat-off1'
TOOL = Path('/srv/ax-pilot-control/code-19/experiments/unified-suite/pilot-aliyun/scripts/run-c4-batch-pretrial-01.py')
TOOL_SHA = 'aefb4ad8763f46cb8a4cbd07669818a568019642814f2de3ca8d7d46483eacf6'
REQUEST = Path('/srv/ax-pilot-control/preparation/c4-aof-reconcile-01/batch.request.json')
REQUEST_SHA = 'bd28482c6bb3d46c288e791e11f3d8aee1462b00ec43516cc523496ccdcf9392'
REUSE = Path('/srv/ax-pilot-control/preparation/c4-aof-reconcile-01/reuse.request.reviewed.json')
REUSE_SHA = 'ce38b30bef6b499fb271609ca0d63a9f0761b97c4cafb99b51e4091ec5058549'
TRIAL_SHA = '9aa432faac25a7b3e304df35cd4671fcc099c194a595d12d741afcbd39211e32'
CELL, POOL, ATTEMPT = 'c5e97774bcffa69a-r1-native', 'pool-a', 'c4-a-flat-off1'
PROOFS = ('work', 'client_verify', 'parent_verify', 'native_exit', 'verify_exit', 'closure', 'cohort')
MAX_SMALL_BYTES = 16 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(path):
    p = Path(path)
    require(p.is_absolute() and p.resolve(strict=True) == p and not p.is_symlink(),
            'Noncanonical or symlink path: ' + str(p))
    return p


def read_small(path):
    p = canonical(path)
    require(p.is_file() and p.stat().st_size <= MAX_SMALL_BYTES, 'Small regular evidence file required: ' + str(p))
    with p.open('rb') as stream:
        raw = stream.read(MAX_SMALL_BYTES + 1)
    require(len(raw) <= MAX_SMALL_BYTES, 'Evidence grew beyond small-file limit')
    return raw


def ref(path):
    return dict(path=str(path), sha256=hashlib.sha256(read_small(path)).hexdigest())


def load(path):
    return json.loads(read_small(path))


def pinned(pin, expected=None):
    require(set(pin) == {'path', 'sha256'} and re.fullmatch('[a-f0-9]{64}', pin['sha256']), 'Invalid evidence pin')
    if expected is not None:
        require(pin['path'] == str(expected), 'Unexpected evidence path: ' + pin['path'])
    raw = read_small(pin['path'])
    require(hashlib.sha256(raw).hexdigest() == pin['sha256'], 'Evidence SHA changed: ' + pin['path'])
    return json.loads(raw)


def absent(path):
    require(not os.path.lexists(path), 'Original next stage or terminal evidence already exists: ' + str(path))


def parse_stat(raw, pid):
    # comm (field 2) can contain spaces and ')'; fields after the final ')' start at 3.
    first, separator, tail = raw.rpartition(') ')
    fields = tail.split()
    require(separator and first.startswith(str(pid) + ' (') and len(fields) >= 50, 'Invalid /proc stat identity')
    return dict(pid=pid, state=fields[0], ppid=int(fields[1]),
                starttime=int(fields[19]), raw_exit_status=int(fields[49]))


def process(pid):
    root = Path('/proc')/str(pid)
    value = parse_stat((root/'stat').read_text(), pid)
    value['cgroup'] = (root/'cgroup').read_text().splitlines()
    if pid == PARENT:
        value['argv'] = (root/'cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
    return value


def identity():
    fields = ('MainPID', 'ActiveState', 'SubState', 'InvocationID')
    raw = subprocess.check_output(['systemctl', 'show', UNIT, *['-p'+k for k in fields]], text=True)
    unit = dict(line.split('=', 1) for line in raw.splitlines())
    require(unit == dict(MainPID=str(PARENT), ActiveState='active', SubState='running', InvocationID=INVOCATION),
            'Original systemd unit/invocation is not active with the exact parent')
    parent, child = process(PARENT), process(CHILD)
    group = ['0::/system.slice/' + UNIT]
    argv = ['/usr/bin/python3', str(TOOL), '--request', str(REQUEST), '--sha256', REQUEST_SHA,
            '--reuse-request', str(REUSE), '--reuse-sha256', REUSE_SHA]
    require(parent['pid'] == PARENT and parent['starttime'] == PARENT_START and parent['state'] == 'T' and
            parent['argv'] == argv and parent['cgroup'] == group, 'Original stopped parent identity changed')
    require(child['pid'] == CHILD and child['ppid'] == PARENT and child['starttime'] == CHILD_START and
            child['state'] == 'Z' and child['raw_exit_status'] == 0 and child['cgroup'] == group,
            'Original Off1 child must be a zombie with raw exit status 0 (Linux stat field 52)')
    return dict(unit=unit, parent=parent, child=child)


def boundary(old):
    fence = canonical(BATCH/'trial-2')
    require(fence.is_dir() and not any(fence.iterdir()), 'Original trial-2 fence must remain an empty directory')
    for name in ('trial-1/exit.json', 'trial-2-request.json', 'failed.json', 'result.json', 'trials.json',
                 'admit', 'admit-request.json', 'formal', 'overhead', 'route.json'):
        absent(BATCH/name)
    for number in range(3, 7):
        absent(BATCH/('trial-'+str(number)))
        absent(BATCH/('trial-'+str(number)+'-request.json'))
    for trial in old['trials'][1:]:
        absent(Path(trial['out']))
    absent(CONTROL/'cells'/CELL)


def evidence():
    require(ref(TOOL)['sha256'] == TOOL_SHA, 'Frozen original parent tool changed')
    original = dict(path=str(REQUEST), sha256=REQUEST_SHA)
    old = pinned(original)
    pinned(dict(path=str(REUSE), sha256=REUSE_SHA))
    require(load(BATCH/'request.json') == old and old['schema'] == 'c4-fork-batch-v1' and
            old['reviewed'] is True and old['out'] == str(BATCH) and old['control'] == str(CONTROL) and
            old['cell_id'] == CELL and old['calibration_verification'] == 'touched-cold-v1' and
            len(old['trials']) == 6 and old['trials'][0]['mode'] == 'off' and
            old['trials'][0]['attempt'] == ATTEMPT and old['trials'][0]['out'] == str(OFF1), 'Frozen original batch differs')
    locks = load(BATCH/'locks-held.json')
    require(locks['request'] == original and locks['pool'] == POOL, 'Original locks receipt differs')
    boundary(old)
    trial_pin = dict(path=str(BATCH/'trial-1-request.json'), sha256=TRIAL_SHA)
    body = pinned(trial_pin)
    calibrator = old['tools']['calibrate']
    require(ref(calibrator['path']) == calibrator, 'Original calibrator changed')
    require(load(BATCH/'trial-1/intent.json') == dict(argv=['python3', calibrator['path'], '--request',
            trial_pin['path'], '--sha256', TRIAL_SHA], automatic_retry=False), 'Original child launch differs')
    require(load(OFF1/'request.json') == load(OFF1/'context.json') == body and
            body['purpose'] == 'observer-calibration-off' and body['attempt'] == ATTEMPT and
            body['verification_policy'] == 'full-reference-v1', 'Original Off1 request/context differs')
    end_pin = ref(OFF1/'finished.json')
    end = pinned(end_pin)
    require(end['passed'] is True and end['automatic_retry'] is False and end['pool'] == POOL and
            end['purpose'] == body['purpose'] and all(end[k] == body[k] for k in ('collector_name', 'collector_out')),
            'Original Off1 did not finish normally')
    q = OFF1/'raw/client/q'/ATTEMPT
    expected = dict(work=OFF1/'work-check.json', client_verify=q/'verify/result.json',
                    parent_verify=q/'verify/parent.json', native_exit=q/'exit.json',
                    verify_exit=q/'verify-exit.json', closure=OFF1/'closure.json', cohort=OFF1/'cohort/result.json')
    proofs = {k: pinned(end[k], expected[k]) for k in PROOFS}
    require(all(v['passed'] is True for v in proofs.values()), 'Original Off1 proof did not pass')
    require(proofs['cohort']['workload_passed'] is True and proofs['cohort']['purpose'] == body['purpose'],
            'Original Off1 cohort did not complete')
    for name in ('native_exit', 'verify_exit'):
        require(proofs[name]['exit_code'] == 0 and proofs[name]['mount_absent'] is True, 'Original mount did not exit normally')
    closed = proofs['closure']
    require(closed['all_exited'] is True and closed['all_unmounted'] is True and
            closed['pending_upload_bytes'] == closed['pending_upload_operations'] == 0, 'Original Off1 is not fully closed')
    full, parent = proofs['client_verify'], proofs['parent_verify']
    binding, identity_ = full['binding'], full['parent_identity']
    require(full['verification_policy'] == parent['verification_policy'] == 'full-reference-v1' and
            full['cell_id'] == parent['cell_id'] == binding['cell_id'] == CELL and
            full['child_name'] == ATTEMPT != old['formal_attempt'] and full['files'] == binding['files'] == 100000 and
            full['work_tag'] == binding['work_tag'] == 'child-01' and binding['arm'] == 'native' and
            binding['preparation'] == body['fork']['preparation'] and binding['asset']['path'] == old['asset']+'/asset.json' and
            binding['source_tree'] == parent['expected'] and binding['expected_child'] == full['expected_child'] and
            full['full_cold_child'] is True and full['parent_unchanged'] is True and parent['full_cold_parent'] is True and
            full['parent_matches'] is True and full['child_matches'] is True and
            full['full_reference'] is None and full['touched_paths'] is None and parent['parent_identity'] == identity_ and
            identity_['schema'] == 'c4-parent-stat-v1' and identity_['entries'] == full['namespace_entries']['parent']+1 and
            re.fullmatch('[a-f0-9]{64}', identity_['sha256']) and
            full['content_read'] == dict(parent=dict(files=100000, bytes=409600000),
                                         child=dict(files=100000, bytes=409591841)),
            'Actual complete 100k parent/child full-reference-v1 proof required')
    archives = pinned(end['archives'], OFF1/'archives.json')
    archive = canonical(OFF1/'archives/client.tar.gz')
    require(len(archives) == 1 and archives[0]['path'] == str(archive) and archive.is_file() and archive.stat().st_size > 0,
            'Original client archive missing')
    check_pin = ref(OFF1/'archives/client-check.json')
    checked = pinned(check_pin)
    require(checked['schema'] == 'suite-archive-check-v1' and checked['passed'] is True and
            checked['archive'] == str(archive) and checked['sha256'] == archives[0]['sha256'] and
            checked['bytes'] == archive.stat().st_size and checked['kind'] == 'client' and
            checked['cell_id'] == CELL and checked['deferred'] == [], 'Original verified archive receipt differs')
    return dict(original_request=original, trial_request=trial_pin, finished=end_pin,
                proofs={k: end[k] for k in PROOFS}, archives=end['archives'], archive_check=check_pin,
                archive_content_rehashed=False, batch_tool=ref(TOOL), fence=str(BATCH/'trial-2'))


def check():
    before = identity()  # A live cold-check child fails before archive/evidence inspection.
    verified = evidence()
    require(identity() == before, 'Process or unit changed during checks')
    return dict(identity=before, evidence=verified)


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == 'posix':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def run(out, resume_parent=False):
    out = Path(out)
    require(out.is_absolute() and out.resolve() == out and not out.is_symlink(), 'Fresh canonical receipt path required')
    canonical(out.parent)
    require(not out.is_relative_to(CONTROL) and out != REQUEST.parent and not out.is_relative_to(TOOL.parent),
            'Receipt must be independent of original control, request, and code directories')
    out.mkdir(exist_ok=False)
    write_new(out/'intent.json', dict(schema='c4-directory-hold-parent-close-v1',
        at=datetime.now(timezone.utc).isoformat(), unit=UNIT, invocation=INVOCATION,
        parent_pid=PARENT, parent_starttime=PARENT_START, child_pid=CHILD, child_starttime=CHILD_START,
        requested_action='pidfd-SIGCONT-original-parent' if resume_parent else 'check-only',
        automatic_retry=False, original_trial_exit_written_by='original-parent-only'))
    sent = False
    try:
        checked = check()
        write_new(out/'check.json', checked)
        if resume_parent:
            require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'Linux pidfd APIs required; no numeric-PID fallback')
            fd = os.pidfd_open(PARENT, 0)
            try:
                rechecked = check()
                require(rechecked == checked, 'Original evidence or identity changed after pidfd_open')
                write_new(out/'signal-intent.json', dict(signal='SIGCONT', parent_pid=PARENT, checked=rechecked))
                require(identity() == checked['identity'], 'Parent/child/unit changed immediately before signal')
                signal.pidfd_send_signal(fd, signal.SIGCONT, None, 0)
                sent = True
            finally:
                os.close(fd)
        result = dict(passed=True, signal_sent=sent, signal='SIGCONT' if sent else None,
                      parent_terminal_state_observed=False, automatic_retry=False,
                      at=datetime.now(timezone.utc).isoformat())
        write_new(out/'result.json', result)
        return result
    except Exception as error:
        write_new(out/'failed.json', dict(passed=False, error=str(error), signal_sent=sent, automatic_retry=False))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='New receipt directory outside original control tree')
    parser.add_argument('--resume-parent', action='store_true', help='After all checks, send only original parent pidfd SIGCONT')
    args = parser.parse_args()
    print(json.dumps(run(args.out, args.resume_parent)))
