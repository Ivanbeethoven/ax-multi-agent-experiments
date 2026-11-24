#!/usr/bin/env bash
# Single-node s3fs (s3fs-fuse) mount & lifecycle controller
# Installed at: /usr/local/bin/s3fs-node-ctl

set -euo pipefail

HOST="$(hostname)"
CONF_FILE="${S3FS_CONF:-/etc/s3fs/s3fs.env}"
PASSWD_FILE="/etc/s3fs/passwd-s3fs"

load_conf() {
  if [ -f "${CONF_FILE}" ]; then
    # shellcheck disable=SC1090
    source "${CONF_FILE}"
  fi
  S3_URL="${S3_URL:-}"
  S3_BUCKET="${S3_BUCKET:-}"
  S3_ACCESS_KEY="${S3_ACCESS_KEY:-}"
  S3_SECRET_KEY="${S3_SECRET_KEY:-}"
  S3_MOUNTPOINT="${S3_MOUNTPOINT:-/mnt/s3fs}"
  S3_USE_PATH_STYLE="${S3_USE_PATH_STYLE:-1}"
  S3_EXTRA_OPTS="${S3_EXTRA_OPTS:-allow_other,nonempty,umask=0022,mp_umask=0022,parallel_count=16,multipart_size=16}"
}

validate_conf() {
  load_conf
  if [ -z "${S3_URL}" ] || [ -z "${S3_BUCKET}" ] || [ -z "${S3_ACCESS_KEY}" ] || [ -z "${S3_SECRET_KEY}" ]; then
    echo "[${HOST}] ERROR: Missing S3_URL / S3_BUCKET / S3_ACCESS_KEY / S3_SECRET_KEY in ${CONF_FILE}" >&2
    exit 1
  fi
  if [[ "${S3_ACCESS_KEY}" == REPLACE_WITH* ]] || [[ "${S3_SECRET_KEY}" == REPLACE_WITH* ]]; then
    echo "[${HOST}] ERROR: Placeholder credentials detected in ${CONF_FILE}. Please set real S3_ACCESS_KEY and S3_SECRET_KEY first." >&2
    exit 1
  fi
}

do_mount() {
  validate_conf
  install -d -m 0700 /etc/s3fs
  printf '%s:%s\n' "${S3_ACCESS_KEY}" "${S3_SECRET_KEY}" > "${PASSWD_FILE}"
  chmod 0600 "${PASSWD_FILE}"

  mkdir -p "${S3_MOUNTPOINT}"
  if mountpoint -q "${S3_MOUNTPOINT}"; then
    echo "[${HOST}] ${S3_MOUNTPOINT} is already mounted ($(findmnt -n -o SOURCE,FSTYPE "${S3_MOUNTPOINT}"))"
    return 0
  fi

  local opts="passwd_file=${PASSWD_FILE},url=${S3_URL}"
  if [ "${S3_USE_PATH_STYLE}" = "1" ]; then
    opts="${opts},use_path_request_style"
  fi
  if [ -n "${S3_EXTRA_OPTS}" ]; then
    opts="${opts},${S3_EXTRA_OPTS}"
  fi

  s3fs "${S3_BUCKET}" "${S3_MOUNTPOINT}" -o "${opts}"
  if ! mountpoint -q "${S3_MOUNTPOINT}"; then
    echo "[${HOST}] ERROR: s3fs failed to mount ${S3_MOUNTPOINT}" >&2
    exit 1
  fi
  echo "[${HOST}] Mounted bucket '${S3_BUCKET}' (${S3_URL}) -> ${S3_MOUNTPOINT}"
}

do_umount() {
  load_conf
  if mountpoint -q "${S3_MOUNTPOINT}"; then
    sync
    fusermount3 -u "${S3_MOUNTPOINT}" 2>/dev/null || umount "${S3_MOUNTPOINT}" || umount -l "${S3_MOUNTPOINT}"
    echo "[${HOST}] Unmounted ${S3_MOUNTPOINT}"
  else
    echo "[${HOST}] ${S3_MOUNTPOINT} is not mounted"
  fi
}

show_status() {
  load_conf
  echo "=== [${HOST}] s3fs Status ==="
  echo "  Binary     : $(command -v s3fs || echo 'MISSING') ($(s3fs --version 2>/dev/null | head -n1 || echo 'n/a'))"
  echo "  Config     : ${CONF_FILE} ($( [ -f "${CONF_FILE}" ] && echo 'present' || echo 'missing' ))"
  echo "  Target     : bucket='${S3_BUCKET:-<unset>}' url='${S3_URL:-<unset>}' -> ${S3_MOUNTPOINT}"
  if mountpoint -q "${S3_MOUNTPOINT}"; then
    echo "  MountState : MOUNTED ($(findmnt -n -o FSTYPE,SIZE,USED,AVAIL "${S3_MOUNTPOINT}" || true))"
  else
    echo "  MountState : NOT MOUNTED"
  fi
}

do_test() {
  load_conf
  if ! mountpoint -q "${S3_MOUNTPOINT}"; then
    echo "[${HOST}] ERROR: ${S3_MOUNTPOINT} is not mounted; run 's3fs-node-ctl mount' first." >&2
    exit 1
  fi
  local tf="${S3_MOUNTPOINT}/.s3fs-smoke-${HOST}-$$.txt"
  local msg="s3fs-ok-${HOST}-$(date -Is)"
  printf '%s\n' "${msg}" > "${tf}"
  local got
  got="$(cat "${tf}")"
  rm -f "${tf}"
  if [ "${got}" != "${msg}" ]; then
    echo "[${HOST}] ERROR: Read-back mismatch on ${S3_MOUNTPOINT}" >&2
    exit 1
  fi
  echo "[${HOST}] Read/write smoke check PASSED on ${S3_MOUNTPOINT}"
}

case "${1:-status}" in
  mount)   do_mount ;;
  umount)  do_umount ;;
  remount) do_umount; do_mount ;;
  status)  show_status ;;
  test)    do_test ;;
  *)
    echo "Usage: $0 {status|mount|umount|remount|test}" >&2
    exit 1
    ;;
esac
