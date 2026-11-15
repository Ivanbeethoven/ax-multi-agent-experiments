# Record the existing user instruction and exact C2 inputs. This does not launch a run.
[CmdletBinding(DefaultParameterSetName='Refresh')]
param(
    [Parameter(Mandatory)][string]$PacketRoot,
    [Parameter(Mandatory)][ValidateRange(20,255)][int]$Database,
    [Parameter(Mandatory,ParameterSetName='FromReceipts')][string]$LatestReceipt,
    [Parameter(Mandatory,ParameterSetName='FromReceipts')][string]$InstancesBeforeReceipt
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
if ($Database -in @(54,70,243)) { throw 'Select an unused DB candidate outside the reserved set' }

function Hash([string]$Path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
function WslPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).Replace('\','/')
    if ($full -cnotmatch '^([A-Za-z]):(/.+)$') { throw 'Expected a Windows drive path' }
    '/mnt/' + $Matches[1].ToLowerInvariant() + $Matches[2]
}
$draftPath = Join-Path $PSScriptRoot 'request.draft.json'
$draft = Get-Content -LiteralPath $draftPath -Raw | ConvertFrom-Json
$manifest = Join-Path $PSScriptRoot '../brewfs-ca07159-build/build-manifest.json'
$verifier = Join-Path $PSScriptRoot '../../../scripts/verify-latest.py'
$manifestSha = 'd4d00a6aab0887fe86c75646ef260594587e95fb2728daaa93d3ee5ccabf1b6e'
if ((Hash $manifest) -cne $manifestSha -or $draft.reviewed -ne $false -or $draft.runnable -ne $false) {
    throw 'Frozen build manifest or unsigned draft differs'
}
$ids = @($draft.instance_ids.client,$draft.instance_ids.metadata,$draft.instance_ids.objects)
$tag = 'c2-current-a-' + [DateTime]::UtcNow.ToString("yyyyMMdd't'HHmmss'z'", [Globalization.CultureInfo]::InvariantCulture)
$out = Join-Path (Resolve-Path -LiteralPath $PacketRoot).Path $tag
New-Item -ItemType Directory -Path $out -ErrorAction Stop | Out-Null
$latest = Join-Path $out 'latest.json'
$before = Join-Path $out 'instances-before.json'

if ($PSCmdlet.ParameterSetName -eq 'FromReceipts') {
    $old = Get-Item -LiteralPath $InstancesBeforeReceipt
    $age = ([DateTime]::UtcNow-$old.LastWriteTimeUtc).TotalSeconds
    if ($age -lt 0 -or $age -gt 300) { throw 'Supplied before-state receipt is stale' }
    Copy-Item -LiteralPath $LatestReceipt -Destination $latest
    Copy-Item -LiteralPath $InstancesBeforeReceipt -Destination $before
    (Get-Item -LiteralPath $before).LastWriteTimeUtc = $old.LastWriteTimeUtc
} else {
    $git = (Get-Command git.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $null = wsl.exe -d Ubuntu-24.04 -u hxy -- python3 -B (WslPath $verifier) --manifest (WslPath $manifest) --out (WslPath $latest) --git (WslPath $git)
    if ($LASTEXITCODE) { throw "Cannot prove both built commits still equal branch tips; inspect $latest" }
    # WSL's clock can lead the Windows host. Wait for the original receipt's
    # timestamp; never restamp it or weaken the later freshness checks.
    $versionAt = [DateTimeOffset](Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json).at
    $ahead = ($versionAt - [DateTimeOffset]::UtcNow).TotalSeconds
    if ($ahead -gt 30) { throw 'Version receipt clock is over 30 seconds ahead of this host' }
    if ($ahead -gt 0) { Start-Sleep -Milliseconds ([int][Math]::Ceiling($ahead*1000)+100) }
    $idJson = ConvertTo-Json -Compress -InputObject $ids
    $raw = aliyun ecs DescribeInstances --RegionId cn-hangzhou --InstanceIds $idJson
    if ($LASTEXITCODE) { throw 'Exact A DescribeInstances failed' }
    $raw | Set-Content -LiteralPath $before -Encoding utf8NoBOM
}

$version = Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json
$expected = @{
    brewfs=@('https://github.com/brewfs/brewfs','main',$draft.build.brewfs_commit)
    rustfs=@('https://github.com/Ivanbeethoven/rustfs','feat/dynamic-block-size-rebased',$draft.build.rustfs_commit)
}
$now = [DateTimeOffset]::UtcNow
$versionAge = ($now-[DateTimeOffset]$version.at).TotalSeconds
$beforeAge = ($now.UtcDateTime-(Get-Item -LiteralPath $before).LastWriteTimeUtc).TotalSeconds
if ($version.passed -isnot [bool] -or !$version.passed -or $version.build_manifest_sha256 -cne $manifestSha -or
    @($version.sources).Count -ne 2 -or $versionAge -lt 0 -or $versionAge -gt 900 -or
    $beforeAge -lt 0 -or $beforeAge -gt 300) { throw 'Fresh exact version and before-state receipts required' }
foreach ($row in $version.sources) {
    $pin = $expected[$row.component]
    if ($row.component -cnotin @('brewfs','rustfs') -or !$pin -or
        $row.repository -cne $pin[0] -or $row.branch -cne $pin[1] -or
        $row.built_commit -cne $pin[2] -or $row.remote_commit -cne $pin[2] -or
        $row.matches -isnot [bool] -or !$row.matches -or $row.lookup_exit_code -ne 0) { throw 'Branch receipt does not match the build' }
    $expected.Remove($row.component)
}
$api = Get-Content -LiteralPath $before -Raw | ConvertFrom-Json
$rows = @($api.Instances.Instance)
if (!$api.RequestId -or $api.TotalCount -ne 3 -or $rows.Count -ne 3 -or (Compare-Object $ids @($rows.InstanceId)) -or
    ($rows | Where-Object {$_.ZoneId -cne 'cn-hangzhou-j' -or $_.Status -cne 'Stopped' -or $_.StoppedMode -cne 'StopCharging'})) {
    throw 'All three exact A hosts must be Stopped/StopCharging in cn-hangzhou-j'
}
$authorization = Join-Path $out 'launch.authorization.json'
[ordered]@{
    schema='c2-current-a-launch-authorization-v1'; source='conversation-user-instruction'
    user_instruction='设定目标 完成所有的c1~c4的小规模实验吧'; user_followup='继续完成实验'; formal_sample=$false
    scope='A-only stock workspace-v1 Packed C2 qualification, 45 minute cap'; tag=$tag; database=$Database
    controller_sha256=(Hash (Join-Path $PSScriptRoot 'run-c2-current-a.py'))
    worker_sha256=(Hash (Join-Path $PSScriptRoot 'qualify-c2-current-a.py'))
    latest_sha256=(Hash $latest); before_sha256=(Hash $before)
} | ConvertTo-Json | Set-Content -LiteralPath $authorization -Encoding utf8NoBOM
[ordered]@{packet=$out; tag=$tag; started=$false; formal_sample=$false; authorization=$authorization;
    draft_sha256=(Hash $draftPath); expires_utc=(@(([DateTimeOffset]$version.at).AddSeconds(900),
        ([DateTimeOffset](Get-Item -LiteralPath $before).LastWriteTimeUtc).AddSeconds(300)) |
        Sort-Object | Select-Object -First 1).ToString('o')} | ConvertTo-Json
