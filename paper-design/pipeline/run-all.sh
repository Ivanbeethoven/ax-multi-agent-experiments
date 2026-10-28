#!/usr/bin/env bash
set -euo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$DIR/common.sh"
source "$DIR/mount.sh"
source "$DIR/gates.sh"

help() {
  cat <<'HELP'
Usage: run-all.sh --config CONFIG --pin backend-pin.json --output-dir DIR [options]

CONFIG: c2 | c3 | c4 | c5 (aliases for flat-v1 | overlay-native |
        overlay-packed | juicefs), or the canonical names directly.
Required for a real run: --pin, --output-dir, --mount-point, --data-dir,
--data-disk and (for G3) --rustfs-evidence.  --dry-run only prints commands
and expected products, and never invokes brewfs, juicefs, FUSE, Docker or fio.
For a dry run, --pin and --output-dir are optional; the output directory
defaults to ./pipeline-dry-run/<config>.

Options:
  --image IMAGE             RustFS image for G0
  --brewfs BIN              BrewFS binary
  --juicefs BIN             JuiceFS binary
  --mount-point DIR         mount point
  --data-dir DIR            local data directory
  --data-disk DIR           bare ext4 disk for G2
  --owner OWNER             workspace owner (overlay)
  --manifest-key KEY        packed manifest key
  --data-config YAML        packed data config
  --init-cwd DIR            packed init cwd (must contain ./data/<manifest-key>)
  --cache-dir DIR           JuiceFS cache directory
  --bucket URL              JuiceFS object-store bucket URL
  --juicefs-name NAME       JuiceFS volume name
  --rustfs-evidence FILE    measured G3 evidence JSON
  --reps N                  repetitions (default 3)
  --dry-run
HELP
}

config='' pin='' out='' image='' mp='' data='' disk='' owner='' manifest='' data_config='' init_cwd=''
cache='' bucket='' jname='' evidence='' reps=3 dry=0 brewfs="${BREWFS_BIN:-brewfs}" juicefs="${JUICEFS_BIN:-juicefs}"
while [[ $# -gt 0 ]]; do
  case $1 in
    --config) config=${2:?missing --config value}; shift 2;; --pin) pin=${2:?missing --pin value}; shift 2;;
    --output-dir) out=${2:?missing --output-dir value}; shift 2;; --image) image=${2:?missing --image value}; shift 2;;
    --mount-point) mp=${2:?missing --mount-point value}; shift 2;; --data-dir) data=${2:?missing --data-dir value}; shift 2;;
    --data-disk) disk=${2:?missing --data-disk value}; shift 2;; --owner) owner=${2:?missing --owner value}; shift 2;;
    --manifest-key) manifest=${2:?missing --manifest-key value}; shift 2;; --data-config) data_config=${2:?missing --data-config value}; shift 2;;
    --init-cwd) init_cwd=${2:?missing --init-cwd value}; shift 2;; --cache-dir) cache=${2:?missing --cache-dir value}; shift 2;;
    --bucket) bucket=${2:?missing --bucket value}; shift 2;; --juicefs-name) jname=${2:?missing --juicefs-name value}; shift 2;;
    --rustfs-evidence) evidence=${2:?missing --rustfs-evidence value}; shift 2;; --reps) reps=${2:?missing --reps value}; shift 2;;
    --brewfs) brewfs=${2:?missing --brewfs value}; shift 2;; --juicefs) juicefs=${2:?missing --juicefs value}; shift 2;;
    --dry-run) dry=1; shift;; -h|--help) help; exit 0;; *) die "unknown option: $1";;
  esac
done
[[ "$reps" =~ ^[1-9][0-9]*$ ]] || die '--reps must be positive'
# Keep the historical names in artifacts while accepting the C2--C5 names
# used by the archived real outputs.  The mapping is deliberately explicit so
# a typo cannot silently select another backend.
case "${config,,}" in
  c2|flat-v1) config=flat-v1;;
  c3|overlay-native) config=overlay-native;;
  c4|overlay-packed) config=overlay-packed;;
  c5|juicefs) config=juicefs;;
  *) die '--config must be c2/c3/c4/c5 or flat-v1/overlay-native/overlay-packed/juicefs';;
esac
if (( dry == 0 )); then
  [[ -n "$pin" && -n "$out" ]] || die '--pin and --output-dir are required for a real run'
else
  out=${out:-"$PWD/pipeline-dry-run/$config"}
