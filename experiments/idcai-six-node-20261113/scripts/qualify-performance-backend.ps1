param([switch]$C1, [switch]$AllC1, [ValidateSet('c2','c3','c4-clone','c4-handoff','c4-fork','c4-fork-candidate')][string[]]$Workloads,
      [ValidateSet('perf-enabled','perf-disabled','perf-block-only')][string]$LayoutProfile,
      [string[]]$OnlyArm,
      [ValidateRange(60,9000)][int]$TraceTimeout = 1100,
      [switch]$C3PackedBudgetDiagnostic,
      [ValidateSet('ax-idcai-100k-v2','ax-idcai-100k-v3','ax-idcai-100k-v4','ax-idcai-100k-v5','ax-idcai-100k-v6','ax-idcai-100k-v7','ax-idcai-100k-v8','ax-idcai-current-v1')][string]$Adapter = 'ax-idcai-current-v1')

if ($AllC1) { $C1 = $true }
if ($LayoutProfile -and ($C1 -or $Workloads)) { throw 'Run the C1 layout qualification by itself.' }
if ($C3PackedBudgetDiagnostic -and ($Adapter -ne 'ax-idcai-100k-v8' -or $OnlyArm.Count -ne 1 -or $OnlyArm[0] -ne 'c3/brewfs-workspace')) {
    throw 'The C3 diagnostic requires v8 and only c3/brewfs-workspace.'
}

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false  # retain receipts before inspecting native exit codes
$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$scp = Join-Path $env:WINDIR 'System32/OpenSSH/scp.exe'
$base = '/srv/ax-idcai-20261113'
$campaign = "$base/campaign-100k-001"
$axRevision = '86f704be9129fbb5f6e466cfbbb15fdf383fc4e8'
$currentBrewFS = $Adapter -eq 'ax-idcai-current-v1'
$brewRevision = if ($currentBrewFS) { '8a215a65cad19d34fd3fd7f04029f97d09617364' } else { 'ca07159f3da4b670e4aeb878faef928e69bc1634' }
$bin = if ($currentBrewFS) { "$base/releases/brewfs-$brewRevision/bin" } else { "$base/bin" }
$producer = if ($currentBrewFS) { 'packed_snapshot_fixture' } else { 'packed_v3_snapshot_fixture' }
$targetRepo = "$base/src/ax-$axRevision"
$backendProfile = if ($LayoutProfile) { $LayoutProfile } else { 'perf-enabled' }
$observerLimit = [Math]::Max(6600, $TraceTimeout + 1200)
$backendLimit = $observerLimit + 600
$workloadArms = @{
    'c2' = @('brewfs-flat','brewfs-workspace','juicefs')
    'c3' = @('brewfs-workspace','juicefs')
    'c4-clone' = @('juicefs')
    'c4-handoff' = @('brewfs-workspace-stay','juicefs-stay','brewfs-workspace-move','juicefs-move')
    'c4-fork-candidate' = @('brewfs-workspace')
    'c4-fork' = @('brewfs-workspace')
}
foreach ($selection in $OnlyArm) {
    $pair = $selection -split '/', 2
    if ($pair.Count -ne 2 -or $pair[0] -notin $Workloads -or $pair[1] -notin $workloadArms[$pair[0]]) {
        throw "Unknown or unselected workload/arm: $selection"
    }
}
if ($Workloads -contains 'c4-handoff') {
    & $ssh -n -T -o BatchMode=yes idcai-jump "d='$base/input/c4-fs-checkpoint-894'; test -s `"`$d/checkpoint.json`" && jq -e '.passed == true and .files == 894 and .archive_sha256 == `"9659f428182c55fbd7d8109bb61d0f37e6ef655f61615746b49baf0d3e5821fe`"' `"`$d/install.json`" >/dev/null"
    if ($LASTEXITCODE -ne 0) { throw 'C4 handoff requires the frozen 894-entry input; the smoke fixture is not a substitute.' }
    & $ssh -n -T -o BatchMode=yes idcai-jump "ssh -F '$base/private/c4-ssh.conf' -o BatchMode=yes -o ConnectTimeout=8 ax-client2 'cd $targetRepo && test -f bench/runtime/tree_verify.py && python3 -m bench.runtime.move_target --identity >/dev/null'"
    if ($LASTEXITCODE -ne 0) { throw 'C4 target runtime is unavailable over the private network.' }
}
$run = 'ax-four-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$out = "$campaign/$run"
$local = Join-Path (Split-Path -Parent $PSScriptRoot) "receipts/$run"
$peers = 'idcai-obj1','idcai-obj2','idcai-obj3','idcai-obj4'
$started = @()
$observers = @()
$cleanup = @()
$utf8 = [Text.UTF8Encoding]::new($false)
function Check-Native($step) {
    if ($LASTEXITCODE -ne 0) { throw "$step exited with $LASTEXITCODE." }
}
[void](New-Item -ItemType Directory -Path $local)
Write-Output "Run: $run ($backendProfile)"
& $ssh -n -T -o BatchMode=yes idcai-jump "test -s '$base/input/pilot-100k-001/input-set.json' && mkdir -m700 '$out'"
Check-Native 'Create qualified-input run directory'
& $scp -B -q (Join-Path $PSScriptRoot 'check-performance-backend.py') "idcai-jump:$out/"
Check-Native 'Install backend check'
if ($C1) {
    & $scp -B -q (Join-Path $PSScriptRoot 'run-performance-c1.py') "idcai-jump:$out/"
    Check-Native 'Install C1 runner'
}
if ($LayoutProfile) {
    & $scp -B -q (Join-Path $PSScriptRoot 'probe-performance-layout.py') "idcai-jump:$out/"
    Check-Native 'Install C1 layout probe'
}
foreach ($peer in $peers) {
    & $scp -B -q (Join-Path $PSScriptRoot 'rustfs-four-node-node.sh') (Join-Path $PSScriptRoot 'otel-performance.yaml') "${peer}:$base/"
    Check-Native "Stage $peer"
}
$creds = ConvertTo-Json -Compress @{
    key = [guid]::NewGuid().ToString('N').Substring(0,16)
    secret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    rpc = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
}
try {
    foreach ($peer in $peers) {
        $started += $peer
        $raw = $creds | & $ssh -T -o BatchMode=yes -o ConnectTimeout=8 $peer "bash '$base/rustfs-four-node-node.sh' start '$run' '$backendProfile' '$backendLimit'"
        Check-Native "Start shared backend on $peer"
        [IO.File]::WriteAllText((Join-Path $local "$peer-deploy.json"),($raw -join "`n"),$utf8)
    }
    $creds | & $ssh -T -o BatchMode=yes -o ServerAliveInterval=15 idcai-jump "flock -n '$base/measurement.lock' timeout 150 '$base/venv/bin/python' '$out/check-performance-backend.py' '$run' '$out'"
    $probeCode = $LASTEXITCODE
    foreach ($name in 'backend-probe.json','redis-info-all.txt','redis-latency-histogram.txt') {
        & $scp -B -q "idcai-jump:$out/$name" $local
        if ($LASTEXITCODE -ne 0 -and $probeCode -eq 0) { throw "Missing probe evidence: $name" }
    }
    if ($probeCode -ne 0) { throw "Backend probe failed ($probeCode)" }
    foreach ($peer in $peers) {
        $capture = "set -e; d='$base/four-node/$run'; for i in 1 2 3 4 5 6; do curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:9464/metrics > `"`$d/metrics.prom`" && grep -q '^rustfs_' `"`$d/metrics.prom`" && break; sleep 3; done; grep -q '^rustfs_' `"`$d/metrics.prom`"; cp /proc/diskstats `"`$d/diskstats.txt`"; cp /proc/net/dev `"`$d/net-dev.txt`"; chronyc tracking > `"`$d/clock.txt`""
        & $ssh -n -T -o BatchMode=yes $peer $capture
        Check-Native "Verify native RustFS metrics on $peer"
        if ($C1 -or $Workloads -or $LayoutProfile) {
            $hashes = & $ssh -n -T -o BatchMode=yes $peer "cd '$base/four-node/$run' && sha256sum metrics.prom diskstats.txt net-dev.txt clock.txt"
            Check-Native "Inventory raw backend evidence on $peer"
            [IO.File]::WriteAllText((Join-Path $local "$peer-backend.sha256"),($hashes -join "`n") + "`n",$utf8)
        } else {
            foreach ($name in 'metrics.prom','diskstats.txt','net-dev.txt','clock.txt') {
                & $scp -B -q "${peer}:$base/four-node/$run/$name" (Join-Path $local "$peer-$name")
                Check-Native "Collect $name from $peer"
            }
        }
    }
    if ($C1 -or $Workloads -or $LayoutProfile) {
        $roles = [ordered]@{'idcai-jump'='client';'idcai-client2'='target';'idcai-obj1'='obj1';'idcai-obj2'='obj2';'idcai-obj3'='obj3';'idcai-obj4'='obj4'}
        foreach ($peer in $roles.Keys) {
            $role = $roles[$peer]
            $samples = "$out/observer-$role"
            & $ssh -n -T -o BatchMode=yes $peer "mkdir -pm700 '$samples'"
            Check-Native "Create observer data path on $peer"
            & $scp -B -q (Join-Path $PSScriptRoot 'perf-host-snapshot.py') "${peer}:$samples/"
            Check-Native "Install observer on $peer"
            $observers += @{peer=$peer; role=$role; path=$samples}
            & $ssh -n -T -o BatchMode=yes $peer "systemd-run --quiet --unit='$run-observer' --property=RuntimeMaxSec=$observerLimit /usr/bin/python3 '$samples/perf-host-snapshot.py' '$role' '$run' '$samples'"
            Check-Native "Start observer on $peer"
        }
        foreach ($observer in $observers) {
            & $ssh -n -T -o BatchMode=yes $observer.peer "test -s '$($observer.path)/sample-000000/sample.json'"
            Check-Native "Verify first observer sample on $($observer.peer)"
        }
    }
    if ($LayoutProfile) {
        $trial = "$out/c1-layout-$LayoutProfile-qualification"
        $creds | & $ssh -T -o BatchMode=yes -o ServerAliveInterval=15 idcai-jump "flock -n '$base/measurement.lock' timeout 180 '$base/venv/bin/python' '$out/probe-performance-layout.py' requests '$run' '$trial' '$LayoutProfile'"
        $requestCode = $LASTEXITCODE
        & $scp -B -q "idcai-jump:$trial/requests.json" (Join-Path $local 'c1-layout-requests.json')
        Check-Native 'Collect C1 layout requests'
        if ($requestCode -ne 0) { throw "C1 layout requests failed: $requestCode" }
        $files = Join-Path $local 'c1-layout-xl2'
        [void](New-Item -ItemType Directory -Path $files)
        for ($i=1; $i -le 4; $i++) {
            $peer = $peers[$i-1]
            foreach ($key in 'default','65536','262144','1048576','4194304') {
                & $scp -B -q "${peer}:$base/four-node/$run/data/$run-layout/object-$key/xl.meta" (Join-Path $files "object-$key-obj$i.xl.meta")
                Check-Native "Collect persisted XL2 on $peer for $key"
            }
        }
        $inputs = @(Get-ChildItem -LiteralPath $files -Filter '*.xl.meta' | Select-Object -ExpandProperty FullName)
        if ($inputs.Count -ne 20) { throw 'Expected 20 raw shard metadata files' }
        & $scp -B -q @inputs "idcai-jump:$trial/"
        Check-Native 'Stage raw XL2 for the pinned decoder'
        & $ssh -n -T -o BatchMode=yes idcai-jump "'$base/venv/bin/python' '$out/probe-performance-layout.py' verify '$trial'"
        $verifyCode = $LASTEXITCODE
        & $scp -B -q "idcai-jump:$trial/result.json" (Join-Path $local 'c1-layout-result.json')
        Check-Native 'Collect C1 layout receipt'
        if ($verifyCode -ne 0) { throw "Persisted C1 B/q did not match the expected geometry ($verifyCode)" }
    }
    if ($C1) {
        $database = 20
        $modes = if ($AllC1) { 'read','write','randread','randwrite','randrw' } else { 'read' }
        $trialFailures = @()
        foreach ($mode in $modes) {
        foreach ($product in 'brewfs','juicefs') {
            if ($mode -eq 'randrw' -and $product -eq 'juicefs') { continue }
            $database++
            $trial = "$out/c1-$mode-$product-qualification"
            $creds | & $ssh -T -o BatchMode=yes -o ServerAliveInterval=15 idcai-jump "AX_IDCAI_TOOLS='$bin' flock -n '$base/measurement.lock' '$base/venv/bin/python' '$out/run-performance-c1.py' '$trial' '$product' '$mode' '$database'"
            $code = $LASTEXITCODE
            & $scp -B -q "idcai-jump:$trial/trial.json" (Join-Path $local "c1-$mode-$product-trial.json")
            Check-Native "Collect C1 $mode $product result"
            Write-Output "C1 $product $mode qualification exit: $code"
            & $ssh -n -T -o BatchMode=yes idcai-jump "! mountpoint -q '$trial/mnt'"
            Check-Native "C1 mount cleanup for $product"
            if ($code -ne 0) { $trialFailures += "$mode/$product" }
        }
        }
        if ($trialFailures.Count) { throw "C1 qualification failures: $($trialFailures -join ', ')" }
    }
    if ($Workloads) {
        $s = "$base/src/$Adapter/experiments/unified-suite/pilot-aliyun/scripts"
        $image = 'sha256:f88833c866a4f5b533be2b202440cf9dcaa2b77b20479ec05ca1c417e6b8b265'
        $common = "--producer '$bin/$producer' --juicefs '$bin/juicefs' --rustfs-image '$image'"
        $db = 40
        $failures = @()
        foreach ($workload in $Workloads) {
            foreach ($arm in $workloadArms[$workload]) {
                if ($OnlyArm -and "$workload/$arm" -notin $OnlyArm) { continue }
                $db++
                $product = if ($arm.StartsWith('brewfs')) { 'brewfs' } else { 'juicefs' }
                $engine = if ($arm -eq 'brewfs-flat') { 'flat-v1' } else { 'workspace-v1' }
                if ($currentBrewFS) { $engine = $engine.Replace('-v1','') }
                $trial = "$out/$workload-$arm-qualification"
                $runner = 'rbm-lifecycle.py'
                $brew = "$bin/brewfs"
                $extra = "--smallfiles '$bin/smallfiles' --prepare-timeout 540 --trace-timeout $TraceTimeout --engine '$engine'"
                switch ($workload) {
                    'c2' { $extra += " --workload c2 --full-trace --trace '$base/input/pytest-posix/pytest-full-02' --replay '$bin/full-fuse-replay'" }
                    'c3' { $extra += ' --workload c3 --prepare-timeout 900' }
                    'c4-clone' { $runner = 'rbm-native-fork.py'; $extra = "--brewfs-revision $brewRevision" }
                    'c4-fork' {
                        if (-not $currentBrewFS) { throw 'C4 upstream fork requires the current BrewFS adapter' }
                        $runner = 'rbm-native-fork.py'
                        $extra = "--brewfs-revision $brewRevision --brewfs-provenance '$base/releases/brewfs-$brewRevision/build.json'"
                    }
                    'c4-fork-candidate' {
                        if ($Adapter -ne 'ax-idcai-100k-v7') { throw 'C4 candidate requires its own adapter' }
                        $runner = 'rbm-native-fork.py'
                        $brew = "$base/diagnostic/c4-fork-3ecde1/brewfs"
                        $extra = "--brewfs-revision ca07159f3da4b670e4aeb878faef928e69bc1634 --brewfs-provenance '$base/diagnostic/c4-fork-3ecde1/build.json'"
                    }
                    'c4-handoff' {
                        $extra += " --workload c4-stay --checkpoint '$base/input/c4-fs-checkpoint-894/checkpoint.json'"
                        if ($arm.EndsWith('-move')) {
                            $extra += " --ssh-config '$base/private/c4-ssh.conf' --target-host ax-client2 --target-repo '$targetRepo' --target-tools '$bin'"
                        }
                    }
                }
                $envExtra = if ($C3PackedBudgetDiagnostic) { 'export AX_IDCAI_C3_PACKED_BUDGET_DIAGNOSTIC=1; ' } else { '' }
                $cmd = "umask 077; $($envExtra)export TMPDIR='$trial.scratch'; mkdir -m700 -- `"`$TMPDIR`" && flock -n '$base/measurement.lock' '$base/venv/bin/python' '$s/$runner' --brewfs '$brew' $common --out '$trial' --product '$product' --redis-db '$db' $extra"
                $creds | & $ssh -T -o BatchMode=yes -o ServerAliveInterval=15 idcai-jump $cmd
                $code = $LASTEXITCODE
                & $scp -B -q "idcai-jump:$trial/receipt.json" (Join-Path $local "$workload-$arm.json")
                Check-Native "Collect $workload/$arm receipt"
                Write-Output "$workload $arm qualification exit: $code"
                & $ssh -n -T -o BatchMode=yes idcai-jump "! findmnt -rn -o TARGET | grep -E '^($trial[.]scratch|/srv/ax-pilot-session)(/|`$)'"
                Check-Native "Verify no remaining mounts from $workload/$arm"
                if ($code -ne 0) { $failures += "$workload/$arm" }
            }
        }
        if ($failures.Count) { throw "Qualification failures: $($failures -join ', ')" }
    }
} finally {
    foreach ($observer in $observers) {
        $peer=$observer.peer; $samples=$observer.path
        & $ssh -n -T -o BatchMode=yes -o ConnectTimeout=8 $peer "touch '$samples/stop'; for i in 1 2 3 4 5 6 7 8; do test -s '$samples/finished.json' && break; sleep 1; done; test -s '$samples/finished.json'"
        if ($LASTEXITCODE -eq 0) {
            & $scp -B -q "${peer}:$samples/finished.json" (Join-Path $local "observer-$($observer.role)-finished.json")
        } else {
            Write-Warning "Observer did not close cleanly on $peer. Raw data retained at $samples."
        }
    }
    foreach ($peer in $started) {
        & $ssh -n -T -o BatchMode=yes -o ConnectTimeout=8 $peer "bash '$base/rustfs-four-node-node.sh' stop '$run'" | Out-Null
        $cleanup += @{node=$peer; stopped=($LASTEXITCODE -eq 0)}
    }
    [IO.File]::WriteAllText((Join-Path $local 'cleanup.json'),(ConvertTo-Json -InputObject $cleanup -Depth 4),$utf8)
}
if (@($cleanup | Where-Object { -not $_.stopped }).Count -gt 0) { throw 'Backend qualification cleanup failed' }
Write-Output $local
