#!/usr/bin/env bash
# Two prebuilt test executables, same local Redis, alternating before/after order.
set -euo pipefail
before=$(realpath "${1:?usage: run_redis_pairs.sh BEFORE_TEST AFTER_TEST NEW_OUTPUT}")
after=$(realpath "${2:?}")
out=${3:?}
[[ -x $before && -x $after && $out == /* && ! -e $out ]] || exit 2
mkdir -p "$out"
exec > >(tee "$out/run.log") 2>&1
container=
cleanup() {
    if [[ -n $container ]]; then docker rm -f "$container" > "$out/cleanup.txt"; fi
}
trap cleanup EXIT
sha256sum "$before" "$after" > "$out/test-binaries.sha256"
docker image inspect redis:7.2-alpine --format '{{.Id}}' > "$out/redis-image.txt"
container=$(docker run --pull=never --detach --name "brewfs-metadata-pairs-$$" \
    --publish 127.0.0.1::6379 redis:7.2-alpine redis-server --save '' --appendonly no)
printf '%s\n' "$container" > "$out/container.txt"
port=$(docker port "$container" 6379/tcp)
export BREWFS_LOCAL_SYNC_REDIS_URL="redis://$port/"
export BREWFS_METADATA_PROFILE=1
for attempt in $(seq 1 50); do
    if docker exec "$container" redis-cli ping | rg -q PONG; then break; fi
    sleep 0.1
done
for pair in 1 2 3; do
    order='before after'
    if [[ $pair == 2 ]]; then order='after before'; fi
    for variant in $order; do
        executable=$before
        if [[ $variant == after ]]; then executable=$after; fi
        "$executable" local_metadata_real_redis_pairs --ignored --nocapture --test-threads=1 \
            > "$out/$pair-$variant.log" 2>&1
        python3 - "$out/$pair-$variant.log" "$out/$pair-$variant.json" <<'PY'
import json, pathlib, sys
rows = [json.loads(line.split('LOCAL_SYNC_RESULT ', 1)[1])
        for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if 'LOCAL_SYNC_RESULT ' in line]
assert len(rows) == 9 and all(row['final_value'] == 99 for row in rows)
pathlib.Path(sys.argv[2]).write_text(json.dumps(rows, indent=2) + '\n')
PY
    done
done
printf '%s\n' passed > "$out/status.txt"
