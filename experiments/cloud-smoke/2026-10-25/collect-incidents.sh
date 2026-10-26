#!/usr/bin/env bash
set -u
cd /data/cloud-smoke/2026-10-25
date -u > results/incidents-host-time.txt
journalctl -u brewfs.service -u brewfs-redis.service -u brewfs-rustfs.service --since '2026-10-25 19:14:00' --no-pager > results/stack-service-journal.log
ps -eo pid,ppid,lstart,stat,args --forest > results/process-tree.txt
systemctl --no-pager --full status apt-daily.service apt-daily-upgrade.service > results/apt-services.txt 2>&1
tail -n 80 /var/log/apt/history.log > results/apt-history-tail.txt 2>&1
tail -n 80 /var/log/unattended-upgrades/unattended-upgrades.log > results/unattended-upgrades-tail.txt 2>&1
tail -n 120 /data/brewfs-logs/brewfs-err.log > results/brewfs-errors.log
tail -n 120 /data/jfs-mount.log > results/jfs-mount-tail.log
systemctl show brewfs.service brewfs-redis.service brewfs-rustfs.service -p MainPID -p NRestarts -p ActiveEnterTimestamp -p Result > results/service-identities.txt
