#!/usr/bin/env bash
set -euo pipefail
export PATH=/home/hxy/.cargo/bin:$PATH
root=/mnt/c/Users/HXY/.cache/brewfs-hot-lower-20261112
label=${1:?new label}
cd "$root/candidate"
export GIT_DIR=/mnt/c/Users/HXY/Documents/Codex/2026-10-22/https-www-usenix-org-conferences-author/work/brewfs-upstream/.git/worktrees/candidate
export GIT_WORK_TREE="$root/candidate"
export GIT_OPTIONAL_LOCKS=0
export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=core.autocrlf GIT_CONFIG_VALUE_0=true GIT_CONFIG_KEY_1=core.safecrlf GIT_CONFIG_VALUE_1=false
export CARGO_TARGET_DIR=/home/hxy/.cache/brewfs-hot-lower-20261112-checks
export CARGO_INCREMENTAL=0 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 CARGO_BUILD_JOBS=2
out="$root/logs/merge-smoke-build-$label"
test ! -e "$out"
mkdir "$out"
exec >"$out/build.log" 2>&1
trap 'printf "%s\n" "$?" >"'"$out"'/exit.txt"' EXIT
git rev-parse HEAD >"$out/source-commit.txt"
rustc -Vv
features=fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability
cargo build --locked --no-default-features --features "$features" --bin brewfs --bin packed_snapshot_fixture
cp "$CARGO_TARGET_DIR/debug/brewfs" "$CARGO_TARGET_DIR/debug/packed_snapshot_fixture" "$out/"
cd "$out"
sha256sum brewfs packed_snapshot_fixture >SHA256SUMS
printf '%s\n' 'Debug build for integration correctness only; no performance claim.' >BUILD-SCOPE.txt
