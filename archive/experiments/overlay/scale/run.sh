#!/usr/bin/env bash
set -euo pipefail
test "$#" = 3 || { echo 'Usage: bash run.sh /baseline-artifacts /candidate-artifacts /new-output' >&2; exit 2; }
baseline=$(realpath "$1")
candidate=$(realpath "$2")
output=$(realpath -m "$3")
here=$(cd -- "$(dirname -- "$0")" && pwd)
test ! -e "$output"
exec 9>/run/lock/ax-overlay-open-diagnostic.lock
flock -n 9
mkdir -p "$output"
for group in small large16 large64; do
  fixture=standard; payload=$((8 << 20)); rows=$((64 << 20)); passes=256; pairs=6
  if test "$group" != small; then
    fixture=many10000; payload=$((64 << 20)); passes=2; pairs=3
  fi
  if test "$group" = large16; then rows=$((16 << 20)); fi
  python3 "$here/run_scale.py" \
    --binary "$baseline/brewfs" --candidate-binary "$candidate/brewfs" \
    --source-patch "$baseline/source.patch" --candidate-source-patch "$candidate/source.patch" \
    --producer "$baseline/packed_snapshot_fixture" \
    --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725 \
    --workspace-format workspace --build-profile optimized-release --comparison overlay-total \
    --object-meter client --cache-state on --metadata-sync remote --pairs "$pairs" --passes "$passes" \
    --total-fixture "$fixture" --total-temperatures warm \
    --total-row-cache-bytes "$rows" --total-payload-cache-bytes "$payload" \
    --out "$output/$group" >"$output/$group.log" 2>&1
  python3 "$here/summarize.py" "$output/$group" >"$output/$group-summary.json"
done
