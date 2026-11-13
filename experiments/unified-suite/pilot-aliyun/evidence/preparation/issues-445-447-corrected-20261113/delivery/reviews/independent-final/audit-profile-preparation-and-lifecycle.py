from pathlib import Path
import ast
import hashlib
import json

ROOT = Path('/home/dev/ax-perf-issues-validation')
OUT = ROOT / 'independent-review-7b844'
P = Path('/home/dev/ax-issue447-profile-prefix-604')
checks = []
sha = {}

def ck(name, value):
    checks.append({'check': name, 'passed': bool(value)})
    assert value, name

def read(p):
    b = p.read_bytes()
    sha[str(p)] = hashlib.sha256(b).hexdigest()
    return b.decode()

manifest = json.loads(read(P / 'manifest.json'))
for name, info in manifest.items():
    b = read(P / name).encode()
    ck('manifest:' + name, len(b) == info['bytes'] and hashlib.sha256(b).hexdigest() == info['sha256'])
    if name.endswith('.py'):
        ast.parse(b.decode())
        ck('python syntax:' + name, True)
old = json.loads(read(P / 'compose-original.json'))
new = json.loads(read(P / 'compose.json'))
oldvol = old['services']['perf']['volumes']
newvol = new['services']['perf']['volumes']
ck('compose volume number', len(newvol) == len(oldvol) + 1)
ck('only diagnostic scripts newly bound readonly', all(x.endswith(':ro') for x in newvol if x not in oldvol))
new['services']['perf']['volumes'] = oldvol
ck('compose other configuration/resources identical', new == old)
plan = json.loads(read(P / 'plan.json'))
ck('diagnostic not acceptance', plan['diagnostic_only'] and not plan['performance_accepted'])
ck('same four-scene ordered prefix', plan['scenes'] == ['fio-seqwrite-direct0', 'fio-seqwrite-direct1', 'fio-randwrite-direct0', 'fio-randwrite-direct1'])
controller = read(P / 'run-profile-pair.py')
hook = read(P / 'profile_hook.py')
container = read(P / 'run-profile-container.sh')
case = read(P / 'run-profile-case.py')
for text, meaning in [
    ("'-e','cpu-clock','-F','49','--strict-freq','--call-graph','dwarf,8192'", 'exact sampling'),
    ("'--clockid','mono'", 'perf monotonic clock'),
    ("'-D','-1','--control'", 'disabled controlled recorder'),
    ("os.write(cfd,b'enable\\n')", 'positive enable handshake'),
    ("actual_sha==expected_sha", 'actual exe pin'),
    ("actual['starttime_ticks']==int(f[19])", 'PID incarnation pin'),
    ("int(ns[-1])==request['pid']", 'container/host PID mapping'),
    ("'lost_event_lines':lost", 'lost/throttle disclosure'),
    ("quality['brewfs_symbol_present']", 'symbol fail closed'),
    ("free-256*1024**3>=total//5", 'capacity reserve preserved'),
]:
    ck(meaning, text in controller)
ck('stop after strict drain before cold verification', container.index('    wait_for_fio_post_write_drain "$tool" || fio_status=1') < container.index('profile604_hook.py stop') < container.index('        verify_fio_remote_durability "$tool" "$work_dir" || fio_status=1'))
ck('three target-only insertion hooks', container.count('profile604_hook.py') == 3)
ck('original core workload retained', all(x in case for x in ["'PERF_FIO_SIZE': '512m'", "'PERF_FIO_RUNTIME': '20'", "'PERF_FIO_RANDSEED': '20261031'", "else '0 1'", "else 4\n"]))
log = 'warn,brewfs::chunk::compact::worker=debug,brewfs::cadapter::s3=debug'
dur = read(P / 'verify_remote_durability.py')
ck('durability sole log allowlist difference', dur.replace("frozenset(('" + log + "', 'warn',", "frozenset(('warn',") == read(P / 'durability-original.py'))

