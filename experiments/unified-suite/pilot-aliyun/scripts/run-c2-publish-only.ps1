# Explicit -Apply is required before any billable action. No resource creation.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ReceiptRoot,
    [Parameter(Mandatory)][string]$SshConfig,
    [Parameter(Mandatory)][string]$Latest,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Resolve-Path -LiteralPath $ReceiptRoot).Path
$cfg = (Resolve-Path -LiteralPath $SshConfig).Path
$latestPath = (Resolve-Path -LiteralPath $Latest).Path
$latestProof = Get-Content -LiteralPath $latestPath -Raw | ConvertFrom-Json
$age = ([DateTimeOffset]::UtcNow-[DateTimeOffset]$latestProof.at).TotalSeconds
$source = @{
    brewfs=@('https://github.com/brewfs/brewfs','main','ca07159f3da4b670e4aeb878faef928e69bc1634')
    rustfs=@('https://github.com/Ivanbeethoven/rustfs','feat/dynamic-block-size-rebased','f4ed202508f2153afb7956bae6f7799b60eccd00')
}
if ($latestProof.passed -isnot [bool] -or !$latestProof.passed -or $age -lt 0 -or $age -gt 300 -or
    $latestProof.build_manifest_sha256 -cne 'd4d00a6aab0887fe86c75646ef260594587e95fb2728daaa93d3ee5ccabf1b6e' -or
    @($latestProof.sources).Count -ne 2) { throw 'Obtain a fresh verify-latest receipt before any start' }
