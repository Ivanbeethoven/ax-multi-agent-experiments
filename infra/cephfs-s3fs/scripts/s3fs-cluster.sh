#!/usr/bin/env bash
# Cluster-wide s3fs (s3fs-fuse) batch controller across all 6 cluster servers
# Can be run EITHER directly on jump/admin node node-coord (/usr/local/bin/s3fs-cluster)
# OR from an external workstation with ssh aliases cluster-node-1..cluster-node-6.

set -euo pipefail

ALL_NODES=("node-coord" "node-meta" "node-store-1" "node-store-2" "node-store-3" "node-store-4")

node_to_external_ssh() {
  case "$1" in
    node-coord) echo "cluster-node-1" ;;
    node-meta) echo "cluster-node-2" ;;
    node-store-1) echo "cluster-node-3" ;;
    node-store-2) echo "cluster-node-4" ;;
    node-store-3) echo "cluster-node-5" ;;
    node-store-4) echo "cluster-node-6" ;;
    *) echo "Unknown node $1" >&2; exit 1 ;;
  esac
}

run_on_node() {
  local node="$1"
  shift
  if [ "$(hostname)" = "node-coord" ]; then
    if [ "${node}" = "node-coord" ]; then
      bash -c "$*"
    else
      ssh -F /etc/ceph/ops_ssh/ssh_config -o BatchMode=yes "${node}" "$*"
    fi
  else
    local alias_name
    alias_name="$(node_to_external_ssh "${node}")"
    ssh -o BatchMode=yes "${alias_name}" "$*"
  fi
}

configure_all() {
  if [ "$#" -lt 4 ]; then
    echo "Usage: $0 configure <S3_URL> <S3_BUCKET> <S3_ACCESS_KEY> <S3_SECRET_KEY> [S3_MOUNTPOINT]" >&2
    echo "Example: $0 configure http://175.168.6.72:9000 bench-bucket myak mysk /mnt/s3fs" >&2
    exit 1
  fi
  local url="$1"
  local bucket="$2"
  local ak="$3"
  local sk="$4"
  local mnt="${5:-/mnt/s3fs}"

  local content
  content="$(cat <<EOF
S3_URL="${url}"
S3_BUCKET="${bucket}"
S3_ACCESS_KEY="${ak}"
S3_SECRET_KEY="${sk}"
S3_MOUNTPOINT="${mnt}"
S3_USE_PATH_STYLE="1"
S3_EXTRA_OPTS="allow_other,nonempty,umask=0022,mp_umask=0022,parallel_count=16,multipart_size=16"
EOF
)"
  local b64
  b64="$(printf '%s\n' "${content}" | base64 -w0)"

  for n in "${ALL_NODES[@]}"; do
    run_on_node "${n}" "install -d -m 0700 /etc/s3fs && echo '${b64}' | base64 -d > /etc/s3fs/s3fs.env && chmod 0600 /etc/s3fs/s3fs.env && echo '[${n}] Updated /etc/s3fs/s3fs.env'"
  done
}

cmd="${1:-status}"
shift || true

case "${cmd}" in
  configure)
    configure_all "$@"
    ;;
  mount|umount|remount|status|test)
    for n in "${ALL_NODES[@]}"; do
      run_on_node "${n}" "/usr/local/bin/s3fs-node-ctl ${cmd}"
    done
    ;;
  *)
    echo "Usage: $0 {status|configure|mount|umount|remount|test}" >&2
    exit 1
    ;;
esac
