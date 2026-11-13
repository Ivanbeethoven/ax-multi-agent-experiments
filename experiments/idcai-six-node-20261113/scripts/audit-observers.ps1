param([Parameter(Mandatory)][ValidatePattern('^ax-four-[0-9a-f]{12}$')][string]$Run)

$ErrorActionPreference = 'Stop'
$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$roles = [ordered]@{'idcai-jump'='client';'idcai-client2'='target';'idcai-obj1'='obj1';'idcai-obj2'='obj2';'idcai-obj3'='obj3';'idcai-obj4'='obj4'}
$code = @'
import json, sys
from pathlib import Path

run, role = sys.argv[1:]
root = Path('/srv/ax-idcai-20261113/campaign-100k-001') / run / ('observer-' + role)
closed = root / 'finished.json'
finished = json.loads(closed.read_text()) if closed.is_file() else {}
samples = sorted(root.glob('sample-*/sample.json'))
records = [json.loads(f.read_text()) for f in samples]
times = [r['monotonic_start_ns'] for r in records]
gaps = [(b-a)/1e9 for a,b in zip(times,times[1:])]
missing = 0
client_process_samples = 0
observed_processes = set()
for path in samples:
    names = ['host-stat.txt','host-diskstats.txt','host-net-dev.txt']
    if role.startswith('obj'):
        names.append('rustfs.prom')
    if role == 'obj4':
        names += ['redis-info.txt','redis-latency.txt']
    sample = json.loads(path.read_text())
    if sample.get('client_process_collection'):
        names.append('client-processes.json')
        process_file = path.parent/'client-processes.json'
        if process_file.is_file():
            processes = json.loads(process_file.read_text())
            client_process_samples += bool(processes)
            for process in processes:
                observed_processes.add((process['pid'], process['start_ticks']))
                names.append(f"client-pid-{process['pid']}-stat.txt")
                if not process.get('exited_during_sample'):
                    names += [f"client-pid-{process['pid']}-{kind}.txt" for kind in ('status','io','cgroup')]
    missing += sum(not (path.parent / name).is_file() for name in names)
errors = root / 'errors.jsonl'
error_count = len(errors.read_text().splitlines()) if errors.is_file() else 0
boots = {r['boot_id'] for r in records}
print(json.dumps(dict(role=role, closed=bool(finished), samples=len(records), expected=finished.get('samples_attempted'),
                      client_process_samples=client_process_samples, observed_process_count=len(observed_processes),
                      error_samples=error_count, missing_raw_files=missing, max_gap_seconds=max(gaps, default=None),
                      passed=bool(finished.get('passed') and len(records) >= 2 and len(boots) == 1 and
                                  all(0 < gap <= 30 for gap in gaps) and not missing and not error_count and
                                  finished.get('samples_attempted') == len(records)))))
'@
$rows = foreach ($peer in $roles.Keys) {
    $role = $roles[$peer]
    $raw = $code | & $ssh -T -o BatchMode=yes -o ConnectTimeout=30 $peer "python3 - '$Run' '$role'"
    if ($LASTEXITCODE -ne 0) { throw "Observer evidence unavailable on $peer" }
    ($raw -join "`n") | ConvertFrom-Json
}
$folder = Join-Path (Split-Path -Parent $PSScriptRoot) "receipts/$Run"
$value = [ordered]@{run=$Run; passed=(@($rows | Where-Object {-not $_.passed}).Count -eq 0); roles=$rows}
$json = ConvertTo-Json -InputObject $value -Depth 4
$name = if ($value.passed) { 'observer-audit.json' } else { 'observer-audit-failed-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '.json' }
[IO.File]::WriteAllText((Join-Path $folder $name), $json, [Text.UTF8Encoding]::new($false))
$json
if (-not $value.passed) { throw "Observer audit failed; inspect $name before continuing this campaign." }
