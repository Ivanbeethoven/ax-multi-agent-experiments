param([Parameter(Mandatory)][ValidatePattern('^ax-four-[0-9a-f]{12}$')][string]$Run)

$ErrorActionPreference = 'Stop'
$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$scp = Join-Path $env:WINDIR 'System32/OpenSSH/scp.exe'
$base = '/srv/ax-idcai-20261113'
$roles = [ordered]@{'idcai-jump'='client';'idcai-client2'='target';'idcai-obj1'='obj1';'idcai-obj2'='obj2';'idcai-obj3'='obj3';'idcai-obj4'='obj4'}
$local = Join-Path (Split-Path -Parent $PSScriptRoot) "receipts/$Run"
$closure = Get-ChildItem -LiteralPath $local -Filter 'closure-*.json' | Sort-Object Name | Select-Object -Last 1
if (-not $closure -or -not (Get-Content -LiteralPath $closure.FullName -Raw | ConvertFrom-Json).passed) {
    throw 'Run close-performance-attempt.ps1 and verify all six nodes before archiving.'
}
$c1 = Join-Path $local 'c1-comparison.json'
if (@(Get-ChildItem -LiteralPath $local -Filter 'c1-*-trial.json').Count -and -not (Test-Path -LiteralPath $c1)) {
    $summarizer = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'summarize-c1.py') -Raw
    $raw = $summarizer | & $ssh -T -o BatchMode=yes -o ConnectTimeout=30 idcai-jump "python3 - '$base/campaign-100k-001/$Run'"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot summarize closed C1 evidence' }
    $value = ($raw -join "`n") | ConvertFrom-Json
    if ($value.run -ne $Run -or $value.schema -ne 'ax-idcai-c1-summary-v1') { throw 'Unexpected C1 summary identity' }
    [IO.File]::WriteAllText($c1, ($raw -join "`n")+"`n", [Text.UTF8Encoding]::new($false))
}
$catalog = Join-Path $local 'raw-archives.json'
if (Test-Path -LiteralPath $catalog) { throw "Archive catalog already exists: $catalog" }
$rows = foreach ($peer in $roles.Keys) {
    $role = $roles[$peer]
    $script = "$base/archive-performance-node.sh"
    & $scp -B -q -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 (Join-Path $PSScriptRoot 'archive-performance-node.sh') "${peer}:$script"
    if ($LASTEXITCODE -ne 0) { throw "Cannot stage archive script on $peer" }
    $raw = & $ssh -n -T -o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=15 $peer "bash '$script' '$Run' '$role'"
    if ($LASTEXITCODE -ne 0) { throw "Archive failed on $peer" }
    $row = ($raw -join "`n") | ConvertFrom-Json
    if ($row.run -ne $Run -or $row.role -ne $role -or $row.bytes -le 0 -or $row.sha256 -notmatch '^[a-f0-9]{64}$') {
        throw "Invalid archive receipt on $peer"
    }
    [ordered]@{host_alias=$peer; run=$Run; role=$role; path=$row.path; sha256=$row.sha256; bytes=$row.bytes; service_journal=$row.service_journal}
}
[IO.File]::WriteAllText($catalog, (ConvertTo-Json -InputObject @($rows) -Depth 4), [Text.UTF8Encoding]::new($false))
Write-Output $catalog
