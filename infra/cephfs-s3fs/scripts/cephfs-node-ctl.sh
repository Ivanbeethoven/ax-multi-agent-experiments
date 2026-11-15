#!/usr/bin/env bash
# Single-node CephFS daemon & mount controller
# Installed at: /usr/local/bin/cephfs-node-ctl

set -euo pipefail

HOST="$(hostname)"
MOUNTPOINT="${CEPHFS_MOUNTPOINT:-/mnt/cephfs}"
MON_ADDRS="175.168.3.66,175.168.3.55,175.168.6.72"
FS_NAME="cephfs"

start_mon() {
  if [ -d "/var/lib/ceph/mon/ceph-${HOST}" ]; then
    systemctl enable --now "ceph-mon@${HOST}.service" >/dev/null 2>&1 || systemctl start "ceph-mon@${HOST}.service"
    echo "[${HOST}] ceph-mon@${HOST} started"
  fi
}

stop_mon() {
  if [ -d "/var/lib/ceph/mon/ceph-${HOST}" ]; then
    systemctl disable --now "ceph-mon@${HOST}.service" >/dev/null 2>&1 || systemctl stop "ceph-mon@${HOST}.service" || true
    echo "[${HOST}] ceph-mon@${HOST} stopped"
  fi
}

start_mgr() {
  if [ -d "/var/lib/ceph/mgr/ceph-${HOST}" ]; then
    systemctl enable --now "ceph-mgr@${HOST}.service" >/dev/null 2>&1 || systemctl start "ceph-mgr@${HOST}.service"
    echo "[${HOST}] ceph-mgr@${HOST} started"
  fi
}

stop_mgr() {
  if [ -d "/var/lib/ceph/mgr/ceph-${HOST}" ]; then
    systemctl disable --now "ceph-mgr@${HOST}.service" >/dev/null 2>&1 || systemctl stop "ceph-mgr@${HOST}.service" || true
    echo "[${HOST}] ceph-mgr@${HOST} stopped"
  fi
}

start_osd() {
  shopt -s nullglob
  for d in /var/lib/ceph/osd/ceph-*; do
    local id="${d##*ceph-}"
    systemctl enable --now "ceph-osd-loop@${id}.service" >/dev/null 2>&1 || systemctl start "ceph-osd-loop@${id}.service"
    systemctl enable --now "ceph-osd@${id}.service" >/dev/null 2>&1 || systemctl start "ceph-osd@${id}.service"
    echo "[${HOST}] ceph-osd@${id} (with loop block) started"
  done
}

stop_osd() {
  shopt -s nullglob
  for d in /var/lib/ceph/osd/ceph-*; do
    local id="${d##*ceph-}"
    systemctl disable --now "ceph-osd@${id}.service" >/dev/null 2>&1 || systemctl stop "ceph-osd@${id}.service" || true
    systemctl disable --now "ceph-osd-loop@${id}.service" >/dev/null 2>&1 || systemctl stop "ceph-osd-loop@${id}.service" || true
    local img="/var/lib/ceph/osd-backing/osd-${id}.img"
    local ldev
    ldev="$(losetup -j "${img}" 2>/dev/null | head -n1 | cut -d: -f1 || true)"
    if [ -n "${ldev}" ]; then
      losetup -d "${ldev}" 2>/dev/null || true
    fi
    echo "[${HOST}] ceph-osd@${id} (and loop block) stopped"
  done
}

start_mds() {
  if [ -d "/var/lib/ceph/mds/ceph-${HOST}" ]; then
    systemctl enable --now "ceph-mds@${HOST}.service" >/dev/null 2>&1 || systemctl start "ceph-mds@${HOST}.service"
    echo "[${HOST}] ceph-mds@${HOST} started"
  fi
}

stop_mds() {
  if [ -d "/var/lib/ceph/mds/ceph-${HOST}" ]; then
    systemctl disable --now "ceph-mds@${HOST}.service" >/dev/null 2>&1 || systemctl stop "ceph-mds@${HOST}.service" || true
    echo "[${HOST}] ceph-mds@${HOST} stopped"
  fi
}

