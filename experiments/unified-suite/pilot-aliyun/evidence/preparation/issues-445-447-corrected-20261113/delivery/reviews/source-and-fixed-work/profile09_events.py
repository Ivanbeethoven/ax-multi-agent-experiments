#!/usr/bin/env python3
"""Bind archived profile worker events to stat-confirmed predecessor files."""
import collections
import datetime
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-09')
OUT = Path('/home/dev/ax-randwrite-drain-diagnosis-01/profile09')
OUT.mkdir(exist_ok=True)
SCENE = 'fio-randwrite-direct1'
DEBT = ['buffer_dirty_bytes', 'writeback_dirty_bytes', 'writeback_live_dirty_bytes',
        'writeback_live_slices', 'writeback_recent_pending_upload_bytes',
        'writeback_recent_pending_upload_slices', 'writeback_stage_inflight_bytes',
        'writeback_remote_upload_inflight_bytes', 'writeback_metadata_inflight']
manifest_bytes = (ROOT / 'manifest.json').read_bytes()
manifest = json.loads(manifest_bytes)
expected = {r['path']: r for r in manifest['files']}
inputs = {}


def read(path):
    b = path.read_bytes()
    rel = str(path.relative_to(ROOT))
    sha = hashlib.sha256(b).hexdigest()
    assert expected[rel]['sha256'] == sha and expected[rel]['bytes'] == len(b), rel
    inputs[rel] = sha
    return b.decode()


def utc_ns(value):
    # No float timestamp conversion; logs have six fractional decimal digits.
    dt = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    delta = dt - datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + dt.microsecond * 1000


def stats(path):
    d = {}
    for line in read(path).splitlines():
        m = re.fullmatch(r'brewfs_(\w+) ([-+0-9.eE]+)', line)
        if m:
            d[m[1]] = float(m[2]) if any(c in m[2] for c in '.eE') else int(m[2])
    return d


