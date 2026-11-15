#!/usr/bin/env bash
set -euo pipefail
# No service outside run_local.py's owner-labelled containers is modified.
test "$#" = 2 || { echo 'Usage: bash run-baseline.sh /absolute/baseline-artifacts /absolute/new-output' >&2; exit 2; }
artifacts=$(realpath "$1")
output=$(realpath -m "$2")
here=$(cd -- "$(dirname -- "$0")" && pwd)
test ! -e "$output"
test -c /dev/fuse
test "$(id -u)" = 0
# Serialize this diagnostic's own invocations. Check other host users before
# running; this lock does not coordinate unrelated experiments.
exec 9>/run/lock/ax-overlay-open-diagnostic.lock
flock -n 9
test "$(sha256sum "$artifacts/brewfs" | cut -d' ' -f1)" = a797321fa0df7a002e2ad6350620e5f4f5397f9ca16bd3cae86812d93e42aee6
test "$(sha256sum "$artifacts/source.patch" | cut -d' ' -f1)" = 647970c1af06eeafd125dd0af608c21561b6e3644513437198164b836ab36484
mkdir -p "$output"
{
  date -Is
  hostname
  uptime
  free -b
  df -B1 /srv /tmp
  docker ps --format '{{.Names}} {{.Status}}'
} >"$output/host-before.txt"
cd "$here"
python3 -m unittest test_detail -v >"$output/harness-tests.log" 2>&1
python3 run_detail.py --binary "$artifacts/brewfs" --producer "$artifacts/packed_snapshot_fixture" \
  --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725 --source-patch "$artifacts/source.patch" \
  --workspace-format workspace --build-profile optimized-release \
  --comparison overlay-total --object-meter client --cache-state on --metadata-sync remote \
  --pairs 6 --passes 256 --out "$output/run" >"$output/run.log" 2>&1
python3 ../overlay-total-a355-20261113/summarize.py "$output/run" >"$output/parity-summary.json"
python3 summarize_detail.py "$output/run" >"$output/detail-summary.json"
