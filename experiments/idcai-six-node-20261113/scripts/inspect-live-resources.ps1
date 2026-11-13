param([Parameter(Mandatory)][ValidatePattern('^ax-four-[0-9a-f]{12}$')][string]$Run)

# Reads existing observer samples. Does not probe mounted workload files or S3.
$ErrorActionPreference = 'Stop'
$roles = [ordered]@{'idcai-jump'='client';'idcai-client2'='target';'idcai-obj1'='obj1';'idcai-obj2'='obj2';'idcai-obj3'='obj3';'idcai-obj4'='obj4'}
$code = @'
import json, subprocess, sys, time
from pathlib import Path

run, role = sys.argv[1:]
root = Path('/srv/ax-idcai-20261113/campaign-100k-001')/run/('observer-'+role)
samples = sorted(root.glob('sample-*/sample.json'))
if len(samples) < 2:
    raise SystemExit('Two completed observer samples required')
before, after = [json.loads(p.read_text()) for p in samples[-2:]]
if before['boot_id'] != after['boot_id']:
    raise SystemExit('Host rebooted between samples')
elapsed = (after['monotonic_start_ns']-before['monotonic_start_ns'])/1e9
if not 0 < elapsed <= 30:
    raise SystemExit('Observer sample gap exceeded 30 seconds')

def fields(path, colon=False):
    rows = {}
    for line in path.read_text().splitlines():
        parts = line.split(':', 1) if colon else line.split(maxsplit=1)
        if len(parts) == 2:
            rows[parts[0]] = parts[1].strip()
    return rows

old, new = samples[-2].parent, samples[-1].parent
cpu = []
for directory in (old, new):
    first = (directory/'host-stat.txt').read_text().splitlines()[0].split()[1:9]
    cpu.append([int(x) for x in first])
delta = [b-a for a,b in zip(*cpu)]
if any(x < 0 for x in delta) or sum(delta) <= 0:
    raise SystemExit('Invalid CPU counters')
memory = fields(new/'host-meminfo.txt', True)
errors = root/'errors.jsonl'
value = dict(run=run, role=role, samples=len(samples),
    observer_state=subprocess.check_output(['systemctl','show',run+'-observer','-p','ActiveState','--value'],text=True).strip(),
    latest_sample_age_seconds=round((time.time_ns()-after['utc_ns'])/1e9, 3),
    error_samples=len(errors.read_text().splitlines()) if errors.exists() else 0,
    interval_seconds=elapsed,
    host_busy_percent=round(100*(sum(delta)-delta[3]-delta[4])/sum(delta), 3),
    host_iowait_percent=round(100*delta[4]/sum(delta), 3),
    memory_available_gib=round(int(memory['MemAvailable'].split()[0])/2**20, 3),
    disk_available_gib=round(after['bytes_free']/2**30, 3),
    inodes_available=after['inodes_free'],
    scope='latest two existing samples; host CPU includes unrelated processes')
if role.startswith('obj'):
    prev = fields(old/'rustfs-cgroup-cpu.stat.txt')
    cur = fields(new/'rustfs-cgroup-cpu.stat.txt')
    used = int(cur['usage_usec'])-int(prev['usage_usec'])
    throttled = int(cur['throttled_usec'])-int(prev['throttled_usec'])
    if min(used, throttled) < 0:
        raise SystemExit('RustFS CPU counter reset')
    value['rustfs'] = dict(cpu_cores=round(used/1e6/elapsed, 3),
        throttled_seconds=round(throttled/1e6, 3),
        memory_gib=round(int((new/'rustfs-cgroup-memory.current.txt').read_text())/2**30, 3),
        memory_events={k:int(v) for k,v in fields(new/'rustfs-cgroup-memory.events.txt').items()})
if role == 'obj4':
    prev, cur = [fields(d/'redis-info.txt', True) for d in (old,new)]
    used = sum(float(cur[k])-float(prev[k]) for k in ('used_cpu_sys','used_cpu_user'))
    ops = int(cur['total_commands_processed'])-int(prev['total_commands_processed'])
    if min(used, ops) < 0:
        raise SystemExit('Redis CPU or operation counter reset')
    value['redis'] = dict(cpu_cores=round(used/elapsed, 3),commands_per_second=round(ops/elapsed, 3),
        used_memory_gib=round(int(cur['used_memory'])/2**30, 3),
        aof_delayed_fsync=int(cur['aof_delayed_fsync']),
        rejected_connections=int(cur['rejected_connections']))
print(json.dumps(value))
'@
$rows = foreach ($peer in $roles.Keys) {
    $raw = $code | ssh -T -o BatchMode=yes -o ConnectTimeout=8 $peer "python3 - '$Run' '$($roles[$peer])'"
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect existing samples on $peer" }
    ($raw -join "`n") | ConvertFrom-Json
}
$folder = Join-Path (Split-Path -Parent $PSScriptRoot) "receipts/$Run"
$path = Join-Path $folder ('live-resources-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '.json')
$json = ConvertTo-Json -InputObject @($rows) -Depth 6
[IO.File]::WriteAllText($path, $json, [Text.UTF8Encoding]::new($false))
$json
Write-Output $path
