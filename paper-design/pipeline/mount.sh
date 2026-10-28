#!/usr/bin/env bash
set -euo pipefail

MOUNT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$MOUNT_DIR/common.sh"

MOUNT_COMMAND=''
INIT_COMMAND=''
WORKSPACE_ID=''
MOUNT_STATUS=''
MOUNT_OUTPUT=''

mount_help() {
  cat <<'EOF'
Usage: mount.sh <operation> [options]

Operations (also sourceable functions):
  flat       --mount-point MP --data-dir DIR [--brewfs BIN] [--redis URL]
  native     --mount-point MP --data-dir DIR --owner OWNER [--brewfs BIN] [--redis URL]
  packed     --mount-point MP --data-dir DIR --manifest-key KEY --data-config YAML --owner OWNER
             [--init-cwd DIR] [--brewfs BIN] [--redis URL]
  juicefs    --mount-point MP --cache-dir DIR --bucket URL --name NAME
             [--juicefs BIN] [--redis URL] [--log FILE]
  unmount    --mount-point MP [--fusermount BIN]
  Add --fuse-keep-cache or --no-fuse-keep-cache to a BrewFS operation.

The Redis URL defaults to db0 for BrewFS and db1 for JuiceFS.  packed captures
the known catalog-capability error and sets MOUNT_STATUS=blocked instead of
terminating the batch.
EOF
}

