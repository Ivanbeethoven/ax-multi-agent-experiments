#!/usr/bin/env bash
# Local source preparation only; never writes to a tested filesystem or cloud API.
set -euo pipefail
[[ $# == 2 || $# == 3 ]] || { echo 'usage: prepare-inputs.sh c3|c4|trace ABS_BUILD_ROOT [100000|1000000]' >&2; exit 2; }
phase=$1
build_root=$2
files=${3:-1000000}
[[ "$files" == 100000 || "$files" == 1000000 ]] || exit 2
[[ "$phase" != trace || "$files" == 1000000 ]] || exit 2
[[ "$build_root" == /* && "$build_root" != / ]] || exit 2
repo_root=${AX_EXPERIMENT_REPO:-$(cd -- "$(dirname -- "$0")/../../../.." && pwd)}
if [[ ${AX_INPUT_SNAPSHOT:-0} != 1 ]]; then
    mkdir -p "$build_root/scripts"
    snapshot="$build_root/scripts/prepare-inputs-$(sha256sum "$0" | cut -d' ' -f1).sh"
    if [[ ! -e "$snapshot" ]]; then
        snapshot_tmp=$(mktemp "$build_root/scripts/.prepare-inputs.XXXXXX")
        if ! cp -- "$0" "$snapshot_tmp"; then
            rm -- "$snapshot_tmp"
            exit 1
        fi
        # Publish only a complete file. Another preparation may publish first.
        if ! ln -- "$snapshot_tmp" "$snapshot" 2>/dev/null && ! cmp -s -- "$snapshot_tmp" "$snapshot"; then
            rm -- "$snapshot_tmp"
            echo 'input preparation snapshot differs' >&2
            exit 1
        fi
        rm -- "$snapshot_tmp"
    fi
    cmp -s -- "$0" "$snapshot" || { echo 'input preparation snapshot differs' >&2; exit 1; }
    export AX_INPUT_SNAPSHOT=1 AX_EXPERIMENT_REPO="$repo_root"
    exec bash "$snapshot" "$phase" "$build_root" "$files"
fi
input_root="$build_root/inputs"
receipt_root="$build_root/receipts"
c3_source="$input_root/million-source"
if [[ "$files" == 100000 ]]; then
    input_root="$input_root/pilot-100k"
    receipt_root="$receipt_root/pilot-100k"
    c3_source="$input_root/smallfiles-source"
fi
mkdir -p "$input_root" "$receipt_root"
cd "$repo_root"
case "$phase" in
c3)
    "$build_root/bin/smallfiles" prepare --root "$c3_source" --output "$receipt_root/c3-source-prepare" --files "$files"
    "$build_root/bin/smallfiles" verify-base --root "$c3_source" --output "$receipt_root/c3-source-verify" --files "$files"
    ;;
c4)
    python3 bench/fs_step.py fixture --root "$input_root/fork-source" --files "$files" --file-bytes 4096 --output "$receipt_root/c4-fixture.json"
    python3 bench/fs_step.py snapshot --root "$input_root/fork-source" --output "$receipt_root/c4-fixture-tree.json"
    ;;
trace)
    python3 scenarios/k8s-containers/scripts/download_archives.py --out "$build_root/inputs/archives" --cases pytest-full-02
    mkdir "$build_root/inputs/cases"
    tar --no-same-owner --same-permissions -xzf "$build_root/inputs/archives/pytest-full-02.tar.gz" -C "$build_root/inputs/cases"
    ;;
*) exit 2 ;;
esac
