param(
    [Parameter(Mandatory)][string]$SshConfig,
    [Parameter(Mandatory)][string]$ReceiptRoot,
    [ValidateRange(20,255)][int]$Database = 243,
    [ValidateRange(0,32)][int]$Workers = 0,
    [ValidateRange(60,900)][int]$VerifySeconds = 480,
    [string]$ParallelBinary,
    [switch]$TracePackedBudget,
    [ValidateSet(0,128)][int]$PackedOutputMib = 0,
    [ValidateSet(0,8)][int]$PackedControlMib = 0
)

$ErrorActionPreference = 'Stop'
$tag = 'c3-current-base-' + (Get-Date -AsUTC -Format 'yyyyMMddTHHmmssZ').ToLowerInvariant()
$dest = "/srv/ax-pilot-control/preparation/$tag"
$local = Join-Path (Resolve-Path -LiteralPath $ReceiptRoot) $tag
$cfg = (Resolve-Path -LiteralPath $SshConfig).Path
$ids = @('i-bp1809mw7rlujtj9f705','i-bp1gy3cfuj9jik0y4oc3','i-bp1h9kaoya0predubdkn')
$idJson = ConvertTo-Json -Compress -InputObject $ids
$targets = @('--InstanceId.1',$ids[0],'--InstanceId.2',$ids[1],'--InstanceId.3',$ids[2])

function Read-Cloud([string]$Name) {
    $raw = aliyun ecs DescribeInstances --RegionId cn-hangzhou --InstanceIds $idJson
    if ($LASTEXITCODE) { throw 'DescribeInstances failed' }
    $cloud = $raw | ConvertFrom-Json
    if (!$cloud.RequestId -or $cloud.TotalCount -ne 3 -or (Compare-Object $ids @($cloud.Instances.Instance.InstanceId))) {
        throw 'API selection not exact'
    }
    $raw | Set-Content -Encoding utf8NoBOM "$local/$Name.json"
    return @($cloud.Instances.Instance)
}
function Get-Unit {
    $state = ssh -F $cfg -oBatchMode=yes -oConnectTimeout=6 ax-pilot-jump "systemctl show $tag.service -p LoadState -p ActiveState -p SubState -p Result -p MainPID -p ExecMainStatus"
    if ($LASTEXITCODE) { throw 'Coordinator unit unavailable' }
    return ($state -join [Environment]::NewLine)
}

if ($Database -eq 70) { throw 'Database 70 owns the original Packed asset' }
if ($PackedOutputMib -and (!$TracePackedBudget -or $Workers -ne 32)) {
    throw 'The 128 MiB qualification must preserve the 32-worker input and trace budget rejections'
}
if ($PackedControlMib -and (!$ParallelBinary -or $PackedOutputMib -ne 128)) {
    throw 'The 8 MiB control probe requires the diagnostic verifier and the fixed 128 MiB output budget'
}
New-Item -ItemType Directory -Path $local -ErrorAction Stop | Out-Null
$before = Read-Cloud 'instances-before'
if ($before | Where-Object {$_.Status -ne 'Stopped' -or $_.StoppedMode -ne 'StopCharging' -or $_.ZoneId -ne 'cn-hangzhou-j'}) {
    throw 'Pool A instances must all be stopped in the original zone'
}
$files = @("$PSScriptRoot/run-c3-workspace-base.py","$PSScriptRoot/qualify-c3-workspace-base.py")
$guardSha = (Get-FileHash -Algorithm SHA256 $files[0]).Hash.ToLowerInvariant()
$workerSha = (Get-FileHash -Algorithm SHA256 $files[1]).Hash.ToLowerInvariant()
$parallelSha = $null
if ($ParallelBinary) {
    if (!$Workers) { throw 'A custom parallel binary requires positive Workers' }
    $file = Get-Item -LiteralPath $ParallelBinary
    if ($file.PSIsContainer -or $file.LinkType -or $file.Length -lt 4 -or $file.Length -gt 8MB) {
        throw 'Expected a regular verifier binary no larger than 8 MiB'
    }
    $parallelSha = (Get-FileHash -Algorithm SHA256 $file.FullName).Hash.ToLowerInvariant()
}
ssh -F $cfg -oBatchMode=yes -oConnectTimeout=8 ax-pilot-jump "mkdir -m 700 $dest"
if ($LASTEXITCODE) { throw 'Unique staging directory required' }
scp -q -F $cfg -oBatchMode=yes -oConnectTimeout=8 @files "ax-pilot-jump:$dest/"
if ($LASTEXITCODE) { throw 'C3 script transfer failed' }
if ($parallelSha) {
    scp -q -F $cfg -oBatchMode=yes -oConnectTimeout=8 $file.FullName "ax-pilot-jump:$dest/smallfiles-verify"
    if ($LASTEXITCODE) { throw 'C3 verifier transfer failed before ECS start' }
}
$validate = "set -eu; cd $dest; echo '$guardSha  run-c3-workspace-base.py' | sha256sum -c -; echo '$workerSha  qualify-c3-workspace-base.py' | sha256sum -c -; python3 -m py_compile run-c3-workspace-base.py qualify-c3-workspace-base.py"
ssh -F $cfg -oBatchMode=yes -oConnectTimeout=8 ax-pilot-jump $validate
if ($LASTEXITCODE) { throw 'Remote staged file validation failed' }
[pscustomobject]@{tag=$tag; dest=$dest; guard_sha256=$guardSha; worker_sha256=$workerSha; database=$Database; workers=$Workers; verify_seconds=$VerifySeconds; parallel_sha256=$parallelSha; trace_packed_budget=[bool]$TracePackedBudget; packed_output_mib=$PackedOutputMib; packed_control_mib=$PackedControlMib} |
    ConvertTo-Json | Set-Content -Encoding utf8NoBOM "$local/stage.json"