rows = []
for arm, trial in [('baseline', 605), ('candidate', 606)]:
    artifact, = (ROOT / f'home/dev/ax-issue447-corrected-{arm}/docker/compose-xfstests/artifacts').iterdir()
    profile = artifact / f'profile{trial}'
    req = json.loads(read(profile / 'start-request.json'))
    before_files = {f['ino']: f for f in req['existing_scene_files']}
    assert len(before_files) == 6
    assert all(f['size'] == 512 << 20 for f in before_files.values())
    for f in before_files.values():
        scene, name = f['relative_path'].split('/')
        receipt_files = json.loads(read(artifact / 'remote-durability' / scene.removeprefix('.perf-') / 'expected.json'))
        match, = (x for x in receipt_files if x['path'] == name)
        assert match['bytes'] == f['size']
        f['expected_sha256'] = match['sha256']
    clocks = [json.loads(x) for x in read(profile / 'clock-markers.jsonl').splitlines()]
    cpu_rows = [x.split('\t', 3) for x in read(artifact / 'client-cpu-raw.tsv').splitlines() if x.startswith(SCENE+'\t')]
    assert [x[1] for x in cpu_rows] == ['tool_start', 'tool_return', 'post-write_extended_drained']
    cpu_stamps = [int(x[2]) for x in cpu_rows]
    assert cpu_stamps == sorted(cpu_stamps)
    cpu_pid = int(cpu_rows[0][3].split(' ', 1)[0])
    assert cpu_pid == req['pid'] and all(int(x[3].split(' ', 1)[0]) == cpu_pid for x in cpu_rows)
    before_receipt = json.loads(read(artifact / 'remote-durability/fio-randwrite-direct0/receipt.json'))
    current_receipt = json.loads(read(artifact / 'remote-durability/fio-randwrite-direct1/receipt.json'))
    assert before_receipt['new_mount']['pid'] == current_receipt['old_mount']['pid'] == cpu_pid
    assert before_receipt['config_sha256_after'] == current_receipt['config_sha256_before']
    phase = {parts[1]: int(parts[2]) for line in read(artifact/'phase-boundaries.tsv').splitlines()
             if (parts := line.split('\t'))[0] == SCENE}
    active_lo, active_hi, strict_hi = cpu_stamps
    log = read(artifact / 'brewfs.log')
    lines = log.splitlines()
    headers = re.compile(r'^\s*(\d{4}-\d\d-\d\dT[\d:.]+Z)\s+(INFO|DEBUG|WARN|ERROR)\s+([^:]+(?:::[^:]+)*): (.*)$')
    events, tasks, puts, pending = [], [], [], {}
    mount_epoch = 0
    for line_number, line in enumerate(lines, 1):
        if line == 'mounted at /mnt/brewfs':
            mount_epoch += 1
        m = headers.match(line)
        if not m:
            continue
        stamp, level, module, message = m.groups()
        t = utc_ns(stamp)
        if module == 'brewfs::chunk::compact::worker':
            chunk = re.search(r'chunk_id: (\d+)', message)
            if not chunk:
                continue
            chunk = int(chunk[1]); ino, index = divmod(chunk, 1_000_000_000)
            event = {'line': line_number, 'utc': stamp, 'unix_ns': t, 'mount_epoch': mount_epoch,
                     'message': message, 'chunk_id': chunk, 'ino': ino, 'chunk_index': index,
                     'predecessor_file': before_files.get(ino)}
            events.append(event)
            key = (mount_epoch, chunk)
            if message.startswith(('Acquired async compact lock', 'Acquired sync compact lock')):
                assert key not in pending
                event['slice_count'] = int(re.search(r'slice_count: (\d+)', message)[1])
                pending[key] = event
            elif message.startswith(('Light compaction completed', 'Heavy compaction completed', 'Compaction skipped', 'Compaction failed')):
                start = pending.pop(key)
                tasks.append({'start': start, 'end': event, 'kind': message.split(' ', 1)[0],
                              'duration_ns': t-start['unix_ns']})
        if module == 'brewfs::cadapter::s3' and message.startswith('close,'):
            block = '\n'.join(lines[line_number:line_number+3])
            span = re.search(r'in brewfs::cadapter::s3::put_object_vectored_simple with (.*)', block)
            if span:
                size = re.search(r'total_size: (\d+)', span[1])
                assert size
                puts.append({'line': line_number, 'utc': stamp, 'unix_ns': t, 'mount_epoch': mount_epoch,
                             'total_size': int(size[1]), 'span_fields': span[1]})
    current = [t for t in tasks if t['start']['unix_ns'] < strict_hi and t['end']['unix_ns'] > active_lo]
    assert current
    epochs = {t['start']['mount_epoch'] for t in current}
    assert len(epochs) == 1
    epoch, = epochs
    epoch_tasks = [t for t in tasks if t['start']['mount_epoch'] == epoch]
    assert all(t['start']['predecessor_file'] for t in epoch_tasks)
    assert all(t['start']['predecessor_file']['relative_path'].startswith('.perf-fio-randwrite-direct0/') for t in epoch_tasks)
    assert {t['start']['chunk_id'] for t in epoch_tasks} == {ino*1_000_000_000+i for ino in (10,11,12,13) for i in range(8)}
    assert len(epoch_tasks) == 32
    for task in current:
        a,b=task['start']['unix_ns'],task['end']['unix_ns']
        task['active_overlap_ns']=max(0,min(b,active_hi)-max(a,active_lo))
        task['drain_overlap_ns']=max(0,min(b,strict_hi)-max(a,active_hi))
        task['coincident_put_closes']=[x for x in puts if a<=x['unix_ns']<=b and x['mount_epoch']==epoch]
        task['put_closes_are_temporal_overlap_not_object_identity']=True
    series=[]
    for path in sorted((artifact/'stats-timeseries'/(SCENE+'-post-write')).glob('*.txt')):
        s=stats(path)
        assert all(k in s for k in DEBT)
        series.append({'unix_ns':int(path.stem),'debt':{k:s[k] for k in DEBT},
                       'put_ops':s['s3_put_ops_total'],'put_bytes':s['s3_put_bytes_total']})
    clear=next(s for s in series if all(v==0 for v in s['debt'].values()))
    assert all(all(v==0 for v in s['debt'].values()) for s in series if s['unix_ns']>=clear['unix_ns'])
    done=stats(artifact/'diagnostics'/f'stats-{SCENE}-post-write-drained-after.txt')
    before=stats(artifact/'diagnostics'/f'stats-{SCENE}-before.txt')
    after_enable=stats(profile/'stats-after-enable-before-tool.txt')
    phase_counts={}
    for label,lo,hi in [('active',active_lo,active_hi),('drain',active_hi,strict_hi),('after_nine_debts_zero',clear['unix_ns'],strict_hi)]:
        completions=[t for t in current if lo<=t['end']['unix_ns']<=hi]
        calls=[p for p in puts if lo<=p['unix_ns']<=hi and p['mount_epoch']==epoch]
        phase_counts[label]={'completed_tasks':len(completions),'kind_counts':dict(collections.Counter(t['kind'] for t in completions)),
                             'completed_chunk_ids':[t['start']['chunk_id'] for t in completions],
                             'put_close_count':len(calls),'put_close_total_size':sum(p['total_size'] for p in calls),
                             'put_close_size_histogram':dict(collections.Counter(p['total_size'] for p in calls))}
    rows.append({'arm':arm,'trial':trial,'artifact':str(artifact),'cpu_pid':cpu_pid,'current_mount_epoch':epoch,
                 'actual_cpu_realtime_ns':dict(zip(['start','return','strict_end'],cpu_stamps)), 'phase_boundaries':phase,
                 'clocks':clocks,'stat_confirmed_predecessor_files':list(before_files.values()),
                 'epoch_completed_task_count':len(epoch_tasks),'epoch_kind_counts':dict(collections.Counter(t['kind'] for t in epoch_tasks)),
                 'epoch_completed_tasks':epoch_tasks,'overlapping_tasks':current,'phase_counts':phase_counts,
                 'first_nine_debts_zero':clear,'post_debt_zero_put_ops':done['s3_put_ops_total']-clear['put_ops'],
                 'post_debt_zero_put_bytes':done['s3_put_bytes_total']-clear['put_bytes'],
                 'all_debts_remain_zero':True,'last_predecessor_completion':max(t['end']['unix_ns'] for t in epoch_tasks),
                 'stat_predecessor_end_ns':before_receipt['finished_unix_ns'],
                 'before_stats':{k:before[k] for k in ['fuse_write_bytes_total','s3_put_ops_total','s3_put_bytes_total']},
                 'after_perf_enable_stats':{k:after_enable[k] for k in ['fuse_write_bytes_total','s3_put_ops_total','s3_put_bytes_total']},
                 'any_simple_put_span_key_present':any(re.search(r'\bkey:',p['span_fields']) for p in puts)})

