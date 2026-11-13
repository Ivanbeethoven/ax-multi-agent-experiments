param([Parameter(Mandatory)][ValidatePattern('^ax-four-[0-9a-f]{12}$')][string]$Run)

$ErrorActionPreference = 'Stop'
$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$base = '/srv/ax-idcai-20261113'
$roles = [ordered]@{'idcai-jump'='client';'idcai-client2'='target';'idcai-obj1'='obj1';'idcai-obj2'='obj2';'idcai-obj3'='obj3';'idcai-obj4'='obj4'}
$direct = @{'idcai-client2'='idcai-60525';'idcai-obj1'='idcai-60103';'idcai-obj2'='idcai-60439';'idcai-obj3'='idcai-60533';'idcai-obj4'='idcai-60555'}
$receiptRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "receipts/$Run"
if (-not (Test-Path -LiteralPath $receiptRoot -PathType Container)) { throw 'Expected an existing attempt receipt directory.' }
$utc = [DateTime]::UtcNow
$receipt = Join-Path $receiptRoot ('closure-' + $utc.ToString('yyyyMMddTHHmmssfffZ') + '.json')
& "$PSScriptRoot/assert-run-idle.ps1" -Run $Run
$script = @'
set -euo pipefail
base=/srv/ax-idcai-20261113
run=__RUN__
role=__ROLE__
campaign="$base/campaign-100k-001/$run"
audit() {
    findmnt -rn -o TARGET | grep -E "^($campaign|/srv/ax-pilot-session)(/|$)" || true
}
mounts=$(audit)
if [[ -n "$mounts" ]]; then
    jq -n --arg role "$role" --arg mounts "$mounts" '{role:$role,passed:false,blocked_by_mounts:$mounts}'
    exit 3
fi
if systemctl is-active --quiet "$run-observer.service"; then
    observer="$campaign/observer-$role"
    [[ -d "$observer" ]]
    touch "$observer/stop"
    for i in $(seq 1 12); do
        if ! systemctl is-active --quiet "$run-observer.service"; then break; fi
        sleep 1
    done
fi
if systemctl is-active --quiet "$run-observer.service"; then exit 4; fi
if [[ "$role" == obj* ]]; then
    bash "$base/rustfs-four-node-node.sh" stop "$run" >/dev/null
fi
for unit in "$run" "$run-otel" "$run-redis" "$run-observer"; do
    state=$(systemctl show -p ActiveState --value "$unit.service")
    case "$state" in inactive|failed) ;; *) exit 5;; esac
done
test -z "$(docker ps -aq --filter "label=ax.experiment.owner=$run")"
test -z "$(ss -lntH '( sport = :19000 or sport = :16379 or sport = :4318 or sport = :9464 or sport = :18910 )')"
test -z "$(audit)"
jq -n --arg role "$role" --arg host "$(hostname)" '{role:$role,hostname:$host,passed:true,owned_services_inactive:true,owned_containers_absent:true,test_ports_clear:true,mounts_absent:true}'
'@
$checks = foreach ($peer in $roles.Keys) {
    $remote = $script.Replace('__RUN__', $Run).Replace('__ROLE__', $roles[$peer])
    $via = $peer
    $raw = $remote | & $ssh -T -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 $via 'tr -d "\r" | bash'
    $code = $LASTEXITCODE
    if ($code -eq 255 -and $direct.ContainsKey($peer)) {
        $via = $direct[$peer]
        $raw = $remote | & $ssh -T -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 $via 'tr -d "\r" | bash'
        $code = $LASTEXITCODE
    }
    $parsed = $null
    if ($raw) { $parsed = ($raw -join "`n") | ConvertFrom-Json }
    [pscustomobject]@{ alias=$peer; via=$via; exit_code=$code; result=$parsed }
}
$proof = [ordered]@{run=$Run; utc=$utc.ToString('o'); passed=(@($checks | Where-Object {$_.exit_code -ne 0 -or -not $_.result.passed}).Count -eq 0); nodes=$checks}
[IO.File]::WriteAllText($receipt, ($proof | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
Write-Output $receipt
if (-not $proof.passed) { throw 'Incomplete cleanup evidence; do not start another trial.' }
