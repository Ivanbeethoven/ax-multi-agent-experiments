#requires -Version 7.4
<#
.SYNOPSIS
Verify one original A pool instance and its private archive before optional release.
.DESCRIPTION
Keep the A physical resource lock held outside this script. Use a new, private
receipt directory. Without -Execute, this script issues only read-only queries.
The deletion acknowledgement is not proof that the instance or its disks are gone.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('client','metadata','objects')][string]$Role,
    [Parameter(Mandatory)][string]$RepoRoot,
    [Parameter(Mandatory)][string]$ReceiptRoot,
    [Parameter(Mandatory)][string]$LocalArchive,
    [Parameter(Mandatory)][string]$SourcePackReceipt,
    [Parameter(Mandatory)][string]$LocalVerifyReceipt,
    [Parameter(Mandatory)][string]$JumpVerifyReceipt,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$region = 'cn-hangzhou'
$zone = 'cn-hangzhou-j'
$campaign = 'ax-pilot-20261111'
$jump = 'i-bp1cp8ccqor34u2bo5os'
$Role = $Role.ToLowerInvariant()
$allow = @{
    client = @{ id = 'i-bp1809mw7rlujtj9f705'; disks = @('d-bp1809mw7rlujtjc8eph','d-bp1809mw7rlujtjc8epi') }
    metadata = @{ id = 'i-bp1gy3cfuj9jik0y4oc3'; disks = @('d-bp1gy3cfuj9jik0upt3j','d-bp1gy3cfuj9jik0upt3k') }
    objects = @{ id = 'i-bp1h9kaoya0predubdkn'; disks = @('d-bp1h9kaoya0predx6kvc','d-bp1h9kaoya0predx6kvd',
        'd-bp1h9kaoya0predx6kve','d-bp1h9kaoya0predx6kvf','d-bp1h9kaoya0predx6kvg') }
}
$id = $allow[$Role].id
$diskIds = $allow[$Role].disks

