#!/usr/bin/env bash
set -euo pipefail
LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$LIB_DIR/common.sh"

wl_help() {
  cat <<'HELP'
Options shared by run-w1.sh ... run-w4.sh:
  --config NAME --mount-point DIR --output CSV
  --ceilings FILE --data-disk DIR --cache-dir DIR --corpus DIR
  --reps N --dry-run
  --brewfs BIN --juicefs BIN
HELP
}

wl_parse_args() {
  WL_CONFIG='' WL_MOUNT='' WL_OUTPUT='' WL_CEILINGS='' WL_DISK='' WL_CACHE='' WL_CORPUS=''
  WL_REPS=3 WL_DRY=0 WL_BREWFS="${BREWFS_BIN:-brewfs}" WL_JUICEFS="${JUICEFS_BIN:-juicefs}"
  while [[ $# -gt 0 ]]; do
    case $1 in
      --config) WL_CONFIG=${2:?missing --config value}; shift 2;;
      --mount-point) WL_MOUNT=${2:?missing --mount-point value}; shift 2;;
      --output) WL_OUTPUT=${2:?missing --output value}; shift 2;;
      --ceilings) WL_CEILINGS=${2:?missing --ceilings value}; shift 2;;
      --data-disk) WL_DISK=${2:?missing --data-disk value}; shift 2;;
      --cache-dir) WL_CACHE=${2:?missing --cache-dir value}; shift 2;;
      --corpus) WL_CORPUS=${2:?missing --corpus value}; shift 2;;
      --reps) WL_REPS=${2:?missing --reps value}; shift 2;;
      --brewfs) WL_BREWFS=${2:?missing --brewfs value}; shift 2;;
      --juicefs) WL_JUICEFS=${2:?missing --juicefs value}; shift 2;;
      --dry-run) WL_DRY=1; shift;;
      -h|--help) wl_help; exit 0;;
      *) die "unknown option: $1";;
    esac
  done
  [[ "$WL_REPS" =~ ^[1-9][0-9]*$ ]] || die '--reps must be a positive integer'
  [[ -n "$WL_CONFIG" ]] || die '--config is required'
  [[ "$WL_CONFIG" =~ ^(flat-v1|overlay-native|overlay-packed|juicefs|C[0-9A-Za-z_-]+)$ ]] || die "unknown config: $WL_CONFIG"
  if (( WL_DRY == 0 )); then
    [[ -n "$WL_MOUNT" && -n "$WL_OUTPUT" ]] || die '--mount-point and --output are required'
    require_dir "$WL_MOUNT"
    write_csv_header "$WL_OUTPUT"
  fi
}

wl_print() { printf '[dry-run] %s\n' "$*"; }
wl_run() {
  local label=$1; shift
  if (( WL_DRY )); then wl_print "$label: $(format_command "$@")"; return 0; fi
  log "$label: $(format_command "$@")"
  "$@"
}

wl_fio_cell() {
  local cell=$1 rw=$2 bs=$3 rep=$4 params=$5 direct=${6:-1}
  local json="${WL_OUTPUT%.csv}-${cell}-rep${rep}.json"
  local -a cmd=("${FIO_BIN:-fio}" --name="$cell" --filename="$WL_MOUNT/.pipeline-$cell-$rep.bin" --rw="$rw" --bs="$bs" --size=8G --numjobs=8 --iodepth=32 --direct="$direct" --ioengine=libaio --time_based=1 --runtime=20 --group_reporting=1 --output-format=json --output="$json")
  if (( WL_DRY )); then wl_print "$cell rep=$rep: $(format_command "${cmd[@]}")"; return 0; fi
  "${cmd[@]}"
  python3 - "$json" "$cell" "$WL_CONFIG" "$rw" "$params" "$rep" "$WL_CEILINGS" "$WL_OUTPUT" <<'PY'
import csv, json, os, re, sys
path, cell, config, workload, params, rep, ceilings_path, out = sys.argv[1:]
with open(path, encoding='utf-8') as fh: root=json.load(fh)
job=root['jobs'][0]
section=job['read'] if workload in ('read','randread') else job['write']
metrics=section
p99=metrics.get('clat_ns',{}).get('percentile',{}).get('99.000000','')
read_bytes=metrics.get('read_bytes', metrics.get('io_bytes',0)) if workload in ('read','randread') else metrics.get('io_bytes',0)
throughput=metrics.get('bw_bytes',0)/1048576
status='ok'; note='fio_json='+path
ceilings={}
if ceilings_path and os.path.exists(ceilings_path):
    with open(ceilings_path, encoding='utf-8') as fh: c=json.load(fh)
    # Accept both the current gate output and historical ceilings.json files.
    if workload=='write': item=c.get('seq_write_1MiB')
    elif workload=='randread': item=c.get('rand_read_4KiB')
    else: item=None
    if item is None:
        for x in c.get('workloads',[]):
            if x.get('workload')==workload: item=x; break
    if item: ceilings=item
limit=ceilings.get('throughput_mibps')
if limit is None and ceilings.get('bw_bytes') is not None: limit=ceilings['bw_bytes']/1048576
if limit is not None and throughput > float(limit):
    status='void(cache-hit)'; note += f';throughput_mibps={throughput:.6f}>g2_ceiling={float(limit):.6f}'
row=[cell,config,workload,params,rep,f'{throughput:.6f}',f"{metrics.get('iops',0):.6f}",f"{float(p99)/1000:.3f}" if p99!='' else '',str(read_bytes),status,note]
with open(out,'a',newline='',encoding='utf-8') as fh: csv.writer(fh).writerow(row)
PY
}

wl_unmount_remount() {
  local label=$1
  if (( WL_DRY )); then wl_print "$label: sync; drop_caches; remount $WL_MOUNT"; return 0; fi
  sync
  drop_linux_caches || true
  warn "remount is delegated to run-all/mount.sh for $label; workload continues on current mount"
}
