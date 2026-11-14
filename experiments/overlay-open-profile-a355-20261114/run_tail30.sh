#!/usr/bin/env bash
# Resolve the short guard's tail-latency concern with more actual operations.
set -euo pipefail
test "$#" = 3 || { echo 'Usage: bash run_tail30.sh /baseline-artifacts /candidate-artifacts /new-output' >&2; exit 2; }
baseline=$(realpath "$1"); candidate=$(realpath "$2"); output=$(realpath -m "$3")
here=$(cd -- "$(dirname -- "$0")" && pwd)
test ! -e "$output"
exec 9>/run/lock/ax-overlay-open-diagnostic.lock
flock -n 9
if pgrep -x rustc >/dev/null; then echo 'Build still running; do not measure' >&2; exit 1; fi
python3 "$here/../overlay-open-a355-20261113/run_fio_guard.py" \
  --prepared-file --fio-runtime 30 --producer "$candidate/packed_snapshot_fixture" \
  --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725 \
  --workspace-format workspace --build-profile optimized-release \
  --cache-state on --metadata-sync remote --pairs 3 --passes 2 \
  --binary "$baseline/brewfs" --candidate-binary "$candidate/brewfs" \
  --source-patch "$baseline/source.patch" --candidate-source-patch "$candidate/source.patch" \
  --comparison overlay-total --object-meter client --total-temperatures warm \
  --total-lower-build candidate --total-fixture standard \
  --total-row-cache-bytes "$((64 << 20))" --total-payload-cache-bytes "$((8 << 20))" \
  --out "$output"
