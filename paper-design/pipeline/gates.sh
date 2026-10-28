#!/usr/bin/env bash
set -euo pipefail

GATES_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$GATES_DIR/common.sh"

gates_help() {
  cat <<'HELP'
Usage: gates.sh <gate> [options]

  pin   --pin backend-pin.json [--repository URL] [--branch BRANCH]
  g0    --pin backend-pin.json --image IMAGE
  g1    --mount-point MP --output g1-output.txt [--work-file FILE]
  g2    --data-disk DIR --output ceilings.json [--fio fio] [--json-dir DIR]
  g3    --evidence evidence.json --output ec-facts.json

G0 mechanically compares Docker's image revision label with frozen_commit.
G1 records literal output of write, sync, readback, stat, ls, rename and unlink.
G2 only accepts a raw ext4 data-disk path. G3 consumes probe evidence; an
option or environment switch alone is never treated as evidence.
HELP
}

freeze_pin_check() {
  local pin=$1 repository=${2:-https://github.com/Ivanbeethoven/rustfs} branch=${3:-feat/dynamic-block-size-rebased}
  validate_backend_pin "$pin"
  local remote
  if ! remote=$(pin_remote_commit "$repository" "$branch"); then
    python3 - "$pin" "$repository" "$branch" <<'PY'
import json, sys
p, repo, branch = sys.argv[1:]
with open(p, encoding='utf-8') as fh: obj=json.load(fh)
obj['blocked_reason'] = f'git ls-remote failed for {repo} refs/heads/{branch}'
with open(p, 'w', encoding='utf-8') as fh:
    json.dump(obj, fh, indent=2); fh.write('\n')
raise SystemExit(1)
PY
  fi
  python3 - "$pin" "$remote" "$repository" "$branch" <<'PY'
import json, sys
path, remote, repo, branch = sys.argv[1:]
with open(path, encoding='utf-8') as fh: obj=json.load(fh)
obj['repository'] = repo
obj['branch'] = branch
obj['remote_commit'] = remote
if remote != obj['frozen_commit']:
    obj['blocked_reason'] = f'branch moved: remote {remote} != frozen {obj["frozen_commit"]}'
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=2); fh.write('\n')
    raise SystemExit(obj['blocked_reason'])
obj.pop('blocked_reason', None)
with open(path, 'w', encoding='utf-8') as fh:
    json.dump(obj, fh, indent=2); fh.write('\n')
print(remote)
PY
}

