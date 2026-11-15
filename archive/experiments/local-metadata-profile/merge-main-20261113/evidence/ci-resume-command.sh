#!/usr/bin/env bash
set -euo pipefail
export PATH=/home/hxy/.cargo/bin:$PATH
root=/mnt/c/Users/HXY/.cache/brewfs-hot-lower-20261112
label=${1:?new label}
if [[ $(id -u) == 0 ]]; then
    bash "$root/defaults-swap.sh"
    exec runuser -u hxy -- bash "$0" "$label"
fi
cd "$root/candidate"
export GIT_DIR=/mnt/c/Users/HXY/Documents/Codex/2026-10-22/https-www-usenix-org-conferences-author/work/brewfs-upstream/.git/worktrees/candidate
export GIT_WORK_TREE="$root/candidate"
export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=core.autocrlf GIT_CONFIG_VALUE_0=true GIT_CONFIG_KEY_1=core.safecrlf GIT_CONFIG_VALUE_1=false
export CARGO_TARGET_DIR=/home/hxy/.cache/brewfs-hot-lower-20261112-checks
export CARGO_INCREMENTAL=0 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 CARGO_BUILD_JOBS=2 RUST_TEST_THREADS=2
unset BREWFS_METADATA_PROFILE BREWFS_METADATA_SYNC BREWFS_METADATA_JOURNAL_DIR BREWFS_METADATA_FLUSH_MS
exec >"$root/logs/merge-finish-$label.log" 2>&1
trap 'printf "%s\n" "$?" >"$root/logs/merge-finish-'"$label"'.exit"' EXIT
date -u --iso-8601=seconds
features=fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability
set -x
cargo fmt --all --check
cargo clippy --locked --workspace --all-targets --no-default-features --features "$features" -- -D warnings
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo test --locked --workspace --lib --bins
cargo test --locked --lib --no-default-features --features "$features"
bash tools/perf/check_local_metadata.sh "$root/logs/merge-redis-$label"
git diff --check
date -u --iso-8601=seconds
