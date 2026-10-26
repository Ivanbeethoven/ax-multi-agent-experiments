#!/usr/bin/env bash
# Run on the existing osdi-smoke host, as root. Do not recreate the host.
set -u
cd /data/cloud-smoke/2026-10-25
export TMPDIR=/data/tmp
export STATE_DIR=/data/brewfs-state LOG_DIR=/data/brewfs-logs
export CONFIG_DIR=/data/brewfs-config DEFAULT_DIR=/data/brewfs-default
export INSTALL_DIR=/data/bin LIBEXEC_DIR=/data/brewfs-libexec
export MOUNT_POINT=/mnt/brewfs REDIS_PORT=6380
export BREWFS_INSTALL_PACKAGES=0
export BREWFS_COMPRESSION=none
export BREWFS_READ_SSD_BYTES=8589934592 BREWFS_WRITE_SSD_BYTES=8589934592
mkdir -p "$TMPDIR" "$INSTALL_DIR"
date -u
sha256sum install_brewfs_single_node.sh
timeout --signal=TERM --kill-after=5s 900 bash install_brewfs_single_node.sh install
rc=$?
printf 'installer_exit=%s\n' "$rc"
printf '{"installer_exit":%s}\n' "$rc" > results/install-status.json
date -u
exit "$rc"
