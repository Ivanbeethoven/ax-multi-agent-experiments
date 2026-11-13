from pathlib import Path
import hashlib
import json
import os
import subprocess

ROOT = Path('/home/dev/ax-perf-issues-validation')
OUT = ROOT / 'independent-review-7b844'
TOOL = 'fio-randwrite-direct1'
DEBT = ['brewfs_writeback_recent_pending_upload_bytes', 'brewfs_writeback_dirty_bytes',
        'brewfs_buffer_dirty_bytes', 'brewfs_writeback_live_dirty_bytes', 'brewfs_writeback_live_slices',
        'brewfs_writeback_recent_pending_upload_slices', 'brewfs_writeback_stage_inflight_bytes',
        'brewfs_writeback_remote_upload_inflight_bytes', 'brewfs_writeback_metadata_inflight']
PUT = 'brewfs_s3_put_bytes_total'
OPS = 'brewfs_s3_put_ops_total'
BATCH = 'brewfs_writeback_upload_batch_bytes_total'
checks = []
sources = {}

def ck(name, truth):
    checks.append({'check': name, 'passed': bool(truth)})
    assert truth, name

def read(path):
    b = path.read_bytes()
    sources[str(path)] = hashlib.sha256(b).hexdigest()
    return b.decode()

def obj(path):
    return json.loads(read(path))

def stats(path):
    vals = {}
    for l in read(path).splitlines():
        bits = l.split()
        if len(bits) == 2 and bits[0].startswith('brewfs_'):
            try:
                vals[bits[0]] = int(bits[1])
            except ValueError:
                pass
    return vals

