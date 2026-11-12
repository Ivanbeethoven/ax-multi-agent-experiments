param([Parameter(Mandatory)][string]$ReceiptRoot)

# Read only: prove that the original B fleet is gone and the retained fleet is stopped.
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $ReceiptRoot) { throw 'Use a new receipt directory' }
$dir = (New-Item -ItemType Directory -Path $ReceiptRoot -ErrorAction Stop).FullName
$region = 'cn-hangzhou'
$removedInstances = @('i-bp1bhdngko7nq3c3jrhp','i-bp14nenf7osz9v1snsp2','i-bp1fmmfkcpr8w99j2bsy')
$removedDisks = @('d-bp1bhdngko7nq3c2i33z','d-bp1bhdngko7nq3c2i340',
    'd-bp1bhdngko7nq3c2i341','d-bp1bhdngko7nq3c2i342','d-bp1bhdngko7nq3c2i343',
    'd-bp14nenf7osz9v1rkk5k','d-bp14nenf7osz9v1rkk5l',
    'd-bp1fmmfkcpr8w99pwgc4','d-bp1fmmfkcpr8w99pwgc5')
$retained = @{
    'i-bp1cp8ccqor34u2bo5os'='ax-c1-jump'
    'i-bp1809mw7rlujtj9f705'='ax-pilot-20261111-client'
    'i-bp1gy3cfuj9jik0y4oc3'='ax-pilot-20261111-metadata'
    'i-bp1h9kaoya0predubdkn'='ax-pilot-20261111-objects'
}
function Query([string]$Name,[string]$Operation,[string]$Filter,[string[]]$Ids) {
    $raw = aliyun ecs $Operation --RegionId $region $Filter (ConvertTo-Json -InputObject $Ids -Compress) | Out-String
    $code = $LASTEXITCODE
    $path = Join-Path $dir ($Name+'.json')
    $raw | Set-Content -LiteralPath $path -Encoding utf8NoBOM
    if ($code) { throw "$Operation failed; raw response retained" }
    $report = $raw | ConvertFrom-Json
    if (!$report.RequestId) { throw 'Missing API request ID' }
    return $report
}
$oldInstances = Query 'removed-instances' 'DescribeInstances' '--InstanceIds' $removedInstances
$oldDisks = Query 'removed-disks' 'DescribeDisks' '--DiskIds' $removedDisks
$kept = Query 'retained-instances' 'DescribeInstances' '--InstanceIds' @($retained.Keys | Sort-Object)
if ($oldInstances.TotalCount -ne 0 -or @($oldInstances.Instances.Instance).Count -ne 0 -or
    $oldDisks.TotalCount -ne 0 -or @($oldDisks.Disks.Disk).Count -ne 0) {
    throw 'Original B resources still exist; no deletion is performed by this auditor'
}
$rows = @($kept.Instances.Instance)
if ($kept.TotalCount -ne $retained.Count -or $rows.Count -ne $retained.Count -or
    @($rows.InstanceId | Sort-Object -Unique).Count -ne $retained.Count -or
    @(Compare-Object @($retained.Keys | Sort-Object) @($rows.InstanceId | Sort-Object)).Count) {
    throw 'Retained fleet membership differs'
}
foreach ($row in $rows) {
    if ($row.InstanceName -cne $retained[$row.InstanceId] -or $row.Status -cne 'Stopped' -or
        $row.StoppedMode -cne 'StopCharging') { throw 'A retained instance differs or is still running' }
}
$proof = [ordered]@{
    schema='ax-original-pool-b-retirement-audit-v1'
    observed_at=[DateTime]::UtcNow.ToString('o')
    passed=$true
    read_only=$true
    removed_instance_ids=$removedInstances
    removed_disk_ids=$removedDisks
    removed_disk_gib=520
    retained_instances=@($rows | Select-Object InstanceId,InstanceName,Status,StoppedMode)
    api_request_ids=@($oldInstances.RequestId,$oldDisks.RequestId,$kept.RequestId)
    raw_response_sha256=@{}
}
foreach ($name in @('removed-instances','removed-disks','retained-instances')) {
    $proof.raw_response_sha256[$name]=(Get-FileHash -LiteralPath (Join-Path $dir ($name+'.json')) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$proof | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $dir 'result.json') -Encoding utf8NoBOM
$proof | ConvertTo-Json -Depth 6
