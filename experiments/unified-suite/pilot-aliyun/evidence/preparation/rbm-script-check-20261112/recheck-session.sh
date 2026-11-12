#!/bin/bash
set -euo pipefail
umask 077
root=/home/dev/ax-script-check-20261112-01
cd "$root/repo"
tar -xzf "$root/synthetic-140.tar.gz"
common=(--brewfs /var/tmp/ax-mode-suite-20261112/bin/brewfs
 --producer /var/tmp/ax-mode-suite-20261112/bin/packed_v3_snapshot_fixture
 --smallfiles /srv/ax-bin/smallfiles --juicefs /usr/local/bin/juicefs
 --brewfs-revision ca07159f3da4b670e4aeb878faef928e69bc1634
 --rustfs-image sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806
 --tiny)
py=/srv/ax-data/workspace-adapter-20261111/venv/bin/python
status=0
"$py" -m bench.experiment_smoke_matrix --out "$root/results-session-retry" \
 --only C4-handoff-juicefs-stay "${common[@]}" --prepare-timeout 180 --trace-timeout 120 \
 --checkpoint /home/dev/ax-session-reopen-input-20261112/verified/checkpoint.json \
 --opencode /home/dev/ax-session-reopen-input-20261112/opencode || status=2
"$py" -m bench.experiment_smoke_matrix --out "$root/results-default-trace" \
 --only C2-replay-brewfs-basic "${common[@]}" \
 --trace "$root/repo/experiments/unified-suite/pilot-aliyun/fixtures/synthetic-140" \
 --replay /var/tmp/ax-full-replay-20261112/release/full-fuse-replay || status=2
exit "$status"
