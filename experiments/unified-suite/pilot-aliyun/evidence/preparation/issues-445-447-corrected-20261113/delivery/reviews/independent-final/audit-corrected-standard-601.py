import ast
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import tarfile
import yaml

ROOT = Path('/home/dev/ax-perf-issues-validation')
P = ROOT / 'corrected-runtime-review-06'
OUT = ROOT / 'independent-review-7b844'
SHA = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
J = lambda p: json.loads(p.read_text())
checks = []

def check(name, condition):
    checks.append(name)
    assert condition, name

def close(name, a, b):
    check(name, math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12))

def metrics(p):
    return {m[1]: int(m[2]) for line in p.read_text().splitlines()
            if (m := re.fullmatch(r'(brewfs_\w+) (\d+)', line))}

receipt = J(P.with_suffix('.receipt.json'))
manifest = J(P / 'manifest.json')
check('archive hash', SHA(P.with_suffix('.tar.gz')) == receipt['sha256'])
check('manifest hash', SHA(P / 'manifest.json') == receipt['manifest_sha256'])
for item in manifest['files']:
    q = P / item['path']
    check('payload:' + item['path'], q.stat().st_size == item['bytes'] and SHA(q) == item['sha256'])
with tarfile.open(P.with_suffix('.tar.gz')) as tar:
    members = len(tar.getmembers())
check('member counts', len(manifest['files']) == 1236 and members == 1237)

tools = [f'fio-{mode}-direct{direct}' for mode in ('seqwrite', 'randwrite', 'randrw') for direct in (0, 1)]
pins = {
    'baseline': ('4f982f45c153a0c31e3e9cd8ff642ec7659c98fd', '0b0a2743aa968f497782d823cb068cd421eb840f82d5739a363d3bc3623c8679'),
    'candidate': ('c8f5f16aefe811540782b952481982179a1bfd6f', '905c4e4ca35162ae6a1ab2de03f4fa18194ef64d9afcbe20cff9bbefd570ce90')
}
budget = dict(read_memory_bytes=4 << 30, write_memory_bytes=4 << 30,
              read_ssd_bytes=8 << 30, write_ssd_bytes=4 << 30, memory_budget_bytes=12 << 30)
debt = ['brewfs_writeback_recent_pending_upload_bytes', 'brewfs_writeback_dirty_bytes',
        'brewfs_buffer_dirty_bytes', 'brewfs_writeback_live_dirty_bytes', 'brewfs_writeback_live_slices',
        'brewfs_writeback_recent_pending_upload_slices', 'brewfs_writeback_stage_inflight_bytes',
        'brewfs_writeback_remote_upload_inflight_bytes', 'brewfs_writeback_metadata_inflight']
