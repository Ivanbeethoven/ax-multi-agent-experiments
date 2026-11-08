#!/usr/bin/env bash
# Safe object-store restart helper for cold-read experiments.
#
# Why this exists
# ---------------
# A Linux page-cache drop (`echo 3 > /proc/sys/vm/drop_caches`) does NOT evict the
# object store's own page cache, so "cold read" measurements silently stay warm.
# The only reliable way to force cold reads is to restart the object store process.
#
# Two traps we hit while doing this by hand:
#   1. `pkill -f rustfs` also matches the ssh command line that contains the word
#      "rustfs", so the command kills its own session and the new server never starts.
#      Use `pkill -x <process-name>` instead.
#   2. Starting the server through a single `ssh host 'pkill ...; sleep; nohup ...&'
#      line can lose the child when the session exits. Start it via `setsid nohup`
#      with stdin/stdout detached, then poll the health endpoint.
#
# Usage
#   object_store_ctl.sh restart --ssh-host root@10.0.0.13 \
#       --process rustfs \
#       --start '/srv/bin/rustfs --address 0.0.0.0:9000 /srv/data' \
#       --health http://127.0.0.1:9000/health \
#       --env-file /etc/bench/object-store.env
#
#   object_store_ctl.sh health --ssh-host root@10.0.0.13 --health http://127.0.0.1:9000/health
#
# Exit codes: 0 healthy, 1 not healthy / restart failed, 2 usage error.

set -u -o pipefail

SSH_HOST=""
PROCESS=""
START_CMD=""
HEALTH_URL=""
ENV_FILE=""
SSH_KEY=""
WAIT_SECONDS=30

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

ACTION="${1:-}"
[ -n "$ACTION" ] || usage
shift || true

while [ $# -gt 0 ]; do
  case "$1" in
    --ssh-host) SSH_HOST="${2:?}"; shift 2 ;;
    --ssh-key) SSH_KEY="${2:?}"; shift 2 ;;
    --process) PROCESS="${2:?}"; shift 2 ;;
    --start) START_CMD="${2:?}"; shift 2 ;;
    --health) HEALTH_URL="${2:?}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?}"; shift 2 ;;
    --wait) WAIT_SECONDS="${2:?}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

[ -n "$SSH_HOST" ] || { echo "--ssh-host is required" >&2; exit 2; }

ssh_base=(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15)
if [ -n "$SSH_KEY" ]; then
  ssh_base+=(-i "$SSH_KEY")
fi

run_remote() {
  "${ssh_base[@]}" "$SSH_HOST" "$1"
}

health_code() {
  run_remote "curl -s -o /dev/null -w '%{http_code}' --max-time 3 '$HEALTH_URL' || true" 2>/dev/null | tr -d '\r'
}

case "$ACTION" in
  health)
    [ -n "$HEALTH_URL" ] || { echo "--health is required" >&2; exit 2; }
    code="$(health_code)"
    echo "health=$code"
    [ "$code" = "200" ] && exit 0 || exit 1
    ;;

  restart)
    [ -n "$PROCESS" ] || { echo "--process is required" >&2; exit 2; }
    [ -n "$START_CMD" ] || { echo "--start is required" >&2; exit 2; }
    [ -n "$HEALTH_URL" ] || { echo "--health is required" >&2; exit 2; }

    # 1) stop (exact match only -- see trap 1 in the header)
    run_remote "pkill -x '$PROCESS' 2>/dev/null; sleep 3" >/dev/null 2>&1 || true

    # 2) start detached (see trap 2 in the header)
    env_prefix=""
    if [ -n "$ENV_FILE" ]; then
      env_prefix="set -a; . '$ENV_FILE'; set +a; "
    fi
    start_line="${env_prefix}setsid nohup ${START_CMD} </dev/null >>/tmp/${PROCESS}-bench.log 2>&1 & echo started"
    run_remote "$start_line" >/dev/null 2>&1 || true

    # 3) poll health
    i=0
    while [ "$i" -lt "$WAIT_SECONDS" ]; do
      i=$((i + 1))
      sleep 1
      code="$(health_code)"
      if [ "$code" = "200" ]; then
        echo "restarted $PROCESS; health=200 after ${i}s"
        exit 0
      fi
    done
    echo "restart of $PROCESS failed; last health=$code" >&2
    run_remote "tail -20 /tmp/${PROCESS}-bench.log 2>/dev/null | cut -c1-200" >&2 2>/dev/null || true
    exit 1
    ;;

  *) usage ;;
esac
