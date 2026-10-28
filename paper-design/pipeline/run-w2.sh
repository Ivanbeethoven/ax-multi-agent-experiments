#!/usr/bin/env bash
set -euo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$DIR/workload-lib.sh"
if [[ $# -eq 0 ]]; then wl_help; exit 2; fi
wl_parse_args "$@"
[[ -n "$WL_CORPUS" ]] || die 'W2 requires --corpus (8 GiB logical corpus)'
if (( WL_DRY == 0 )); then require_dir "$WL_CORPUS"; fi
params_read='temperature=%s,corpus=8GiB,cache_budget=8192MiB,bs=1M,numjobs=8,iodepth=32,direct=0,runtime=20s'
params_rand='temperature=%s,corpus=8GiB,cache_budget=8192MiB,bs=4K,numjobs=8,iodepth=32,direct=0,runtime=20s'
for rw_bs in 'read 1M' 'randread 4k'; do
  read -r rw bs <<<"$rw_bs"
  for temp in cold hot; do
    if [[ "$temp" == cold ]]; then
      # JuiceFS has a private cache; drop it as well as kernel page cache.
      if [[ "$WL_CONFIG" == juicefs ]]; then
        if (( WL_DRY )); then wl_print "JuiceFS cold reset: rm -rf ${WL_CACHE:?}/*; recreate cache-dir"; else [[ -n "$WL_CACHE" ]] || die 'JuiceFS W2 requires --cache-dir'; find "$WL_CACHE" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null || true; fi
      fi
      wl_unmount_remount 'W2 cold (drop_caches + remount)'
    else
      if (( WL_DRY )); then wl_print 'W2 hot prewarm: read corpus once, then time the second pass'; else log "W2 hot prewarm: $WL_CORPUS"; fi
    fi
    if [[ "$bs" == 1M ]]; then params=$(printf "$params_read" "$temp"); else params=$(printf "$params_rand" "$temp"); fi
    for rep in $(seq 1 "$WL_REPS"); do wl_fio_cell "w2-${rw}-${temp}" "$rw" "$bs" "$rep" "$params" 0; done
  done
done
if (( WL_DRY == 0 )); then
  # The generated CSV is checked here so a nonzero hot read is visible.  The
  # paired cold/hot ratio (<2x) is marked void by the post-run summarizer.
python3 - "$WL_OUTPUT" <<'PY'
import csv, sys
path=sys.argv[1]
with open(path, newline='', encoding='utf-8') as f: rows=list(csv.DictReader(f))
groups={}
for r in rows: groups.setdefault((r['workload'], r['rep']), {})['hot' if '-hot' in r['cell_id'] else 'cold']=r
for pair in groups.values():
    cold, hot = pair.get('cold'), pair.get('hot')
    if not cold or not hot: continue
    try: ratio=float(hot['throughput_mibps'])/float(cold['throughput_mibps'])
    except (ValueError, ZeroDivisionError): ratio=0
    for r in (cold, hot): r['note'] += f';hot_cold_ratio={ratio:.6f}'
    if ratio < 2:
        for r in (cold, hot): r['status']='void(heat/cold <2x)'
    if hot['read_bytes'] not in ('','0'):
        try: hot_bytes=float(hot['read_bytes'])
        except ValueError: hot_bytes=1
        if hot_bytes > 4096:
            hot['status']='void(hot read_bytes not approximately zero)'
            hot['note'] += ';hot_read_bytes_expected~0'
with open(path,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f, fieldnames=['cell_id','config','workload','params','rep','throughput_mibps','iops','p99_us','read_bytes','status','note']); w.writeheader(); w.writerows(rows)
PY
fi