owner = J(P / 'runtime/regression-corrected512-01/comparison.json')
owner_pairs = {p['tool']: p for p in owner['pairs']}
arms, trials, capacities, resources = {}, {}, {}, {}
for arm, (commit, binary) in pins.items():
    trial = P / f'trials/ax447-corrected-{arm}-regression-601'
    tr = trials[arm] = J(trial / 'receipt.json')
    check(f'{arm}:qualified', tr['qualified'] is True and tr['status'] == 0)
    check(f'{arm}:source', tr['source']['commit'] == commit and tr['source']['dirty'] is False)
    check(f'{arm}:binary', tr['binary'] == dict(commit=commit, sha256=binary))
    check(f'{arm}:no owned containers', not (trial / 'remaining-owned-containers.json').read_text().strip())
    error_gate = J(trial / 'services/error-gate.json')
    check(f'{arm}:server error gate', error_gate['passed'] and error_gate['error_count'] == 0
          and error_gate['log_sha256'] == SHA(trial / 'services/rustfs.log'))
    for line in (trial / 'services/rustfs.log').read_text().splitlines():
        if line.startswith('{'):
            event = json.loads(line)
            check(f'{arm}:raw server record', event.get('level') != 'ERROR' and int(event.get('status_code') or 0) < 500)
    compose = J(trial / 'rendered-compose.json')
    resources[arm] = {s: {k: compose['services'][s][k] for k in ('cpus', 'mem_limit')}
                      for s in ('perf', 'redis', 'rustfs')}
    a = next((P / 'artifacts' / arm).iterdir())
    check(f'{arm}:no invalid markers', not list(a.glob('*.invalid')))
    cfg = yaml.safe_load((a / 'backend.yml').read_text())
    check(f'{arm}:cache budgets', all(cfg['cache'][k] == v for k, v in budget.items()))
    check(f'{arm}:RustFS runtime', J(a / 'rustfs-runtime-pin.json')['passed'] is True)
    check(f'{arm}:all six tools pass', [(x['tool'], x['status']) for x in csv.DictReader((a/'perf-summary.tsv').read_text().splitlines(), delimiter='\t')]
          == [(t, 'pass') for t in tools])
    phases = {}
    for line in (a / 'phase-boundaries.tsv').read_text().splitlines():
        tool, stage, ns = line.split('\t')
        check(f'{arm}:unique phase {tool}/{stage}', (tool, stage) not in phases)
        phases[tool, stage] = int(ns)
    cpu = {}
    hz = int((a / 'client-cpu-clock-ticks.txt').read_text().strip())
    check(f'{arm}:clock ticks', hz == 100)
    for line in (a / 'client-cpu-raw.tsv').read_text().splitlines():
        tool, stage, ns, raw = line.split('\t', 3)
        tail = raw.rsplit(') ', 1)[1].split()
        cpu[tool, stage] = dict(observed_ns=int(ns), pid=int(raw.split(' (', 1)[0]),
                               starttime=int(tail[19]), user=int(tail[11]), system=int(tail[12]))
    arms[arm] = {}
    for tool in tools:
        fio = J(a / f'results/{tool}.json')
        check(f'{arm}/{tool}:one reporting group', len(fio['jobs']) == 1)
        job = fio['jobs'][0]
        opt = {k: v for k, v in job['job options'].items() if k != 'write_lat_log'}
        check(f'{arm}/{tool}:actual workload', opt['bs'] == '4m' and opt['size'] == '512m'
              and opt['runtime'] == '20' and opt['iodepth'] == '1' and opt['ioengine'] == 'io_uring'
              and opt['randseed'] == '20261031' and opt['randrepeat'] == '1'
              and opt['direct'] == tool[-1] and opt['numjobs'] == ('1' if 'seqwrite' in tool else '4'))
        check(f'{arm}/{tool}:fio errors', job['error'] == 0 and all(job[d]['short_ios'] == job[d]['drop_ios'] == 0 for d in ('read', 'write')))
        start, ret, drain = [phases[tool, s] for s in ('tool_start', 'tool_return', 'post-write_extended_drained')]
        obs = [cpu[tool, s] for s in ('tool_start', 'tool_return', 'post-write_extended_drained')]
        check(f'{arm}/{tool}:same process', len({(o['pid'], o['starttime']) for o in obs}) == 1)
        check(f'{arm}/{tool}:ordered CPU and wall boundaries', obs[0]['observed_ns'] <= start <= ret <= obs[1]['observed_ns'] <= drain <= obs[2]['observed_ns'])
        ticks = [o['user'] + o['system'] for o in obs]
        check(f'{arm}/{tool}:monotonic CPU', ticks[0] <= ticks[1] <= ticks[2])
        read_b, write_b = job['read']['io_bytes'], job['write']['io_bytes']
        total = read_b + write_b
        row = dict(job_options=opt, read_bytes=read_b, write_bytes=write_b, logical_bytes=total,
                   active_s=(ret-start)/1e9, drained_s=(drain-start)/1e9, post_return_s=(drain-ret)/1e9,
                   active_cpu_s=(ticks[1]-ticks[0])/hz, drain_cpu_s=(ticks[2]-ticks[1])/hz,
                   full_cpu_s=(ticks[2]-ticks[0])/hz, cpu_observations=obs,
                   read_p99_ns=job['read']['lat_ns'].get('percentile', {}).get('99.000000') if read_b else None,
                   write_p99_ns=job['write']['lat_ns'].get('percentile', {}).get('99.000000') if write_b else None,
                   write_clat_p99_ns=job['write']['clat_ns'].get('percentile', {}).get('99.000000') if write_b else None)
        row['full_cpu_s_per_GiB'] = row['full_cpu_s'] / (total / 2**30)
        row['drained_MiB_s'] = total / 2**20 / row['drained_s']
        row['active_MiB_s'] = total / 2**20 / row['active_s']
        row['drain_cpu_share'] = row['drain_cpu_s'] / row['full_cpu_s']
        d = a / 'remote-durability' / tool
        dr = J(d / 'receipt.json'); expected, observed = J(d/'expected.json'), J(d/'observed.json')
        check(f'{arm}/{tool}:durability passed', dr['passed'] is True and dr['phase']=='complete' and not dr['reasons'] and not dr['log_failures_before'] and not dr['log_failures_after'])
        count = 1 if 'seqwrite' in tool else 4
        check(f'{arm}/{tool}:all full-file digests equal', expected == observed and len(expected)==count and all(x['bytes']==512*1024**2 for x in expected))
        check(f'{arm}/{tool}:empty cache normal remount', dr['new_cache_was_empty'] and dr['old_cache_preserved'] and dr['new_cache_root'] != dr['old_cache_root'] and dr['unmount']['old_process_terminated'] and not dr['unmount']['forced'])
        check(f'{arm}/{tool}:same binary and argv', dr['new_mount']['executable_sha256'] == dr['old_mount']['executable_sha256'] == binary and dr['new_mount']['argv'] == dr['old_mount']['argv'] and dr['new_mount']['pid'] != dr['old_mount']['pid'])
        before, after = [yaml.safe_load((d/n).read_text()) for n in ('config-before.yaml','config-after.yaml')]
        before['cache'].pop('root'); after['cache'].pop('root')
        check(f'{arm}/{tool}:only cache root changes', before == after)
        check(f'{arm}/{tool}:durability outside measured boundaries', dr['timing_outside_measured_tool_and_strict_drain'] and dr['started_unix_ns'] > drain)
        row['cold_validation_s'] = dr['total_validation_ns']/1e9
        row['final_unique_file_bytes'] = sum(x['bytes'] for x in expected)
        samples = sorted((a/'stats-timeseries'/f'{tool}-post-write').glob('*.txt'))
        check(f'{arm}/{tool}:at least three drain samples', len(samples) >= 3)
        final = [metrics(s) for s in samples[-3:]]
        check(f'{arm}/{tool}:three zero debt samples', all(all(s[k] == 0 for k in debt) for s in final))
        check(f'{arm}/{tool}:PUT counters stable', len({(s['brewfs_s3_put_ops_total'],s['brewfs_s3_put_bytes_total']) for s in final}) == 1)
        row['drain_sample_count'] = len(samples)
        row['drain_last_three_sha256'] = [SHA(s) for s in samples[-3:]]
        for own, ours in [('read_bytes','read_bytes'),('write_bytes','write_bytes'),('active_wall_s','active_s'),('active_plus_strict_drain_wall_s','drained_s'),('active_cpu_s','active_cpu_s'),('active_plus_drain_cpu_s','full_cpu_s'),('active_plus_drain_cpu_s_per_GiB','full_cpu_s_per_GiB'),('active_plus_drain_logical_MiB_s','drained_MiB_s')]:
            close(f'{arm}/{tool}:owner recompute {own}', owner_pairs[tool][arm][own], row[ours])
        arms[arm][tool] = row
    cap = [json.loads(l) for l in (a/'capacity-scenes.jsonl').read_text().splitlines()]
    check(f'{arm}:all twelve scene boundaries', len(cap)==12 and {(r['tool'],r['phase']) for r in cap} == {(t,s) for t in tools for s in ('before_scene','after_scene')})
    host = [json.loads(l) for l in (P/f'runtime/regression-corrected512-01/ax447-corrected-{arm}-regression-601-capacity.jsonl').read_text().splitlines()]
    for r in cap + host:
        check(f'{arm}:capacity sample', r['passed'] and r['free_bytes'] >= r['total_bytes']//5 and r['free_inodes'] >= r['total_inodes']//5 and r['total_bytes']==540729581568)
    capacities[arm] = dict(scene_samples=len(cap),host_samples=len(host),min_sampled_available_bytes=min(r['free_bytes'] for r in cap+host),total_bytes=host[0]['total_bytes'])

check('same resources', resources['baseline']==resources['candidate'])
check('same image IDs', {k:v['id'] for k,v in trials['baseline']['images'].items()} == {k:v['id'] for k,v in trials['candidate']['images'].items()})
for key in ('harness_sha256','compose_sha256','container_harness_sha256','durability_helper_sha256','known_tail_helper_sha256','case_harness_sha256'):
    check('same harness '+key,trials['baseline'][key]==trials['candidate'][key])
for key in trials['baseline']['environment']:
    if key.startswith(('BREWFS_READ_', 'BREWFS_WRITE_', 'BREWFS_MEMORY_', 'BREWFS_FUSE_', 'BREWFS_COMPRESSION', 'BREWFS_VERIFY_', 'PERF_FIO_')):
        check('same effective env '+key,trials['baseline']['environment'][key]==trials['candidate']['environment'][key])
pairs=[]
for tool in tools:
    b,c=arms['baseline'][tool],arms['candidate'][tool]
    check(tool+':matching actual job options',b['job_options']==c['job_options'])
    ratios={k:c[k]/b[k] if b[k] else None for k in ('read_bytes','write_bytes','logical_bytes','active_s','drained_s','post_return_s','active_cpu_s','drain_cpu_s','full_cpu_s','full_cpu_s_per_GiB','drained_MiB_s','active_MiB_s','read_p99_ns','write_p99_ns')}
    for direction in ('read','write'):
        own=owner_pairs[tool]['worst_group_p99_candidate_over_baseline'][direction]['lat_p99_ns']
        got=ratios[direction+'_p99_ns']
        if own is None:check(tool+':null latency '+direction,got is None)
        else:close(tool+':raw p99 '+direction,own,got)
    pairs.append(dict(tool=tool,baseline=b,candidate=c,candidate_over_baseline=ratios))

delta=P/'protocol/workspace-failure-retention-01'
for name in ('run-row-long-guards.py','run-workspace-guards.py'):
    old,new=[ast.parse((delta/(name+suffix)).read_text()) for suffix in ('.before','')]
    functions=lambda t:{n.name:ast.dump(n,include_attributes=False) for n in t.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    check('unchanged workload functions '+name,functions(old)==functions(new))
oldm=J(P/'harness/review-manifest-07.json')['files'];newm=J(P/'harness/review-manifest-08.json')['files']
check('manifest08 changes only cleanup scripts',{k for k in oldm if oldm[k]!=newm[k]}=={'run-row-long-guards.py','run-workspace-guards.py'})

report = dict(schema='independent-standard-601-review-v1',decision='CORRECTNESS_PASS_NUMERIC_HOLD',pr_qualified=False,
              archive=dict(path=str(P),tar_sha256=receipt['sha256'],manifest_sha256=receipt['manifest_sha256'],payload_files=1236,total_tar_members=1237),
              checks_passed=len(checks),runtime_sources={k:v[0] for k,v in pins.items()},publication_source='e2c964c55fafdcced34af58e840ebddda1cf9ea4',
              actual_workload='Original seqwrite has one 512MiB job; randwrite/randrw have four. Each uses 4MiB blocks, depth1, io_uring, time_based20s, same seed and direct matrix. Time-based completed bytes differ; ratios use actual logical bytes.',
              correctness='All 12 actual cells pass fio, strict drain, same-binary empty-cache normal-remount full SHA, client/service gates, and owned cleanup. No performance pass inferred.',
              resources=resources,cache_config_bytes=budget,capacity=capacities,pairs=pairs,
              interpretation={
                  'randwrite_direct1':'Full CPU 34.76 to 38.63 seconds is +3.87 seconds, not a few 10ms ticks. Logical writes fall 13.58 percent; full CPU/GiB rises 28.60 percent and drained throughput falls 11.62 percent. Active CPU falls 12.56 to 10.20s while drain CPU rises 22.20 to 28.43s. Total wall alone hides this. No causal attribution established.',
                  'seqwrite_direct1':'Write total-latency p99 rises 34.865152 to 39.059456ms (+12.03 percent) despite nearly unchanged CPU/GiB and higher drained throughput. clat is separately retained and is not total latency.',
                  'randrw_direct1':'Write total-latency p99 rises 69.730304 to 89.653248ms (+28.57 percent); full CPU/GiB is +0.24 percent, drained throughput +1.83 percent. Keep each metric separately.',
                  'order_and_repeats':'One baseline-then-candidate pair per scene cannot establish stable nonregression. Preserve this pair and evaluate authorized reverse-order repeats without averaging away a regression.',
                  'denominators':'Logical FIO bytes include repeated overwrites and mixed read+write operations. Full CPU is BrewFS process CPU only, not system/server CPU. Cold verification is outside timed boundaries. p99 is the actual single reporting group, not pooled or averaged.',
                  'row_scope_future':'The legacy row cache enforces entry count, not the new byte env budget. Future row pressure evidence must say candidate retained-byte bound, not equal enforced byte caps or process RSS. Hot guards use identical resident working sets/data/TTL/operations and separately assess regressions.'},
              workspace_cleanup_delta='Only top-level failure cleanup changes; all workload function ASTs unchanged. No workspace data has yet been qualified on this revision.',
              remaining=['Reverse-order review of the three direct1 concerns','New corrected cached target repeated gain','Corrected workspace actual-path and byte-bound checks','Corrected long-row hot guard'])
out=OUT/'corrected-standard-601-review-01.json'
out.write_text(json.dumps(report,indent=2)+'\n')
(OUT/'corrected-standard-601-checks-01.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps({'decision':report['decision'],'checks':len(checks),'review_sha256':SHA(out),'capacity':capacities,
                  'pairs':[{k:v for k,v in p.items() if k in ('tool','candidate_over_baseline')} for p in pairs]},indent=2))
