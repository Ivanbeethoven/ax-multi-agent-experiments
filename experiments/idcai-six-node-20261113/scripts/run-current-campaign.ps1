param([ValidatePattern('^[a-z0-9-]+$')][string]$Campaign = 'brewfs-8a215a6-001')

$ErrorActionPreference = 'Stop'
$root = Join-Path (Split-Path -Parent $PSScriptRoot) 'receipts'
$summary = Join-Path $root $Campaign
[void](New-Item -ItemType Directory -Path $summary -Force)
$revision = '8a215a65cad19d34fd3fd7f04029f97d09617364'
$release = "/srv/ax-idcai-20261113/releases/brewfs-$revision"
$restoration = Join-Path $root 'c2-mode-restoration.json'
if (-not (Test-Path -LiteralPath $restoration)) {
    scp -B -q 'idcai-jump:/srv/ax-idcai-20261113/input/pytest-posix/pytest-full-02/mode-restoration.json' $restoration
    if ($LASTEXITCODE -ne 0) { throw 'Cannot collect the verified C2 input receipt' }
}
$inputProof = Get-Content -LiteralPath $restoration -Raw | ConvertFrom-Json
if (-not $inputProof.passed -or $inputProof.entries -ne 714 -or
    $inputProof.events_sha256 -ne '0300a208c3fff9bbe125d40e2214f46d459d7cad3b86141e1960a8cf9384c45f' -or
    $inputProof.initial_tree_sha256 -ne 'c1b9b066e6660301ad944fff78de1910e6388021c6caa90923355848eb1c2847') {
    throw 'C2 input receipt differs from this campaign'
}
foreach ($hostAlias in 'idcai-jump','idcai-client2') {
    ssh -n -T -o BatchMode=yes $hostAlias "test -s '$release/installed.json' && cd '$release/bin' && sha256sum -c SHA256SUMS"
    if ($LASTEXITCODE -ne 0) { throw "Current release is unavailable on $hostAlias" }
}
$stages = @('c2-brewfs-flat','c2-brewfs-workspace','c2-juicefs','c1',
            'c1-layout-enabled','c1-layout-disabled','c1-layout-block-only','c4-handoff','c3','c4-clone','c4-fork')
foreach ($stage in $stages) {
    $done = Join-Path $summary "$stage.json"
    if (Test-Path -LiteralPath $done) {
        Write-Output "Retained existing stage: $stage"
        continue
    }
    $before = @(Get-ChildItem -LiteralPath $root -Directory | Select-Object -ExpandProperty Name)
    $failure = $null
    try {
        if ($stage -eq 'c1') {
            & "$PSScriptRoot/qualify-performance-backend.ps1" -AllC1 -Adapter ax-idcai-current-v1
        } elseif ($stage.StartsWith('c1-layout-')) {
            & "$PSScriptRoot/qualify-performance-backend.ps1" -LayoutProfile ('perf-' + $stage.Substring(10)) -Adapter ax-idcai-current-v1
        } elseif ($stage.StartsWith('c2-')) {
            $arm = $stage.Substring(3)
            & "$PSScriptRoot/qualify-performance-backend.ps1" -Workloads c2 -OnlyArm "c2/$arm" -TraceTimeout 9000 -Adapter ax-idcai-current-v1
        } else {
            & "$PSScriptRoot/qualify-performance-backend.ps1" -Workloads $stage -Adapter ax-idcai-current-v1
        }
    } catch { $failure = $_.Exception.Message }
    $new = @(Get-ChildItem -LiteralPath $root -Directory | Where-Object {$_.Name -notin $before -and $_.Name -match '^ax-four-'})
    if ($new.Count -ne 1) { throw "Cannot identify stage $stage; error: $failure" }
    $run = $new[0].Name
    & "$PSScriptRoot/close-performance-attempt.ps1" -Run $run
    if (Test-Path -LiteralPath (Join-Path $new[0].FullName 'observer-client-finished.json')) {
        & "$PSScriptRoot/audit-observers.ps1" -Run $run
        & "$PSScriptRoot/archive-performance-attempt.ps1" -Run $run
    }
    if ($stage.StartsWith('c2-') -and (Test-Path -LiteralPath (Join-Path $new[0].FullName "$stage.json"))) {
        & "$PSScriptRoot/archive-c2.ps1" -Run $run -OnlyArm $stage.Substring(3)
    }
    $value = [ordered]@{stage=$stage; run=$run; brewfs_revision=$revision; passed=($null -eq $failure); error=$failure}
    [IO.File]::WriteAllText($done, (ConvertTo-Json -InputObject $value -Depth 4), [Text.UTF8Encoding]::new($false))
    if ($stage -eq 'c2-juicefs') {
        $flat = (Get-Content (Join-Path $summary 'c2-brewfs-flat.json') -Raw | ConvertFrom-Json).run
        $workspace = (Get-Content (Join-Path $summary 'c2-brewfs-workspace.json') -Raw | ConvertFrom-Json).run
        $experiment = Split-Path -Parent $PSScriptRoot
        Push-Location $experiment
        try {
            wsl.exe -d Ubuntu-24.04 -- python3 scripts/summarize-c2.py `
                "receipts/$flat" `
                --arm-run "brewfs-workspace=receipts/$workspace" `
                --arm-run "juicefs=receipts/$run"
        } finally { Pop-Location }
    }
}
