$ErrorActionPreference = 'Stop'
$resultRoot = Join-Path $PSScriptRoot 'results'
$manifest = Get-Content (Join-Path $resultRoot 'cloud-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$issues = [System.Collections.Generic.List[string]]::new()
$hashCount = 0
foreach ($entry in $manifest.files.PSObject.Properties) {
    $target = Join-Path $resultRoot $entry.Name
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
        $issues.Add("missing raw file: $($entry.Name)")
        continue
    }
    $actual = Get-FileHash -LiteralPath $target -Algorithm SHA256
    if ($actual.Hash.ToLowerInvariant() -ne $entry.Value.sha256 -or (Get-Item -LiteralPath $target).Length -ne $entry.Value.bytes) {
        $issues.Add("raw file hash/size mismatch: $($entry.Name)")
    }
    $hashCount++
}
foreach ($entry in $manifest.automation_scripts.PSObject.Properties) {
    $target = Join-Path $PSScriptRoot $entry.Name
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) {
        $issues.Add("automation script hash mismatch: $($entry.Name)")
    }
}
$jsonCount = 0
foreach ($file in Get-ChildItem -LiteralPath $resultRoot -Filter '*.json' -Recurse -File) {
    try {
        $null = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $jsonCount++
    } catch {
        $issues.Add("invalid JSON: $($file.FullName): $_")
    }
}
$summary = Get-Content (Join-Path $resultRoot 'summary.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($check in $summary.checks.PSObject.Properties) {
    if ($check.Value -ne $true) { $issues.Add("final audit failed: $($check.Name)") }
}
$traceHash = (Get-FileHash -LiteralPath (Join-Path $resultRoot 'workload.jsonl') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($traceHash -ne $summary.trace_sha256) { $issues.Add('delivered workload SHA differs from measured SHA') }
$report = Get-Content (Join-Path $PSScriptRoot 'report.md') -Raw -Encoding UTF8
foreach ($match in [regex]::Matches($report, '\]\(([^)]+)\)')) {
    $link = $match.Groups[1].Value
    if ($link -match '^https?://' -or $link.StartsWith('#')) { continue }
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $link))) { $issues.Add("broken report link: $link") }
}
$verification = [ordered]@{
    verified_at_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    cloud_file_hashes_checked = $hashCount
    automation_script_hashes_checked = @($manifest.automation_scripts.PSObject.Properties).Count
    json_files_parsed = $jsonCount
    measured_trace_sha256 = $summary.trace_sha256
    delivered_trace_sha256 = $traceHash
    final_audit_checks = $summary.checks
    errors = @($issues.ToArray())
    passed = ($issues.Count -eq 0)
}
$out = $verification | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText((Join-Path $resultRoot 'local-verification.json'), $out + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
Write-Output $out
if ($issues.Count -ne 0) { exit 1 }
