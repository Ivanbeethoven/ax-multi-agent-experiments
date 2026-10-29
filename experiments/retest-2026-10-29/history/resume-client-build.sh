#!/usr/bin/env bash
set -euo pipefail
export PATH="${HOME}/.cargo/bin:$PATH"
task_root=/home/hxy/ax-retest-20261029/build-release
[[ "$(cat "$task_root/brewfs-source-sha.txt")" == 80ddaf739e163c8933b0bb84390c4aa559bd2b40 ]]
[[ -f "$task_root/brewfs/Cargo.lock" ]]
export CARGO_TARGET_DIR="$task_root/brewfs-target" CARGO_BUILD_JOBS=1
export CARGO_INCREMENTAL=0 CARGO_PROFILE_RELEASE_DEBUG=0
cd "$task_root/brewfs"
printf '%s\n' '--no-default-features fuse-io-uring-runtime,workspace-overlay,native-packed-base,rustfs-ec-block-size-hint' > "$task_root/features-fuse.txt"
cargo build --locked --release --no-default-features --bin brewfs --bin packed_v3_snapshot_fixture \
  --features fuse-io-uring-runtime,workspace-overlay,native-packed-base,rustfs-ec-block-size-hint \
  > "$task_root/brewfs-build-fuse.log" 2>&1
sha256sum "$CARGO_TARGET_DIR/release/brewfs" "$CARGO_TARGET_DIR/release/packed_v3_snapshot_fixture" \
  > "$task_root/brewfs-binaries.sha256"
cat "$task_root/brewfs-binaries.sha256"
