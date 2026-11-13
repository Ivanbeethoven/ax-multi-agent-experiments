$ErrorActionPreference = 'Stop'
$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$nodes = [ordered]@{
    'idcai-jump' = 'ax-jump.ax.internal'
    'idcai-client2' = 'ax-client2.ax.internal'
    'idcai-obj1' = 'ax-obj1.ax.internal'
    'idcai-obj2' = 'ax-obj2.ax.internal'
    'idcai-obj3' = 'ax-obj3.ax.internal'
    'idcai-obj4' = 'ax-obj4.ax.internal'
}
$result = foreach ($source in $nodes.Keys) {
    $peers = ($nodes.Values | Where-Object { $_ -ne $nodes[$source] }) -join ' '
    $probe = 'for peer in ' + $peers + '; do ping -n -I axwg0 -c 2 -i 0.2 -W 2 "$peer" >/dev/null 2>&1 && printf "%s\tpass\n" "$peer" || printf "%s\tfail\n" "$peer"; done'
    $lines = & $ssh -n -T -o BatchMode=yes -o ConnectTimeout=8 $source $probe
    if ($LASTEXITCODE -ne 0 -or @($lines).Count -ne 5) {
        throw "SSH or probe failed on $source."
    }
    foreach ($line in $lines) {
        $peer, $state = $line -split "`t", 2
        [pscustomobject]@{ Source = $source; Target = $peer; Status = $state }
    }
}
$result
if (@($result | Where-Object { $_.Status -ne 'pass' }).Count -ne 0) {
    throw 'At least one directed private link failed.'
}
