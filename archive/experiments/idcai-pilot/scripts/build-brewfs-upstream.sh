#!/usr/bin/env bash
set -euo pipefail
revision=${1:?full source revision}
[[ "$revision" =~ ^[a-f0-9]{40}$ ]]
base=/srv/ax-idcai-20261113
source="$base/src/brewfs-$revision"
out="$base/builds/brewfs-$revision"
test "$(git -C "$source" rev-parse HEAD)" = "$revision"
test -z "$(git -C "$source" status --porcelain)"
mkdir -p "$out"
test ! -e "$out/build.json"
exec 8>"$base/build.lock"
flock -n 8
if findmnt -rn -o TARGET | grep -Eq '^/srv/(ax-idcai-20261113/campaign-|ax-pilot-session)'; then
    echo 'Close experiment mounts before compiling' >&2; exit 2
fi
export CARGO_HOME="$base/cargo-build" CARGO_TARGET_DIR="$base/targets/brewfs"
export CARGO_BUILD_JOBS=24 CARGO_INCREMENTAL=0
export PATH="$base/toolchain-1.99.0/bin:$PATH" RUSTC="$base/toolchain-1.99.0/bin/rustc"
export RUSTFLAGS='-C target-cpu=x86-64'
export CARGO_NET_RETRY=1 CARGO_HTTP_TIMEOUT=40 CARGO_NET_GIT_FETCH_WITH_CLI=true GIT_TERMINAL_PROMPT=0
cd "$source"
date -u +%FT%TZ > "$out/start.txt"
rustc -Vv > "$out/rustc.txt"
cargo -V > "$out/cargo.txt"
git show -s --format=fuller HEAD > "$out/source.txt"
cp Cargo.lock "$out/Cargo.lock"
printf '%s\n' 'cargo build --locked --release --offline --features workspace,observability,rustfs-ec-block-size-hint --bin brewfs --bin packed_snapshot_fixture' > "$out/command.txt"
if ! cargo fetch --locked --offline --target x86_64-unknown-linux-gnu > "$out/fetch.log" 2>&1; then
    cargo fetch --locked --target x86_64-unknown-linux-gnu >> "$out/fetch.log" 2>&1
fi
set +e
cargo build --locked --release --offline --features workspace,observability,rustfs-ec-block-size-hint \
    --bin brewfs --bin packed_snapshot_fixture > "$out/build.log" 2>&1
code=$?
set -e
printf '%s\n' "$code" > "$out/build-exit.txt"
date -u +%FT%TZ > "$out/end.txt"
[[ "$code" == 0 ]]
mkdir "$out/bin"
for name in brewfs packed_snapshot_fixture; do
    original="$CARGO_TARGET_DIR/release/$name"
    sha256sum "$original" >> "$out/unstripped.sha256"
    objcopy --strip-debug "$original" "$out/bin/$name"
    chmod 755 "$out/bin/$name"
    "$out/bin/$name" --help > "$out/$name-help.txt"
done
test -z "$(git status --porcelain)"
cd "$out/bin"
sha256sum brewfs packed_snapshot_fixture > SHA256SUMS
tar -I 'zstd -T4 -3' -cf "$out/binaries.tar.zst" brewfs packed_snapshot_fixture SHA256SUMS
cd "$out"
sha256sum binaries.tar.zst > archive.sha256
python3 - "$revision" <<'PY'
import hashlib,json,socket,sys
from pathlib import Path
p=Path.cwd()
def sha(f):
    with f.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
result=dict(passed=True,source_commit=sys.argv[1],source_clean=True,build_host=socket.gethostname(),
    build_profile='release; upstream defaults; distributed copies have debug symbols stripped',
    features=['workspace','observability','rustfs-ec-block-size-hint'],default_features=True,
    target_cpu='x86-64',jobs=24,command=(p/'command.txt').read_text().strip(),
    started_utc=(p/'start.txt').read_text().strip(),ended_utc=(p/'end.txt').read_text().strip(),
    cargo_lock_sha256=sha(p/'Cargo.lock'),build_log_sha256=sha(p/'build.log'),
    archive_sha256=sha(p/'binaries.tar.zst'),archive_bytes=(p/'binaries.tar.zst').stat().st_size,
    artifacts={f.name:dict(sha256=sha(f),bytes=f.stat().st_size) for f in (p/'bin').iterdir() if f.name!='SHA256SUMS'})
(p/'build.json').write_text(json.dumps(result,indent=2)+'\n')
PY
