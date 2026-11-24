#!/usr/bin/env bash
set -euo pipefail
scripts="$(cd "$(dirname "$0")" && pwd -P)"
root="$(realpath -m "${DIAG_ROOT:?Set DIAG_ROOT under /srv/ax-cluster/diagnostics}")"
case "$root" in /srv/ax-cluster/diagnostics/*) ;; *) exit 2;; esac
mkdir -p "$root"
cd "$root"
exec 9>/run/lock/ax-dynamic-fio.lock
flock -n 9
if (($# < 7)); then
    echo 'Usage: run.sh BINARY WORKERS HINT QUANTUM READ_MEMORY_MIB POLICY OPERATION [OPERATION...]' >&2
    exit 2
fi
binary="$1"
if [[ "$binary" != image ]]; then binary="$(realpath "$binary")"; fi
workers="$2"
hint="$3"
enabled="$4"
memory="$5"
policy="$6"
shift 6
case "$workers" in [1-8]) ;; *) exit 2;; esac
case "$hint" in auto|off) ;; *) exit 2;; esac
case "$enabled" in true|false) ;; *) exit 2;; esac
case "$memory" in 512|4096) ;; *) exit 2;; esac
case "$policy" in backend-default|whole-object|sparse) ;; *) exit 2;; esac
for mode in "$@"; do
    case "$mode" in seqread|randread|seqwrite|randwrite) ;; *) exit 2;; esac
done
if [[ "$binary" != image ]]; then test -x "$binary"; fi
run="fio-$(date +%Y%m%dT%H%M%S-%N)"
mkdir "$run"
if [[ -n "$(ss -lntH '( sport = :37379 or sport = :39000 or sport = :39567 )')" ]]; then
    echo "A diagnostic port is already in use" >&2
    exit 1
fi
redis-server --bind 127.0.0.1 --port 37379 --dir "$PWD/$run" --appendonly yes --daemonize no > "$run/redis.log" 2>&1 &
redis_pid=$!
stop_redis() {
    kill -TERM "$redis_pid" 2>/dev/null || true
    wait "$redis_pid" || true
}
trap stop_redis EXIT
for ((i=0; i<40; i++)); do
    kill -0 "$redis_pid"
    if [[ "$(redis-cli -h 127.0.0.1 -p 37379 ping 2>/dev/null)" = PONG ]]; then break; fi
    sleep .25
done
test "$(redis-cli -h 127.0.0.1 -p 37379 ping)" = PONG
image=ax-cluster/rustfs:f4ed202508f2153afb7956bae6f7799b60eccd00
python=/srv/ax-cluster-20261113/venv/bin/python3
printf '%s\n' "$binary" "$workers" "$hint" "$enabled" "$memory" "$policy" "$@" > "$run/parameters.txt"
entry=/candidate-rustfs
args=(-v "$binary:$entry:ro")
if [[ "$binary" = image ]]; then entry=/usr/bin/rustfs; args=(); fi
obs=()
if [[ ${DIAG_OTEL:-0} = 1 ]]; then
    obs=(-e RUSTFS_OBS_ENDPOINT=http://127.0.0.1:34318
         -e RUSTFS_OBS_METER_INTERVAL=2
         -e RUSTFS_OBS_TRACES_EXPORT_ENABLED=false
         -e RUSTFS_OBS_LOGS_EXPORT_ENABLED=false
         -e RUSTFS_OBS_PROFILING_EXPORT_ENABLED=false)
fi
index=0
for condition in candidate; do
    index=$((index + 1))
    dir="$PWD/$run/$index-$condition"
    mkdir -p "$dir"/data/{1,2,3,4}
    name="ax-dynamic-followup-$run-$index-$condition"
    docker run -d --name "$name" --network host --user 0:0 --cpus 16 --memory 16g \
        -e RUSTFS_ACCESS_KEY=dynamicdiag -e RUSTFS_SECRET_KEY=localdiagnostic-only \
        "${obs[@]}" -e RUSTFS_OBS_PUT_STAGE_METRICS_ENABLED=true -e RUSTFS_DISK_FSYNC_WORKERS="$workers" -e RUSTFS_EC_READ_QUANTUM_ENABLE="$enabled" -e RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED=true \
        -e RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE=true -e RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED=true -e RUSTFS_CONSOLE_ENABLE=false -e RUSTFS_UNSAFE_BYPASS_DISK_CHECK=true -v "$dir/data:/vol" "${args[@]}" --entrypoint "$entry" "$image" \
        --address 127.0.0.1:39000 /vol/{1,2,3,4} > "$dir/container"
    cleanup() { docker logs "$name" > "$dir/service.log" 2>&1 || true; docker stop "$name" >/dev/null || true; }
    trap 'cleanup; stop_redis' EXIT
    for ((i=0;i<60;i++)); do
        test "$(docker inspect -f "{{.State.Running}}" "$name")" = true
        if curl -fsS http://127.0.0.1:39000/health/live >/dev/null 2>&1; then break; fi
        sleep 1
    done
    curl -fsS http://127.0.0.1:39000/health/live >/dev/null
    test "$(docker inspect -f "{{.State.Running}}" "$name")" = true
    cat /proc/diskstats > "$dir/disk-before"
    "$python" "$scripts/fio.py" "$dir" "$hint" "$memory" "$policy" "$@"
    cat /proc/diskstats > "$dir/disk-after"
    cleanup
    trap stop_redis EXIT
done
