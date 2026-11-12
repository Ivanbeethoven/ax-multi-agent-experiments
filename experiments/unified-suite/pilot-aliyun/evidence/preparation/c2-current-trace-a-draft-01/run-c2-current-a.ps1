# Run only with an operator-reviewed packet. Keeps the coordinator lock until exact StopCharging evidence.
param(
    [Parameter(Mandatory)][string]$SshConfig,
    [Parameter(Mandatory)][string]$ReceiptRoot,
    [Parameter(Mandatory)][string]$Latest,
    [Parameter(Mandatory)][string]$InstancesBefore,
    [Parameter(Mandatory)][string]$OperatorReview
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$review = Get-Content -LiteralPath $OperatorReview -Raw | ConvertFrom-Json
$tag = $review.tag
$db = $review.database
if ($review.schema -ne 'c2-current-a-operator-review-v1' -or $review.reviewed -isnot [bool] -or
    !$review.reviewed -or [string]::IsNullOrWhiteSpace($review.reviewer) -or
    $review.scope -ne 'A-only stock workspace-v1 Packed C2 qualification, 45 minute cap' -or
    $tag -cnotmatch '^c2-current-a-[0-9]{8}t[0-9]{6}z$' -or $db -isnot [long] -and $db -isnot [int] -or
    $db -lt 20 -or $db -gt 255 -or $db -in @(54,70,243)) { throw 'Exact external operator review required' }

$controller = Join-Path $PSScriptRoot 'run-c2-current-a.py'
$worker = Join-Path $PSScriptRoot 'qualify-c2-current-a.py'
function Hash([string]$Path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
if ((Hash $controller) -cne $review.controller_sha256 -or (Hash $worker) -cne $review.worker_sha256 -or
    (Hash $Latest) -cne $review.latest_sha256 -or (Hash $InstancesBefore) -cne $review.before_sha256) {
    throw 'Operator review does not bind the supplied files'
}
$beforeAge = ([DateTime]::UtcNow-(Get-Item -LiteralPath $InstancesBefore).LastWriteTimeUtc).TotalSeconds
if ($beforeAge -lt 0 -or $beforeAge -gt 300) {
    throw 'Obtain a current before-state read and bind it in the review'
}
$local = Join-Path (Resolve-Path -LiteralPath $ReceiptRoot) $tag
New-Item -ItemType Directory -Path $local -ErrorAction Stop | Out-Null
$dest = "/srv/ax-pilot-control/preparation/$tag"
$cfg = (Resolve-Path -LiteralPath $SshConfig).Path
$sshArgs = @('-F',$cfg,'-oBatchMode=yes','-oConnectTimeout=5','-oServerAliveInterval=4','-oServerAliveCountMax=2')
$ids = @('i-bp1809mw7rlujtj9f705','i-bp1gy3cfuj9jik0y4oc3','i-bp1h9kaoya0predubdkn')
$targets = @('--InstanceId.1',$ids[0],'--InstanceId.2',$ids[1],'--InstanceId.3',$ids[2])
$idJson = ConvertTo-Json -Compress -InputObject $ids

function Save-Cloud([string]$Name) {
    $raw = aliyun ecs DescribeInstances --RegionId cn-hangzhou --InstanceIds $idJson
    if ($LASTEXITCODE) { throw 'Exact DescribeInstances failed' }
    $data = $raw | ConvertFrom-Json
    $rows = @($data.Instances.Instance)
    if (!$data.RequestId -or $data.TotalCount -ne 3 -or $rows.Count -ne 3 -or
        (Compare-Object $ids @($rows.InstanceId)) -or ($rows | Where-Object {$_.ZoneId -ne 'cn-hangzhou-j'})) {
        throw 'Unexpected instance IDs or zone'
    }
    $raw | Set-Content -Encoding utf8NoBOM (Join-Path $local "$Name.json")
    return @($rows | Where-Object {$_.Status -ne 'Stopped' -or $_.StoppedMode -ne 'StopCharging'})
}

# Never stage into a shadow directory on the jump system disk. This call is read-only.
$jumpCheck = Get-Content -LiteralPath (Join-Path $PSScriptRoot '../../../scripts/restore-existing-jump-control.py') -Raw -Encoding utf8 |
    ssh @sshArgs ax-pilot-jump "python3 - && mkdir -m 700 $dest"
if ($LASTEXITCODE -or !$jumpCheck -or !(($jumpCheck | ConvertFrom-Json).passed)) {
    throw 'Exact jump control disk or fresh staging directory not proved; no A workers started'
}
$jumpCheck | Set-Content -Encoding utf8NoBOM (Join-Path $local 'jump-control.json')
$files = [ordered]@{
    'run-c2-current-a.py'=$controller; 'qualify-c2-current-a.py'=$worker
    'a-pool-up.py'=(Join-Path $PSScriptRoot '../../../scripts/a-pool-up.py')
    'latest.json'=$Latest; 'instances-before.json'=$InstancesBefore; 'operator.reviewed.json'=$OperatorReview
}
foreach ($file in $files.GetEnumerator()) {
    scp -pq @sshArgs $file.Value "ax-pilot-jump:$dest/$($file.Key)"
    if ($LASTEXITCODE) { throw 'C2 staging failed before any ECS action' }
}
ssh @sshArgs ax-pilot-jump "systemd-run --unit=$tag --remain-after-exit --property=Type=exec /usr/bin/python3 -B $dest/run-c2-current-a.py --database $db"
if ($LASTEXITCODE) { throw 'A lock owner unit did not start' }
$attempted = $false
try {
    $armBy = (Get-Date).AddSeconds(20)
    do {
        $armed = ssh @sshArgs ax-pilot-jump "test -s $dest/locks-held.json && cat $dest/locks-held.json"
        if (!$LASTEXITCODE) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $armBy)
    if (!$armed) { throw 'No canonical lock acknowledgement; no ECS start requested' }
    $arm = $armed | ConvertFrom-Json
    if ($arm.tag -cne $tag -or $arm.stop_by_epoch - $arm.armed_epoch -ne 2700) { throw 'Wrong lock/window' }
    if (@(Save-Cloud 'instances-before-start-live').Count) { throw 'An A host is not stopped under lock' }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($now - $arm.armed_epoch -gt 45 -or $now -lt $arm.armed_epoch - 4) { throw 'Launch window not fresh' }
    $active = ssh @sshArgs ax-pilot-jump "systemctl show $tag.service -p MainPID --value"
    if ($LASTEXITCODE -or $active -notmatch '^[1-9][0-9]*$') { throw 'A lock holder is gone' }
    $attempted = $true
    $start = aliyun ecs StartInstances --RegionId cn-hangzhou --BatchOptimization AllTogether @targets
    $code = $LASTEXITCODE
    $start | Set-Content -Encoding utf8NoBOM (Join-Path $local 'start.json')
    if ($code -or !($start | ConvertFrom-Json).RequestId) { throw 'Start request failed or is ambiguous' }
    $returnAt = [DateTimeOffset]::FromUnixTimeSeconds($arm.stop_by_epoch-90).UtcDateTime
    do {
        $ready = ssh @sshArgs ax-pilot-jump "test -s $dest/ready-for-stop.json && cat $dest/ready-for-stop.json"
        if (!$LASTEXITCODE) {
            $ready | Set-Content -Encoding utf8NoBOM (Join-Path $local 'ready-for-stop.json')
            break
        }
        $active = ssh @sshArgs ax-pilot-jump "systemctl show $tag.service -p MainPID --value"
        if ($LASTEXITCODE -or $active -notmatch '^[1-9][0-9]*$') {
            throw 'Pool lock holder disappeared during the window'
        }
        Start-Sleep -Seconds 8
    } while ((Get-Date).ToUniversalTime() -lt $returnAt)
} finally {
    if ($attempted) {
        $stop = aliyun ecs StopInstances --RegionId cn-hangzhou --StoppedMode StopCharging --ForceStop false --BatchOptimization SuccessFirst @targets
        $stop | Set-Content -Encoding utf8NoBOM (Join-Path $local 'stop.json')
    }
    $until = (Get-Date).AddSeconds(135)
    $stopped = $false
    do {
        $stamp = Get-Date -AsUTC -Format 'yyyyMMddTHHmmssZ'
        try {
            $remaining = @(Save-Cloud "after-stop-$stamp")
            if (!$remaining) { $stopped = $true; break }
        } catch {
            Write-Warning 'Cloud status unavailable; lock stays held'
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $until)
    if (!$stopped) { throw "A StopCharging not confirmed. Check cloud state now; lock remains held: $dest" }
    $waitReady = (Get-Date).AddSeconds(65)
    do {
        ssh @sshArgs ax-pilot-jump "test -s $dest/ready-for-stop.json"
        if (!$LASTEXITCODE) { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $waitReady)
    if ($LASTEXITCODE -eq 0) {
        if (@(Save-Cloud 'instances-after-stop').Count) { throw 'A changed before closing the lock' }
        scp -q @sshArgs (Join-Path $local 'instances-after-stop.json') "ax-pilot-jump:$dest/instances-after-stop.upload"
        if ($LASTEXITCODE) { throw "StopCharging proved locally but lock acknowledgment transfer failed: $dest" }
        ssh @sshArgs ax-pilot-jump "ln $dest/instances-after-stop.upload $dest/instances-after-stop.json"
        if ($LASTEXITCODE) { throw "StopCharging proved locally but atomic lock acknowledgment failed: $dest" }
        $closeBy = (Get-Date).AddSeconds(12)
        do {
            ssh @sshArgs ax-pilot-jump "test -s $dest/closed.json"
            if (!$LASTEXITCODE) { break }
            Start-Sleep -Seconds 2
        } while ((Get-Date) -lt $closeBy)
    } else {
        Write-Warning "A stopped; coordinator closure status needs readback at $dest"
    }
    scp -q -r @sshArgs "ax-pilot-jump:$dest" (Join-Path $local 'coordinator')
    if ($LASTEXITCODE) { throw "A stopped; retrieve coordinator evidence at $dest" }
}
$closed = Join-Path $local 'coordinator/closed.json'
if (!(Test-Path -LiteralPath $closed) -or !(Get-Content -LiteralPath $closed -Raw | ConvertFrom-Json).passed) {
    throw "Qualification incomplete; preserve the first attempt at $local"
}
$closed
