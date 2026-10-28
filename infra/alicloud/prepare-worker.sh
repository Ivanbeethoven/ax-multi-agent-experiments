#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
: "${AX_BUILD_ROOT:?Set AX_BUILD_ROOT to a new build directory on a mounted data disk}"
for command in cargo python3 docker fio redis-cli fusermount3 findmnt git; do
  command -v "$command" >/dev/null || { printf 'missing prerequisite: %s\n' "$command" >&2; exit 1; }
done
[[ ! -e "$AX_BUILD_ROOT" ]] || { printf 'build directory already exists\n' >&2; exit 1; }
mkdir -p "$AX_BUILD_ROOT"
[[ $(findmnt -n -o MAJ:MIN -T "$AX_BUILD_ROOT") != $(findmnt -n -o MAJ:MIN -T /) ]] || {
  printf 'build on the data disk, not the system filesystem\n' >&2; exit 1;
}
export CARGO_TARGET_DIR="$AX_BUILD_ROOT"
cargo build --release --locked --manifest-path "$ROOT/tools/fs-probe/Cargo.toml"
cargo build --release --locked --manifest-path "$ROOT/tools/full-fuse-replay/Cargo.toml"
sha256sum "$AX_BUILD_ROOT/release/ax-fs-probe" "$AX_BUILD_ROOT/release/full-fuse-replay" > "$AX_BUILD_ROOT/binaries.sha256"
printf 'tools built; record source commit and hashes from %s\n' "$AX_BUILD_ROOT/binaries.sha256"