foreach ($row in $latestProof.sources) {
    $pin = $source[$row.component]
    if (!$pin -or $row.repository -cne $pin[0] -or $row.branch -cne $pin[1] -or
        $row.built_commit -cne $pin[2] -or $row.remote_commit -cne $pin[2] -or
        $row.matches -isnot [bool] -or !$row.matches -or $row.lookup_exit_code -ne 0) {
        throw 'Branch tip differs or was unavailable. No cloud instance started.'
    }
    $source.Remove($row.component)
}
$jump='i-bp1cp8ccqor34u2bo5os'
$workers=@('i-bp1809mw7rlujtj9f705','i-bp1h9kaoya0predubdkn')
$metadata='i-bp1gy3cfuj9jik0y4oc3'
$roles=@{
    $jump='ax-c1-jump'
    $workers[0]='ax-pilot-20261111-client'
    $workers[1]='ax-pilot-20261111-objects'
    $metadata='ax-pilot-20261111-metadata'
}
$all=@($jump,$workers[0],$workers[1],$metadata)
$a=@($workers[0],$workers[1],$metadata)
$targets=@('--InstanceId.1',$workers[0],'--InstanceId.2',$workers[1])
$worker=Join-Path $PSScriptRoot 'c2-publish-only.py'
$restore=Join-Path $PSScriptRoot 'a-pool-up.py'
$jumpRestore=Join-Path $PSScriptRoot 'restore-existing-jump-control.py'
$reference=Join-Path $PSScriptRoot '../evidence/preparation/c2-current-trace-a-draft-01/run-c2-current-a.py'
function Hash([string]$Path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
if ((Hash $reference) -cne '6b718199077ee17241131ed03d2da3c209e689f26a3ea0ce219da0a4568a77a8' -or
    (Hash $restore) -cne '09616fb18b8f3e75ec7425d19c5171d1b138b9da00e79d15436725b028f60f3d' -or
    (Hash $jumpRestore) -cne 'ef5a5ae88b68e6977a657b3d6e64033358af10a868d89418cba04f3afb4d2141') {
    throw 'Existing recovery helper byte identity differs'
}
if (!$Apply) {
    [ordered]@{started=$false; jump=$jump; workers=$workers; remains_stopped=$metadata;
        window_seconds=720; producer_seconds=300; formal_sample=$false;
        worker_sha256=(Hash $worker); latest_sha256=(Hash $latestPath)} | ConvertTo-Json
    return
}
$tag='c2-pubdiag-'+[DateTime]::UtcNow.ToString("yyyyMMdd't'HHmmss'z'",[Globalization.CultureInfo]::InvariantCulture)
$out=Join-Path $root $tag
New-Item -ItemType Directory -Path $out -ErrorAction Stop | Out-Null
$remote="/srv/ax-pilot-control/preparation/$tag"
$sshOpts=@('-F',$cfg,'-oBatchMode=yes','-oConnectTimeout=5','-oServerAliveInterval=4','-oServerAliveCountMax=2')
$begin=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$end=$begin+720
$workBy=$end-105
function Now { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
function Write-Utf8([string]$Name,[string]$Value) {
    [IO.File]::WriteAllText((Join-Path $out $Name),$Value+[char]10,[Text.UTF8Encoding]::new($false))
}
$aliyun = (Get-Command aliyun -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
function Cloud([string[]]$ArgsList) {
    $info=[Diagnostics.ProcessStartInfo]::new($aliyun)
    $info.UseShellExecute=$false
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    foreach ($arg in @('ecs',$ArgsList[0],'--RegionId','cn-hangzhou')+@($ArgsList | Select-Object -Skip 1)) {
        $info.ArgumentList.Add($arg)
    }
    $proc=[Diagnostics.Process]::Start($info)
    try {
        $stdout=$proc.StandardOutput.ReadToEndAsync()
        $stderr=$proc.StandardError.ReadToEndAsync()
        if (!$proc.WaitForExit(12000)) {
            $proc.Kill($true)
            throw 'Cloud request timed out locally; reconcile exact instance states'
        }
        $null=$stderr.GetAwaiter().GetResult()
        return @{raw=$stdout.GetAwaiter().GetResult();code=$proc.ExitCode}
    } finally { $proc.Dispose() }
}
function Inspect([string[]]$Ids,[string]$Name) {
    $got=Cloud @('DescribeInstances','--InstanceIds',(ConvertTo-Json -Compress -InputObject $Ids),'--PageSize','100')
    $raw=$got.raw
    if ($got.code) { throw 'Exact instance read failed' }
    $api=$raw | ConvertFrom-Json
    $rows=@($api.Instances.Instance)
    if (!$api.RequestId -or $api.TotalCount -ne $Ids.Count -or $rows.Count -ne $Ids.Count -or
        (Compare-Object $Ids @($rows.InstanceId) -CaseSensitive) -or
        ($rows | Where-Object {$_.InstanceName -cne $roles[$_.InstanceId] -or $_.ZoneId -cne 'cn-hangzhou-j'})) {
        throw 'Instance identity mismatch'
    }
    if ($Name) { Write-Utf8 $Name $raw }
    return $rows
}
function Stopped($Row) { $Row.Status -ceq 'Stopped' -and $Row.StoppedMode -ceq 'StopCharging' }
function Save-Action([string]$Name,[string]$Raw,[int]$Code) {
    Write-Utf8 "$Name.json" $Raw
    if ($Code -or !($Raw | ConvertFrom-Json).RequestId) { throw "Unacknowledged $Name API" }
}
function Upload([string]$Path,[string]$Name) {
    scp -pq @sshOpts $Path "ax-pilot-jump:$remote/$Name"
    if ($LASTEXITCODE) { throw "Could not stage $Name" }
}
function Snapshot([string]$Name) {
    $json=ssh @sshOpts ax-pilot-jump "test -s $remote/$Name.json && cat $remote/$Name.json"
    if (!$LASTEXITCODE -and $json) {
        $value=$json | Out-String
        Write-Utf8 "$Name.json" $value
        return $value | ConvertFrom-Json
    }
}
function Wait-Stopped([string[]]$Ids,[long]$Until,[string]$Name) {
    do {
        try {
            $r=@(Inspect $Ids $Name)
            if (!($r | Where-Object {!(Stopped $_)})) { return $true }
        } catch { Write-Warning 'ECS stop readback unavailable; do not release the lock' }
        if ((Now) -ge $Until) { return $false }
        Start-Sleep -Seconds 3
    } while ($true)
}
if ((@(Inspect $all 'all-before.json') | Where-Object {!(Stopped $_)})) { throw 'A or jump already active' }
$jumpAttempted=$workersAttempted=$unitRequested=$false
$workersStopped=$true
$artifactOk=$false
try {
    $jumpAttempted=$true
    $api=Cloud @('StartInstance','--InstanceId',$jump)
    Save-Action 'jump-start' $api.raw $api.code
    $limit=[Math]::Min($begin+105,$workBy-425)
    do {
        $jumpRow=@(Inspect @($jump) '')[0]
        if ($jumpRow.Status -ceq 'Running') {
            ssh @sshOpts ax-pilot-jump true
            if (!$LASTEXITCODE) { break }
        }
        if ((Now) -ge $limit) { throw 'Jump not ready within window' }
        Start-Sleep -Seconds 3
    } while ($true)
    $restored=Get-Content -LiteralPath $jumpRestore -Raw -Encoding utf8 |
        ssh @sshOpts ax-pilot-jump 'python3 - --mount'
    if ($LASTEXITCODE -or !($restored | ConvertFrom-Json).passed) { throw 'Exact control disk is not available' }
    Write-Utf8 'jump-restore.json' ($restored | Out-String)
    if ((@(Inspect $a 'instances-before.json') | Where-Object {!(Stopped $_)})) { throw 'A roles are not idle' }
    ssh @sshOpts ax-pilot-jump "mkdir -m 700 $remote"
    if ($LASTEXITCODE) { throw 'Dedicated staging directory unavailable' }
    Write-Utf8 'staged-shas.json' ([ordered]@{worker=(Hash $worker);latest=(Hash $latestPath)} | ConvertTo-Json)
    Upload $worker 'c2-publish-only.py'
    Upload $reference 'reference.py'
    Upload $restore 'a-pool-up.py'
    Upload $latestPath 'latest.json'
    Upload (Join-Path $out 'instances-before.json') 'instances-before.json'
    Upload (Join-Path $out 'staged-shas.json') 'staged-shas.json'
    $unitRequested=$true
    ssh @sshOpts ax-pilot-jump "systemd-run --quiet --unit=$tag --remain-after-exit --property=Type=exec /usr/bin/python3 -B $remote/c2-publish-only.py coordinator --tag $tag --deadline-epoch $workBy"
    if ($LASTEXITCODE) { throw 'A lock holder launch was unacknowledged' }
    $limit=[Math]::Min((Now)+15,$workBy-400)
    do {
        $armed=Snapshot 'locks-held'
        if ($armed) { break }
        if ((Now) -ge $limit) { throw 'No canonical A lock; no workers started' }
        Start-Sleep -Seconds 2
    } while ($true)
    if ($armed.tag -cne $tag -or $armed.work_deadline_epoch -ne $workBy -or $armed.formal_sample -ne $false) {
        throw 'Wrong A lock acknowledgment'
    }
    if ((@(Inspect $a 'under-lock-before.json') | Where-Object {!(Stopped $_)})) { throw 'A hosts changed under lock' }
    $active=ssh @sshOpts ax-pilot-jump "systemctl show $tag.service -p MainPID --value"
    if ($LASTEXITCODE -or $active -notmatch '^[1-9][0-9]*$' -or (Now) -gt $workBy-395) { throw 'No live lock or insufficient window' }
    $workersAttempted=$true
    $workersStopped=$false
    $api=Cloud (@('StartInstances','--BatchOptimization','AllTogether')+$targets)
    Save-Action 'workers-start' $api.raw $api.code
    $limit=[Math]::Min((Now)+90,$workBy-360)
    do {
        $states=@(Inspect $a 'instances-running.json')
        if (!(Stopped ($states | Where-Object InstanceId -ceq $metadata))) { throw 'Metadata unexpectedly started; stop owned nodes only' }
        if (!($states | Where-Object {$_.InstanceId -cin $workers -and $_.Status -cne 'Running'})) {
            Upload (Join-Path $out 'instances-running.json') 'instances-running.upload'
            ssh @sshOpts ax-pilot-jump "ln $remote/instances-running.upload $remote/instances-running.json"
            if ($LASTEXITCODE) { throw 'Exact running acknowledgment was not installed' }
            break
        }
        if ((Now) -ge $limit) { throw 'Workers did not both become ready within the bounded window' }
        Start-Sleep -Seconds 3
    } while ($true)
    do {
        $ready=Snapshot 'ready-for-stop'
        if ($ready) {
            if ($ready.launched) { scp -pq -r @sshOpts "ax-pilot-jump:$remote/worker" (Join-Path $out 'worker') }
            if ($ready.launched -and !$LASTEXITCODE -and (Test-Path -LiteralPath (Join-Path $out 'worker/result.json'))) {
                $p=Snapshot 'worker-sha256'
                if (!$p) { throw 'No worker evidence hashes' }
                foreach ($entry in $p.PSObject.Properties) {
                    if ($entry.Name -cnotin @('initial.json','intent.json','process.ndjson','object-count.ndjson','producer-exit.json','object-count-final.json','result.json') -or
                        (Hash (Join-Path "$out/worker" $entry.Name)) -cne $entry.Value) { throw 'Worker evidence mismatch' }
                }
                $artifactOk=$true
            }
            break
        }
        if ((Now) -ge $workBy+12) { throw 'Work deadline reached before ready-for-stop' }
        Start-Sleep -Seconds 3
    } while ($true)
} finally {
    if ($workersAttempted) {
        try {
            $api=Cloud (@('StopInstances','--StoppedMode','StopCharging','--ForceStop','false','--BatchOptimization','SuccessFirst')+$targets)
            Save-Action 'workers-stop' $api.raw $api.code
        } catch { Write-Warning 'Worker stop acknowledgement unavailable; checking live status' }
        $workersStopped=Wait-Stopped $workers ($end-35) 'workers-final'
    }
    if ($jumpAttempted -and $workersStopped) {
        try {
            if ($unitRequested) {
                if ((Snapshot 'ready-for-stop') -and !( @(Inspect $a 'instances-after-stop.json') | Where-Object {!(Stopped $_)})) {
                    Upload (Join-Path $out 'instances-after-stop.json') 'instances-after-stop.upload'
                    ssh @sshOpts ax-pilot-jump "ln $remote/instances-after-stop.upload $remote/instances-after-stop.json"
                    if ($LASTEXITCODE) { throw 'Stop receipt could not be bound to the held locks' }
                    Start-Sleep -Seconds 3
                }
                scp -pq -r @sshOpts "ax-pilot-jump:$remote" (Join-Path $out 'coordinator')
                if ($LASTEXITCODE) { Write-Warning "Coordinator readback failed; evidence retained at $remote" }
            }
        } finally {
            try {
                $api=Cloud @('StopInstance','--InstanceId',$jump,'--ForceStop','false','--StoppedMode','StopCharging')
                Save-Action 'jump-stop' $api.raw $api.code
            } catch { Write-Warning 'Jump stop acknowledgement unavailable; checking live status' }
            if (!(Wait-Stopped @($jump) $end 'jump-final')) { throw "Jump StopCharging not proved; inspect $out" }
        }
    } elseif ($jumpAttempted) {
        throw "Workers StopCharging not proved; leave coordinator locks held and check exact instances. $out"
    }
}
$closedPath=Join-Path $out 'coordinator/closed.json'
if (!$artifactOk -or !$ready.passed -or !(Test-Path -LiteralPath $closedPath) -or
    !(Get-Content -LiteralPath $closedPath -Raw | ConvertFrom-Json).passed) {
    throw "Diagnostic failed or publication unproved; preserve its evidence at $out"
}
Write-Output $out
