#!/usr/bin/env bash
set -euo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$DIR/workload-lib.sh"
if [[ $# -eq 0 ]]; then wl_help; exit 2; fi
wl_parse_args "$@"
if (( WL_DRY == 0 )); then require_dir "$WL_MOUNT"; fi
sizes=(4096 65536 1048576)
keep_values=(false true)
[[ "$WL_CONFIG" == juicefs ]] && keep_values=(none)
for keep in "${keep_values[@]}"; do
  if (( WL_DRY )); then
    if [[ "$keep" == true ]]; then wl_print "W3 remount before group: mount.sh ... --fuse-keep-cache"; elif [[ "$keep" == false ]]; then wl_print "W3 remount before group: mount.sh ... --no-fuse-keep-cache"; else wl_print 'W3 JuiceFS: no equivalent --fuse-keep-cache switch'; fi
  fi
  for size in "${sizes[@]}"; do
    params="keep_cache=${keep},size=${size},rounds=200,open-read-close-open-read-close"
    for rep in $(seq 1 "$WL_REPS"); do
      out_json="${WL_OUTPUT%.csv}-w3-${keep}-${size}-rep${rep}.json"
      if (( WL_DRY )); then
        wl_print "W3 keep_cache=$keep size=$size rep=$rep: python3 open→read→close→open→read→close 200 rounds; output=$out_json"
        continue
      fi
      python3 - "$WL_MOUNT" "$size" "$out_json" <<'PY'
import json, os, sys, time
mount, size, out = sys.argv[1:]
path=os.path.join(mount, '.pipeline-w3-file')
with open(path,'wb') as f: f.truncate(int(size))
first=second=0
start=time.perf_counter_ns()
for _ in range(200):
    with open(path,'rb') as f: first += len(f.read(int(size)))
    with open(path,'rb') as f: second += len(f.read(int(size)))
elapsed=time.perf_counter_ns()-start
obj={'first_read_bytes':first,'second_read_bytes':second,'elapsed_ns':elapsed,'rounds':200,'size':int(size)}
with open(out,'w',encoding='utf-8') as f: json.dump(obj,f,indent=2); f.write('\n')
os.unlink(path)
PY
      python3 - "$out_json" "$WL_CONFIG" "$keep" "$size" "$rep" "$params" "$WL_OUTPUT" <<'PY'
import csv,json,sys
j,config,keep,size,rep,params,out=sys.argv[1:]
with open(j,encoding='utf-8') as f: x=json.load(f)
second=x['second_read_bytes']; elapsed=x['elapsed_ns']/1e9
# Criterion is explicitly the second read's byte count, never a first-read proxy.
throughput=(second/1048576)/elapsed if elapsed else 0
iops=200*2/elapsed if elapsed else 0
note=f"second_read_bytes={second};first_read_bytes={x['first_read_bytes']};criterion=second_read_bytes"
if config=='juicefs': note+=';equivalent_keep_cache=none'
row=[f'w3-keep-{keep}-size-{size}',config,'open-read-open-read',params,rep,f'{throughput:.6f}',f'{iops:.6f}','',str(second),'ok',note]
with open(out,'a',newline='',encoding='utf-8') as f: csv.writer(f).writerow(row)
PY
    done
  done
done
