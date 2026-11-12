#!/bin/bash
# Retain the original failures and retry only the four trace routes after repair.
set -euo pipefail
umask 077
root=${1:?Usage: retry-rbm-trace-check.sh RUN_ROOT}
test "$(id -u)" = 0 && test "$(hostname)" = rbm-UrRupheQ
test "$(realpath -e "$root")" = "$root"
case "$root" in /home/dev/ax-script-check-*) ;; *) exit 64 ;; esac
test ! -e "$root/results-retry"
cd "$root/repo"
while systemctl is-active --quiet ax-script-check-20261112-01.service; do sleep 2; done
exec /srv/ax-data/workspace-adapter-20261111/venv/bin/python -m bench.experiment_smoke_matrix \
  --out "$root/results-retry" \
  --only C2-replay-brewfs-basic --only C2-replay-brewfs-full \
  --only C2-replay-juicefs --only C2-replay-dynamic-off \
  --brewfs /var/tmp/ax-mode-suite-20261112/bin/brewfs \
  --producer /var/tmp/ax-mode-suite-20261112/bin/packed_v3_snapshot_fixture \
  --smallfiles /srv/ax-bin/smallfiles --juicefs /usr/local/bin/juicefs \
  --brewfs-revision ca07159f3da4b670e4aeb878faef928e69bc1634 \
  --rustfs-image sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806 \
  --trace /var/tmp/ax-recorded-smoke-20261112-02 \
  --replay /var/tmp/ax-full-replay-20261112/release/full-fuse-replay \
  --full-trace --trace-timeout 60 --tiny