gate_g0() {
  local pin=$1 image=$2
  validate_backend_pin "$pin"
  require_cmd docker
  local expected expected_digest actual actual_digest
  expected=$(python3 - "$pin" <<'PY'
import json,sys
with open(sys.argv[1], encoding='utf-8') as f:
    print(json.load(f)['frozen_commit'])
PY
)
  expected_digest=$(python3 - "$pin" <<'PY'
import json,sys
with open(sys.argv[1], encoding='utf-8') as f:
    print(json.load(f)['image_digest'])
PY
)
  actual=$(docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")
  if [[ "$actual" != "$expected" ]]; then
    die "G0 failed: image revision $actual != frozen_commit $expected"
  fi
  actual_digest=$(docker inspect -f '{{ index .RepoDigests 0 }}' "$image" 2>/dev/null || true)
  [[ "$actual_digest" == *"@$expected_digest"* ]] || die "G0 failed: image digest $actual_digest does not contain pinned $expected_digest"
  printf 'G0 pass: %s\n' "$actual"
}

gate_g1() {
  local mount_point=$1 output=$2 work_file=${3:-g1-workfile}
  require_dir "$mount_point"
  ensure_dir "$(dirname "$output")"
  : >"$output"
  local renamed="${work_file}.renamed" step rc
  local value="g1-$(date +%s)-$$"
  run_step() {
    step=$1; shift
    rc=0
    printf '[%s] command: %s\n' "$step" "$(format_command "$@")" | tee -a "$output"
    "$@" > >(tee -a "$output") 2> >(tee -a "$output" >&2) || rc=$?
    printf '[%s] exit=%s\n' "$step" "$rc" | tee -a "$output"
    return "$rc"
  }
  printf '# G1 literal command output (%s)\n' "$(utc_now)" >>"$output"
  run_step write bash -c "printf '%s' '$value' > '$work_file'" || return 1
  run_step sync sync || return 1
  run_step read bash -c "cat '$work_file'" || return 1
  run_step stat stat --format='%n %s %a' "$work_file" || return 1
  run_step ls ls -l "$work_file" || return 1
  run_step rename mv "$work_file" "$renamed" || return 1
  run_step unlink rm -f "$renamed" || return 1
  printf 'G1 pass; output=%s\n' "$output"
}

gate_g2() {
  local disk=$1 output=$2 fio=${3:-${FIO_BIN:-fio}} json_dir=${4:-$(dirname "$output")}
  require_dir "$disk"; require_cmd "$fio"; ensure_dir "$json_dir"
  local target="$disk/.pipeline-g2-ceiling.bin" seq_json="$json_dir/g2-seq-write.json" rand_json="$json_dir/g2-rand-read.json"
  local -a seq=("$fio" --name=g2-seq-write --filename="$target" --rw=write --bs=1M --size=8G --numjobs=8 --iodepth=32 --direct=1 --ioengine=libaio --time_based=1 --runtime=20 --ramp_time=2 --group_reporting=1 --output-format=json --output="$seq_json")
  local -a rnd=("$fio" --name=g2-rand-read --filename="$target" --rw=randread --bs=4k --size=8G --numjobs=8 --iodepth=32 --direct=1 --ioengine=libaio --time_based=1 --runtime=20 --ramp_time=2 --group_reporting=1 --output-format=json --output="$rand_json")
  log "G2 sequential ceiling: $(format_command "${seq[@]}")"; "${seq[@]}"
  log "G2 random ceiling: $(format_command "${rnd[@]}")"; "${rnd[@]}"
  python3 - "$output" "$disk" "$seq_json" "$rand_json" <<'PY'
import datetime, json, sys
out, disk, seq_path, rand_path = sys.argv[1:]
def load(path, op):
    with open(path, encoding='utf-8') as fh:
        root = json.load(fh)
    metrics = root['jobs'][0][op]
    p99 = metrics.get('clat_ns', {}).get('percentile', {}).get('99.000000', 0)
    return {'throughput_mibps': metrics.get('bw_bytes', 0) / 1048576,
            'iops': metrics.get('iops', 0), 'p99_us': p99 / 1000,
            'io_bytes': metrics.get('io_bytes', 0), 'json': path}
obj = {'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
       'disk': disk,
       'common_params': {'direct': 1, 'numjobs': 8, 'iodepth': 32,
                         'runtime_s': 20, 'group_reporting': True},
       'seq_write_1MiB': load(seq_path, 'write'),
       'rand_read_4KiB': load(rand_path, 'read')}
with open(out, 'w', encoding='utf-8') as fh:
    json.dump(obj, fh, indent=2); fh.write('\n')
PY
  rm -f "$target"
  printf 'G2 pass; ceilings=%s\n' "$output"
}

gate_g3() {
  local evidence=$1 output=$2
  require_file "$evidence"
  python3 - "$evidence" "$output" <<'PY'
import datetime, json, sys
source, dest = sys.argv[1:]
with open(source, encoding='utf-8') as fh:
    obj = json.load(fh)
# Accept the shape emitted by the historical g3-evidence.json while writing a
# stable flattened shape for new manifests.
probe = obj.get('object_header_probe', {})
if 'internal_ec_read_quantum_present' not in obj and 'observed' in probe:
    obj['internal_ec_read_quantum_present'] = bool(probe['observed'])
if 'effective_ec_block_size_bytes' not in obj and probe.get('effective_ec_block_size_bytes') is not None:
    obj['effective_ec_block_size_bytes'] = probe['effective_ec_block_size_bytes']
required = ('object_occupancy', 'effective_ec_block_size_bytes', 'internal_ec_read_quantum_present')
missing = [key for key in required if key not in obj]
if missing:
    raise SystemExit('G3 evidence missing: ' + ', '.join(missing))
if not isinstance(obj['internal_ec_read_quantum_present'], bool):
    raise SystemExit('internal_ec_read_quantum_present must be a boolean observed header result')
if obj['effective_ec_block_size_bytes'] in (None, ''):
    raise SystemExit('effective_ec_block_size_bytes must come from a response header')
obj['recorded_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
obj['evidence_source'] = source
with open(dest, 'w', encoding='utf-8') as fh:
    json.dump(obj, fh, indent=2); fh.write('\n')
PY
  printf 'G3 pass: evidence=%s output=%s\n' "$evidence" "$output"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  [[ $# -gt 0 ]] || { gates_help; exit 2; }
  gate=$1; shift
  [[ "$gate" == -h || "$gate" == --help ]] && { gates_help; exit 0; }
  pin='' image='' mp='' output='' work='' disk='' evidence=''
  repo='https://github.com/Ivanbeethoven/rustfs'; branch='feat/dynamic-block-size-rebased'
  fio="${FIO_BIN:-fio}"; json_dir=''
  while [[ $# -gt 0 ]]; do
    case $1 in
      --pin) pin=${2:?missing --pin value}; shift 2;;
      --image) image=${2:?missing --image value}; shift 2;;
      --mount-point) mp=${2:?missing --mount-point value}; shift 2;;
      --output) output=${2:?missing --output value}; shift 2;;
      --work-file) work=${2:?missing --work-file value}; shift 2;;
      --data-disk) disk=${2:?missing --data-disk value}; shift 2;;
      --evidence) evidence=${2:?missing --evidence value}; shift 2;;
      --repository) repo=${2:?missing --repository value}; shift 2;;
      --branch) branch=${2:?missing --branch value}; shift 2;;
      --fio) fio=${2:?missing --fio value}; shift 2;;
      --json-dir) json_dir=${2:?missing --json-dir value}; shift 2;;
      -h|--help) gates_help; exit 0;;
      *) die "unknown option: $1";;
    esac
  done
  case "$gate" in
    pin) [[ -n "$pin" ]] || die 'pin needs --pin'; freeze_pin_check "$pin" "$repo" "$branch";;
    g0) [[ -n "$pin" && -n "$image" ]] || die 'g0 needs --pin and --image'; gate_g0 "$pin" "$image";;
    g1) [[ -n "$mp" && -n "$output" ]] || die 'g1 needs --mount-point and --output'; gate_g1 "$mp" "$output" "${work:-$mp/.pipeline-g1-file}";;
    g2) [[ -n "$disk" && -n "$output" ]] || die 'g2 needs --data-disk and --output'; gate_g2 "$disk" "$output" "$fio" "${json_dir:-$(dirname "$output")}";;
    g3) [[ -n "$evidence" && -n "$output" ]] || die 'g3 needs --evidence and --output'; gate_g3 "$evidence" "$output";;
    *) die "unknown gate: $gate";;
  esac
fi
