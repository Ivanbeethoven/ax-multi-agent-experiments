#!/usr/bin/env bash
set -euo pipefail
cd /srv/ax-c4-observer-scratch/o/c4-a-flat-on1
out=/srv/ax-receipts/fold/fold-real-03-preparation-v3/independent-readback.txt
set -o noclobber
exec > "$out.tmp" 2>&1
date -u --iso-8601=seconds
find . -mindepth 1 -maxdepth 1 -printf '%y %f\n' | sort
stat -c '%s %n' samples.ndjson
sha256sum samples.ndjson finished.json /srv/ax-receipts/fold/fold-real-03.json
date -u --iso-8601=seconds
sync "$out.tmp"
mv -nT "$out.tmp" "$out"
