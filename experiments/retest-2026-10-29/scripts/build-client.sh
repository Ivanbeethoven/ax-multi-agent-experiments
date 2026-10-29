#!/usr/bin/env bash
# One build per frozen source/toolchain/features; never runs a measurement.
set -euo pipefail
export PATH="${HOME}/.cargo/bin:$PATH"
[[ $# == 3 ]] || { echo 'usage: build-client.sh CLEAN_SOURCE SHA NEW_BUILD_ROOT' >&2; exit 2; }
source_root="$1"; revision="$2"; build_root="$3"
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$build_root" == /* ]] || exit 2
[[ "$(git -C "$source_root" rev-parse HEAD)" == "$revision" ]] || exit 2
# Export only the pinned Git object, never the working tree. This also avoids
# DrvFS mode/LFS-filter differences being mistaken for source modifications.
[[ ! -e "$build_root/brewfs" ]] || { echo 'build root already initialized; inspect existing build first' >&2; exit 2; }
mkdir -p "$build_root/brewfs"
git -C "$source_root" archive "$revision" | tar -xf - -C "$build_root/brewfs"
printf '%s\n' "$revision" > "$build_root/brewfs-source-sha.txt"
cd "$build_root/brewfs"
export CARGO_TARGET_DIR="$build_root/brewfs-target" CARGO_BUILD_JOBS=1
export CARGO_INCREMENTAL=0 CARGO_PROFILE_RELEASE_DEBUG=0
rustc -Vv > "$build_root/rustc.txt"
cargo -V > "$build_root/cargo.txt"
printf '%s\n' '--no-default-features fuse-io-uring-runtime,workspace-overlay,native-packed-base,rustfs-ec-block-size-hint' > "$build_root/features.txt"
cargo build --locked --release --no-default-features --bin brewfs --bin packed_v3_snapshot_fixture \
  --features fuse-io-uring-runtime,workspace-overlay,native-packed-base,rustfs-ec-block-size-hint \
  > "$build_root/brewfs-build.log" 2>&1
sha256sum "$CARGO_TARGET_DIR/release/brewfs" "$CARGO_TARGET_DIR/release/packed_v3_snapshot_fixture" \
  > "$build_root/brewfs-binaries.sha256"
cat "$build_root/brewfs-binaries.sha256"
