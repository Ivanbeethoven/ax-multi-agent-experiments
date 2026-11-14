#!/usr/bin/env bash
# Build both reviewed commits with identical settings. Does not start services.
set -euo pipefail
test "$#" = 4 || { echo 'Usage: bash build.sh /brewfs-repository baseline-commit candidate-commit /new-output' >&2; exit 2; }
repository=$(realpath "$1")
baseline=$(git -C "$repository" rev-parse --verify "$2^{commit}")
candidate=$(git -C "$repository" rev-parse --verify "$3^{commit}")
output=$(realpath -m "$4")
base=03c8cc55d61473533c00a8afe13b4ef1fcd9b725
test ! -e "$output"
mkdir -p "$output"
export CARGO_INCREMENTAL=0 CARGO_PROFILE_RELEASE_DEBUG=0
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-12}" CARGO_TARGET_DIR="$output/target"
features=fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability
rustc -Vv >"$output/toolchain.txt"
for arm in baseline candidate; do
  commit=$baseline
  if test "$arm" = candidate; then commit=$candidate; fi
  git -C "$repository" merge-base --is-ancestor "$base" "$commit"
  git -C "$repository" worktree add --detach "$output/$arm-source" "$commit"
  mkdir "$output/$arm-artifacts"
  (
    cd "$output/$arm-source"
    cargo --config profile.release.package.brewfs.opt-level=3 build --locked --release \
      --no-default-features --features "$features" --bin brewfs --bin packed_snapshot_fixture
    cp "$CARGO_TARGET_DIR/release/brewfs" "$output/$arm-artifacts/"
    # Freeze one producer for both arms, independent of the candidate changes.
    if test "$arm" = baseline; then cp "$CARGO_TARGET_DIR/release/packed_snapshot_fixture" "$output/packed_snapshot_fixture"; fi
    cp "$output/packed_snapshot_fixture" "$output/$arm-artifacts/"
    git diff --binary "$base" "$commit" --output="$output/$arm-artifacts/source.patch" -- src
    printf '%s\n' "$commit" >"$output/$arm-artifacts/commit.txt"
    cd "$output/$arm-artifacts"
    sha256sum brewfs packed_snapshot_fixture source.patch >SHA256SUMS
  ) >"$output/$arm-build.log" 2>&1
done
