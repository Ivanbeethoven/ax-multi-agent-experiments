#!/usr/bin/env bash
# Reuse A355 only. All services are local Redis/RustFS containers owned by
# run_local.py; no cloud calls, SQLite, shared Redis or global cache dropping.
set -euo pipefail
test "$#" = 3 || { echo 'Usage: bash run.sh /baseline-artifacts /candidate-artifacts /new-output' >&2; exit 2; }
baseline=$(realpath "$1"); candidate=$(realpath "$2"); output=$(realpath -m "$3")
here=$(cd -- "$(dirname -- "$0")" && pwd)
test ! -e "$output"
exec 9>/run/lock/ax-overlay-open-diagnostic.lock
flock -n 9
if pgrep -x rustc >/dev/null; then echo 'Build still running; do not measure' >&2; exit 1; fi
mkdir -p "$output"
trap 'echo $? > "$output/exit-code.txt"' EXIT
{ date -Is; uname -a; uptime; df -B1 "$output"; docker ps --format '{{.Names}} {{.Status}}'; } >"$output/host.txt"
sha256sum "$baseline/brewfs" "$baseline/source.patch" "$candidate/brewfs" "$candidate/source.patch" "$candidate/packed_snapshot_fixture" >"$output/SHA256SUMS"
common=(--producer "$candidate/packed_snapshot_fixture"
  --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725
  --workspace-format workspace --build-profile optimized-release
  --cache-state on --metadata-sync remote --pairs 3)
for kind in replace mixed; do
  python3 "$here/../overlay-fixes-a355-20261114/run_correctness.py" --check "$kind" \
    --binary "$candidate/brewfs" --source-patch "$candidate/source.patch" \
    "${common[@]}" --comparison empty-cache --object-meter proxy \
    --rust-log warn,brewfs::fuse=debug,brewfs::vfs::fs=debug \
    --metadata-edits 8 --passes 2 --out "$output/$kind" >"$output/$kind.log" 2>&1
done
paired=(--binary "$baseline/brewfs" --candidate-binary "$candidate/brewfs"
  --source-patch "$baseline/source.patch" --candidate-source-patch "$candidate/source.patch"
  --comparison overlay-total --object-meter client --total-temperatures warm --total-lower-build candidate
  --total-row-cache-bytes "$((64 << 20))")
python3 "$here/../overlay-open-a355-20261113/run_fio_guard.py" --prepared-file \
  "${common[@]}" "${paired[@]}" --passes 2 --total-fixture standard \
  --total-payload-cache-bytes "$((8 << 20))" --out "$output/fio-paired" >"$output/fio-paired.log" 2>&1
for group in small large64; do
  fixture=standard; payload=$((8 << 20)); passes=256
  if test "$group" = large64; then fixture=many10000; payload=$((64 << 20)); passes=2; fi
  python3 "$here/../overlay-scale-a355-20261114/run_scale.py" \
    "${common[@]}" "${paired[@]}" --passes "$passes" --total-fixture "$fixture" \
    --total-payload-cache-bytes "$payload" --out "$output/$group" >"$output/$group.log" 2>&1
  python3 "$here/../overlay-scale-a355-20261114/summarize.py" "$output/$group" >"$output/$group-summary.json"
done
python3 "$here/../overlay-fixes-a355-20261114/report.py" "$output" >"$output/comparison.md"
# The two following batches are diagnostics, excluded from accepted timings.
for diagnostic in stages existing; do
  python3 "$here/profile_$diagnostic.py" --binary "$candidate/brewfs" \
    --source-patch "$candidate/source.patch" "${common[@]}" \
    --comparison overlay-total --object-meter client --passes 8 --total-fixture standard \
    --total-temperatures warm --total-row-cache-bytes "$((64 << 20))" \
    --total-payload-cache-bytes "$((8 << 20))" \
    --out "$output/profile-$diagnostic" >"$output/profile-$diagnostic.log" 2>&1
done
