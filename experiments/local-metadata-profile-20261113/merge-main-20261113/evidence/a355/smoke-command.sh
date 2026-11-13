#!/usr/bin/env bash
set -euo pipefail
root=/srv/ax-overlay-merge-20261113-1081395
cd "$root"
test "$(hostname)" = A355
test -f ownership.json
test ! -e smoke-01
exec >smoke-01.log 2>&1
trap 'printf "%s\n" "$?" >smoke-01.exit' EXIT
sha256sum --check TRANSFER-SHA256SUMS
docker load -i a355-images.tar.gz
chmod u+x brewfs packed_snapshot_fixture
mkdir tmp
export TMPDIR="$root/tmp" TOKIO_WORKER_THREADS=4 GIT_OPTIONAL_LOCKS=0
unset BREWFS_METADATA_PROFILE BREWFS_METADATA_MAX_BYTES BREWFS_METADATA_MAX_OPERATIONS BREWFS_METADATA_FLUSH_MS
printf '%s\n' 'Debug correctness smoke; not a performance measurement.' 'TOKIO_WORKER_THREADS=4'
set -x
python3 run_local.py --binary "$root/brewfs" --producer "$root/packed_snapshot_fixture" \
  --source-commit "$(cat source-commit.txt)" --source-patch "$root/source.patch" \
  --workspace-format workspace --build-profile debug --pairs 3 --passes 4 --metadata-edits 16 \
  --metadata-sync local-buffered --cache-state both --out "$root/smoke-01"
python3 verify_results.py "$root/smoke-01" >smoke-01-verification.json
docker ps --all --format '{{.Names}} {{.Status}}' >containers-after.txt
findmnt --json >mounts-after.json
