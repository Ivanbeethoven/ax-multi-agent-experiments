param(
    [Parameter(Mandatory)][ValidateSet('Preserve','Release')][string]$Action,
    [Parameter(Mandatory)][string]$ReceiptRoot,
    [string]$SshConfig,
    [string]$InventorySha256
)

$ErrorActionPreference = 'Stop'
$dir = (Resolve-Path -LiteralPath $ReceiptRoot).Path
$region = 'cn-hangzhou'
$disk = 'd-bp16jaiu2fcmbij8mg7i'
$objects = 'i-bp1h9kaoya0predubdkn'
$jump = 'i-bp1cp8ccqor34u2bo5os'
$peers = @('i-bp1gy3cfuj9jik0y4oc3','i-bp1809mw7rlujtj9f705',
    'i-bp1fmmfkcpr8w99j2bsy','i-bp1bhdngko7nq3c3jrhp','i-bp14nenf7osz9v1snsp2')
$archiveName = 'c4-a-flat-on1-objects.tar.gz'
$archiveSha = 'b9362d9f84d724d7afdeeaefbf82b032a772c1dfad456c015c47c806e818ffde'
$archiveSize = 379864420
$tag = 'retire-on1-' + (Get-Date -AsUTC -Format yyyyMMddHHmmss)
$step = 0

function Sha([string]$path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Api([string]$operation, [string[]]$arguments) {
    $script:step++
    $file = Join-Path $dir ('{0}-{1:D3}-{2}.json' -f $tag,$script:step,$operation)
    $raw = aliyun ecs $operation --region $region @arguments | Out-String
    $code = $LASTEXITCODE
    $raw | Set-Content -Encoding utf8NoBOM -LiteralPath $file
    if ($code) { throw "$operation failed; see $file" }
    $report = $raw | ConvertFrom-Json
    if (!$report.RequestId) { throw "$operation did not return a request ID: $file" }
    return $report
}
function Instances([string[]]$ids) {
    $report = Api DescribeInstances @('--RegionId',$region,'--InstanceIds',
        (ConvertTo-Json -InputObject $ids -Compress))
    $rows = @($report.Instances.Instance)
    if ($report.TotalCount -ne $ids.Count -or $rows.Count -ne $ids.Count -or
        (Compare-Object ($ids | Sort-Object) ($rows.InstanceId | Sort-Object))) {
        throw 'Instance set does not match the requested IDs'
    }
    return $rows
}
function Idle {
    $rows = @(Instances (@($jump,$objects)+$peers))
    if (@($rows | Where-Object {$_.Status -ne 'Stopped' -or $_.StoppedMode -ne 'StopCharging'}).Count) {
        throw 'The experiment fleet is not idle'
    }
}
function Disk {
    $result = Api DescribeDisks @('--RegionId',$region,'--DiskIds',
        (ConvertTo-Json -InputObject @($disk) -Compress))
    $rows = @($result.Disks.Disk)
    if ($result.TotalCount -eq 0 -and $rows.Count -eq 0) { return $null }
    if ($result.TotalCount -ne 1 -or $rows.Count -ne 1 -or $rows[0].DiskId -ne $disk) {
        throw 'Unexpected disk query result'
    }
    $row = $rows[0]
    $tags = @{}
    foreach ($entry in $row.Tags.Tag) { $tags[$entry.TagKey] = $entry.TagValue }
    if ($row.DiskName -ne 'ax-pilot-20261111-observer-scratch' -or $row.Type -ne 'data' -or
        $row.Size -ne 100 -or $row.ZoneId -ne 'cn-hangzhou-j' -or $row.DiskChargeType -ne 'PostPaid' -or
        $tags.campaign -ne 'ax-pilot-20261111' -or $tags.role -ne 'c4-observer-scratch') {
        throw 'Disk ownership does not match retired On1 scratch'
    }
    return $row
}
function Wait-Instance([string]$id,[string]$status,[int]$seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    do {
        $row = @(Instances @($id))[0]
        if ($row.Status -eq $status -and ($status -ne 'Stopped' -or $row.StoppedMode -eq 'StopCharging')) { return }
        Start-Sleep -Seconds 4
    } while ((Get-Date) -lt $end)
    throw "Instance $id did not reach $status"
}
function Verify-Archive([string]$path) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf) -or
        (Get-Item -LiteralPath $path).Length -ne $archiveSize -or (Sha $path) -ne $archiveSha) {
        throw 'Exact rejected archive bytes are not retained'
    }
}
function Copy-Archive([string[]]$arguments) {
    $cmd = Get-Command scp.exe -CommandType Application
    $info = [System.Diagnostics.ProcessStartInfo]::new($cmd.Source)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    foreach ($argument in $arguments) { $info.ArgumentList.Add($argument) }
    $process = [System.Diagnostics.Process]::Start($info)
    try {
        if (!$process.WaitForExit(180000)) {
            $process.Kill($true)
            throw 'Archive copy exceeded 180 seconds'
        }
        if ($process.ExitCode) { throw "Archive copy failed: exit $($process.ExitCode)" }
    } finally { $process.Dispose() }
}
function Read-Retention([string]$path,[string]$inventoryHash) {
    $report = Get-Content -LiteralPath (Join-Path $path 'extra.json') -Raw | ConvertFrom-Json
    $index = Get-Content -LiteralPath (Join-Path $path 'inventory.json') -Raw | ConvertFrom-Json
    $pack = Join-Path $path 'extra.tar.gz'
    if ($report.schema -cne 'ax-retired-on1-scratch-extras-v1' -or $report.passed -ne $true -or
        $index.schema -cne 'ax-retired-on1-scratch-inventory-v1' -or $index.disk_id -cne $disk -or
        $report.inventory_sha256 -cne $inventoryHash -or
        $report.archive.sha256 -cne (Sha $pack) -or
        $report.archive.bytes -ne (Get-Item -LiteralPath $pack).Length) {
        throw 'Residual archive does not match the remote receipt and reviewed inventory'
    }
    $inventoryPaths = @($index.regular | ForEach-Object {$_.path} | Sort-Object)
    $retainedPaths = @(@($report.members | ForEach-Object {$_.path}) +
        @($report.excluded.PSObject.Properties.Name) | Sort-Object)
    if ($inventoryPaths.Count -ne $retainedPaths.Count -or (Compare-Object $inventoryPaths $retainedPaths -CaseSensitive) -or
        @($index.special | Where-Object {$_.kind -ne 'socket'}).Count) {
        throw 'Unaccounted scratch members or non-socket special entries'
    }
    return $report
}