life = []
for version, trial in [('06', 601), ('08', 603)]:
    r = ROOT / ('corrected-runtime-review-' + version)
    for arm in ['baseline', 'candidate']:
        a = next((r / 'artifacts' / arm).iterdir())
        cpu = json.loads(read(a / 'client-cpu-summary.json'))
        ck(f'{trial}/{arm}: six tools three boundaries', len(cpu['tools']) == 6 and all(len(t['observations']) == 3 for t in cpu['tools']))
        samples = {}
        for t in cpu['tools']:
            for z in t['observations']:
                samples.setdefault(z['pid'], []).append(z['observed_ns'])
        raw_keys = set()
        for p in (a / 'system-timeseries').rglob('*.json'):
            s = json.loads(read(p))
            samples.setdefault(s['pid'], []).append(s['wall_ns'])
            raw_keys.update(s['raw'])
        receipts = []
        for p in sorted((a / 'remote-durability').glob('*/receipt.json')):
            d = json.loads(read(p))
            ck(f'{trial}/{arm}/{p.parent.name}: no terminal CPU fields', set(d['old_mount']) == {'argv','cache_root','executable_sha256','pid','starttime','state'} and set(d['new_mount']) == set(d['old_mount']))
            t = next(x for x in cpu['tools'] if x['tool'] == p.parent.name)
            ck(f'{trial}/{arm}/{p.parent.name}: sampled CPU ends before warm/cold validation', max(samples[t['pid']]) < d['started_unix_ns'])
            receipts.append({'tool':p.parent.name, 'old_pid':d['old_mount']['pid'], 'new_pid':d['new_mount']['pid'], 'warm_hash_ns':d['warm_hash_ns'], 'validation_start_ns':d['started_unix_ns'], 'last_old_pid_observed_ns':max(samples[t['pid']])})
        last = next(x for x in receipts if x['tool'] == 'fio-randrw-direct1')
        ck(f'{trial}/{arm}: last cold verification PID unsampled', last['new_pid'] not in samples)
        ck(f'{trial}/{arm}: no cumulative cgroup CPU usage', not any(x.endswith('/cpu.stat') or 'cpuacct.usage' in x for x in raw_keys))
        life.append({'trial':trial, 'arm':arm, 'tool_boundary_pid_count':len(cpu['tools']), 'system_series_directories':sorted(p.name for p in (a/'system-timeseries').iterdir()), 'receipts':receipts, 'last_cold_pid_without_cpu_sample':last['new_pid']})

out = {
    'schema':'independent-profile604-preparation-and-lifecycle-v1',
    'checks_passed':len(checks), 'profile_preparation':'PASS_STATIC_DIAGNOSTIC_ONLY',
    'profile_executed':False, 'integrated_pr_qualified':False,
    'profile_manifest_sha256':sha[str(P/'manifest.json')],
    'lifecycle_decision':'COMPLETE_LIFETIME_CPU_NOT_RECONSTRUCTABLE_FROM_601_603',
    'lifecycle_arms':life,
    'interpretation':[
        'Six process-window CPU sums cannot stand for all mount lifecycle cost. Each window ends before warm full hashing and normal unmount; those terminal process ticks were not archived.',
        'The final cold-validation process also lacks any CPU sample. Prefill system snapshots do not close its lifetime or any prior terminal tail.',
        'No captured cgroup cumulative CPU usage supplies the missing whole-arm total. Prior whole-sequence scene observations remain valid but cannot prove cost transfer cancels regression.',
        '604 is diagnostic only and retains default background behavior. Logging, setup pause and profiling alter execution timing.',
        'Its original before.stats precedes metadata inventory, ELF/library capture and enable ACK. Do not treat its full native delta as the exact active/drain profile window; preserve setup elapsed time and add a separate post-ACK native snapshot if possible.',
        'Resolved symbols and no lost records do not prove all async call stacks complete. Preserve unknown/truncated stack limitations, do not label every CRC or unparented PUT as compaction.',
    ],
    'source_sha256':sha,
}
f = OUT / 'profile604-static-and-lifecycle-review-01.json'
assert not f.exists()
f.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
(OUT/'profile604-static-and-lifecycle-checks-01.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps({'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'checks':len(checks)}))
