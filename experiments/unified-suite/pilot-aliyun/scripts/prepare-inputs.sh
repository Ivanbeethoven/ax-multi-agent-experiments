#!/usr/bin/env bash
# Local source preparation only; never writes to a tested filesystem or cloud API.
set -euo pipefail
[[ $# == 2 ]] || { echo 'usage: prepare-inputs.sh c3|c4|trace ABS_BUILD_ROOT' >&2; exit 2; }
phase=$1
build_root=$2
[[ "$build_root" == /* && "$build_root" != / ]] || exit 2
repo_root=${AX_EXPERIMENT_REPO:-$(cd -- "$(dirname -- "$0")/../../../.." && pwd)}
if [[ ${AX_INPUT_SNAPSHOT:-0} != 1 ]]; then
    mkdir -p "$build_root/scripts"
    snapshot="$build_root/scripts/prepare-inputs-$(sha256sum "$0" | cut -d' ' -f1).sh"
    [[ -e "$snapshot" ]] || cp -- "$0" "$snapshot"
    export AX_INPUT_SNAPSHOT=1 AX_EXPERIMENT_REPO="$repo_root"
    exec bash "$snapshot" "$phase" "$build_root"
fi
mkdir -p "$build_root/inputs" "$build_root/receipts"
cd "$repo_root"
case "$phase" in
c3)
    "$build_root/bin/smallfiles" prepare --root "$build_root/inputs/million-source" --output "$build_root/receipts/c3-source-prepare" --files 1000000
    "$build_root/bin/smallfiles" verify-base --root "$build_root/inputs/million-source" --output "$build_root/receipts/c3-source-verify" --files 1000000
    ;;
c4)
    python3 bench/fs_step.py fixture --root "$build_root/inputs/fork-source" --files 1000000 --file-bytes 4096 --output "$build_root/receipts/c4-fixture.json"
    python3 bench/fs_step.py snapshot --root "$build_root/inputs/fork-source" --output "$build_root/receipts/c4-fixture-tree.json"
    ;;
trace)
    python3 scenarios/k8s-containers/scripts/download_archives.py --out "$build_root/inputs/archives" --cases pytest-full-02
    mkdir "$build_root/inputs/cases"
    tar --no-same-owner --same-permissions -xzf "$build_root/inputs/archives/pytest-full-02.tar.gz" -C "$build_root/inputs/cases"
    ;;
*) exit 2 ;;
esac
