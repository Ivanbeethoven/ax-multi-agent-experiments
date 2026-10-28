#!/usr/bin/env bash
set -euo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$DIR/workload-lib.sh"
if [[ $# -eq 0 ]]; then wl_help; exit 2; fi
wl_parse_args "$@"
if (( WL_DRY == 0 )); then require_dir "$WL_MOUNT"; fi
for ttl in 0 1000; do
  for n in 1000 10000 100000; do
    params="ttl_ms=${ttl},inodes=${n},operations=100000"
    for rep in $(seq 1 "$WL_REPS"); do
      out_json="${WL_OUTPUT%.csv}-w4-ttl${ttl}-n${n}-rep${rep}.json"
      if (( WL_DRY )); then
        wl_print "W4 ttl=$ttl inodes=$n rep=$rep: stat 100000 paths; read .stats attr_valid_ns/entry_valid_ns and row-cache hit/miss; output=$out_json"
        continue
      fi
      python3 - "$WL_MOUNT" "$n" "$ttl" "$out_json" <<'PY'
import json, os, sys, time
mount,n,ttl,out=sys.argv[1:]; n=int(n); ttl=int(ttl)
paths=[os.path.join(mount, f'.pipeline-w4-{i % n}') for i in range(n)]
for p in paths:
    open(p,'ab').close()
start=time.perf_counter_ns(); total=0
for i in range(100000): total += os.stat(paths[i % n]).st_size
elapsed=time.perf_counter_ns()-start
iops=100000/(elapsed/1e9) if elapsed else 0
obj={'operations':100000,'inodes':n,'ttl_ms':ttl,'elapsed_ns':elapsed,'iops':iops,'fuse_getattr_ops':100000,'attr_valid_ns':ttl*1000000,'entry_valid_ns':ttl*1000000,'row_cache_hit':None,'row_cache_miss':None}
stats_path=os.path.join(mount,'.stats')
if os.path.isfile(stats_path):
    try:
        with open(stats_path,encoding='utf-8') as sf: observed=json.load(sf)
        for key in ('fuse_getattr_ops','attr_valid_ns','entry_valid_ns','row_cache_hit','row_cache_miss','line_cache_hit','line_cache_miss'):
            if key in observed: obj[key]=observed[key]
    except (OSError, ValueError):
        obj['stats_read_error']='invalid .stats JSON'
with open(out,'w',encoding='utf-8') as f: json.dump(obj,f,indent=2); f.write('\n')
for p in paths:
    try: os.unlink(p)
    except FileNotFoundError: pass
PY
      python3 - "$out_json" "$WL_CONFIG" "$params" "$rep" "$WL_OUTPUT" <<'PY'
import csv,json,sys
j,config,params,rep,out=sys.argv[1:]
with open(j,encoding='utf-8') as f: x=json.load(f)
iops=float(x['iops']); status='ok'; note=(f"fuse_getattr_ops={x['fuse_getattr_ops']};attr_valid_ns={x['attr_valid_ns']};entry_valid_ns={x['entry_valid_ns']};" f"row_cache_hit={x['row_cache_hit']};row_cache_miss={x['row_cache_miss']};line_cache_hit={x.get('line_cache_hit','NA')};line_cache_miss={x.get('line_cache_miss','NA')}")
if iops > 100000:
    status='void(kernel-cache)'; note+=';kernel-cache threshold=100000 iops'
row=[f"w4-ttl-{x['ttl_ms']}-n-{x['inodes']}",config,'stat',params,rep,'',f'{iops:.6f}','', '0',status,note]
with open(out,'a',newline='',encoding='utf-8') as f: csv.writer(f).writerow(row)
PY
    done
  done
done
