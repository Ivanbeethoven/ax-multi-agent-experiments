#!/usr/bin/env bash
set -euo pipefail
root=/mnt/c/Users/HXY/.cache/brewfs-hot-lower-20261112
build="$root/logs/merge-smoke-build-02"
harness="$root/experiments/experiments/hot-lower-20261112"
out="$root/logs/a355-bundle.tar.gz"
test ! -e "$out"
(
  cd "$build"
  sha256sum brewfs packed_snapshot_fixture source-commit.txt source.patch BUILD-SCOPE.txt
  cd "$harness"
  sha256sum run_local.py verify_results.py
  cd "$root/logs"
  sha256sum a355-images.tar.gz
) >"$root/logs/TRANSFER-SHA256SUMS"
tar -czf "$out" -C "$build" brewfs packed_snapshot_fixture source-commit.txt source.patch BUILD-SCOPE.txt \
  -C "$harness" run_local.py verify_results.py \
  -C "$root/logs" a355-images.tar.gz TRANSFER-SHA256SUMS \
  -C "$root" a355-smoke.sh
sha256sum "$out" >"$out.sha256"
