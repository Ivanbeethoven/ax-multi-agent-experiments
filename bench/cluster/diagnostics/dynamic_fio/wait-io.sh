#!/usr/bin/env bash
set -euo pipefail
disk="$(lsblk -ndo PKNAME "$(findmnt -n -o SOURCE -T "$PWD")")"
test -n "$disk"
busy() { awk -v disk="$disk" '$3 == disk {print $13; found=1; exit} END {exit !found}' /proc/diskstats; }
for attempt in {1..120}; do
    before="$(busy)"
    sleep 2
    after="$(busy)"
    if ((after - before < 300)); then exit 0; fi
    sleep 3
done
echo 'Local disk remained busy for ten minutes' >&2
exit 1
