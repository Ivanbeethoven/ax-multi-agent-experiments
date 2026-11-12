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
function Now { $begin+[long][Math]::Floor($clock.Elapsed.TotalSeconds) }
function Write-Utf8([string]$Name,[string]$Value) {
    [IO.File]::WriteAllText((Join-Path $out $Name),$Value+[char]10,[Text.UTF8Encoding]::new($false))
}
$aliExe=(Get-Command aliyun -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$sshExe=(Get-Command ssh -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$scpExe=(Get-Command scp -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$clock=[Diagnostics.Stopwatch]::StartNew()

function Native([string]$Exe,[string[]]$Argv,[long]$Until,[int]$Cap,[string]$InputText) {
    $budget=[int][Math]::Min($Cap*1000,($Until-$begin)*1000-$clock.ElapsedMilliseconds)
    if ($budget -le 0) { throw [TimeoutException]::new('Local operation window exhausted') }
    $timer=[Diagnostics.Stopwatch]::StartNew()
    $info=[Diagnostics.ProcessStartInfo]::new($Exe)
    $info.UseShellExecute=$false
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    $info.RedirectStandardInput=($InputText.Length -gt 0)
    if ($info.RedirectStandardInput) { $info.StandardInputEncoding=[Text.UTF8Encoding]::new($false) }
    foreach ($arg in $Argv) { $info.ArgumentList.Add($arg) }
    $proc=[Diagnostics.Process]::Start($info)
    try {
        $stdout=$proc.StandardOutput.ReadToEndAsync()
        $stderr=$proc.StandardError.ReadToEndAsync()
        if ($info.RedirectStandardInput) {
            $w=$proc.StandardInput.WriteAsync($InputText)
            if (!$w.Wait([int][Math]::Max(0,$budget-$timer.ElapsedMilliseconds))) {
                throw [TimeoutException]::new('Native operation input timed out')
            }
            $proc.StandardInput.Close()
        }
        if (!$proc.WaitForExit([int][Math]::Max(0,$budget-$timer.ElapsedMilliseconds)) -or
            !$stdout.Wait([int][Math]::Max(0,$budget-$timer.ElapsedMilliseconds)) -or
            !$stderr.Wait([int][Math]::Max(0,$budget-$timer.ElapsedMilliseconds))) {
            throw [TimeoutException]::new('Native operation timed out')
        }
        return @{raw=$stdout.GetAwaiter().GetResult();code=$proc.ExitCode}
    } finally {
        if (!$proc.HasExited) {
            try { $proc.Kill($true) } catch { Write-Warning 'Local timed command termination was not acknowledged' }
        }
        $proc.Dispose()
    }
}
function Cloud([string[]]$Argv,[long]$Until) {
    Native $aliExe (@('ecs',$Argv[0],'--RegionId','cn-hangzhou')+@($Argv | Select-Object -Skip 1)) $Until 12
}
function Remote([string]$Command,[long]$Until,[int]$Cap=8,[string]$InputText) {
    Native $sshExe ($sshOpts+@('ax-pilot-jump',$Command)) $Until $Cap $InputText
}
function Copy-Secure([string]$Source,[string]$Destination,[long]$Until,[switch]$Tree) {
    $flags=@('-pq')
    if ($Tree) { $flags+= '-r' }
    Native $scpExe ($flags+$sshOpts+@($Source,$Destination)) $Until 12
}
function Inspect([string[]]$Ids,[string]$Name,[long]$Until) {
    $got=Cloud @('DescribeInstances','--InstanceIds',(ConvertTo-Json -Compress -InputObject $Ids),'--PageSize','100') $Until
    if ($got.code) { throw 'Exact instance read failed' }
    $api=$got.raw | ConvertFrom-Json
    $rows=@($api.Instances.Instance)
    if (!$api.RequestId -or $api.TotalCount -ne $Ids.Count -or $rows.Count -ne $Ids.Count -or
        (Compare-Object $Ids @($rows.InstanceId) -CaseSensitive) -or
        ($rows | Where-Object {$_.InstanceName -cne $roles[$_.InstanceId] -or $_.ZoneId -cne 'cn-hangzhou-j'})) {
        throw 'Instance identity mismatch'
    }
    if ($Name) { Write-Utf8 $Name $got.raw }
    return $rows
}
function Stopped($Row) { $Row.Status -ceq 'Stopped' -and $Row.StoppedMode -ceq 'StopCharging' }
function Save-Action([string]$Name,$Response) {
    Write-Utf8 "$Name.json" $Response.raw
    if ($Response.code -or !($Response.raw | ConvertFrom-Json).RequestId) { throw "Unacknowledged $Name API" }
}
function Upload([string]$Path,[string]$Name,[long]$Until) {
    if ((Copy-Secure $Path "ax-pilot-jump:$remote/$Name" $Until).code) { throw "Could not stage $Name" }
}
function Snapshot([string]$Name,[long]$Until) {
    $file=Remote "test -s $remote/$Name.json && cat $remote/$Name.json" $Until
    if (!$file.code -and $file.raw) {
        Write-Utf8 "$Name.json" $file.raw
        return $file.raw | ConvertFrom-Json
    }
}
function Sleep-Within([long]$Until,[int]$Seconds) {
    $ms=[int][Math]::Min($Seconds*1000,($Until-$begin)*1000-$clock.ElapsedMilliseconds)
    if ($ms -gt 0) { Start-Sleep -Milliseconds $ms }
}
function Wait-Stopped([string[]]$Ids,[long]$Until,[string]$Name) {
    while ((Now) -lt $Until) {
        try {
            $r=@(Inspect $Ids $Name $Until)
            if (!($r | Where-Object {!(Stopped $_)})) { return $true }
        } catch { Write-Warning 'ECS stop readback unavailable' }
        Sleep-Within $Until 3
    }
    return $false
}
function Stall([string]$Phase,[string]$Detail) {
    Write-Utf8 'cleanup-stalled.json' ([ordered]@{status='stalled_cleanup'; phase=$Phase; detail=$Detail;
        attempt=$tag; jump=$jump; owned_workers=$workers; metadata_not_owned=$metadata;
        at=[DateTimeOffset]::UtcNow.ToString('o'); formal_sample=$false} | ConvertTo-Json)
    throw "Cleanup stalled at $Phase; see $out/cleanup-stalled.json"
}
if ((@(Inspect $all 'all-before.json' ($begin+15)) | Where-Object {!(Stopped $_)})) { throw 'A or jump already active' }
$jumpAttempted=$workersAttempted=$unitRequested=$artifactOk=$false
$ready=$null
$stagingBy=$workBy-408
$artifactBy=$end-80
try {
    $jumpAttempted=$true
    Save-Action 'jump-start' (Cloud @('StartInstance','--InstanceId',$jump) ($begin+30))
    $limit=[Math]::Min($begin+105,$stagingBy-40)
    do {
        $jumpRow=@(Inspect @($jump) '' $limit)[0]
        if ($jumpRow.Status -ceq 'Running' -and !(Remote 'true' $limit).code) { break }
        Sleep-Within $limit 3
    } while ((Now) -lt $limit)
    if ((Now) -ge $limit) { throw 'Jump not ready within window' }
    $restored=Remote 'python3 - --mount' ([Math]::Min((Now)+58,$stagingBy-20)) 58 (Get-Content -LiteralPath $jumpRestore -Raw -Encoding utf8)
    if ($restored.code -or !($restored.raw | ConvertFrom-Json).passed) { throw 'Exact control disk is not available' }
    Write-Utf8 'jump-restore.json' $restored.raw
    if ((@(Inspect $a 'instances-before.json' $stagingBy) | Where-Object {!(Stopped $_)})) { throw 'A roles are not idle' }
    if ((Remote "mkdir -m 700 $remote" $stagingBy).code) { throw 'Dedicated staging directory unavailable' }
    Write-Utf8 'staged-shas.json' ([ordered]@{worker=(Hash $worker);latest=(Hash $latestPath)} | ConvertTo-Json)
    Upload $worker 'c2-publish-only.py' $stagingBy
    Upload $reference 'reference.py' $stagingBy
    Upload $restore 'a-pool-up.py' $stagingBy
    Upload $latestPath 'latest.json' $stagingBy
    Upload (Join-Path $out 'instances-before.json') 'instances-before.json' $stagingBy
    Upload (Join-Path $out 'staged-shas.json') 'staged-shas.json' $stagingBy
    $unitRequested=$true
    if ((Remote "systemd-run --quiet --unit=$tag --remain-after-exit --property=Type=exec /usr/bin/python3 -B $remote/c2-publish-only.py coordinator --tag $tag --deadline-epoch $workBy" $stagingBy).code) {
        throw 'A lock holder launch was unacknowledged'
    }
    $startBy=$workBy-395
    $limit=[Math]::Min((Now)+15,$startBy-5)
    do {
        $armed=Snapshot 'locks-held' $limit
        if ($armed) { break }
        Sleep-Within $limit 2
    } while ((Now) -lt $limit)
    if (!$armed -or $armed.tag -cne $tag -or $armed.work_deadline_epoch -ne $workBy -or $armed.formal_sample -ne $false) {
        throw 'Missing or wrong canonical A lock acknowledgment; no workers started'
    }
    if ((@(Inspect $a 'under-lock-before.json' $startBy) | Where-Object {!(Stopped $_)})) { throw 'A hosts changed under lock' }
    $active=Remote "systemctl show $tag.service -p MainPID --value" $startBy
    if ($active.code -or $active.raw.Trim() -notmatch '^[1-9][0-9]*$' -or (Now) -ge $startBy) {
        throw 'No live lock or insufficient window'
    }
    $workersAttempted=$true
    Save-Action 'workers-start' (Cloud (@('StartInstances','--BatchOptimization','AllTogether')+$targets) $startBy)
    $limit=[Math]::Min((Now)+90,$workBy-360)
    do {
        $states=@(Inspect $a 'instances-running.json' $limit)
        if (!(Stopped ($states | Where-Object InstanceId -ceq $metadata))) { throw 'Metadata unexpectedly started; stop owned nodes only' }
        if (!($states | Where-Object {$_.InstanceId -cin $workers -and $_.Status -cne 'Running'})) {
            Upload (Join-Path $out 'instances-running.json') 'instances-running.upload' $limit
            if ((Remote "ln $remote/instances-running.upload $remote/instances-running.json" $limit).code) {
                throw 'Exact running acknowledgment was not installed'
            }
            break
        }
        Sleep-Within $limit 3
    } while ((Now) -lt $limit)
    if ((Now) -ge $limit) { throw 'Workers did not both become ready within the bounded window' }
    $pollBy=$workBy+12
    do {
        $ready=Snapshot 'ready-for-stop' $pollBy
        if ($ready) { break }
        Sleep-Within $pollBy 3
    } while ((Now) -lt $pollBy)
    if (!$ready) { throw 'Work deadline reached before ready-for-stop' }
} finally {
    if ($unitRequested) {
        try {
            $ready=Snapshot 'ready-for-stop' $artifactBy
            if ($ready.launched) {
                $got=Copy-Secure "ax-pilot-jump:$remote/worker" (Join-Path $out 'worker') $artifactBy -Tree
                if ($got.code -or !(Test-Path -LiteralPath (Join-Path $out 'worker/result.json'))) {
                    throw 'Worker evidence pull incomplete'
                }
                $p=Snapshot 'worker-sha256' $artifactBy
                if (!$p) { throw 'No worker evidence hashes' }
                foreach ($entry in $p.PSObject.Properties) {
                    if ($entry.Name -cnotin @('initial.json','intent.json','process.ndjson','object-count.ndjson','producer-exit.json','object-count-final.json','result.json') -or
                        (Hash (Join-Path "$out/worker" $entry.Name)) -cne $entry.Value) { throw 'Worker evidence mismatch' }
                }
                $artifactOk=$true
            }
        } catch {
            try { Write-Utf8 'pre-stop-evidence-warning.json' (@{incomplete=$true; exception=$_.Exception.GetType().Name} | ConvertTo-Json) }
            catch { Write-Warning 'Local pre-stop warning receipt could not be written' }
            Write-Warning 'Pre-stop evidence pull incomplete; continue StopCharging'
        }
    }
    if ($workersAttempted) {
        try {
            Save-Action 'workers-stop' (Cloud (@('StopInstances','--StoppedMode','StopCharging','--ForceStop','false','--BatchOptimization','SuccessFirst')+$targets) ((Now)+12))
        } catch { Write-Warning 'Worker stop unacknowledged; checking exact A state' }
    }
    if ($unitRequested -or $workersAttempted) {
        if (!(Wait-Stopped $a ($end-35) 'instances-after-stop.json')) {
            Stall 'A-stop-proof' 'A client/objects/metadata not all proved StopCharging; keep coordinator and any held locks; inspect exact IDs now.'
        }
    }
    if ($jumpAttempted) {
        try {
            if ($unitRequested) {
                $returnBy=$end-23
                if ($ready) {
                    Upload (Join-Path $out 'instances-after-stop.json') 'instances-after-stop.upload' $returnBy
                    if ((Remote "ln $remote/instances-after-stop.upload $remote/instances-after-stop.json" $returnBy).code) {
                        throw 'Stop receipt could not be bound to the held locks'
                    }
                    Sleep-Within $returnBy 3
                }
                if ((Copy-Secure "ax-pilot-jump:$remote" (Join-Path $out 'coordinator') $returnBy -Tree).code) {
                    throw 'Coordinator evidence pull failed'
                }
            }
        } catch {
            Write-Utf8 'coordinator-evidence-warning.json' (@{incomplete=$true; exception=$_.Exception.GetType().Name} | ConvertTo-Json)
            Write-Warning "Coordinator evidence retained at $remote"
        } finally {
            try {
                Save-Action 'jump-stop' (Cloud @('StopInstance','--InstanceId',$jump,'--ForceStop','false','--StoppedMode','StopCharging') ((Now)+12))
            } catch { Write-Warning 'Jump stop unacknowledged; checking exact state' }
            if (!(Wait-Stopped @($jump) ([Math]::Max($end,(Now)+12)) 'jump-final.json')) {
                Stall 'jump-stop-proof' 'Owned A is proved stopped where applicable; jump StopCharging not proved, inspect exact jump ID now.'
            }
        }
    }
}
$closedPath=Join-Path $out 'coordinator/closed.json'
if (!$artifactOk -or !$ready.passed -or !(Test-Path -LiteralPath $closedPath) -or
    !(Get-Content -LiteralPath $closedPath -Raw | ConvertFrom-Json).passed) {
    throw "Diagnostic failed or publication unproved; preserve its evidence at $out"
}
Write-Output $out
