#!/usr/bin/env bash
set -euo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$DIR/workload-lib.sh"

if [[ $# -eq 0 ]]; then wl_help; exit 2; fi
wl_parse_args "$@"
if [[ -z "$WL_CEILINGS" && "$WL_DRY" -eq 0 ]]; then die 'W1 requires --ceilings (G2 output)'; fi
params_write='bs=1M,numjobs=8,iodepth=32,direct=1,runtime=20s'
params_rand='bs=4K,numjobs=8,iodepth=32,direct=1,runtime=20s'
for rep in $(seq 1 "$WL_REPS"); do
  wl_fio_cell w1-write write 1M "$rep" "$params_write"
  wl_fio_cell w1-read read 1M "$rep" "$params_write"
  wl_fio_cell w1-randwrite randwrite 4k "$rep" "$params_rand"
  wl_fio_cell w1-randread randread 4k "$rep" "$params_rand"
done
if (( WL_DRY == 0 )); then log "W1 output: $WL_OUTPUT (read_bytes is taken from each fio JSON increment)"; fi
