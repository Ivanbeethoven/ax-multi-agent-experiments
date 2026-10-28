#!/usr/bin/env bash
set -euo pipefail

# Shared shell helpers.  Workload scripts source this file; it is also safe to
# execute directly and will only print its help text.

PIPELINE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PIPELINE_DIR

die() { printf 'pipeline: error: %s\n' "$*" >&2; return 1; }
warn() { printf 'pipeline: warning: %s\n' "$*" >&2; }
log() { printf '[pipeline] %s\n' "$*" >&2; }

usage_common() {
  cat <<'EOF'
Common environment variables:
  BREWFS_BIN       brewfs executable (default: brewfs)
  JUICEFS_BIN      juicefs executable (default: juicefs)
  FIO_BIN          fio executable (default: fio)
  REDIS_HOST       Redis host (default: 127.0.0.1)
  RUSTFS_IMAGE     image inspected by G0
  BACKEND_PIN      batch backend-pin.json path
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_file() { [[ -f "$1" ]] || die "file not found: $1"; }
require_dir() { [[ -d "$1" ]] || die "directory not found: $1"; }

ensure_dir() { mkdir -p "$1"; }

utc_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

redis_url() {
  local db=${1:?Redis database number is required}
  printf 'redis://%s:6379/%s' "${REDIS_HOST:-127.0.0.1}" "$db"
}

assert_no_placeholders() {
  local value=${1:-}
  case "$value" in
    *REPLACE_WITH*|*REGISTRY/*) die "deployment placeholder found: $value";;
  esac
}

json_required_fields() {
  local json_file=$1; shift
  require_file "$json_file"
  python3 - "$json_file" "$@" <<'PY'
import json
import sys
path, *fields = sys.argv[1:]
with open(path, encoding="utf-8") as fh:
    obj = json.load(fh)
missing = [f for f in fields if f not in obj or obj[f] in (None, "")]
if missing:
    raise SystemExit("missing required JSON fields: " + ", ".join(missing))
PY
}

validate_backend_pin() {
  local pin=${1:?backend-pin.json path is required}
  json_required_fields "$pin" repository branch frozen_commit image_reference image_digest built_from_commit resolved_at brewfs_client_sha resolved_by
  python3 - "$pin" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    p = json.load(fh)
for key in ("frozen_commit", "built_from_commit"):
    value = p[key]
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", str(value)):
        raise SystemExit(f"{key} is not a commit SHA: {value!r}")
if p["frozen_commit"] != p["built_from_commit"]:
    raise SystemExit("frozen_commit and built_from_commit differ")
if p.get("repository") != "https://github.com/Ivanbeethoven/rustfs":
    raise SystemExit("repository must be https://github.com/Ivanbeethoven/rustfs")
if p.get("branch") != "feat/dynamic-block-size-rebased":
    raise SystemExit("branch must be feat/dynamic-block-size-rebased")
for key in ("image_reference", "image_digest"):
    if any(token in str(p[key]) for token in ("REPLACE_WITH", "REGISTRY/")):
        raise SystemExit(f"placeholder in {key}")
if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", str(p["image_digest"])):
    raise SystemExit("image_digest must be an immutable sha256 digest")
if p.get("blocked_reason"):
    raise SystemExit("backend pin is blocked: " + str(p["blocked_reason"]))
PY
}

pin_remote_commit() {
  local repository=${1:-https://github.com/Ivanbeethoven/rustfs}
  local branch=${2:-feat/dynamic-block-size-rebased}
  require_cmd git
  git ls-remote "$repository" "refs/heads/$branch" | awk 'NF {print $1; found=1} END {if (!found) exit 1}'
}

write_csv_header() {
  local output=$1
  mkdir -p "$(dirname "$output")"
  if [[ ! -e "$output" ]]; then
    printf 'cell_id,config,workload,params,rep,throughput_mibps,iops,p99_us,read_bytes,status,note\n' >"$output"
  fi
}

append_csv_row() {
  # The caller passes exactly eleven fields.  Python's csv writer is used so
  # params and notes containing commas remain valid CSV.
  [[ $# -eq 12 ]] || die "append_csv_row expects output plus 11 fields"
  local output=$1; shift
  python3 - "$output" "$@" <<'PY'
import csv, sys
path, *fields = sys.argv[1:]
with open(path, "a", newline="", encoding="utf-8") as fh:
    csv.writer(fh).writerow(fields)
PY
}

csv_status_ok() { [[ "$1" == ok || "$1" == void\(*\) || "$1" == unmeasured\(*\) ]]; }

drop_linux_caches() {
  sync
  if [[ -w /proc/sys/vm/drop_caches ]]; then
    printf 3 >/proc/sys/vm/drop_caches
  else
    warn 'cannot write /proc/sys/vm/drop_caches; cold run is not reproducible'
    return 1
  fi
}

format_command() {
  printf '%q ' "$@"
  printf '\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  usage_common
fi
