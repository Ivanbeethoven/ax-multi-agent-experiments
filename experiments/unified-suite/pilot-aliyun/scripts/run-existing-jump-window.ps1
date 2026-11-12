param(
    [Parameter(Mandatory)][string]$ReceiptRoot,
    [Parameter(Mandatory)][string]$SshConfig,
    [Parameter(Mandatory)][ValidateSet('C3Cohort','C1Reverse')][string]$Activity
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ReceiptRoot).Path
$cfg = (Resolve-Path -LiteralPath $SshConfig).Path
$jump = 'i-bp1cp8ccqor34u2bo5os'
$journal = Join-Path $root ("window-" + $Activity.ToLowerInvariant() + "-" + (Get-Date -AsUTC -Format yyyyMMddTHHmmssZ))
New-Item -ItemType Directory -Path $journal -ErrorAction Stop | Out-Null

function Read-Jump([string]$Name) {
    $raw = aliyun ecs DescribeInstances --RegionId cn-hangzhou --InstanceIds ('["'+$jump+'"]') | Out-String
    if ($LASTEXITCODE) { throw 'Cannot verify jump instance' }
    $raw | Set-Content -Encoding utf8NoBOM "$journal/$Name.json"
    $report = $raw | ConvertFrom-Json
    $rows = @($report.Instances.Instance)
    if (!$report.RequestId -or $rows.Count -ne 1 -or $rows[0].InstanceId -ne $jump -or
        $rows[0].InstanceName -ne 'ax-c1-jump') { throw 'Jump identity mismatch' }
    return $rows[0]
}

$before = Read-Jump before
if ($before.Status -ne 'Stopped' -or $before.StoppedMode -ne 'StopCharging') { throw 'Jump is already in use' }
$owned = $false
try {
    $owned = $true
    $raw = aliyun ecs StartInstance --RegionId cn-hangzhou --InstanceId $jump | Out-String
    $raw | Set-Content -Encoding utf8NoBOM "$journal/start.json"
    if ($LASTEXITCODE -or !($raw | ConvertFrom-Json).RequestId) { throw 'Jump start not acknowledged' }
    Write-Output "Jump window $Activity, receipt $journal"
    $deadline = (Get-Date).AddSeconds(115)
    $ready = $false
    do {
        $row = Read-Jump last-ready
        if ($row.Status -eq 'Running') {
            ssh -F $cfg -oBatchMode=yes -oConnectTimeout=5 ax-pilot-jump true
            if ($LASTEXITCODE -eq 0) { $ready=$true; break }
        }
        Start-Sleep -Seconds 4
    } while ((Get-Date) -lt $deadline)
    if (!$ready) { throw 'Jump did not become ready within 115 seconds' }
    $restore = Get-Content -LiteralPath "$PSScriptRoot/restore-existing-jump-control.py" -Raw -Encoding utf8 |
        ssh -F $cfg -oBatchMode=yes -oConnectTimeout=8 ax-pilot-jump 'python3 - --mount'
    $restore | Set-Content -Encoding utf8NoBOM "$journal/restore.json"
    if ($LASTEXITCODE -or !($restore | ConvertFrom-Json).passed) { throw 'Existing control disk is not ready' }
    if ($Activity -eq 'C3Cohort') {
        & "$PSScriptRoot/run-c3-workspace-base.ps1" -SshConfig $cfg -ReceiptRoot $root -Database 0 -Workers 32 -VerifySeconds 240 -TracePackedBudget -PackedOutputMib 128 -PackedControlMib 8 -DiagnosticCohort
    } else {
        $full = $journal.Replace('\','/')
        if ($full -notmatch '^([A-Za-z]):(/.+)$') { throw 'Expected a drive-backed receipt path' }
        $out = '/mnt/' + $Matches[1].ToLowerInvariant() + $Matches[2] + '/attempt'
        $repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../../..')).Path
        Push-Location -LiteralPath $repo
        try {
            $tool = 'experiments/unified-suite/pilot-aliyun/scripts/run-b-c1-scratch.py'
            $prior = 'experiments/unified-suite/pilot-aliyun/evidence/preparation/c1-b-randrw-scratch-02'
            wsl.exe -d Ubuntu-24.04 -u hxy -- python3 -B $tool --order brewfs-first --match-run $prior --out $out --apply
            if ($LASTEXITCODE) { throw "C1 did not close successfully; inspect $journal/attempt" }
            wsl.exe -d Ubuntu-24.04 -u hxy -- python3 -B experiments/unified-suite/pilot-aliyun/scripts/audit-b-c1-scratch.py $out --out "$out/audit.json"
            if ($LASTEXITCODE) { throw "C1 attempt closed but audit failed: $journal/attempt" }
        } finally {
            Pop-Location
        }
    }
} finally {
    if ($owned) {
        $row = Read-Jump pre-stop
        if ($row.Status -ne 'Stopped' -or $row.StoppedMode -ne 'StopCharging') {
            $raw = aliyun ecs StopInstance --RegionId cn-hangzhou --InstanceId $jump --ForceStop false --StoppedMode StopCharging | Out-String
            $raw | Set-Content -Encoding utf8NoBOM "$journal/stop.json"
            if ($LASTEXITCODE -or !($raw | ConvertFrom-Json).RequestId) { Write-Warning 'Jump stop unacknowledged; checking exact ID' }
        }
        $deadline = (Get-Date).AddSeconds(110)
        do {
            $row = Read-Jump final
            if ($row.Status -eq 'Stopped' -and $row.StoppedMode -eq 'StopCharging') { break }
            Start-Sleep -Seconds 5
        } while ((Get-Date) -lt $deadline)
        Write-Output "$($row.InstanceName): $($row.Status)/$($row.StoppedMode)"
        if ($row.Status -ne 'Stopped' -or $row.StoppedMode -ne 'StopCharging') { throw 'Verify jump stop immediately' }
    }
}