normalize_workspace_id() {
  local output=$1 id
  id=$(printf '%s\n' "$output" | grep -Eio '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32}' | head -n1 || true)
  id=${id//-/}
  [[ "$id" =~ ^[0-9a-fA-F]{32}$ ]] || die "workspace init did not print a 32-hex UUID; output: $output"
  printf '%s' "$id"
}

run_init_workspace() {
  local init_kind=$1; shift
  local brewfs=$1; shift
  local output
  local -a cmd=("$brewfs" workspace "$init_kind" "$@")
  INIT_COMMAND=$(format_command "${cmd[@]}")
  log "running: $INIT_COMMAND"
  if ! output=$("${cmd[@]}" 2>&1); then
    MOUNT_OUTPUT=$output
    MOUNT_STATUS=failed
    printf '%s\n' "$output" >&2
    return 1
  fi
  MOUNT_OUTPUT=$output
  WORKSPACE_ID=$(normalize_workspace_id "$output")
  log "workspace UUID: $WORKSPACE_ID"
}

mount_flat() {
  local mount_point=$1 data_dir=$2
  local brewfs=${3:-${BREWFS_BIN:-brewfs}} redis=${4:-$(redis_url 0)} keep_cache=${5:-${BREWFS_KEEP_CACHE:-}}
  assert_no_placeholders "$mount_point"; assert_no_placeholders "$data_dir"
  ensure_dir "$mount_point"
  local -a cmd=("$brewfs" mount --data-backend local-fs --data-dir "$data_dir" --meta-backend redis --meta-url "$redis" "$mount_point")
  [[ "$keep_cache" == true ]] && cmd+=(--fuse-keep-cache)
  MOUNT_COMMAND=$(format_command "${cmd[@]}"); MOUNT_STATUS=starting; MOUNT_OUTPUT=''
  log "running: $MOUNT_COMMAND"
  "${cmd[@]}" 2>&1 | tee /tmp/brewfs-mount-$$.log
  MOUNT_STATUS=mounted
  printf '%s' "$MOUNT_COMMAND"
}

mount_overlay_native() {
  local mount_point=$1 data_dir=$2 owner=$3
  local brewfs=${4:-${BREWFS_BIN:-brewfs}} redis=${5:-$(redis_url 0)} keep_cache=${6:-${BREWFS_KEEP_CACHE:-}}
  assert_no_placeholders "$mount_point"; assert_no_placeholders "$data_dir"
  ensure_dir "$mount_point"
  run_init_workspace init-volume "$brewfs" --meta-backend redis --meta-url "$redis" --owner "$owner"
  local -a cmd=("$brewfs" mount --privileged --volume-format workspace-v1 --workspace "$WORKSPACE_ID" --meta-backend redis --meta-url "$redis" --data-backend local-fs --data-dir "$data_dir" "$mount_point")
  [[ "$keep_cache" == true ]] && cmd+=(--fuse-keep-cache)
  MOUNT_COMMAND=$(format_command "${cmd[@]}"); MOUNT_STATUS=starting
  log "running: $MOUNT_COMMAND"
  "${cmd[@]}" 2>&1 | tee /tmp/brewfs-mount-$$.log
  MOUNT_STATUS=mounted
  printf '%s' "$MOUNT_COMMAND"
}

mount_overlay_packed() {
  local mount_point=$1 data_dir=$2 manifest_key=$3 data_config=$4 owner=$5
  local brewfs=${6:-${BREWFS_BIN:-brewfs}} redis=${7:-$(redis_url 0)} init_cwd=${8:-$(dirname "$data_config")} keep_cache=${9:-${BREWFS_KEEP_CACHE:-}}
  assert_no_placeholders "$mount_point"; assert_no_placeholders "$data_dir"; assert_no_placeholders "$manifest_key"
  require_file "$data_config"
  if grep -Eq 'REPLACE_WITH|REGISTRY/' "$data_config"; then die "deployment placeholder found in data config: $data_config"; fi
  ensure_dir "$mount_point"; ensure_dir "$init_cwd"
  local init_output
  local -a init=("$brewfs" workspace init-packed-volume --manifest-key "$manifest_key" --data-config "$data_config" --meta-backend redis --meta-url "$redis" --owner "$owner")
  INIT_COMMAND=$(format_command "${init[@]}")
  log "running from $init_cwd: $INIT_COMMAND"
  if ! init_output=$(cd "$init_cwd" && "${init[@]}" 2>&1); then
    MOUNT_OUTPUT=$init_output; MOUNT_STATUS=failed
    printf '%s\n' "$init_output" >&2
    return 1
  fi
  MOUNT_OUTPUT=$init_output
  WORKSPACE_ID=$(normalize_workspace_id "$init_output")
  local -a cmd=("$brewfs" mount --privileged --volume-format workspace-v1 --workspace "$WORKSPACE_ID" --meta-backend redis --meta-url "$redis" --data-backend local-fs --data-dir "$data_dir" "$mount_point")
  [[ "$keep_cache" == true ]] && cmd+=(--fuse-keep-cache)
  MOUNT_COMMAND=$(format_command "${cmd[@]}")
  log "running: $MOUNT_COMMAND"
  local mount_output rc=0
  mount_output=$("${cmd[@]}" 2>&1) || rc=$?
  MOUNT_OUTPUT+=$'\n'"$mount_output"
  if (( rc != 0 )); then
    if grep -Fq 'packed workspace binding requires complete catalog permission/ID/lifecycle capabilities' <<<"$mount_output"; then
      MOUNT_STATUS=blocked
      warn "packed mount blocked by catalog capability: $mount_output"
      printf '%s' "$MOUNT_COMMAND"
      return 0
    fi
    MOUNT_STATUS=failed
    printf '%s\n' "$mount_output" >&2
    return "$rc"
  fi
  MOUNT_STATUS=mounted
  printf '%s' "$MOUNT_COMMAND"
}

mount_juicefs() {
  local mount_point=$1 cache_dir=$2 bucket=$3 name=$4
  local juicefs=${5:-${JUICEFS_BIN:-juicefs}} redis=${6:-$(redis_url 1)} log_file=${7:-${mount_point%/*}/juicefs.log}
  assert_no_placeholders "$mount_point"; assert_no_placeholders "$cache_dir"; assert_no_placeholders "$bucket"
  ensure_dir "$mount_point"; ensure_dir "$cache_dir"; ensure_dir "$(dirname "$log_file")"
  local -a format=("$juicefs" format "$redis" "$name" --storage s3 --bucket "$bucket" --block-size 4M)
  local -a cmd=("$juicefs" mount "$redis" "$mount_point" -d --cache-dir="$cache_dir" --cache-size=8192 --writeback --no-syslog --log "$log_file")
  INIT_COMMAND=$(format_command "${format[@]}"); MOUNT_COMMAND=$(format_command "${cmd[@]}")
  log "running: $INIT_COMMAND"
  "${format[@]}"
  log "running: $MOUNT_COMMAND"
  "${cmd[@]}" 2>&1 | tee /tmp/juicefs-mount-$$.log
  MOUNT_STATUS=mounted
  printf '%s' "$MOUNT_COMMAND"
}

unmount_point() {
  local mount_point=$1 fusermount=${2:-fusermount3}
  local -a cmd=("$fusermount" -u "$mount_point")
  log "running: $(format_command "${cmd[@]}")"
  "${cmd[@]}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  [[ $# -gt 0 ]] || { mount_help; exit 2; }
  op=$1; shift
  [[ "$op" == --help || "$op" == -h ]] && { mount_help; exit 0; }
  mount_point=''; data_dir=''; owner=''; manifest_key=''; data_config=''; init_cwd=''; cache_dir=''; bucket=''; name=''; brewfs="${BREWFS_BIN:-brewfs}"; juicefs="${JUICEFS_BIN:-juicefs}"; redis=''; log_file=''; fusermount='fusermount3'; keep_cache=''
  while [[ $# -gt 0 ]]; do
    case $1 in
      --mount-point) mount_point=${2:?missing value for --mount-point}; shift 2;;
      --data-dir) data_dir=${2:?missing value for --data-dir}; shift 2;;
      --owner) owner=${2:?missing value for --owner}; shift 2;;
      --manifest-key) manifest_key=${2:?missing value for --manifest-key}; shift 2;;
      --data-config) data_config=${2:?missing value for --data-config}; shift 2;;
      --init-cwd) init_cwd=${2:?missing value for --init-cwd}; shift 2;;
      --cache-dir) cache_dir=${2:?missing value for --cache-dir}; shift 2;;
      --bucket) bucket=${2:?missing value for --bucket}; shift 2;;
      --name) name=${2:?missing value for --name}; shift 2;;
      --brewfs) brewfs=${2:?missing value for --brewfs}; shift 2;;
      --juicefs) juicefs=${2:?missing value for --juicefs}; shift 2;;
      --redis) redis=${2:?missing value for --redis}; shift 2;;
      --log) log_file=${2:?missing value for --log}; shift 2;;
      --fusermount) fusermount=${2:?missing value for --fusermount}; shift 2;;
      --fuse-keep-cache) keep_cache=true; shift;;
      --no-fuse-keep-cache) keep_cache=false; shift;;
      -h|--help) mount_help; exit 0;;
      *) die "unknown option: $1";;
    esac
  done
  case "$op" in
    flat) [[ -n "$mount_point" && -n "$data_dir" ]] || die 'flat needs --mount-point and --data-dir'; mount_flat "$mount_point" "$data_dir" "$brewfs" "${redis:-$(redis_url 0)}" "$keep_cache";;
    native) [[ -n "$mount_point" && -n "$data_dir" && -n "$owner" ]] || die 'native needs --mount-point, --data-dir and --owner'; mount_overlay_native "$mount_point" "$data_dir" "$owner" "$brewfs" "${redis:-$(redis_url 0)}" "$keep_cache";;
    packed) [[ -n "$mount_point" && -n "$data_dir" && -n "$owner" && -n "$manifest_key" && -n "$data_config" ]] || die 'packed needs mount/data/owner/manifest/data-config'; mount_overlay_packed "$mount_point" "$data_dir" "$manifest_key" "$data_config" "$owner" "$brewfs" "${redis:-$(redis_url 0)}" "${init_cwd:-$(dirname "$data_config")}" "$keep_cache";;
    juicefs) [[ -n "$mount_point" && -n "$cache_dir" && -n "$bucket" && -n "$name" ]] || die 'juicefs needs mount/cache/bucket/name'; mount_juicefs "$mount_point" "$cache_dir" "$bucket" "$name" "$juicefs" "${redis:-$(redis_url 1)}" "$log_file";;
    unmount) [[ -n "$mount_point" ]] || die 'unmount needs --mount-point'; unmount_point "$mount_point" "$fusermount";;
    *) die "unknown operation: $op";;
  esac
fi
