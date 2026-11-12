#requires -Version 7.4
param([Parameter(Mandatory)][string]$ReceiptRoot)

# Read only. This audit covers the original A pool, not a name-prefix search.
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $ReceiptRoot) { throw 'Use a new receipt directory' }
$dir = (New-Item -ItemType Directory -Path $ReceiptRoot).FullName
$removedInstances = @('i-bp1809mw7rlujtj9f705','i-bp1gy3cfuj9jik0y4oc3','i-bp1h9kaoya0predubdkn')
$removedDisks = @('d-bp1809mw7rlujtjc8eph','d-bp1809mw7rlujtjc8epi',
    'd-bp1gy3cfuj9jik0upt3j','d-bp1gy3cfuj9jik0upt3k',
    'd-bp1h9kaoya0predx6kvc','d-bp1h9kaoya0predx6kvd','d-bp1h9kaoya0predx6kve',
    'd-bp1h9kaoya0predx6kvf','d-bp1h9kaoya0predx6kvg')
$jumpId = 'i-bp1cp8ccqor34u2bo5os'
$jumpDisks = @('d-bp1cp8ccqor34u2bn4ib','d-bp166nue4ac1q7e5oar2')
function Query([string]$Name,[string]$Operation,[string]$Filter,[string[]]$Ids) {
    $raw = aliyun ecs $Operation --RegionId cn-hangzhou $Filter (ConvertTo-Json -InputObject $Ids -Compress) | Out-String
    $code = $LASTEXITCODE
    $raw | Set-Content -LiteralPath (Join-Path $dir ($Name+'.json')) -Encoding utf8NoBOM
    if ($code) { throw "$Operation failed; response retained" }
    $value = $raw | ConvertFrom-Json
    if (!$value.RequestId -or $value.Code) { throw 'Invalid API response' }
    return $value
}
$instances = Query 'removed-instances' 'DescribeInstances' '--InstanceIds' $removedInstances
$disks = Query 'removed-disks' 'DescribeDisks' '--DiskIds' $removedDisks
$jump = Query 'retained-jump' 'DescribeInstances' '--InstanceIds' @($jumpId)
$kept = Query 'retained-disks' 'DescribeDisks' '--DiskIds' $jumpDisks
if ($instances.TotalCount -ne 0 -or @($instances.Instances.Instance).Count -ne 0 -or
    $disks.TotalCount -ne 0 -or @($disks.Disks.Disk).Count -ne 0) {
    throw 'An original A resource still exists; this auditor does not delete it'
}
$node = @($jump.Instances.Instance)
if ($jump.TotalCount -ne 1 -or $node.Count -ne 1 -or $node[0].InstanceId -cne $jumpId -or
    $node[0].InstanceName -cne 'ax-c1-jump' -or $node[0].Status -cne 'Stopped' -or
    $node[0].StoppedMode -cne 'StopCharging') { throw 'Retained jump is absent or not StopCharging' }
$rows = @($kept.Disks.Disk)
if ($kept.TotalCount -ne 2 -or $rows.Count -ne 2 -or
    @(Compare-Object ($jumpDisks | Sort-Object) ($rows.DiskId | Sort-Object)).Count -or
    @($rows | Where-Object {$_.InstanceId -cne $jumpId -or $_.Status -cne 'In_use'}).Count) {
    throw 'Retained jump disk set differs'
}
$hashes = @{}
foreach ($name in @('removed-instances','removed-disks','retained-jump','retained-disks')) {
    $hashes[$name] = (Get-FileHash -LiteralPath (Join-Path $dir ($name+'.json'))).Hash.ToLowerInvariant()
}
[ordered]@{
    schema='ax-original-pool-a-retirement-audit-v1'
    observed_at_utc=[DateTime]::UtcNow.ToString('o')
    read_only=$true
    removed_instance_ids=$removedInstances
    removed_disk_ids=$removedDisks
    removed_disk_gib=520
    retained_instance=@($node | Select-Object InstanceId,InstanceName,Status,StoppedMode)
    retained_disks=@($rows | Select-Object DiskId,InstanceId,Size,Status)
    raw_response_sha256=$hashes
    verified=$true
} | ConvertTo-Json -Depth 6 | Tee-Object -FilePath (Join-Path $dir 'result.json')