Idle
$row = Disk
if ($Action -eq 'Release') {
    $inventory = Join-Path $dir 'inventory.json'
    if ($InventorySha256 -cnotmatch '^[0-9a-f]{64}$' -or (Sha $inventory) -cne $InventorySha256) {
        throw 'Pass the reviewed inventory file SHA-256'
    }
    Verify-Archive (Join-Path $dir $archiveName)
    $index = Get-Content -LiteralPath $inventory -Raw | ConvertFrom-Json
    $unmounted = Get-Content -LiteralPath (Join-Path $dir 'unmounted.json') -Raw | ConvertFrom-Json
    if ($index.disk_id -cne $disk -or $unmounted.unmounted -ne $true -or
        $unmounted.tag -cne $index.tag -or $unmounted.uuid -cne $index.uuid) {
        throw 'Readonly mount shutdown receipt does not match disk and boot inventory'
    }
    [void](Read-Retention $dir $InventorySha256)
    if ($null -eq $row) { Write-Output "$disk already absent"; return }
    if ($row.Status -eq 'In_use' -and $row.InstanceId -eq $objects) {
        [void](Api DetachDisk @('--InstanceId',$objects,'--DiskId',$disk))
    } elseif ($row.Status -ne 'Available' -or $row.InstanceId) {
        throw 'Unexpected disk attachment state'
    }
    $end = (Get-Date).AddSeconds(90)
    do {
        $row = Disk
        if ($null -eq $row -or ($row.Status -eq 'Available' -and !$row.InstanceId)) { break }
        if ($row.InstanceId -and $row.InstanceId -ne $objects) { throw 'Disk was attached elsewhere' }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $end)
    if ($row) {
        if ($row.Status -ne 'Available' -or $row.InstanceId) { throw 'Disk did not detach' }
        [void](Api DeleteDisk @('--DiskId',$disk))
        $end = (Get-Date).AddSeconds(90)
        do {
            $row = Disk
            if ($null -eq $row) { break }
            Start-Sleep -Seconds 3
        } while ((Get-Date) -lt $end)
    }
    if ($row) { throw 'Disk still present after deletion' }
    Idle
    Write-Output "$disk absent from DescribeDisks; all seven instances are StopCharging"
    return
}

