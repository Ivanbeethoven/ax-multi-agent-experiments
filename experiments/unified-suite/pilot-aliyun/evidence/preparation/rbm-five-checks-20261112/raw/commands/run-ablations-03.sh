#!/usr/bin/env bash
set -uo pipefail
umask 077
job=/home/dev/ax-five-fixes-20261112
cd "$job/ax" || exit 2
mkdir "$job/ablation-results-03" || exit 2
status=0
for spec in C2-replay:packed-off C3-smallfiles:packed-off C2-replay:all-off; do
  experiment=${spec%:*}; mode=${spec#*:}
  /srv/ax-data/workspace-adapter-20261111/venv/bin/python -m bench.experiment_smoke \
    --experiment "$experiment" --mode "$mode" --tiny \
    --brewfs "$job/candidate-03/bin/brewfs" --producer "$job/candidate-03/bin/packed_v3_snapshot_fixture" \
    --brewfs-provenance "$job/candidate-03/build-provenance.json" \
    --smallfiles /srv/ax-bin/smallfiles --juicefs /usr/local/bin/juicefs \
    --brewfs-revision ca07159f3da4b670e4aeb878faef928e69bc1634 \
    --rustfs-image sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806 \
    --full-trace --trace /var/tmp/ax-recorded-smoke-20261112-02 \
    --replay /var/tmp/ax-full-replay-20261112/release/full-fuse-replay \
    --out "$job/ablation-results-03/$experiment-$mode" \
    > "$job/ablation-results-03/$experiment-$mode.log" 2>&1
  rc=$?
  printf '%s %s\n' "$spec" "$rc" >> "$job/ablation-results-03/exits.txt"
  if ((rc)); then status=2; fi
done
exit "$status"
