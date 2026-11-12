#!/bin/bash
# Reuse the pinned rbm tools. Fresh output, private services, no cloud resources.
set -euo pipefail
umask 077
root=${1:?Usage: run-rbm-script-check.sh NEW_RUN_ROOT}
test "$(id -u)" = 0
test "$(hostname)" = rbm-UrRupheQ
test "$(realpath -e "$root")" = "$root"
case "$root" in /home/dev/ax-script-check-*) ;; *) exit 64 ;; esac
test -d "$root/repo" && test ! -e "$root/results"
cd "$root/repo"
py=/srv/ax-data/workspace-adapter-20261111/venv/bin/python
"$py" - "$root" <<'PY'
import hashlib,json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1])
source='ca07159f3da4b670e4aeb878faef928e69bc1634'
rustfs='f4ed202508f2153afb7956bae6f7799b60eccd00'
image='sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806'
base=Path('/var/tmp/ax-mode-suite-20261112/bin')
build=json.loads((base.parent/'build.json').read_text())
assert build['source_commit']==source
pins={}
for name in ('brewfs','packed_v3_snapshot_fixture'):
    with (base/name).open('rb') as stream:
        pins[name]=hashlib.file_digest(stream,'sha256').hexdigest()
    assert pins[name]==build['binaries'][name]
info=json.loads(subprocess.check_output(['docker','image','inspect',image]))[0]
assert info['Config']['Labels']['org.opencontainers.image.revision']==rustfs
input_path=Path('/var/tmp/ax-recorded-smoke-20261112-02')
metrics=json.loads((input_path/'metrics.json').read_text())
assert metrics['callbacks']==367
c2='/home/dev/ax-brewfs-c2-namespace-20261112.F2B3t2Iv/seqfix-01.prIedKe5/brewfs-c2-namespace-seqfix-01-linux-x86_64'
c4='/home/dev/ax-brewfs-c4-build-20261112.GWrxnDJ8/nlink-02/brewfs-linux-x86_64'
evidence=root/'repo/experiments/unified-suite/pilot-aliyun/evidence/preparation'
overrides={
    'C2-runtime-'+mode:['--brewfs',c2,'--brewfs-provenance',str(evidence/'c2-rbm-namespace-seqfix-01/raw/summary.json')]
    for mode in ('brewfs-full','overlay-off')}
overrides['C4-fork-brewfs-full']=['--brewfs',c4,'--brewfs-provenance',str(evidence/'c4-local-packed-fork-candidate-01/root-nlink-build-receipt.json')]
(root/'case-overrides.json').write_text(json.dumps(overrides,indent=2)+'\n')
inventory=dict(host='rbm-UrRupheQ',cloud_instances_started=0,run_root=str(root),
    source_commit=source,rustfs_revision=rustfs,rustfs_image=image,binaries=pins,
    input_path=str(input_path),input_callbacks=367,c3_files=8,c4_fork_files=2,
    scope='single-host script checks, not performance or cross-host qualification',
    preserved_containers=subprocess.check_output(['docker','ps','-q','--no-trunc']).decode().split())
(root/'inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
PY
exec "$py" -m bench.experiment_smoke_matrix \
  --out "$root/results" --case-overrides "$root/case-overrides.json" \
  --brewfs /var/tmp/ax-mode-suite-20261112/bin/brewfs \
  --producer /var/tmp/ax-mode-suite-20261112/bin/packed_v3_snapshot_fixture \
  --smallfiles /srv/ax-bin/smallfiles --juicefs /usr/local/bin/juicefs \
  --brewfs-revision ca07159f3da4b670e4aeb878faef928e69bc1634 \
  --rustfs-image sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806 \
  --rustfs-revision f4ed202508f2153afb7956bae6f7799b60eccd00 \
  --trace /var/tmp/ax-recorded-smoke-20261112-02 \
  --replay /var/tmp/ax-full-replay-20261112/release/full-fuse-replay \
  --full-trace --trace-timeout 60 --tiny \
  --checkpoint /home/dev/ax-session-reopen-input-20261112/verified/checkpoint.json \
  --opencode /home/dev/ax-session-reopen-input-20261112/opencode
