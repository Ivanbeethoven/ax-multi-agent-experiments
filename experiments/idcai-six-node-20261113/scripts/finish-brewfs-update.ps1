param([switch]$RunCampaign)

$ErrorActionPreference = 'Stop'
$revision = '8a215a65cad19d34fd3fd7f04029f97d09617364'
$base = '/srv/ax-idcai-20261113'
$build = "$base/builds/brewfs-$revision"
$deadline = [DateTime]::UtcNow.AddHours(2)
while ($true) {
    $state = ssh -n -T -o BatchMode=yes -o ConnectTimeout=8 idcai-jump "if test -s '$build/build.json'; then echo ready; else systemctl show ax-brewfs-build-8a215a6 -p ActiveState --value; fi"
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read remote build state; the server retains its logs.' }
    if ($state -eq 'ready') { break }
    if ($state -notin 'active','activating') { throw "BrewFS server build ended: $state" }
    if ([DateTime]::UtcNow -gt $deadline) { throw 'Build monitoring deadline exceeded.' }
    Start-Sleep -Seconds 20
}
ssh -n -T -o BatchMode=yes -o ServerAliveInterval=15 idcai-jump "bash '$base/distribute-brewfs-release.sh' '$revision'"
if ($LASTEXITCODE -ne 0) { throw 'BrewFS release distribution failed.' }
$out = Join-Path (Split-Path -Parent $PSScriptRoot) 'receipts/brewfs-update-8a215a6'
[void](New-Item -ItemType Directory -Path $out -Force)
foreach ($name in 'build.json','source-installed.json','target-installed.json','command.txt','rustc.txt','cargo.txt','archive.sha256','build-exit.txt','source.txt','build.log','unstripped.sha256') {
    scp -B -q "idcai-jump:$build/$name" $out
    if ($LASTEXITCODE -ne 0) { throw "Cannot collect $name" }
}
Write-Output $out
if ($RunCampaign) { & "$PSScriptRoot/run-current-campaign.ps1" }
