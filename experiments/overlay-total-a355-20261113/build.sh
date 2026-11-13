#!/usr/bin/env bash
set -euo pipefail
test "$#" = 2 || { echo 'Usage: bash build.sh /absolute/brewfs-clone /absolute/new-output' >&2; exit 2; }
repository=$(realpath "$1")
output=$(realpath -m "$2")
here=$(cd -- "$(dirname -- "$0")" && pwd)
test ! -e "$output"
mkdir -p "$output"
git -C "$repository" worktree add --detach "$output/source" 03c8cc55d61473533c00a8afe13b4ef1fcd9b725
cd "$output/source"
python3 - <<'PY'
from pathlib import Path
p = Path('src/cli.rs')
p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))
PY
git apply --check "$here/retention-parity.patch"
git apply "$here/retention-parity.patch"
export CARGO_INCREMENTAL=0 CARGO_PROFILE_RELEASE_DEBUG=0 CARGO_TARGET_DIR="$output/target"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-16}"
features=fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability
cargo fmt --all --check
cargo --config profile.release.package.brewfs.opt-level=3 build --locked --release \
  --no-default-features --features "$features" --bin brewfs --bin packed_snapshot_fixture >"$output/build.log" 2>&1
mkdir "$output/artifacts"
cp "$output/target/release/brewfs" "$output/target/release/packed_snapshot_fixture" "$output/artifacts/"
cp "$here/retention-parity.patch" "$output/artifacts/source.patch"
cd "$output/artifacts"
sha256sum brewfs packed_snapshot_fixture source.patch >SHA256SUMS
