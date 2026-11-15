#!/usr/bin/env bash
# Linux/WSL only. Uses existing local Docker images; creates no cloud resources.
set -euo pipefail
source_dir=$(realpath "${1:?usage: run.sh BREWFS_CHECKOUT BUILD_DIRECTORY NEW_OUTPUT_DIRECTORY}")
build=$(realpath "${2:?build directory required}")
out=$(realpath -m "${3:?new output directory required}")
here=$(cd -- "$(dirname -- "$0")" && pwd)
[[ $EUID == 0 ]] || { echo 'Run the FUSE stage as root' >&2; exit 2; }
[[ ! -e $out ]] || { echo "Output already exists: $out" >&2; exit 2; }
mkdir -p -- "$out"
unset BREWFS_METADATA_SYNC BREWFS_METADATA_JOURNAL_DIR
unset BREWFS_METADATA_FLUSH_MS BREWFS_METADATA_MAX_OPERATIONS BREWFS_METADATA_MAX_BYTES
export BREWFS_METADATA_FLUSH_MS=10
python=${PYTHON:-python3}
source_patch=${BREWFS_SOURCE_PATCH:-$build/source.patch}
cd -- "$source_dir"
for mode in remote local-journal local-buffered; do
    "$python" "$here/../hot-lower-20261112/run_local.py" \
        --binary "$build/brewfs" --producer "$build/packed_v3_snapshot_fixture" \
        --source-commit "$(cat "$build/source-commit.txt")" --source-patch "$source_patch" \
        --metadata-sync "$mode" --metadata-edits 100 --pairs 3 --passes 4 --out "$out/fuse-$mode"
done
BREWFS_METADATA_FLUSH_MS=100 "$python" "$here/../hot-lower-20261112/run_local.py" \
    --binary "$build/brewfs" --producer "$build/packed_v3_snapshot_fixture" \
    --source-commit "$(cat "$build/source-commit.txt")" --source-patch "$source_patch" \
    --metadata-sync local-buffered --metadata-edits 100 --pairs 3 --passes 4 \
    --out "$out/fuse-local-buffered-100ms"
"$python" "$here/../hot-lower-20261112/run_compose_guard.py" \
    --repository "$source_dir" --binary "$build/brewfs" --out "$out/write-guard-0" \
    --direct 0 --tools fio-seqwrite fio-randwrite fio-randrw
for tool in fio-seqwrite fio-randwrite fio-randrw; do
    "$python" "$here/../hot-lower-20261112/run_compose_guard.py" \
        --repository "$source_dir" --binary "$build/brewfs" --out "$out/write-guard-1-$tool" \
        --direct 1 --tools "$tool"
done
"$python" "$here/../hot-lower-20261112/verify_results.py" \
    "$out/fuse-remote" "$out/fuse-local-journal" "$out/fuse-local-buffered" \
    "$out/fuse-local-buffered-100ms" "$out/write-guard-0" \
    "$out/write-guard-1-fio-seqwrite" "$out/write-guard-1-fio-randwrite" \
    "$out/write-guard-1-fio-randrw" > "$out/verification.json"
printf '%s\n' passed > "$out/status.txt"
