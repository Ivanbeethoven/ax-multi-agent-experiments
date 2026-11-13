#!/usr/bin/env bash
# Create an immutable archive of one closed attempt on the node that produced it.
set -euo pipefail
run=${1:?run}; role=${2:?role}
[[ "$run" =~ ^ax-four-[0-9a-f]{12}$ ]]
[[ "$role" =~ ^(client|target|obj[1-4])$ ]]
base=/srv/ax-idcai-20261113
campaign="$base/campaign-100k-001/$run"
storage="$base/four-node/$run"
observer="$campaign/observer-$role"
for unit in "$run" "$run-otel" "$run-redis" "$run-observer"; do
    state=$(systemctl show -p ActiveState --value "$unit.service")
    case "$state" in inactive|failed) ;; *) echo "Still active: $unit" >&2; exit 1;; esac
done
if findmnt -rn -o TARGET | grep -Eq "^($campaign|/srv/ax-pilot-session)(/|$)"; then
    echo 'Mount still present' >&2; exit 1
fi
test -s "$observer/finished.json"
path="$base/evidence-archives/$run"
install -d -m700 "$path"
archive="$path/$role.tar.gz"
umask 077
receipt() {
    gzip -t -- "$archive"
    local sha
    sha=$(sha256sum -- "$archive" | cut -d' ' -f1)
    local journal=null
    if [[ -f "$storage/service-journal-required" ]]; then
        journal=$(python3 "$storage/collect-service-journal.py" "$run")
    fi
    jq -n --arg run "$run" --arg role "$role" --arg path "$archive" --arg sha "$sha" \
        --argjson journal "$journal" --argjson bytes "$(stat -c %s "$archive")" \
        '{run:$run,role:$role,path:$path,sha256:$sha,bytes:$bytes,service_journal:$journal}'
}
if [[ -f "$archive" ]]; then receipt; exit; fi
tmp=$(mktemp "$path/.${role}.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
members=("${observer#"$base/"}")
if [[ "$role" == client ]]; then
    for item in "$campaign"/{backend-probe.json,redis-info-all.txt,redis-latency-histogram.txt,run-performance-c1.py,probe-performance-layout.py,c[1-4]*-qualification}; do
        [[ ! -e "$item" ]] || members+=("${item#"$base/"}")
    done
fi
if [[ "$role" == obj* ]]; then
    if [[ -f "$storage/service-journal-required" ]]; then
        python3 "$storage/collect-service-journal.py" "$run" >/dev/null
        members+=("${storage#"$base/"}/service-journal.jsonl" "${storage#"$base/"}/service-journal-proof.json" "${storage#"$base/"}/service-journal-required")
        members+=("${storage#"$base/"}/deploy-node.sh" "${storage#"$base/"}/collect-service-journal.py")
    fi
    for name in metrics.prom diskstats.txt net-dev.txt clock.txt; do
        test -s "$storage/$name"
        members+=("${storage#"$base/"}/$name")
    done
fi
tar -C "$base" --exclude='*.private.json' --exclude='*-private.json' --exclude='*-private.log' \
    --exclude='*.scratch' --exclude='*/mnt' -czf "$tmp" -- "${members[@]}"
gzip -t -- "$tmp"
mv -n -- "$tmp" "$archive"
test ! -e "$tmp"
receipt
