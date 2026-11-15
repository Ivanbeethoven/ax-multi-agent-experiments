#!/usr/bin/env bash
# RustFS 1.0.3 rejects installer-generated stdout/rolling-log path collision.
set -euo pipefail
cd /data/cloud-smoke/2026-10-25
cp /etc/systemd/system/brewfs-rustfs.service results/rustfs-service-before-fix.txt
cp /data/brewfs-logs/rustfs-err.log results/rustfs-log-sink-conflict.log
sed -i 's|StandardOutput=append:/data/brewfs-logs/rustfs.log|StandardOutput=append:/data/brewfs-logs/rustfs-stdout.log|' /etc/systemd/system/brewfs-rustfs.service
systemctl daemon-reload
systemctl restart brewfs-rustfs.service
systemctl cat brewfs-rustfs.service > results/rustfs-service-after-fix.txt