$serviceSeconds = $VerifySeconds + 325
$launch = "systemd-run --unit=$tag --remain-after-exit --property=RuntimeMaxSec=${serviceSeconds}s --property=TimeoutStopSec=25s /usr/bin/python3 -B $dest/run-c3-workspace-base.py --worker-sha256 $workerSha --database $Database --workers $Workers --verify-seconds $VerifySeconds"
if ($parallelSha) { $launch += " --parallel-sha256 $parallelSha" }
if ($TracePackedBudget) { $launch += " --trace-packed-budget" }
if ($PackedOutputMib) { $launch += " --packed-output-mib $PackedOutputMib" }
if ($PackedControlMib) { $launch += " --packed-control-mib $PackedControlMib" }
ssh -F $cfg -oBatchMode=yes -oConnectTimeout=8 ax-pilot-jump $launch
if ($LASTEXITCODE) { throw 'A guard launch failed' }
$attempted = $false
try {
    $deadline = (Get-Date).AddSeconds(18)
    do {
        $ready = ssh -F $cfg -oBatchMode=yes -oConnectTimeout=6 ax-pilot-jump "test -s $dest/locks-held.json && cat $dest/locks-held.json"
        if ($LASTEXITCODE -eq 0) { break }
        if ((Get-Unit) -match 'ActiveState=failed') { throw 'Coordinator refused before starting any instance' }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if (!$ready -or (Get-Unit) -notmatch 'MainPID=[1-9]') { throw 'No active pool A lock holder' }
    $ready | Set-Content -Encoding utf8NoBOM "$local/lock-before-start.json"
    $attempted = $true
    $start = aliyun ecs StartInstances --RegionId cn-hangzhou --BatchOptimization AllTogether @targets
    $start | Set-Content -Encoding utf8NoBOM "$local/start.json"
    $start
    if ($LASTEXITCODE -or !($start | ConvertFrom-Json).RequestId) { throw 'Start request not confirmed' }
    $deadline = (Get-Date).AddSeconds($serviceSeconds+15)
    do {
        $state = Get-Unit
        if ($state -match 'MainPID=0') { break }
        Start-Sleep -Seconds 9
    } while ((Get-Date) -lt $deadline)
    $state | Set-Content -Encoding utf8NoBOM "$local/unit-result.txt"
    $state
    if ($state -notmatch 'MainPID=0' -or $state -notmatch 'Result=success') { throw "C3 qualification did not succeed: $local" }
} finally {
    try {
        ssh -F $cfg -oBatchMode=yes -oConnectTimeout=6 ax-pilot-jump "journalctl -u $tag.service --no-pager -n 35" |
            Set-Content -Encoding utf8NoBOM "$local/unit-journal.txt"
        scp -q -r -F $cfg -oBatchMode=yes -oConnectTimeout=6 "ax-pilot-jump:$dest" "$local/receipt"
        if ($LASTEXITCODE) { Write-Warning "Coordinator receipts need readback at $dest" }
    } finally {
        if ($attempted) {
            $stop = aliyun ecs StopInstances --RegionId cn-hangzhou --StoppedMode StopCharging --ForceStop false --BatchOptimization SuccessFirst @targets
            $stop | Set-Content -Encoding utf8NoBOM "$local/stop.json"
            $stop
            $deadline = (Get-Date).AddSeconds(100)
            do {
                $rows = Read-Cloud ('after-stop-' + (Get-Date -AsUTC -Format 'HHmmss'))
                $remaining = @($rows | Where-Object {$_.Status -ne 'Stopped' -or $_.StoppedMode -ne 'StopCharging'})
                if (!$remaining) { break }
                Start-Sleep -Seconds 6
            } while ((Get-Date) -lt $deadline)
            $rows | Select-Object InstanceId,Status,StoppedMode | Format-Table -AutoSize
            if ($remaining) { throw "Check remaining billable pool A hosts: $($remaining.InstanceId -join ', ')" }
        }
    }
}
