#!/usr/bin/env bash
set -euo pipefail
test "$#" = 3 || { echo 'Usage: bash build.sh /absolute/brewfs-repository /absolute/new-output candidate-commit' >&2; exit 2; }
repository=$(realpath "$1")
output=$(realpath -m "$2")
candidate=$(git -C "$repository" rev-parse --verify "$3^{commit}")
base=03c8cc55d61473533c00a8afe13b4ef1fcd9b725
here=$(cd -- "$(dirname -- "$0")" && pwd)
test ! -e "$output"
mkdir -p "$output"
git -C "$repository" worktree add --detach "$output/baseline-source" "$base"
git -C "$repository" worktree add --detach "$output/candidate-source" "$candidate"
# The candidate commit must include the same opt-in standalone cache wiring.
# Normalize Windows checkout line endings before applying the baseline patch.
python3 - "$output/baseline-source/src/cli.rs" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); p.write_bytes(p.read_bytes().replace(b'\r\n',b'\n'))
PY
git -C "$output/baseline-source" apply "$here/../overlay-total-a355-20261113/retention-parity.patch"
export CARGO_INCREMENTAL=0 CARGO_PROFILE_RELEASE_DEBUG=0
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-16}" CARGO_TARGET_DIR="$output/target"
features=fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability
rustc -Vv >"$output/toolchain.txt"
for arm in baseline candidate; do
  mkdir "$output/$arm-artifacts"
  (
    cd "$output/$arm-source"
    cargo --config profile.release.package.brewfs.opt-level=3 build --locked --release \
      --no-default-features --features "$features" --bin brewfs --bin packed_snapshot_fixture
    cp "$CARGO_TARGET_DIR/release/brewfs" "$CARGO_TARGET_DIR/release/packed_snapshot_fixture" "$output/$arm-artifacts/"
    if test "$arm" = baseline; then
      cp "$here/../overlay-total-a355-20261113/retention-parity.patch" "$output/$arm-artifacts/source.patch"
    else
      git diff --binary "$base" "$candidate" --output="$output/$arm-artifacts/source.patch"
    fi
    cd "$output/$arm-artifacts"
    sha256sum brewfs packed_snapshot_fixture source.patch >SHA256SUMS
  ) >"$output/$arm-build.log" 2>&1
done
printf '%s\n' "$base" >"$output/baseline-commit.txt"
printf '%s\n' "$candidate" >"$output/candidate-commit.txt"
