#!/usr/bin/env bash
set -euo pipefail
root=/mnt/c/Users/HXY/.cache/brewfs-hot-lower-20261112
out="$root/logs/merge-redis-binary-03"
test ! -e "$out"
mkdir "$out"
exec >"$out/run.log" 2>&1
container=
cleanup() {
    rc=$?
    if [[ -n $container ]]; then docker rm -f "$container" >"$out/cleanup.txt"; fi
    printf '%s\n' "$rc" >"$out/exit.txt"
}
trap cleanup EXIT
binary=/home/hxy/.cache/brewfs-hot-lower-20261112-checks/debug/deps/brewfs-3b98b2e55bd86571
test -x "$binary"
sha256sum "$binary" >"$out/test-binary.sha256"
printf '%s\n' '9bb380d1832859962b52e960f58cfe37a6c099e2' >"$out/tested-source-tree.txt"
container=$(docker run --pull=never --detach --name "brewfs-merge-redis-$$" --publish 127.0.0.1::6379 redis:7.2-alpine redis-server --save '' --appendonly no)
printf '%s\n' "$container" >"$out/container.txt"
docker image inspect redis:7.2-alpine --format '{{.Id}}' >"$out/redis-image.txt"
port=$(docker port "$container" 6379/tcp)
export BREWFS_LOCAL_SYNC_REDIS_URL="redis://$port/"
for attempt in $(seq 1 50); do
    if docker exec "$container" redis-cli ping | rg -q PONG; then break; fi
    sleep 0.1
done
set -x
"$binary" local_metadata --nocapture --test-threads=2
"$binary" local_metadata_real_redis_pairs --ignored --nocapture --test-threads=1 | tee "$out/redis-pairs.log"
"$binary" local_metadata_untimed_redis_pairs --ignored --nocapture --test-threads=1 | tee "$out/untimed-pairs.log"
set +x
python3 - "$out" <<'PY'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
for name, prefix, expected in [('redis-pairs', 'LOCAL_SYNC_RESULT ', 9), ('untimed-pairs', 'UNTIMED_READ_RESULT ', 6)]:
    rows=[json.loads(line.split(prefix,1)[1]) for line in (p/(name+'.log')).read_text().splitlines() if prefix in line]
    assert len(rows)==expected
    (p/(name+'.json')).write_text(json.dumps(rows,indent=2)+'\n')
PY