do_mount_kernel() {
  mkdir -p "${MOUNTPOINT}"
  if mountpoint -q "${MOUNTPOINT}"; then
    local fstype
    fstype="$(findmnt -n -o FSTYPE "${MOUNTPOINT}" || true)"
    if [ "${fstype}" = "ceph" ]; then
      echo "[${HOST}] ${MOUNTPOINT} already mounted (kernel ceph)"
      return 0
    fi
    umount "${MOUNTPOINT}"
  fi
  mount -t ceph "${MON_ADDRS}:/" "${MOUNTPOINT}" \
    -o "name=admin,secretfile=/etc/ceph/admin.secret,mds_namespace=${FS_NAME},_netdev,noatime"
  echo "[${HOST}] ${MOUNTPOINT} mounted (kernel ceph)"
}

do_mount_fuse() {
  mkdir -p "${MOUNTPOINT}"
  if mountpoint -q "${MOUNTPOINT}"; then
    local fstype
    fstype="$(findmnt -n -o FSTYPE "${MOUNTPOINT}" || true)"
    if [ "${fstype}" = "fuse.ceph-fuse" ]; then
      echo "[${HOST}] ${MOUNTPOINT} already mounted (ceph-fuse)"
      return 0
    fi
    umount "${MOUNTPOINT}"
  fi
  ceph-fuse "${MOUNTPOINT}" --client_fs="${FS_NAME}" -o noatime
  echo "[${HOST}] ${MOUNTPOINT} mounted (ceph-fuse)"
}

do_umount() {
  if mountpoint -q "${MOUNTPOINT}"; then
    sync
    umount "${MOUNTPOINT}" || umount -l "${MOUNTPOINT}"
    echo "[${HOST}] ${MOUNTPOINT} unmounted"
  else
    echo "[${HOST}] ${MOUNTPOINT} is not mounted"
  fi
}

show_status() {
  echo "=== [${HOST}] Ceph Daemons & Mount Status ==="
  local units=()
  [ -d "/var/lib/ceph/mon/ceph-${HOST}" ] && units+=("ceph-mon@${HOST}")
  [ -d "/var/lib/ceph/mgr/ceph-${HOST}" ] && units+=("ceph-mgr@${HOST}")
  [ -d "/var/lib/ceph/mds/ceph-${HOST}" ] && units+=("ceph-mds@${HOST}")
  shopt -s nullglob
  for d in /var/lib/ceph/osd/ceph-*; do
    local id="${d##*ceph-}"
    units+=("ceph-osd-loop@${id}" "ceph-osd@${id}")
  done
  if [ "${#units[@]}" -eq 0 ]; then
    echo "  Daemons: (none configured on this node)"
  else
    for u in "${units[@]}"; do
      printf "  %-22s : %s\n" "${u}" "$(systemctl is-active "${u}.service" || true)"
    done
  fi
  if mountpoint -q "${MOUNTPOINT}"; then
    echo "  Mount (${MOUNTPOINT}) : MOUNTED ($(findmnt -n -o FSTYPE,SIZE,USED,AVAIL,USE% "${MOUNTPOINT}"))"
  else
    echo "  Mount (${MOUNTPOINT}) : NOT MOUNTED"
  fi
}

case "${1:-status}" in
  start-mon)   start_mon ;;
  stop-mon)    stop_mon ;;
  start-mgr)   start_mgr ;;
  stop-mgr)    stop_mgr ;;
  start-osd)   start_osd ;;
  stop-osd)    stop_osd ;;
  start-mds)   start_mds ;;
  stop-mds)    stop_mds ;;
  start-daemons)
    start_mon
    start_mgr
    start_osd
    start_mds
    ;;
  stop-daemons)
    stop_mds
    stop_osd
    stop_mgr
    stop_mon
    ;;
  mount)       do_mount_kernel ;;
  mount-fuse)  do_mount_fuse ;;
  umount)      do_umount ;;
  status)      show_status ;;
  *)
    echo "Usage: $0 {status|start-daemons|stop-daemons|start-mon|stop-mon|start-mgr|stop-mgr|start-osd|stop-osd|start-mds|stop-mds|mount|mount-fuse|umount}" >&2
    exit 1
    ;;
esac