arms = []
for number, trial in [('06', 601), ('08', 603)]:
    root = ROOT / ('corrected-runtime-review-' + number)
    for arm in ['baseline', 'candidate']:
        artifact = next((root / 'artifacts' / arm).iterdir())
        before = stats(artifact / 'diagnostics' / ('stats-' + TOOL + '-before.txt'))
        final = stats(artifact / 'diagnostics' / ('stats-' + TOOL + '-post-write-drained-after.txt'))
        samples = [(int(p.stem), stats(p), str(p)) for p in sorted((artifact / 'stats-timeseries' / (TOOL + '-post-write')).glob('*.txt'))]
        zero_i = next(i for i, (_, v, _) in enumerate(samples) if all(v[k] == 0 for k in DEBT))
        zero_time, zero, zero_path = samples[zero_i]
        ck(f'{trial}/{arm}: all nine debts remain zero after first observed zero', all(all(v[k] == 0 for k in DEBT) for _, v, _ in samples[zero_i:]))
        ck(f'{trial}/{arm}: PUT progresses after debt zero', final[PUT] > zero[PUT] and final[OPS] > zero[OPS])
        ck(f'{trial}/{arm}: writer upload bytes stop after debt zero', all(v[BATCH] == zero[BATCH] for _, v, _ in samples[zero_i:]) and final[BATCH] == zero[BATCH])
        ck(f'{trial}/{arm}: target before has no FUSE writes or upload batches', before['brewfs_fuse_write_ops_total'] == 0 and before['brewfs_fuse_write_bytes_total'] == 0 and before[BATCH] == 0)
        ck(f'{trial}/{arm}: target before already has PUT', before[PUT] > 0 and before[OPS] > 0)
        cpu = next(x for x in obj(artifact / 'client-cpu-summary.json')['tools'] if x['tool'] == TOOL)
        prior = obj(artifact / 'remote-durability/fio-randwrite-direct0/receipt.json')
        ck(f'{trial}/{arm}: prior remount is actual target PID/starttime', prior['new_mount']['pid'] == cpu['pid'] and int(prior['new_mount']['starttime']) == cpu['starttime_ticks'])
        ck(f'{trial}/{arm}: prior verification precedes target CPU start', prior['finished_unix_ns'] <= cpu['observations'][0]['observed_ns'])
        ck(f'{trial}/{arm}: CPU window contains zero-debt late PUT interval', cpu['observations'][1]['observed_ns'] < zero_time < samples[-1][0] < cpu['observations'][2]['observed_ns'])
        ck(f'{trial}/{arm}: stable process CPU identity', len({(x['pid'], x['starttime_ticks']) for x in cpu['observations']}) == 1)
        fio = obj(artifact / 'results' / (TOOL + '.json'))
        prior_fio = obj(artifact / 'results/fio-randwrite-direct0.json')
        written = sum(x['write']['io_bytes'] for x in fio['jobs'])
        prior_written = sum(x['write']['io_bytes'] for x in prior_fio['jobs'])
        config_before = read(artifact / 'remote-durability/fio-randwrite-direct1/config-before.yaml')
        config_after_prior = read(artifact / 'remote-durability/fio-randwrite-direct0/config-after.yaml')
        ck(f'{trial}/{arm}: prior remount config unchanged into target', config_before == config_after_prior)
        ck(f'{trial}/{arm}: ordinary Redis/object store persists', 'redis://redis:6379/0' in config_before and 'bucket: brewfs-data' in config_before)
        ck(f'{trial}/{arm}: no background disabling config', 'no_background_jobs: true' not in config_before and 'no_background_jobs: 1' not in config_before)
        delta_put, delta_ops, delta_batch = final[PUT] - before[PUT], final[OPS] - before[OPS], final[BATCH] - before[BATCH]
        extra = delta_put - delta_batch - 4 * delta_ops
        ck(f'{trial}/{arm}: positive PUT payload outside current writer batches', extra > 0)
        row = {
            'trial': trial, 'arm': arm, 'artifact': str(artifact),
            'pid': cpu['pid'], 'starttime_ticks': cpu['starttime_ticks'],
            'prior_durable_remount_pid': prior['new_mount']['pid'],
            'gap_prior_cold_verification_end_to_target_cpu_start_s': (cpu['observations'][0]['observed_ns'] - prior['finished_unix_ns']) / 1e9,
            'prior_randwrite0_logical_write_bytes': prior_written,
            'target_logical_write_bytes': written,
            'before_existing_put_bytes': before[PUT], 'before_existing_put_ops': before[OPS],
            'target_put_delta_bytes': delta_put, 'target_put_delta_ops': delta_ops,
            'target_writer_upload_batch_delta_bytes': delta_batch,
            'put_payload_minus_writer_batches_and_4byte_headers': extra,
            'put_payload_minus_writer_batches_and_4byte_headers_mib': extra / 2**20,
            'first_observed_zero_debt_sample': zero_path,
            'first_observed_zero_debt_from_first_drain_sample_s': (zero_time - samples[0][0]) / 1e9,
            'last_drain_sample_from_first_drain_sample_s': (samples[-1][0] - samples[0][0]) / 1e9,
            'put_after_first_observed_zero_debt_bytes': final[PUT] - zero[PUT],
            'put_after_first_observed_zero_debt_ops': final[OPS] - zero[OPS],
            'cpu_active_s': cpu['tool_cpu']['total_s'],
            'cpu_drain_s': cpu['post_tool_drain_cpu']['total_s'],
            'cpu_full_s': cpu['tool_and_drain_cpu']['total_s'],
            'cpu_full_s_per_gib': cpu['tool_and_drain_cpu']['total_s'] / (written / 2**30),
        }
        arms.append(row)

repo = '/home/dev/ax-writeback-commit-cleanup-freeze.git'
commit = 'c8f5f16aefe811540782b952481982179a1bfd6f'
env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}
code = {}
for p in ['src/chunk/compact/worker.rs', 'src/chunk/compact/compactor.rs', 'src/meta/config.rs', 'src/vfs/fs/mod.rs', 'src/chunk/store.rs']:
    data = subprocess.run(['git', '--git-dir=' + repo, 'show', commit + ':' + p], env=env, check=True, capture_output=True).stdout
    code[p] = data.decode()
    sources[commit + ':' + p] = hashlib.sha256(data).hexdigest()
