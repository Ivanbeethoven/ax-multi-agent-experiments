#!/usr/bin/env bash
set -uo pipefail
umask 077
job=/home/dev/ax-five-fixes-20261112
cd "$job/ax" || exit 2
mkdir "$job/move-results-fs-04" || exit 2
status=0
for product in brewfs-full juicefs; do
  /srv/ax-data/workspace-adapter-20261111/venv/bin/python -m bench.experiment_smoke \
    --experiment C4-handoff --mode "$product" --placement move --tiny \
    --brewfs "$job/move-tools/brewfs" \
    --producer /var/tmp/ax-mode-suite-20261112/bin/packed_v3_snapshot_fixture \
    --smallfiles /srv/ax-bin/smallfiles --juicefs "$job/move-tools/juicefs" \
    --brewfs-revision ca07159f3da4b670e4aeb878faef928e69bc1634 \
    --rustfs-image sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806 \
    --checkpoint /home/dev/ax-session-reopen-input-20261112/verified/checkpoint.json \
    --prepare-timeout 180 \
    --ssh-config "$job/ssh-config" --target-host ax-five-target \
    --target-check-timeout 900 --target-repo /root/ax-five-smoke-20261112/repo \
    --target-tools /root/ax-five-smoke-20261112/move-tools \
    --out "$job/move-results-fs-04/C4-handoff-$product-move" \
    > "$job/move-results-fs-04/$product.log" 2>&1
  rc=$?
  printf '%s %s\n' "$product" "$rc" >> "$job/move-results-fs-04/exits.txt"
  if ((rc)); then status=2; fi
done
exit "$status"
