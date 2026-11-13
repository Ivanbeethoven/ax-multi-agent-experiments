#!/usr/bin/env bash
set -euo pipefail
action=${1:?start or stop}
run=${2:?run identifier}
[[ "$run" =~ ^ax-four-[0-9a-f]{12}$ ]]
profile=${3:-smoke}
[[ "$profile" == smoke || "$profile" == perf-enabled || "$profile" == perf-disabled || "$profile" == perf-block-only ]]
base=/srv/ax-idcai-20261113
dir="$base/four-node/$run"
unit="$run.service"
otel_unit="$run-otel.service"
redis_unit="$run-redis.service"
case "$action" in
    stop)
        systemctl stop "$unit" 2>/dev/null || true
        systemctl stop "$redis_unit" "$otel_unit" 2>/dev/null || true
        for container in "$run" "$run-otel"; do
            if docker container inspect "$container" >/dev/null 2>&1; then
                [[ $(docker inspect -f '{{index .Config.Labels "ax.experiment.owner"}}' "$container") == "$run" ]]
                docker rm -f "$container" >/dev/null
            fi
        done
        if systemctl is-active --quiet "$unit" || systemctl is-active --quiet "$redis_unit" ||
           systemctl is-active --quiet "$otel_unit" || docker container inspect "$run" >/dev/null 2>&1; then
            echo "Temporary service did not stop: $run" >&2; exit 1
        fi
        if [[ -d "$dir" ]]; then rm -f -- "$dir/env" "$dir/redis-private.conf"; fi
        jq -n --arg run "$run" '{run:$run, stopped:true}'
        exit 0
        ;;
    start) ;;
    *) exit 2 ;;
esac

ip=$(ip -4 -o addr show dev axwg0 | awk '{sub(/\/.*/, "", $4); print $4}')
[[ "$ip" =~ ^10\.77\.13\.2[1-4]$ ]]
port=19000
[[ -z $(ss -lntH "sport = :$port") ]]
receipt="$base/receipts/rustfs-image-installed.json"
image=$(jq -er '.image_id | select(test("^sha256:[0-9a-f]{64}$"))' "$receipt")
revision=$(jq -er '.source_commit | select(test("^[0-9a-f]{40}$"))' "$receipt")
test "$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" = "$revision"
umask 077
mkdir -p "$base/four-node"
mkdir "$dir"
install -d -m0700 -o 10001 -g 10001 "$dir/data"
envfile="$dir/env"
trap 'rm -f -- "$envfile"' ERR
jq -er '
  if (.key|test("^[0-9a-f]{16}$")) and (.secret|test("^[0-9a-f]{64}$")) and (.rpc|test("^[0-9a-f]{64}$"))
  then "RUSTFS_ACCESS_KEY="+.key, "RUSTFS_SECRET_KEY="+.secret, "RUSTFS_RPC_SECRET="+.rpc
  else error("invalid test credentials") end' > "$envfile"
