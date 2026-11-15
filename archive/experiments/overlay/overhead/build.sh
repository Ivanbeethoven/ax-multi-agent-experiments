#!/usr/bin/env bash
# Build the diagnostic in a fresh worktree from an existing BrewFS clone.
set -euo pipefail
test "$#" = 2 || { echo 'Usage: bash build.sh /absolute/brewfs-clone /absolute/new-output' >&2; exit 2; }
repository=$(realpath "$1")
output=$(realpath -m "$2")
here=$(cd -- "$(dirname -- "$0")" && pwd)
commit=03c8cc55d61473533c00a8afe13b4ef1fcd9b725
test ! -e "$output"
mkdir -p "$output"
git -C "$repository" worktree add --detach "$output/source" "$commit"
cd "$output/source"
# The recorded Git archive contains CRLF text; normalize only patch targets.
python3 - "$here/ablation.patch" <<'PY'
from pathlib import Path
import hashlib, sys
patch = Path(sys.argv[1])
assert hashlib.sha256(patch.read_bytes()).hexdigest() == '7cda6c9524b2440bc61a3ad9057814100088e2b735ce4d733b7616201cbdb466'
root = Path.cwd().resolve()
for line in patch.read_text().splitlines():
    if line.startswith('+++ b/'):
        path = (root / line[6:]).resolve()
        assert path.is_relative_to(root)
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
PY
git apply --check "$here/ablation.patch"
git apply "$here/ablation.patch"
export CARGO_INCREMENTAL=0 CARGO_PROFILE_RELEASE_DEBUG=0
export CARGO_TARGET_DIR="$output/target"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-16}"
rustc --version --verbose >"$output/toolchain.txt"
cargo --config profile.release.package.brewfs.opt-level=3 build --locked --release \
  --no-default-features \
  --features fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability \
  --bin brewfs --bin packed_snapshot_fixture >"$output/build.log" 2>&1
mkdir "$output/artifacts"
cp "$output/target/release/brewfs" "$output/target/release/packed_snapshot_fixture" "$output/artifacts/"
cp "$here/ablation.patch" "$output/artifacts/source.patch"
printf '%s\n' "$commit" >"$output/artifacts/source-commit.txt"
cd "$output/artifacts"
sha256sum brewfs packed_snapshot_fixture source.patch >SHA256SUMS
