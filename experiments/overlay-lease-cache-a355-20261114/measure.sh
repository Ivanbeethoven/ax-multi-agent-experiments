#!/usr/bin/env bash
set -euo pipefail
test "$#" = 1
r=$(realpath "$1")
trap 'echo $? > "$r/measure.exit"' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
deadline=$((SECONDS + 7200))
while test ! -f "$r/build-check.exit"; do
  test "$SECONDS" -lt "$deadline" || exit 124
  sleep 10
done
test "$(cat "$r/build-check.exit")" = 0
test "$(cat "$r/checks/status.txt")" = passed
sleep 30
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.proxy GIT_CONFIG_VALUE_0=http://127.0.0.1:17901
bash "$r/harness/experiments/overlay-lease-cache-a355-20261114/run.sh" \
  /srv/ax-overlay-open-retry-20261114/artifacts "$r/artifacts" "$r/results"