ck('source: tick before compaction scan', code['src/chunk/compact/worker.rs'].index('ticker.tick().await;') < code['src/chunk/compact/worker.rs'].index('if let Err(e) = run_compaction_cycle'))
ck('source: default no_background_jobs false', 'no_background_jobs: false' in code['src/meta/config.rs'])
ck('source: default compact period 600s', 'interval: Duration::from_secs(600)' in code['src/meta/config.rs'])
ck('source: normal mounted VFS enables background jobs', 'let enabled = !meta_layer.options().no_background_jobs;' in code['src/vfs/fs/mod.rs'])
ck('source: compaction invokes shared store direct write', '.write_fresh_range(key, span.offset, &data[offset..offset + take])' in code['src/chunk/compact/compactor.rs'])
harness = ROOT / 'corrected-runtime-review-08/harness/run-measured-perf-in-container.sh'
h = read(harness)
ck('harness: only current scene workdir removed', 'local work_dir="$mount_dir/.perf-${tool}"' in h and 'rm -rf "$work_dir"' in h)
ck('harness: strict drain includes PUT stability', '"$put_ops" == "$previous_put"' in h and 'record_client_cpu "$tool" "${phase}_extended_drained"' in h)

result = {
    'schema': 'independent-compaction-overlap-review-v1', 'checks_passed': len(checks),
    'decision': 'REPEATED_TOTAL_PROCESS_CPU_REGRESSION_HOLD_BACKGROUND_OVERLAP_PLAUSIBLE_NOT_YET_ATTRIBUTED',
    'integrated_pr_qualified': False,
    'arms': arms,
    'confirmed': [
        'Each randwrite1 process is the live fresh-cache remount created by the preceding randwrite0 full cold verification; same metadata database and bucket remain.',
        'The remount process has already issued hundreds of MiB of PUT with zero FUSE writes/upload batches before target CPU start.',
        'All nine recorded writer debt gauges stay zero while PUT continues and current writer upload batch bytes stay fixed. Whole-process CPU/drain window includes this interval.',
        'Same scene order and settings are applied, but time-based prior scenes produce different bytes and potentially different persisted slice history on the two arms; target before counters already differ.',
        'The mounted default starts background jobs and the compaction worker waits on the interval first tick before scanning; heavy compaction uses the shared block store without FileWriter batch accounting.',
    ],
    'limits': [
        'No dedicated compaction counter/object trace/stack in original raw proves all extra PUT or its CPU is compaction. The residual is an accounting observation, not a causal CPU measurement.',
        'Only three per-process CPU boundaries are archived; exact CPU after first debt-zero cannot be reconstructed, and compaction CPU must not be subtracted by estimating wall time.',
        'Original tests remain valid whole-sequence process-cost observations, not isolated current-scene writer-cost estimates. Opposite order does not remove within-arm historical background state.',
        'Default background cost may be a real candidate consequence; discovering it does not waive the repeated regression or establish a measurement error.',
    ],
    'profile_review': {
        'status': 'design acceptable pending exact frozen implementation review',
        'keep': ['same sources/binaries/budgets/default compaction', 'same prefix seqwrite0/1 then randwrite0/1 and cold-remount lifecycle', 'each arm independent project on same host'],
        'capture': ['cpu-clock 49Hz/dwarf8192 all process threads', 'PID/starttime/exe identity and initial/existing thread coverage', 'perf clock and realtime/monotonic mapping at original tool start/return/strict-drain boundaries', 'lost/throttled/truncated/unknown stack counts and symbol quality', 'prior remount and target-before native gauges/PUT plus drain samples'],
        'qualification': 'diagnostic only; profiled numbers cannot replace unprofiled performance acceptance',
        'if_confirmed': 'Propose an explicit matched persisted prehistory/fixture with default background mechanisms retained, or separately attribute the full sequence. Do not silently disable compaction or subtract estimated CPU.',
    },
    'source_sha256': sources,
}
OUT.mkdir(exist_ok=True)
path = OUT / 'compaction-overlap-review-01.json'
assert not path.exists()
path.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
(OUT / 'compaction-overlap-checks-01.json').write_text(json.dumps(checks, indent=2) + '\n')
print(json.dumps({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'checks': len(checks), 'arms': arms}, indent=2))
