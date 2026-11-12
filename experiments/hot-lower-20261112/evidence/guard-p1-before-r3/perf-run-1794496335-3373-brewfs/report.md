# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 21 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 26 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 909.82 MiB/s | 227.45 | 0.000 ms | 27.656 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 357.86 MiB/s | 89.47 | 164.03 MiB/s | 41.01 | 333.447 ms | 170.918 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 8.954 s | +0.000 s | - | 2.251 s | +0.000 s | - | - |
| fio-randrw | 0 | 21 s | 81.572 s | -60.572 s | 0.26x | 20.533 s | +0.467 s | 1.02x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.533 s | 26.000 s | 46.533 s | 157.91 MiB/s | 72.38 MiB/s | 230.29 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 15.794 ms | 0.000 ms | 0.000 ms | 130.548 ms | 333.447 ms | 650.117 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 17.433 ms | 0.000 ms | 0.000 ms | 25.035 ms | 27.656 ms | 39.059 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 8.028 ms | 0.000 ms | 0.000 ms | 70.779 ms | 170.918 ms | 541.065 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=747, usec/call=67.15<br>exec: calls=1805, usec/call=5.47<br>get: calls=3768, usec/call=1.74<br>rpush: calls=725, usec/call=7.18<br>incrby: calls=725, usec/call=5.31 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 85.3% (34203/40111) | 7348.0 MiB | 3368.0 MiB | 2152.0 MiB | 0.0 MiB | 2152.0 MiB | 0.0 MiB | 0.0 MiB | GET=1802, PUT=307 | GET=23.01 ms, PUT=363.38 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=1802, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=725 ops/3368.0 MiB/47418.5 ms, foreground=stage 47.42s/commit_wait 8.57s, flush_wait=4 ops/0.71s/16 slices, stage_fail=0, commit_before_stage=632, remote_inflight=2152.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=14, max_buffer_dirty=2200.0 MiB, max_live_dirty=336.0 MiB, max_live_slices=71, max_recent_pending=2160.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=80.0 MiB, max_remote_inflight=2164.0 MiB, last_buffer_dirty=2172.0 MiB, slices=create 725/reuse 2643/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=725 avg=4.6 MiB blocks=1.16/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 56/flush 14/auto 649/max 6/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=535 | GET=23.01 ms, PUT=227.06 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 725/reuse 2643/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 56/flush 14/auto 649/max 6/age 0 |
| fio-randrw-post-write-runner-drained | 85.3% (34203/40111) | 7348.0 MiB | 3368.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=1802, PUT=842 | GET=23.01 ms, PUT=276.76 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=1802, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=725 ops/3368.0 MiB/47418.5 ms, foreground=stage 47.42s/commit_wait 8.57s, flush_wait=4 ops/0.71s/16 slices, stage_fail=0, commit_before_stage=632, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 725/reuse 2643/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=725 avg=4.6 MiB blocks=1.16/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 56/flush 14/auto 649/max 6/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1792.0 MiB | 0.0 MiB | 1792.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=64 | GET=0.00 ms, PUT=187.51 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=32 ops/2048.0 MiB/19078.5 ms, foreground=stage 19.08s/commit_wait 0.00s, flush_wait=4 ops/0.30s/4 slices, stage_fail=0, commit_before_stage=32, remote_inflight=1792.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=2, max_buffer_dirty=906.0 MiB, max_live_dirty=202.0 MiB, max_live_slices=4, max_recent_pending=704.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=512.0 MiB, max_remote_inflight=704.0 MiB, last_buffer_dirty=906.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=32 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=448 | GET=0.00 ms, PUT=210.85 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=207.58 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/19092.2 ms, foreground=stage 19.09s/commit_wait 0.00s, flush_wait=5 ops/0.32s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=21.1x (15.8ms→333ms). Likely S3 retry or cache miss.
