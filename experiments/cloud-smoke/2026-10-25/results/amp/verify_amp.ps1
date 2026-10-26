$ErrorActionPreference = 'Stop'
$ampRoot = $PSScriptRoot
$oldRoot = Split-Path $ampRoot -Parent
$dateRoot = Split-Path $oldRoot -Parent
$summary = Get-Content -Raw -Encoding UTF8 (Join-Path $ampRoot 'summary.json') | ConvertFrom-Json
$invariant = [Globalization.CultureInfo]::InvariantCulture
$checks = 0
function Assert-Equal($actual, $expected, [string]$context) {
    if ($actual -ne $expected) { throw "$context actual=$actual expected=$expected" }
    $script:checks++
}
function Metric([string]$raw, [string]$name, [string]$method = '') {
    [decimal]$sum = 0
    foreach ($line in ($raw -split "`n")) {
        if (-not ($line.StartsWith($name + '{') -or $line.StartsWith($name + ' '))) { continue }
        if ($method -and -not $line.Contains('method="' + $method + '"')) { continue }
        $value = $line.Substring($line.LastIndexOf(' ') + 1).Trim()
        $sum += [decimal]::Parse($value, [Globalization.NumberStyles]::Float, $invariant)
    }
    return $sum
}
function Hash([string]$path) { return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
Assert-Equal @($summary.cases).Count 16 'case count'
$byCase = @{}
foreach ($row in $summary.cases) {
    $byCase[$row.case] = $row
    $directory = Join-Path $ampRoot $row.case
    $aPath = Join-Path $directory 'before.json'
    $bPath = Join-Path $directory 'after.json'
    Assert-Equal (Hash $aPath) $row.before_sha256 ($row.case + ' before hash')
    Assert-Equal (Hash $bPath) $row.after_sha256 ($row.case + ' after hash')
    $a = Get-Content -Raw -Encoding UTF8 $aPath | ConvertFrom-Json
    $b = Get-Content -Raw -Encoding UTF8 $bPath | ConvertFrom-Json
    $actualResult = Get-Content -Raw -Encoding UTF8 (Join-Path $directory 'result.json') | ConvertFrom-Json
    Assert-Equal $actualResult.validation.ok $true ($row.case + ' content validation')
    foreach ($client in 'original','default','writeback','small') {
        $pa = $a.clients.$client.raw
        $pb = $b.clients.$client.raw
        foreach ($method in 'PUT','GET') {
            foreach ($spec in @(
                @('juicefs_object_request_data_bytes', ($method + '_bytes')),
                @('juicefs_object_request_durations_histogram_seconds_count', ($method + '_count'))
            )) {
                $v = (Metric $pb $spec[0] $method) - (Metric $pa $spec[0] $method)
                $expected = if ($client -eq $row.client) { $row.($spec[1]) } else { 0 }
                Assert-Equal $v $expected ($row.case + ' ' + $client + ' ' + $spec[1])
            }
        }
        foreach ($name in 'juicefs_compact_size_histogram_bytes_count','juicefs_compact_size_histogram_bytes_sum') {
            $v = (Metric $pb $name) - (Metric $pa $name)
            $expected = if ($client -eq $row.client) { $row.$name } else { 0 }
            Assert-Equal $v $expected ($row.case + ' ' + $client + ' ' + $name)
        }
    }
    $pa = $a.clients.($row.client).raw
    $pb = $b.clients.($row.client).raw
    Assert-Equal ((Metric $pb 'juicefs_fuse_written_size_bytes_sum') - (Metric $pa 'juicefs_fuse_written_size_bytes_sum')) $row.logical_write_bytes ($row.case + ' FUSE bytes')
    foreach ($name in 'juicefs_staging_blocks','juicefs_staging_block_bytes','juicefs_staging_writing_blocks') {
        Assert-Equal (Metric $pb $name) 0 ($row.case + ' drained ' + $name)
    }
    foreach ($name in 'juicefs_object_request_errors','juicefs_staging_block_errors') {
        Assert-Equal ((Metric $pb $name) - (Metric $pa $name)) 0 ($row.case + ' errors ' + $name)
    }
}
$f = $byCase.F_read_trigger.first_cycle
Assert-Equal $f.write_phase.PUT_bytes 16384 'probe write PUT bytes'
Assert-Equal $f.write_phase.PUT_count 4 'probe write PUT count'
Assert-Equal $f.write_phase.GET_bytes 0 'probe write GET bytes'
Assert-Equal $f.write_phase.juicefs_compact_size_histogram_bytes_count 0 'probe write compactions'
Assert-Equal $f.read_phase.PUT_bytes 4194304 'probe read PUT bytes'
Assert-Equal $f.read_phase.PUT_count 1 'probe read PUT count'
Assert-Equal $f.read_phase.juicefs_fuse_written_size_bytes_sum 0 'probe no new write'
Assert-Equal $f.read_phase.juicefs_compact_size_histogram_bytes_count 1 'probe read compaction'
foreach ($name in 'F_read_100','D_read_100','E_read_100') {
    Assert-Equal $byCase[$name].validation.sha256 $byCase.F_write_400.validation.sha256 ($name + ' same final content')
    Assert-Equal $byCase[$name].PUT_bytes 421068800 ($name + ' fixed PUT bytes')
    Assert-Equal $byCase[$name].juicefs_compact_size_histogram_bytes_count 100 ($name + ' fixed compactions')
}
Assert-Equal ($byCase.F_read_100.PUT_bytes - $byCase.F_write_400.PUT_bytes) (96 * 4194304) 'read-induced extra PUT'
Assert-Equal $byCase.E_each.PUT_bytes $byCase.A_each.PUT_bytes 'small block same bytes'
Assert-Equal ($byCase.E_each.PUT_count - $byCase.A_each.PUT_count) 630 'small block extra requests'
$document = [IO.File]::ReadAllText((Join-Path $dateRoot 'jfs-write-amplification.md'))
foreach ($row in $summary.cases) {
    $line = @($document -split "`n" | Where-Object { $_ -match ('^\| ' + [regex]::Escape($row.case) + ' \| [0-9]') })
    Assert-Equal $line.Count 1 ($row.case + ' report row')
    $cells = @($line[0].Split('|') | ForEach-Object { $_.Trim() })
    $hasReadColumn = $cells.Count -eq 12
    $putIndex = if ($hasReadColumn) { 4 } else { 3 }
    Assert-Equal ([long]$cells[2].Replace(',','')) $row.logical_write_bytes ($row.case + ' report write bytes')
    Assert-Equal ([long]$cells[$putIndex].Replace(',','')) $row.PUT_bytes ($row.case + ' report PUT bytes')
    Assert-Equal ([long]$cells[$putIndex+1].Replace(',','')) $row.PUT_count ($row.case + ' report PUT count')
    Assert-Equal ([long]$cells[$putIndex+2].Replace(',','')) $row.GET_bytes ($row.case + ' report GET bytes')
    Assert-Equal ([long]$cells[$putIndex+3].Replace(',','')) $row.GET_count ($row.case + ' report GET count')
}
$oldManifest = Get-Content -Raw -Encoding UTF8 (Join-Path $oldRoot 'cloud-manifest.json') | ConvertFrom-Json
$oldCount = 0
foreach ($entry in $oldManifest.files.PSObject.Properties) {
    $path = Join-Path $oldRoot $entry.Name
    if (Test-Path -LiteralPath $path) {
        Assert-Equal (Hash $path) $entry.Value.sha256 ('old archive ' + $entry.Name)
        $oldCount++
    }
}
$sources = Get-Content -Raw -Encoding UTF8 (Join-Path $ampRoot 'source-final-manifest.json') | ConvertFrom-Json
foreach ($source in $sources) {
    Assert-Equal (Hash (Join-Path (Join-Path $ampRoot 'source') $source.file)) $source.sha256 ('source ' + $source.file)
}
$reportPath = Join-Path $dateRoot 'report.md'
$reportBytes = [IO.File]::ReadAllBytes($reportPath)
$lastLine = Get-Content -Encoding UTF8 $reportPath | Where-Object { $_.Trim() } | Select-Object -Last 1
Assert-Equal $lastLine.Contains('(jfs-write-amplification.md)') $true 'report link suffix'
$suffix = [Text.Encoding]::UTF8.GetBytes("`n" + $lastLine + "`n")
$originalBytes = New-Object byte[] ($reportBytes.Length - $suffix.Length)
[Array]::Copy($reportBytes,$originalBytes,$originalBytes.Length)
$sha256 = [Security.Cryptography.SHA256]::Create()
$originalHash = ([BitConverter]::ToString($sha256.ComputeHash($originalBytes))).Replace('-','').ToLowerInvariant()
Assert-Equal $originalHash ([IO.File]::ReadAllText((Join-Path $ampRoot 'report-original.sha256')).Trim()) 'report original bytes unchanged'
$finish = Get-Content -Raw -Encoding UTF8 (Join-Path $ampRoot 'finish.json') | ConvertFrom-Json
foreach ($cmd in $finish.unmounts) { Assert-Equal $cmd.returncode 0 'experiment unmount' }
Assert-Equal $finish.original_mount.returncode 0 'original mount retained'
Assert-Equal $finish.brewfs_mount_presence_only.returncode 0 'BrewFS mount retained'
Assert-Equal $finish.formats.'1'.BlockSize 4096 'default block KiB'
Assert-Equal $finish.formats.'2'.BlockSize 64 'small block KiB'
$verification = @{ utc=[DateTime]::UtcNow.ToString('o'); ok=$true; assertions=$checks; cases=16; original_archive_files_unchanged=$oldCount; report_original_bytes_unchanged=$true; sources_verified=@($sources).Count }
$verification | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $ampRoot 'local-verification.json')
$verification | ConvertTo-Json -Depth 4