function Require([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function File-Sha([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Pinned-Json([string]$Path, [string]$ExpectedSha) {
    # Parse the same bytes that were hashed.
    $bytes = [IO.File]::ReadAllBytes($Path)
    $sha = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
    Require ($sha -ceq $ExpectedSha) "Pinned evidence SHA differs: $Path"
    [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
}
function Same-Ids([string[]]$Expected, [string[]]$Actual) {
    $Expected.Count -eq $Actual.Count -and
        (($Expected | Sort-Object) -join ',') -ceq (($Actual | Sort-Object) -join ',')
}
function Utc-Stamp($Value) {
    Require ($null -ne $Value) 'Creation timestamp missing'
    if ($Value -is [DateTime]) { return $Value.ToUniversalTime() }
    [DateTimeOffset]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture).UtcDateTime
}
function Invoke-Ecs([string]$Name, [string]$Operation, [string[]]$Options) {
    $raw = & $aliyun ecs $Operation --RegionId $region @Options | Out-String
    $code = $LASTEXITCODE
    $file = Join-Path $dir ($Name + '.json')
    [IO.File]::WriteAllText($file, $raw, [Text.UTF8Encoding]::new($false))
    Require ($code -eq 0) "$Operation failed; raw response retained: $file"
    $result = $raw | ConvertFrom-Json
    Require ($result.RequestId -is [string] -and $result.RequestId.Length -gt 0 -and -not $result.Code) `
        "$Operation did not return a successful request ID: $file"
    $result
}

$repo = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath
$evidence = 'experiments/unified-suite/pilot-aliyun/evidence/preparation'
$ownershipSha = 'a2a970ce0548cd5f3a5a09ac0e0da4f4bec92443d75dbd2e54ede735e72eb9c1'
$ownership = Pinned-Json (Join-Path $repo "$evidence/cleanup-a-ownership-01/ownership.json") $ownershipSha
Require ($ownership.schema -ceq 'ax-pilot-cleanup-a-ownership-v1' -and $ownership.region -ceq $region -and
    $ownership.zone -ceq $zone -and $ownership.campaign -ceq $campaign -and
    $ownership.protected.jump.instance_id -ceq $jump) 'Ownership scope differs'
$candidates = @($ownership.release_candidates | Where-Object role -CEQ $Role)
Require ($candidates.Count -eq 1) 'Expected exactly one ownership candidate'
$candidate = $candidates[0]
Require ($candidate.instance_id -ceq $id -and $candidate.instance_name -ceq "$campaign-$Role" -and
    (Same-Ids $diskIds @($candidate.disks.disk_id))) 'Candidate differs from the exact original A allowlist'

$historical = Pinned-Json (Join-Path $repo $ownership.historical_owned_sets.path) $ownership.historical_owned_sets.sha256
$originalRows = @($historical.resources | Where-Object role -CEQ $Role)
Require ($historical.passed -eq $true -and $originalRows.Count -eq 1) 'Historical release-set proof missing'
$original = $originalRows[0]
$pin = $candidate.creation_receipt
Require ($original.schema -ceq 'ax-pilot-owned-release-set-v1' -and $original.passed -eq $true -and
    $original.resource_id -ceq $id -and $original.campaign -ceq $campaign -and
    $original.preserve_jump_instance -ceq $jump -and $original.creation.sha256 -ceq $pin.sha256 -and
    (Same-Ids $diskIds @($original.owned_disk_ids))) 'Historical role, creation SHA or disk set differs'
$created = Pinned-Json (Join-Path $repo $pin.path) $pin.sha256
Require ($created.preexisting -is [bool] -and -not $created.preexisting -and
    $pin.preexisting -is [bool] -and -not $pin.preexisting -and
    $created.instance_id -ceq $id -and $created.role -ceq $Role -and $created.campaign -ceq $campaign -and
    $created.request_id -ceq $pin.create_request_id -and $created.request_sha256 -ceq $pin.request_sha256) `
    'Original creation proof differs or resource was pre-existing'

$receiptPaths = @($SourcePackReceipt, $LocalVerifyReceipt, $JumpVerifyReceipt) | ForEach-Object {
    (Resolve-Path -LiteralPath $_).ProviderPath
}
Require (@($receiptPaths | Sort-Object -Unique).Count -eq 3) 'Supply three separate verification receipts'
$pack = Get-Content -LiteralPath $receiptPaths[0] -Raw | ConvertFrom-Json
$local = Get-Content -LiteralPath $receiptPaths[1] -Raw | ConvertFrom-Json
$jumpProof = Get-Content -LiteralPath $receiptPaths[2] -Raw | ConvertFrom-Json
$archivePath = (Resolve-Path -LiteralPath $LocalArchive).ProviderPath
$archive = [IO.File]::Open($archivePath, 'Open', 'Read', 'Read')
try {
    $actualSha = (Get-FileHash -InputStream $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    $memberCount = $pack.archive.verified_members
    Require ($pack.role -ceq $Role -and $pack.instance -ceq $id -and
        $pack.all_data_disks_unmounted -is [bool] -and $pack.all_data_disks_unmounted -and
        $null -ne $pack.unknown_count -and $pack.unknown_count -eq 0 -and
        $pack.archive.bytes -eq $archive.Length -and $archive.Length -gt 0 -and
        $pack.archive.sha256 -ceq $actualSha -and $local.sha256 -ceq $actualSha -and
        $jumpProof.sha256 -ceq $actualSha -and $memberCount -ge 2 -and
        $local.verified_members -eq $memberCount -and $jumpProof.verified_members -eq $memberCount -and
        $pack.evidence_files + 2 -eq $memberCount) 'Source, local and jump archive proofs do not agree'

    $archive.Position = 0
    $gzip = [IO.Compression.GZipStream]::new($archive, [IO.Compression.CompressionMode]::Decompress, $true)
    $tar = [System.Formats.Tar.TarReader]::new($gzip, $false)
    try {
        $entry = $tar.GetNextEntry()
        Require ($entry.Name -ceq 'retirement-inventory.json' -and $entry.EntryType -eq 'RegularFile' -and
            $entry.Length -gt 0 -and $entry.Length -le 8MB) 'Archive lacks the bounded original inventory'
        $buffer = [IO.MemoryStream]::new()
        try {
            $entry.DataStream.CopyTo($buffer)
            $inventoryBytes = $buffer.ToArray()
        } finally { $buffer.Dispose() }
        $inventorySha = [Convert]::ToHexString(
            [Security.Cryptography.SHA256]::HashData($inventoryBytes)).ToLowerInvariant()
        Require ($inventorySha -ceq $pack.inventory_sha256) 'Archived inventory differs from source scan'
        $inventory = [Text.Encoding]::UTF8.GetString($inventoryBytes) | ConvertFrom-Json
        $archivedDisks = @($inventory.system_disk.disk_id) +
            @($inventory.data_disks.PSObject.Properties | ForEach-Object { $_.Value.disk_id })
        Require ($inventory.schema -ceq 'ax-a-backend-retirement-inventory-v1' -and
            $inventory.instance -ceq $id -and $inventory.role -ceq $Role -and
            $inventory.unknown -is [array] -and $inventory.unknown.Count -eq 0 -and
            $inventory.evidence_files -eq $pack.evidence_files -and
            (Same-Ids $diskIds $archivedDisks)) 'Archived inventory does not bind all original disks'
    } finally { $tar.Dispose() }
} finally { $archive.Dispose() }

Require (-not (Test-Path -LiteralPath $ReceiptRoot)) 'Use a new, private receipt directory'
$dir = (New-Item -Path $ReceiptRoot -ItemType Directory -ErrorAction Stop).FullName
$aliyun = (Get-Command aliyun -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$node = Invoke-Ecs 'before-instance' 'DescribeInstances' @(
    '--InstanceIds', (ConvertTo-Json -InputObject @($id) -Compress), '--PageSize', '100')
$disks = Invoke-Ecs 'before-disks' 'DescribeDisks' @('--InstanceId', $id, '--PageSize', '100')
$rows = @($node.Instances.Instance)
Require ($node.TotalCount -eq 1 -and $rows.Count -eq 1) 'Exact instance query differs'
$instance = $rows[0]
Require ($instance.InstanceId -ceq $id -and $instance.InstanceName -ceq $candidate.instance_name -and
    (Utc-Stamp $instance.CreationTime) -eq (Utc-Stamp $candidate.api_creation_time) -and
    $instance.ZoneId -ceq $zone -and $instance.InstanceChargeType -ceq 'PostPaid' -and
    $instance.Status -ceq 'Stopped' -and $instance.StoppedMode -ceq 'StopCharging' -and
    $instance.DeletionProtection -is [bool] -and -not $instance.DeletionProtection) `
    'Exact original instance identity or stopped/billing state differs'
$actualDisks = @($disks.Disks.Disk)
Require ($disks.TotalCount -eq $diskIds.Count -and (Same-Ids $diskIds @($actualDisks.DiskId))) `
    'Attached disk set differs; an extra or missing disk forbids release'
foreach ($disk in $actualDisks) {
    $expected = @($candidate.disks | Where-Object disk_id -CEQ $disk.DiskId)[0]
    Require ($disk.InstanceId -ceq $id -and $disk.ZoneId -ceq $zone -and
        $disk.DeleteWithInstance -is [bool] -and $disk.DeleteWithInstance -and
        $disk.DiskChargeType -ceq 'PostPaid' -and $disk.Status -ceq 'In_use' -and
        $disk.Type -ceq $expected.type -and $disk.Size -eq $expected.size_gib -and
        $disk.Category -ceq $expected.category -and $disk.PerformanceLevel -ceq $expected.performance_level) `
        "Original attachment, release flag, or disk properties differ: $($disk.DiskId)"
}

$proof = [ordered]@{
    schema = 'ax-pilot-a-owned-release-preflight-v1'
    observed_at_utc = [DateTime]::UtcNow.ToString('o')
    role = $Role
    instance_id = $id
    disk_ids = $diskIds
    ownership_sha256 = $ownershipSha
    creation_sha256 = $pin.sha256
    historical_owned_sets_sha256 = $ownership.historical_owned_sets.sha256
    inventory_sha256 = $inventorySha
    archive_sha256 = $actualSha
    verified_members = $memberCount
    source_pack_receipt_sha256 = File-Sha $receiptPaths[0]
    local_verify_receipt_sha256 = File-Sha $receiptPaths[1]
    jump_verify_receipt_sha256 = File-Sha $receiptPaths[2]
    instance_request_id = $node.RequestId
    attached_disks_request_id = $disks.RequestId
    execute_requested = $Execute.IsPresent
    release_confirmed = $false
}
$proof | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $dir 'preflight.json') -Encoding utf8NoBOM
if ($Execute) {
    $ack = Invoke-Ecs 'delete-ack' 'DeleteInstance' @('--InstanceId', $id, '--Force', 'false')
    Write-Output "DeleteInstance acknowledged for $id ($($ack.RequestId)); verify instance and disk absence separately."
} else {
    Write-Output "Read-only preflight passed for $id; no release request was sent."
}
