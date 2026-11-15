param(
    [Parameter(Mandatory=$true)][string]$LocalRoot,
    [Parameter(Mandatory=$true)][string]$OriginalIoArchive,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$Region = 'cn-hangzhou'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$manifestPath = Join-Path $repoRoot 'scenarios/k8s-containers/validation/drive-migration-2026-11-10.json'
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$cli = (Get-Command aliyun -ErrorAction Stop).Source
$localBase = (Resolve-Path -LiteralPath $LocalRoot).ProviderPath
$ioOriginal = (Resolve-Path -LiteralPath $OriginalIoArchive).ProviderPath
[IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null
$outputBase = (Resolve-Path -LiteralPath $OutputDirectory).ProviderPath
$receiptPath = Join-Path $outputBase 'restoration.json'
$receipt = [ordered]@{
    format = 'historical-oss-restore-v1'
    source_manifest_sha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLower()
    bucket = $manifest.source.bucket
    prefix = $manifest.source.prefix
    started_at = [DateTime]::UtcNow.ToString('o')
    complete = $false
    verification_method = 'Pinned local SHA256; independent local CRC64 equals OSS server CRC64; exact remote size and SHA256 metadata'
    objects = @()
}
function Save-Receipt {
    $receipt.updated_at = [DateTime]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
}
function Read-Remote([string]$Uri) {
    $raw = & $cli ossutil stat $Uri --region $Region --output-format json 2>&1
    if ($LASTEXITCODE -eq 0) { return ($raw -join "`n" | ConvertFrom-Json) }
    if (($raw -join "`n") -match 'NoSuchKey|Not Found|status code: 404|StatusCode: 404') { return $null }
    throw "Unable to inspect $Uri : $($raw -join ' ')"
}
function Check-Remote($Remote, $Expected) {
    if ([Int64]$Remote.'Content-Length' -ne $Expected.bytes) { throw "Remote size mismatch: $($Expected.uri)" }
    if ([string]$Remote.'X-Oss-Hash-Crc64ecma' -ne $Expected.crc64) { throw "Remote CRC64 mismatch: $($Expected.uri)" }
    if ([string]$Remote.'X-Oss-Meta-Sha256' -ne $Expected.sha256) { throw "Remote SHA256 metadata mismatch: $($Expected.uri)" }
}

# Verify every source before uploading anything. Existing source bytes are never modified.
$sources = @()
foreach ($entry in $manifest.objects) {
    $relative = $entry.drive_path -replace '^traces/historical-oss/', ''
    $source = Join-Path $localBase $relative
    if ($entry.oss_key -eq 'full-fuse/20261023/deepseek-opencode-20261026/io-compatible.tar.gz') { $source = $ioOriginal }
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing source: $source" }
    if ((Get-Item -LiteralPath $source).Length -ne $entry.bytes) { throw "Source size mismatch: $source" }
    $sha = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLower()
    if ($sha -ne $entry.sha256) { throw "Source SHA256 mismatch: $source" }
    $crcOutput = & $cli ossutil hash crc64 $source 2>&1
    if ($LASTEXITCODE -ne 0 -or ($crcOutput -join "`n") -notmatch '(?m)^(\d+)\s+') { throw "CRC64 calculation failed: $source" }
    $sources += [pscustomobject]@{
        key = $entry.oss_key; uri = "oss://$($manifest.source.bucket)/$($entry.oss_key)"
        local_path = $source; bytes = [Int64]$entry.bytes; sha256 = $sha; crc64 = $Matches[1]
    }
    Write-Output "SOURCE VERIFIED $($entry.oss_key) $($entry.bytes)"
}
if ($sources.Count -ne 28 -or ($sources | Measure-Object bytes -Sum).Sum -ne 10480588662) { throw 'Unexpected restoration scope' }
Save-Receipt
foreach ($entry in $sources) {
    $remote = Read-Remote $entry.uri
    $action = 'already-present'
    if ($null -eq $remote) {
        $index = $receipt.objects.Count + 1
        $logPath = Join-Path $outputBase ('upload-{0:d2}.log' -f $index)
        & $cli ossutil cp $entry.local_path $entry.uri --region $Region --metadata "sha256=$($entry.sha256)" --parallel 6 --part-size 16Mi --no-progress *> $logPath
        if ($LASTEXITCODE -ne 0) { throw "Upload failed: $($entry.uri); see $logPath" }
        $remote = Read-Remote $entry.uri
        $action = 'uploaded'
    }
    Check-Remote $remote $entry
    $receipt.objects += [ordered]@{
        oss_key = $entry.key; bytes = $entry.bytes; sha256 = $entry.sha256
        local_crc64 = $entry.crc64; remote_crc64 = [string]$remote.'X-Oss-Hash-Crc64ecma'
        remote_etag = [string]$remote.Etag; action = $action; verified = $true
        verified_at = [DateTime]::UtcNow.ToString('o')
    }
    Save-Receipt
    Write-Output "RESTORED VERIFIED $($receipt.objects.Count)/28 $($entry.key)"
}
$listing = & $cli ossutil ls "oss://$($manifest.source.bucket)/$($manifest.source.prefix)" --region $Region 2>&1
if ($LASTEXITCODE -ne 0 -or ($listing -join "`n") -notmatch 'Object Number is:\s*28\s*$') { throw 'Unexpected final OSS listing; retain uploaded objects and inspect' }
$listing | Set-Content -LiteralPath (Join-Path $outputBase 'oss-listing.txt') -Encoding utf8
$receipt.object_count = 28
$receipt.bytes = 10480588662
$receipt.complete = $true
$receipt.completed_at = [DateTime]::UtcNow.ToString('o')
Save-Receipt
Write-Output 'RESTORATION COMPLETE: 28 verified objects, seven original trace archives. No data deleted.'