if ($null -eq $row -or $row.Status -ne 'In_use' -or $row.InstanceId -ne $objects) {
    throw 'Original observer disk is not attached to A objects'
}
$cfg = (Resolve-Path -LiteralPath $SshConfig).Path
foreach ($local in @('inventory.json','unmounted.json','extra.json','extra.tar.gz',$archiveName,"$archiveName.partial")) {
    if (Test-Path -LiteralPath (Join-Path $dir $local)) { throw "Destination already exists: $local" }
}
$remote = "/srv/ax-receipts/$tag"
$mount = "/run/ax-retired-on1-scratch-$tag"
$helper = Join-Path $PSScriptRoot 'retired-on1-scratch.py'
$helperSha = Sha $helper
$sshOpts = @('-F',$cfg,'-oBatchMode=yes','-oConnectTimeout=7')
$started = [System.Collections.Generic.List[string]]::new()
$mounted = $false
try {
    foreach ($id in @($jump,$objects)) {
        $started.Add($id)
        [void](Api StartInstance @('--RegionId',$region,'--InstanceId',$id))
        Wait-Instance $id 'Running' 105
    }
    $end = (Get-Date).AddSeconds(55)
    do {
        ssh @sshOpts ax-pilot-objects true
        if (!$LASTEXITCODE) { break }
        Start-Sleep -Seconds 4
    } while ((Get-Date) -lt $end)
    if ($LASTEXITCODE) { throw 'A objects SSH did not become available' }
    ssh @sshOpts ax-pilot-objects "umask 077; mkdir '$remote'"
    if ($LASTEXITCODE) { throw 'Cannot create exclusive remote receipt path' }
    scp @sshOpts $helper "ax-pilot-objects:${remote}/tool.py"
    if ($LASTEXITCODE) { throw 'Cannot install remote helper' }
    $remoteSha = ssh @sshOpts ax-pilot-objects "sha256sum '$remote/tool.py'"
    if ($LASTEXITCODE -or ($remoteSha -split '\s+')[0] -ne $helperSha) { throw 'Remote helper hash mismatch' }
    $ready = ssh @sshOpts ax-pilot-objects "timeout 100s python3 '$remote/tool.py' mount --tag '$tag' --out '$remote/inventory.json'"
    if ($LASTEXITCODE) { throw 'Readonly disk inspection failed' }
    $mounted = $true
    $ready | Set-Content -Encoding utf8NoBOM -LiteralPath (Join-Path $dir 'mount-stdout.json')
    scp @sshOpts "ax-pilot-objects:${remote}/inventory.json" (Join-Path $dir 'inventory.json')
    if ($LASTEXITCODE) { throw 'Inventory copy failed' }
    $inventoryHash = Sha (Join-Path $dir 'inventory.json')
    $mountedInfo = ($ready -join "`n") | ConvertFrom-Json
    if ($mountedInfo.ready -ne $true -or $mountedInfo.sha256 -cne $inventoryHash -or $mountedInfo.mount -cne $mount) {
        throw 'Readonly mount did not return the expected inventory'
    }
    Copy-Archive ($sshOpts + @('-oServerAliveInterval=10','-oServerAliveCountMax=3',
        "ax-pilot-objects:${mount}/archives/$archiveName", (Join-Path $dir "$archiveName.partial")))
    Verify-Archive (Join-Path $dir "$archiveName.partial")
    Move-Item -LiteralPath (Join-Path $dir "$archiveName.partial") -Destination (Join-Path $dir $archiveName)
    ssh @sshOpts ax-pilot-objects "timeout --kill-after=8s 480s python3 '$remote/tool.py' pack --receipt '$remote/inventory.json' --sha256 '$inventoryHash' --out '$remote/extra.tar.gz' --report '$remote/extra.json'"
    if ($LASTEXITCODE) { throw 'Retired scratch has unverified files; disk will be retained' }
    foreach ($name in @('extra.json','extra.tar.gz')) {
        Copy-Archive ($sshOpts + @('-oServerAliveInterval=10','-oServerAliveCountMax=3',
            "ax-pilot-objects:${remote}/$name", (Join-Path $dir $name)))
    }
    [void](Read-Retention $dir $inventoryHash)
    Write-Output "Archives verified; review inventory SHA256: $inventoryHash"
} finally {
    try {
        if ($mounted) {
            $line = ssh @sshOpts ax-pilot-objects "sha256sum '$remote/inventory.json'"
            $exit = $LASTEXITCODE
            $remoteHash = ($line -split '\s+')[0]
            if ($exit -or $remoteHash -cnotmatch '^[0-9a-f]{64}$') { throw 'Remote inventory hash missing' }
            $result = ssh @sshOpts ax-pilot-objects "timeout 30s python3 '$remote/tool.py' unmount --receipt '$remote/inventory.json' --sha256 '$remoteHash'"
            $exit = $LASTEXITCODE
            if ($exit) { throw 'Readonly disk unmount failed' }
            $result | Set-Content -Encoding utf8NoBOM -LiteralPath (Join-Path $dir 'unmounted.json')
        }
    } finally {
        $stopErrors = @()
        foreach ($id in @($started | Sort-Object -Descending)) {
            try {
                $instance = @(Instances @($id))[0]
                if ($instance.Status -ne 'Stopped' -or $instance.StoppedMode -ne 'StopCharging') {
                    [void](Api StopInstance @('--RegionId',$region,'--InstanceId',$id,'--ForceStop','false','--StoppedMode','StopCharging'))
                }
                Wait-Instance $id 'Stopped' 120
            } catch { $stopErrors += "$id : $_" }
        }
        if ($stopErrors.Count) { throw "Verify shutdown immediately: $($stopErrors -join '; ')" }
        Idle
    }
}