source={}
for arm in ['baseline','candidate']:
    repo=Path('/home/dev/ax-issue447-corrected-'+arm)
    head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True,env=dict(os.environ,GIT_OPTIONAL_LOCKS='0')).strip()
    files={str(p):hashlib.sha256((repo/p).read_bytes()).hexdigest() for p in map(Path,[
        'src/chunk/compact/worker.rs','src/chunk/compact/compactor.rs','src/cadapter/s3.rs','src/meta/stores/redis/mod.rs'])}
    source[arm]={'head':head,'files':files}
assert source['baseline']['files']==source['candidate']['files']
result={'kind':'profile09-compaction-predecessor-binding','passed':True,'raw_manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),
        'source':source,'rows':rows,'inputs_sha256':inputs,
        'scope':'Archived diagnostic evidence of predecessor compaction overlapping current active/drain; not a throughput gate or per-task CPU attribution.',
        'limits':['Source and stat mapping establish task predecessor identity, not each anonymous S3 span key.',
                  'Task elapsed overlap includes I/O awaits and is not CPU time; no subtraction from process CPU is valid.',
                  '605/606 profiling/logging and time-based quantities differ from formal 601/603; no cross-campaign absolute averaging.',
                  'Sampled stack attribution and clock/sample quality are reviewed independently; this result does not assign a CPU fraction.',
                  'All 32 prior randwrite chunks completed in this diagnostic mount; this does not establish eventual whole-namespace lifecycle completion.']}
(OUT/'events.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'events_sha256':hashlib.sha256((OUT/'events.json').read_bytes()).hexdigest(),
                  'inputs_checked':len(inputs),'rows':[{k:r[k] for k in ['arm','epoch_kind_counts','phase_counts','post_debt_zero_put_ops','post_debt_zero_put_bytes','any_simple_put_span_key_present']}for r in rows]},indent=2))
