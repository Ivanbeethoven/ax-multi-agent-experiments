#!/usr/bin/env bash
# Mixed I/O and upper-mutation guard, separate from read latency measurements.
set -euo pipefail
test "$#" = 3 || { echo 'Usage: bash run-regression.sh /baseline-artifacts /candidate-artifacts /new-output' >&2; exit 2; }
baseline=$(realpath "$1")
candidate=$(realpath "$2")
output=$(realpath -m "$3")
here=$(cd -- "$(dirname -- "$0")" && pwd)
test ! -e "$output"
exec 9>/run/lock/ax-overlay-open-diagnostic.lock
flock -n 9
mkdir -p "$output"
for pair in 1 2; do
  arms='baseline candidate'
  if (( pair % 2 == 0 )); then arms='candidate baseline'; fi
  for arm in $arms; do
    artifacts=$baseline
    if test "$arm" = candidate; then artifacts=$candidate; fi
    name="pair${pair}-${arm}"
    python3 "$here/run_fio_guard.py" \
      --prepared-file \
      --binary "$artifacts/brewfs" --producer "$baseline/packed_snapshot_fixture" \
      --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725 --source-patch "$artifacts/source.patch" \
      --workspace-format workspace --build-profile optimized-release \
      --comparison empty-cache --object-meter proxy --cache-state on \
      --metadata-sync remote --metadata-edits 8 --pairs 3 --passes 32 \
      --out "$output/$name" >"$output/$name.log" 2>&1
  done
done
