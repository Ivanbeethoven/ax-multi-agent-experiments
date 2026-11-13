param([Parameter(Mandatory)][ValidatePattern('^ax-four-[0-9a-f]{12}$')][string]$Run,
      [ValidateSet('brewfs-flat','brewfs-workspace','juicefs')][string]$OnlyArm)

$ErrorActionPreference = 'Stop'
$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$scp = Join-Path $env:WINDIR 'System32/OpenSSH/scp.exe'
$local = Join-Path (Split-Path -Parent $PSScriptRoot) "receipts/$Run"
$campaign = '/srv/ax-idcai-20261113/campaign-100k-001'
$cmd = @'
set -eu
umask 077
cd __RUNPATH__
arms=(__ARMS__)
for name in "${arms[@]}"; do
    test -s "c2-$name-qualification/receipt.json"
done
archive=__ARCHIVE__
if [[ ! -e "$archive" ]]; then
    tmp=$(mktemp .c2-raw.XXXXXX)
    trap 'rm -f -- "$tmp"' EXIT
    files=()
    for name in "${arms[@]}"; do
        trial="c2-$name-qualification"
        files+=("$trial/receipt.json" "$trial/stages.jsonl")
        for f in "$trial"/{brewfs,juicefs}/{run,verify-touched,*.prom,initial.log,reopen.log}; do
            [[ ! -e "$f" ]] || files+=("$f")
        done
    done
    tar -czf "$tmp" -- "${files[@]}"
    gzip -t -- "$tmp"
    mv -n -- "$tmp" "$archive"
fi
sha256sum -- "$archive"
'@
$arms = if ($OnlyArm) { $OnlyArm } else { 'brewfs-flat brewfs-workspace juicefs' }
$archive = if ($OnlyArm) { "c2-$OnlyArm-raw.tar.gz" } else { 'c2-raw.tar.gz' }
$raw = $cmd.Replace('__RUNPATH__', "$campaign/$Run").Replace('__ARMS__', $arms).Replace('__ARCHIVE__', $archive) | & $ssh -T -o BatchMode=yes -o ConnectTimeout=8 idcai-jump 'tr -d "\r" | bash'
if ($LASTEXITCODE -ne 0) { throw 'C2 raw archive requires completed receipts for the selected arms.' }
$hash = (($raw -join "`n") -split '\s+')[0]
if ($hash -notmatch '^[a-f0-9]{64}$') { throw 'Bad C2 raw archive checksum.' }
$dest = Join-Path $local $archive
if (-not (Test-Path -LiteralPath $dest)) {
    $tmp = "$dest.partial"
    & $scp -B -q "idcai-jump:$campaign/$Run/$archive" $tmp
    if ($LASTEXITCODE -ne 0 -or (Get-FileHash -LiteralPath $tmp).Hash.ToLowerInvariant() -ne $hash) {
        throw 'C2 archive download did not match its checksum.'
    }
    Move-Item -LiteralPath $tmp -Destination $dest
}
if ((Get-FileHash -LiteralPath $dest).Hash.ToLowerInvariant() -ne $hash) { throw 'Existing C2 archive has the wrong checksum.' }
[IO.File]::WriteAllText("$dest.sha256", "$hash  $archive`n", [Text.UTF8Encoding]::new($false))
Write-Output $dest
