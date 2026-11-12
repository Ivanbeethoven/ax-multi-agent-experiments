#!/usr/bin/env bash
# Build a diagnostic candidate in an isolated rbm checkout, never an active target.
set -euo pipefail
job=${1:?isolated job directory}
artifact_dir=${2:-$job}
test ! -e "$artifact_dir/bin" || { echo 'Refusing to overwrite existing artifacts' >&2; exit 2; }
mkdir -p "$artifact_dir"
exec 9>/home/dev/.ax-rbm-heavy.lock
flock 9
cd "$job/brewfs"
export PATH=/root/.cargo/bin:$PATH
export CARGO_TARGET_DIR="$job/target" CARGO_INCREMENTAL=0
date -u +%FT%TZ > "$artifact_dir/build-started"
set +e
cargo --config profile.release.package.brewfs.opt-level=0 \
  --config profile.release.package.brewfs.debug=0 build --offline --locked --release -j 3 \
  --features workspace-overlay,observability,rustfs-ec-block-size-hint \
  --bin brewfs --bin packed_v3_snapshot_fixture > "$artifact_dir/build.log" 2>&1
rc=$?
printf '%s\n' "$rc" > "$artifact_dir/build.exit"
set -e
date -u +%FT%TZ > "$artifact_dir/build-ended"
if ((rc)); then exit "$rc"; fi
mkdir "$artifact_dir/bin"
for name in brewfs packed_v3_snapshot_fixture; do
  # Separate derived artifacts: never strip the shared target or previous binaries.
  objcopy --strip-debug "$job/target/release/$name" "$artifact_dir/bin/$name"
  chmod 755 "$artifact_dir/bin/$name"
done
sha256sum "$artifact_dir/bin/"* > "$artifact_dir/artifacts.sha256"
