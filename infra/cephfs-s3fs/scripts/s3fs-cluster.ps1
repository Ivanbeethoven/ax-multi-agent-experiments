# Windows PowerShell wrapper for 6-node s3fs cluster control via idcai-60001
param(
    [Parameter(Position=0)]
    [ValidateSet("status","configure","mount","umount","remount","test")]
    [string]$Action = "status",

    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ArgsRest
)

$ErrorActionPreference = "Stop"
$escapedArgs = ""
if ($null -ne $ArgsRest -and $ArgsRest.Count -gt 0) {
    $escapedArgs = ($ArgsRest | Where-Object { $null -ne $_ } | ForEach-Object { "'" + ($_.Replace("'", "'`"'`"'")) + "'" }) -join " "
}
ssh -o BatchMode=yes idcai-60001 "/usr/local/bin/s3fs-cluster $Action $escapedArgs"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
