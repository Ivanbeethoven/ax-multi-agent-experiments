#!/usr/bin/env python3
"""Read frozen 601/603 artifacts; emit facts, never rewrite raw evidence."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

OUT = Path('/home/dev/ax-randwrite-drain-diagnosis-01')
OUT.mkdir(exist_ok=True)
ROOT = Path('/home/dev/ax-perf-issues-validation')
SCENE = 'fio-randwrite-direct1'
DEBT = [
    'buffer_dirty_bytes', 'writeback_dirty_bytes', 'writeback_live_dirty_bytes',
    'writeback_live_slices', 'writeback_recent_pending_upload_bytes',
    'writeback_recent_pending_upload_slices', 'writeback_stage_inflight_bytes',
    'writeback_remote_upload_inflight_bytes', 'writeback_metadata_inflight',
]
inputs = {}


def read(path):
    body = path.read_bytes()
    inputs[str(path)] = hashlib.sha256(body).hexdigest()
    return body.decode()


def stats(path):
    d = {}
    for line in read(path).splitlines():
        match = re.fullmatch(r'brewfs_(\w+) ([-+0-9.eE]+)', line)
        if match:
            value = match[2]
            d[match[1]] = float(value) if any(c in value for c in '.eE') else int(value)
    return d


rows = []
for review, trial in [('06', 601), ('08', 603)]:
    base = ROOT / ('corrected-runtime-review-' + review)
    read(base / 'manifest.json')
    for arm in ['baseline', 'candidate']:
        artifact, = (base / 'artifacts' / arm).glob('perf-run-*')
        cpu = next(x for x in json.loads(read(artifact / 'client-cpu-summary.json'))['tools'] if x['tool'] == SCENE)
        phase = next(x for x in json.loads(read(artifact / 'precise-phase-summary.json'))['tools'] if x['tool'] == SCENE)
        snapshots = {key: stats(artifact / 'diagnostics' / f'stats-{SCENE}-{name}.txt')
                     for key, name in [('before', 'before'), ('active_end', 'after'), ('drained', 'post-write-drained-after')]}
        before, after, done = (snapshots[k] for k in ['before', 'active_end', 'drained'])
        series = []
        for path in sorted((artifact / 'stats-timeseries' / (SCENE + '-post-write')).glob('*.txt')):
            d = stats(path)
            assert all(k in d for k in DEBT)
            series.append({'time_ns': int(path.stem), 'debt': {k: d[k] for k in DEBT},
                           'put_ops': d['s3_put_ops_total'], 'put_bytes': d['s3_put_bytes_total'],
                           'get_ops': d['s3_get_ops_total'], 'get_bytes': d['s3_get_bytes_total'],
                           'upload_batch_bytes': d['writeback_upload_batch_bytes_total']})
        clear = next(x for x in series if all(v == 0 for v in x['debt'].values()))
        assert all(all(v == 0 for v in x['debt'].values()) for x in series if x['time_ns'] >= clear['time_ns'])
        prev = json.loads(read(artifact / 'remote-durability/fio-randwrite-direct0/receipt.json'))
        current = json.loads(read(artifact / 'remote-durability/fio-randwrite-direct1/receipt.json'))
        assert prev['passed'] and current['passed']
        assert prev['new_mount']['pid'] == cpu['pid'] == current['old_mount']['pid']
        assert int(prev['new_mount']['starttime']) == cpu['starttime_ticks']
        assert prev['config_sha256_after'] == current['config_sha256_before']
        read(artifact / 'remote-durability/fio-randwrite-direct1/config-before.yaml')
        prior_argv = read(artifact / 'tools/fio-randwrite-direct0.argv')
        current_argv = read(artifact / 'tools/fio-randwrite-direct1.argv')
        fio = json.loads(read(artifact / 'results/fio-randwrite-direct1.json'))
        write_bytes = sum(x['write']['io_bytes'] for x in fio['jobs'])
        delta = {k: done[k] - before.get(k, 0) for k in done}
        assert delta['writeback_upload_batch_bytes_total'] == write_bytes
        assert before['fuse_write_bytes_total'] == before['writeback_stage_bytes_total'] == 0
        put_bytes, put_ops = delta['s3_put_bytes_total'], delta['s3_put_ops_total']
        windows = [phase['tool_start_ns'], phase['tool_return_ns'], phase['strict_drained_ns']]
        offsets = [x['observed_ns'] - t for x, t in zip(cpu['observations'], windows)]
        assert max(abs(x) for x in offsets) < 100_000_000
        redis = {}
        for which in ['before', 'after']:
            text = read(artifact / 'diagnostics' / f'redis-{SCENE}-{which}.txt')
            redis[which] = {m[1]: {'calls': int(m[2]), 'usec': int(m[3])}
                            for m in re.finditer(r'cmdstat_([^:]+):calls=(\d+),usec=(\d+)', text)}
        rows.append({
            'trial': trial, 'arm': arm, 'artifact': str(artifact), 'cpu': cpu, 'phase': phase,
            'cpu_sample_minus_phase_ns': offsets, 'logical_write_bytes': write_bytes,
            'native_snapshots': snapshots, 'native_full_delta': delta, 'drain_series': series,
            'first_all_nine_debts_zero': clear,
            'all_debts_stay_zero_after_first_clear': True,
            'post_debt_zero_put_ops': done['s3_put_ops_total'] - clear['put_ops'],
            'post_debt_zero_put_bytes': done['s3_put_bytes_total'] - clear['put_bytes'],
            'post_debt_zero_until_strict_end_ns': phase['strict_drained_ns'] - clear['time_ns'],
            'put_payload_excess_assuming_raw_4byte_header': put_bytes - write_bytes - 4 * put_ops,
            'previous_scene_durability_end_ns': prev['finished_unix_ns'],
            'gap_previous_validation_to_current_start_ns': phase['tool_start_ns'] - prev['finished_unix_ns'],
            'previous_scene_created_current_pid': True,
            'previous_cache_root_now_current': prev['new_cache_root'],
            'previous_and_current_argv': [prior_argv, current_argv],
            'redis_active_only': redis,
            'post_drain_system_samples_present': (artifact / 'system-timeseries' / (SCENE + '-post-write')).exists(),
        })

source = {}
for arm in ['baseline', 'candidate']:
    repo = Path('/home/dev/ax-issue447-corrected-' + arm)
    head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    source[arm] = {'head': head, 'files': {}}
    for rel in ['src/chunk/compact/worker.rs', 'src/chunk/compact/compactor.rs',
                'src/chunk/store.rs', 'src/chunk/compress.rs', 'src/vfs/fs/mod.rs',
                'src/meta/config.rs', 'src/vfs/io/writer.rs']:
        body = read(repo / rel)
        source[arm]['files'][rel] = hashlib.sha256(body.encode()).hexdigest()
same_compaction = all(source['baseline']['files'][p] == source['candidate']['files'][p]
                      for p in source['baseline']['files'] if p != 'src/vfs/io/writer.rs')
assert same_compaction
out = {'scope': 'Frozen 601 AB and 603 BA full-six-scene matrices; diagnostic only, not new acceptance or causal proof',
       'source': source, 'common_compaction_store_meta_sources_byte_identical': same_compaction,
       'rows': rows, 'inputs_sha256': inputs,
       'limitations': ['No per-compaction native counter or successful INFO log in these warn-level artifacts.',
                       'Native successful ObjectBlockStore PUT totals are not independent HTTP request traces.',
                       'No sampled process CPU or system/cgroup time series within drain; only its start/end CPU.',
                       '4-byte raw-object-header residual is bookkeeping, not proof of object provenance.',
                       'Do not average absolute 601/603 results or erase their observed regression.']}
(OUT / 'facts.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps({'facts_sha256': hashlib.sha256((OUT / 'facts.json').read_bytes()).hexdigest(),
                  'rows': [{k: x[k] for k in ['trial', 'arm', 'post_debt_zero_put_ops', 'post_debt_zero_put_bytes',
                             'post_debt_zero_until_strict_end_ns', 'put_payload_excess_assuming_raw_4byte_header',
                             'cpu_sample_minus_phase_ns']} for x in rows]}, indent=2))