fi
assert_no_placeholders "$pin"; assert_no_placeholders "$out"; assert_no_placeholders "${image:-none}"

if (( dry )); then
  printf 'DRY RUN: no commands are executed.\n'
  printf 'expected output directory: %s\n' "$out"
  printf 'expected products: backend-pin.json, g1-output.txt, ceilings.json, ec-facts.json, w1.csv, w2.csv, w3.csv, w4.csv, run-manifest.json, summary.md, summary.json\n'
  printf 'pin refresh: git ls-remote https://github.com/Ivanbeethoven/rustfs refs/heads/feat/dynamic-block-size-rebased\n'
  printf 'G0: docker inspect -f '\''{{ index .Config.Labels "org.opencontainers.image.revision" }}'\'' %s\n' "${image:-<image required>}"
  case "$config" in
    flat-v1) format_command "$brewfs" mount --data-backend local-fs --data-dir "${data:-<DATA_DIR>}" --meta-backend redis --meta-url redis://127.0.0.1:6379/0 "${mp:-<MOUNT_POINT>}";;
    overlay-native) format_command "$brewfs" workspace init-volume --meta-backend redis --meta-url redis://127.0.0.1:6379/0 --owner "${owner:-<OWNER>}"; printf 'then parse UUID and mount --privileged --volume-format workspace-v1 --workspace <UUID> ...\n';;
    overlay-packed) format_command "$brewfs" workspace init-packed-volume --manifest-key "${manifest:-<MANIFEST_KEY>}" --data-config "${data_config:-<DATA_CONFIG>}" --meta-backend redis --meta-url redis://127.0.0.1:6379/0 --owner "${owner:-<OWNER>}"; printf 'then parse UUID and capture packed capability error if present\n';;
    juicefs) format_command "$juicefs" mount redis://127.0.0.1:6379/1 "${mp:-<MOUNT_POINT>}" -d --cache-dir="${cache:-<CACHE_DIR>}" --cache-size=8192 --writeback --no-syslog --log "${out}/juicefs.log";;
  esac
  printf 'G1: write -> sync -> read -> stat -> ls -> rename -> unlink; literal output %s/g1-output.txt\n' "$out"
  printf 'G2: fio rw=write bs=1M and rw=randread bs=4k on data disk %s, direct=1 numjobs=8 iodepth=32 runtime=20s -> %s/ceilings.json\n' "${disk:-<DATA_DISK>}" "$out"
  printf 'G3: measured header/occupancy evidence %s -> %s/ec-facts.json\n' "${evidence:-<EVIDENCE_JSON>}" "$out"
  for w in w1 w2 w3 w4; do printf 'run-%s.sh --config %s --mount-point %s --output %s/%s.csv --reps %s --dry-run\n' "$w" "$config" "${mp:-<MOUNT_POINT>}" "$out" "$w" "$reps"; done
  printf 'summarize.py %s/*.csv --output-md %s/summary.md --output-json %s/summary.json\n' "$out" "$out" "$out"
  exit 0
fi

[[ -n "$mp" && -n "$data" && -n "$disk" ]] || die 'real run requires --mount-point, --data-dir and --data-disk'
if [[ -n "$data_config" && -f "$data_config" ]] && grep -Eq 'REPLACE_WITH|REGISTRY/' "$data_config"; then
  die "deployment placeholder found in data config: $data_config"
fi
validate_backend_pin "$pin"
ensure_dir "$out"
# The required per-batch remote lookup happens before any workload starts.
cp "$pin" "$out/backend-pin.json"
batch_pin="$out/backend-pin.json"
if ! freeze_pin_check "$batch_pin" >"$out/backend-pin-remote.txt" 2>&1; then
  printf 'unmeasured(pin: see backend-pin-remote.txt)\n' >"$out/gate-failure.txt"
  exit 1
fi
if [[ -z "$image" ]]; then die 'real run requires --image for G0'; fi
if ! gate_g0 "$batch_pin" "$image" >"$out/g0.txt" 2>&1; then
  printf 'unmeasured(G0: see g0.txt)\n' >"$out/gate-failure.txt"
  exit 1
fi

