param([Parameter(Mandatory)][ValidatePattern('^ax-four-[0-9a-f]{12}$')][string]$Run)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$receiptRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "receipts/$Run"
if (-not (Test-Path -LiteralPath $receiptRoot -PathType Container)) { throw 'Expected an existing attempt receipt directory.' }
$idleCode = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'check-run-idle.py') -Raw
$idle = foreach ($peer in 'idcai-jump','idcai-client2','idcai-obj1','idcai-obj2','idcai-obj3','idcai-obj4') {
    $raw = $idleCode | & $ssh -T -o BatchMode=yes -o ConnectTimeout=30 $peer "python3 - '$Run'"
    $exitCode = $LASTEXITCODE
    $parsed = $null
    $errorText = $null
    try { if ($raw) { $parsed = ($raw -join "`n") | ConvertFrom-Json } }
    catch { $errorText = 'Remote reply was not a valid idle receipt' }
    [ordered]@{node=$peer; exit_code=$exitCode; result=$parsed; error=$errorText}
}
$idlePath = Join-Path $receiptRoot ('idle-check-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '.json')
[IO.File]::WriteAllText($idlePath, (ConvertTo-Json -InputObject @($idle) -Depth 6), [Text.UTF8Encoding]::new($false))
if (@($idle | Where-Object {$_.exit_code -ne 0 -or $_.error -or -not $_.result.idle}).Count) {
    throw 'No services stopped: a workload/mount is still active or an idle check failed. Inspect idle-check receipt first.'
}
