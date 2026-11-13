#!/usr/bin/env bash
set -euo pipefail
test "$#" = 3 || { echo 'Usage: bash run-compare.sh /baseline-artifacts /candidate-artifacts /new-output' >&2; exit 2; }
baseline=$(realpath "$1")
candidate=$(realpath "$2")
output=$(realpath -m "$3")
here=$(cd -- "$(dirname -- "$0")" && pwd)
test ! -e "$output"
exec 9>/run/lock/ax-overlay-open-diagnostic.lock
flock -n 9
mkdir -p "$output"
cd "$here"
python3 -m unittest test_detail -v >"$output/harness-tests.log" 2>&1
python3 run_detail.py --binary "$baseline/brewfs" --candidate-binary "$candidate/brewfs" \
  --candidate-source-patch "$candidate/source.patch" --producer "$baseline/packed_snapshot_fixture" \
  --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725 --source-patch "$baseline/source.patch" \
  --workspace-format workspace --build-profile optimized-release \
  --comparison overlay-total --object-meter client --cache-state on --metadata-sync remote \
  --pairs 6 --passes 256 --out "$output/run" >"$output/run.log" 2>&1
python3 ../overlay-total-a355-20261113/summarize.py "$output/run" >"$output/parity-summary.json"
python3 summarize_detail.py "$output/run" >"$output/detail-summary.json"