dynamic=true
if [[ "$profile" == perf-disabled ]]; then dynamic=false; fi
quantum="$dynamic"
if [[ "$profile" == perf-block-only ]]; then quantum=false; fi
runtime=600
cpus=2
memory=2g
if [[ "$profile" != smoke ]]; then
    runtime=${4:-7200}
    [[ "$runtime" =~ ^[0-9]{4,5}$ ]] && ((runtime >= 7200 && runtime <= 10800))
    cpus=8
    memory=24g
    [[ -z $(ss -lntH '( sport = :4318 or sport = :9464 )') ]]
    if [[ "$ip" == 10.77.13.24 ]]; then [[ -z $(ss -lntH 'sport = :16379') ]]; fi
    otel=z6q59od2j4w2gw3rnp.xuanyuan.run/otel/opentelemetry-collector-contrib@sha256:e40d61f10c9a617cca1ffd1be4d7e2b3a4b0f0001798ff07e58cabe82e618cea
    docker image inspect "$otel" >/dev/null
    chmod 0711 "$dir"
    install -m644 "$base/otel-performance.yaml" "$dir/otel.yaml"
    systemd-run --quiet --unit="$otel_unit" --property="RuntimeMaxSec=$runtime" \
        --property="ExecStopPost=-/usr/bin/docker rm -f $run-otel" \
        /usr/bin/docker run --rm --name "$run-otel" --label "ax.experiment.owner=$run" \
        --pull=never --network=host --cpus 1 --memory 1g \
        --mount "type=bind,source=$dir/otel.yaml,target=/etc/otelcol-contrib/config.yaml,readonly" \
        "$otel" --config=/etc/otelcol-contrib/config.yaml
    if [[ "$ip" == 10.77.13.24 ]]; then
        install -d -m700 "$dir/redis"
        {
            printf 'bind %s\nport 16379\nprotected-mode yes\n' "$ip"
            printf 'dir %s/redis\n' "$dir"
            printf '%s\n' 'databases 256' 'appendonly yes' 'appendfsync always' 'save ""' 'maxmemory 51539607552' \
                'maxmemory-policy noeviction' 'auto-aof-rewrite-percentage 100' \
                'auto-aof-rewrite-min-size 64mb' 'no-appendfsync-on-rewrite no' 'io-threads 1' 'latency-monitor-threshold 1'
        } > "$dir/redis-public.conf"
        cp "$dir/redis-public.conf" "$dir/redis-private.conf"
        sed -n 's/^RUSTFS_RPC_SECRET=/requirepass /p' "$envfile" >> "$dir/redis-private.conf"
        systemd-run --quiet --unit="$redis_unit" --property="RuntimeMaxSec=$runtime" \
            --property=MemoryMax=64G --property=CPUQuota=200% /usr/bin/redis-server "$dir/redis-private.conf"
    fi
fi
{
    printf 'RUSTFS_ADDRESS=%s:%s\n' "$ip" "$port"
    printf 'RUSTFS_VOLUMES=http://10.77.13.{21...24}:%s/data/rustfs0\n' "$port"
    printf '%s\n' RUSTFS_CONSOLE_ENABLE=false RUSTFS_CONSOLE_ADDRESS=127.0.0.1:19001 RUSTFS_UNSAFE_BYPASS_DISK_CHECK=false
    for flag in BLOCK_SIZE_HINT_ENABLE BLOCK_SIZE_HINT_FLEET_CONFIRMED; do
        printf 'RUSTFS_EC_%s=%s\n' "$flag" "$dynamic"
    done
    for flag in READ_QUANTUM_ENABLE READ_QUANTUM_FLEET_CONFIRMED; do
        printf 'RUSTFS_EC_%s=%s\n' "$flag" "$quantum"
    done
    if [[ "$profile" != smoke ]]; then
        printf '%s\n' RUSTFS_OBS_ENDPOINT=http://127.0.0.1:4318 RUSTFS_OBS_METRICS_EXPORT_ENABLED=true OTEL_METRIC_EXPORT_INTERVAL=5000
    fi
} >> "$envfile"

# Use one endpoint on the node's existing physical drive; never format or repartition it.
systemd-run --quiet --unit="$unit" --property="RuntimeMaxSec=$runtime" \
    --property="ExecStopPost=-/usr/bin/docker rm -f $run" \
    /usr/bin/docker run --rm --name "$run" --label "ax.experiment.owner=$run" \
    --pull=never --network=host --cpus "$cpus" --memory "$memory" --env-file "$envfile" \
    --mount "type=bind,source=$dir/data,target=/data/rustfs0" "$image"
jq -n --arg run "$run" --arg ip "$ip" --arg revision "$revision" --arg profile "$profile" --arg dev "$(findmnt -n -o SOURCE -T "$dir/data")" \
    --argjson hint "$dynamic" --argjson quantum "$quantum" \
    '{run:$run, address:$ip, source_commit:$revision, profile:$profile, backing_filesystem_device:$dev, local_endpoints:1, disk_bypass:false, configured_block_hint:$hint, configured_read_quantum:$quantum}'
