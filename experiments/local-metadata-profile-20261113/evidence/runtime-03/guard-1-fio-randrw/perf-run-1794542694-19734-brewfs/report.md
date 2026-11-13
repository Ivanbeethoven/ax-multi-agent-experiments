# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 21 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 33 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 1 | 4m | 4 | 0.00 B/s | 0.00 | 1.03 GiB/s | 264.60 | 0.000 ms | 0.010 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 1 | 4m | 4 | 416.84 MiB/s | 104.21 | 186.92 MiB/s | 46.73 | 0.010 ms | 0.012 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 1 | 0 s | 7.617 s | +0.000 s | - | 1.935 s | +0.000 s | - | - |
| fio-randrw | 1 | 21 s | 82.847 s | -61.847 s | 0.25x | 20.929 s | +0.071 s | 1.00x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.929 s | 33.000 s | 53.929 s | 161.77 MiB/s | 72.54 MiB/s | 234.31 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 0.003 ms | 0.000 ms | 0.000 ms | 0.005 ms | 0.010 ms | 0.048 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 0.003 ms | 0.000 ms | 0.000 ms | 0.004 ms | 0.010 ms | 0.052 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 0.004 ms | 0.000 ms | 0.000 ms | 0.006 ms | 0.012 ms | 0.093 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=815, usec/call=319.86<br>exec: calls=1440, usec/call=5.65<br>get: calls=2739, usec/call=1.76<br>rpush: calls=796, usec/call=4.34<br>incrby: calls=796, usec/call=3.71 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 69.7% (4622/6629) | 8724.0 MiB | 3912.0 MiB | 3048.0 MiB | 0.0 MiB | 3048.0 MiB | 0.0 MiB | 0.0 MiB | GET=1052, PUT=217 | GET=11.20 ms, PUT=535.14 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=1052, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=796 ops/3912.0 MiB/308328.8 ms, foreground=stage 308.33s/commit_wait 28.02s, flush_wait=4 ops/0.14s/3 slices, stage_fail=0, commit_before_stage=678, remote_inflight=3048.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=11, max_buffer_dirty=3103.0 MiB, max_live_dirty=590.0 MiB, max_live_slices=111, max_recent_pending=2980.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=100.0 MiB, max_remote_inflight=2984.0 MiB, last_buffer_dirty=3103.0 MiB, slices=create 796/reuse 3116/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=796 avg=4.9 MiB blocks=1.23/batch partial_tail=0.01 (size 0/max 0/flush 2/auto 2/age 0), partial_origin=normal 4/cached 0/mixed 0/unknown 0, auto_detail=age 2/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 2/cached 0/mixed 0/unknown 0, freeze=size 64/flush 3/auto 655/max 74/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=760 | GET=11.20 ms, PUT=212.81 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 796/reuse 3116/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 64/flush 3/auto 655/max 74/age 0 |
| fio-randrw-post-write-runner-drained | 69.7% (4622/6629) | 8724.0 MiB | 3912.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=1052, PUT=980 | GET=11.20 ms, PUT=284.20 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=1052, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=796 ops/3912.0 MiB/308328.8 ms, foreground=stage 308.33s/commit_wait 28.02s, flush_wait=4 ops/0.14s/3 slices, stage_fail=0, commit_before_stage=678, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 796/reuse 3116/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=796 avg=4.9 MiB blocks=1.23/batch partial_tail=0.01 (size 0/max 0/flush 2/auto 2/age 0), partial_origin=normal 4/cached 0/mixed 0/unknown 0, auto_detail=age 2/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 2/cached 0/mixed 0/unknown 0, freeze=size 64/flush 3/auto 655/max 74/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1920.0 MiB | 0.0 MiB | 1920.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=42 | GET=0.00 ms, PUT=262.45 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=32 ops/2048.0 MiB/17731.5 ms, foreground=stage 17.73s/commit_wait 0.00s, flush_wait=4 ops/0.29s/4 slices, stage_fail=0, commit_before_stage=32, remote_inflight=1920.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=2, max_buffer_dirty=988.0 MiB, max_live_dirty=284.0 MiB, max_live_slices=8, max_recent_pending=704.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=768.0 MiB, max_remote_inflight=960.0 MiB, last_buffer_dirty=988.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=32 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=470 | GET=0.00 ms, PUT=207.71 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=211.84 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/17744.1 ms, foreground=stage 17.74s/commit_wait 0.00s, flush_wait=5 ops/0.30s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