mount_rc=0
case "$config" in
  flat-v1) mount_flat "$mp" "$data" "$brewfs" "$(redis_url 0)" >/dev/null || mount_rc=$?; meta_db=0; meta_url=$(redis_url 0);;
  overlay-native) [[ -n "$owner" ]] || die 'overlay-native needs --owner'; mount_overlay_native "$mp" "$data" "$owner" "$brewfs" "$(redis_url 0)" >/dev/null || mount_rc=$?; meta_db=0; meta_url=$(redis_url 0);;
  overlay-packed) [[ -n "$owner" && -n "$manifest" && -n "$data_config" ]] || die 'overlay-packed needs --owner, --manifest-key and --data-config'; mount_overlay_packed "$mp" "$data" "$manifest" "$data_config" "$owner" "$brewfs" "$(redis_url 0)" "${init_cwd:-$(dirname "$data_config")}" >/dev/null || mount_rc=$?; meta_db=0; meta_url=$(redis_url 0);;
  juicefs) [[ -n "$cache" && -n "$bucket" && -n "$jname" ]] || die 'juicefs needs --cache-dir, --bucket and --juicefs-name'; mount_juicefs "$mp" "$cache" "$bucket" "$jname" "$juicefs" "$(redis_url 1)" "$out/juicefs.log" >/dev/null || mount_rc=$?; meta_db=1; meta_url=$(redis_url 1);;
esac

write_manifest() {
  local status=$1
  python3 - "$out/run-manifest.json" "$config" "$batch_pin" "$status" "$meta_url" "$meta_db" "$MOUNT_COMMAND" "$MOUNT_STATUS" "$MOUNT_OUTPUT" "$out/ec-facts.json" <<'PY'
import json,sys,datetime
path,config,pin,status,meta_url,meta_db,mount_command,mount_status,mount_output,ec_path=sys.argv[1:]
with open(pin,encoding='utf-8') as f: pin_obj=json.load(f)
obj={'date':datetime.date.today().isoformat(),'config':config,'status':status,'meta_backend':'redis','meta_url':meta_url,'redis_db':int(meta_db),'backend_pin':pin_obj,'mount_command_original':mount_command,'mount_status':mount_status,'mount_output':mount_output}
try:
    with open(ec_path,encoding='utf-8') as f: obj['runtime_ec_facts']=json.load(f)
except OSError: pass
with open(path,'w',encoding='utf-8') as f: json.dump(obj,f,indent=2); f.write('\n')
PY
}
if (( mount_rc != 0 || MOUNT_STATUS == blocked )); then
  reason="G1 mount failed: ${MOUNT_OUTPUT:-${MOUNT_STATUS:-unknown}}"
  write_manifest "unmeasured(G1: $reason)"
  printf '%s\n' "$reason" >"$out/g1-output.txt"
  printf 'configuration unmeasured; manifest=%s\n' "$out/run-manifest.json"
  exit 0
fi
if ! gate_g1 "$mp" "$out/g1-output.txt" "$mp/.pipeline-g1-file"; then
  write_manifest 'unmeasured(G1: see g1-output.txt)'; exit 0
fi
# G2 is deliberately on the raw data disk, before W1. G3 requires actual probe evidence.
if ! gate_g2 "$disk" "$out/ceilings.json" "${FIO_BIN:-fio}" "$out" >"$out/g2-output.txt" 2>&1; then
  write_manifest 'unmeasured(G2: see g2-output.txt)'
  exit 1
fi
if [[ -z "$evidence" ]]; then
  printf '%s\n' 'missing --rustfs-evidence containing measured headers and occupancy' >"$out/g3-output.txt"
  write_manifest 'unmeasured(G3: missing evidence)'
  exit 1
fi
if ! gate_g3 "$evidence" "$out/ec-facts.json" >"$out/g3-output.txt" 2>&1; then
  write_manifest 'unmeasured(G3: see g3-output.txt)'
  exit 1
fi
for w in w1 w2 w3 w4; do
  args=("$DIR/run-$w.sh" --config "$config" --mount-point "$mp" --output "$out/$w.csv" --ceilings "$out/ceilings.json" --data-disk "$disk" --cache-dir "${cache:-$out/cache}" --corpus "${data}/pipeline-corpus" --reps "$reps")
  [[ "$config" == juicefs ]] && args+=(--juicefs "$juicefs") || args+=(--brewfs "$brewfs")
  "${args[@]}"
done
unmount_point "$mp" || warn 'unmount returned nonzero; inspect mount state'
write_manifest ok
python3 "$DIR/validate.py" "$out"/*.csv
python3 "$DIR/summarize.py" "$out"/*.csv --ceilings "$out/ceilings.json" --output-md "$out/summary.md" --output-json "$out/summary.json"
printf 'pipeline complete: %s\n' "$out"
