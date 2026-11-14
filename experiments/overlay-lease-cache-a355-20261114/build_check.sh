#!/usr/bin/env bash
# Existing A355 only. No SQLite tests, service allocation, or network install.
set -euo pipefail
test "$#" = 1
r=$(realpath "$1")
cd "$r/candidate"
export PATH=/srv/ax-overlay-tax-20261113/toolchain/bin:$PATH
export CARGO_HOME=/srv/ax-overlay-tax-20261113/cargo CARGO_NET_OFFLINE=true
export CARGO_INCREMENTAL=0 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0
export CARGO_PROFILE_RELEASE_DEBUG=0 CARGO_BUILD_JOBS=12
features=fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability
trap 'echo $? > "$r/build-check.exit"' EXIT
cargo fmt --all --check
mkdir "$r/artifacts"
rustc -Vv >"$r/artifacts/rustc.txt"
python3 - "$r" <<'PY'
import hashlib, json, pathlib, sys
r = pathlib.Path(sys.argv[1])
old = json.loads((r / 'source-manifest.json').read_text())
new = {name: hashlib.sha256((r / 'candidate' / name).read_bytes()).hexdigest() for name in old}
assert new == old, 'Source differs from the staged manifest'
(r / 'artifacts/source-manifest.json').write_text(json.dumps(new, indent=2))
PY
cp "$r/source.patch" "$r/provenance.json" "$r/artifacts/"
(
  export CARGO_TARGET_DIR=/srv/ax-overlay-tax-20261113/release-target
  trap 'echo $? > "$r/build.exit"' EXIT
  cargo --config profile.release.package.brewfs.opt-level=3 build --offline --locked --release \
    --no-default-features --features "$features" --bin brewfs >"$r/build.log" 2>&1
  cp "$CARGO_TARGET_DIR/release/brewfs" "$r/artifacts/brewfs"
  cp /srv/ax-overlay-open-retry-20261114/artifacts/packed_snapshot_fixture "$r/artifacts/packed_snapshot_fixture"
  sha256sum "$r/artifacts/brewfs" "$r/artifacts/packed_snapshot_fixture" >"$r/artifacts/binary-sha256.txt"
) & build=$!
(
  export CARGO_TARGET_DIR=/srv/ax-overlay-tax-20261113/target
  trap 'echo $? > "$r/check.exit"' EXIT
  cargo test --offline --locked --lib --no-default-features --features "$features" \
    lease_retention -- --nocapture >"$r/lease-retention.log" 2>&1
  cargo test --offline --locked --lib --no-default-features --features "$features" \
    packed_rewind_reuses -- --nocapture >"$r/rewind.log" 2>&1
  bash tools/perf/check_overlay_hot.sh "$r/checks"
) & checks=$!
status=0
wait "$build" || status=$?
wait "$checks" || status=$?
exit "$status"
