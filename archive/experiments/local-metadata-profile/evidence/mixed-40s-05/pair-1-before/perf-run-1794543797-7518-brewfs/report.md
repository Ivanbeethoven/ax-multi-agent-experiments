# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 44 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 46 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 1.01 GiB/s | 257.55 | 0.000 ms | 26.345 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 302.70 MiB/s | 75.67 | 133.73 MiB/s | 33.43 | 152.044 ms | 2197.815 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 7.946 s | +0.000 s | - | 1.988 s | +0.000 s | - | - |
| fio-randrw | 0 | 44 s | 163.901 s | -119.901 s | 0.27x | 40.978 s | +3.022 s | 1.07x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 40.978 s | 46.000 s | 86.978 s | 142.61 MiB/s | 63.00 MiB/s | 205.62 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 10.420 ms | 0.000 ms | 0.000 ms | 54.788 ms | 152.044 ms | 387.973 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 15.532 ms | 0.000 ms | 0.000 ms | 21.103 ms | 26.345 ms | 40.108 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 7.963 ms | 0.000 ms | 0.000 ms | 57.410 ms | 2197.815 ms | 7482.638 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=1203, usec/call=53.12<br>exec: calls=2391, usec/call=5.12<br>get: calls=4880, usec/call=1.51<br>rpush: calls=1170, usec/call=4.71<br>incrby: calls=1170, usec/call=3.29 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 86.7% (43232/49854) | 12404.0 MiB | 5480.0 MiB | 3052.0 MiB | 0.0 MiB | 3052.0 MiB | 0.0 MiB | 0.0 MiB | GET=2714, PUT=610 | GET=284.66 ms, PUT=387.62 ms | diagnostics/stats-fio-randrw-after.txt; range=1, full=2712, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1170 ops/5480.0 MiB/263718.9 ms, foreground=stage 263.72s/commit_wait 24.31s, flush_wait=4 ops/0.12s/4 slices, stage_fail=0, commit_before_stage=1033, remote_inflight=3052.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=22, max_buffer_dirty=3393.0 MiB, max_live_dirty=408.0 MiB, max_live_slices=83, max_recent_pending=3393.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=32.0 MiB, max_remote_inflight=3393.0 MiB, last_buffer_dirty=3192.0 MiB, slices=create 1170/reuse 4310/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1170 avg=4.7 MiB blocks=1.17/batch partial_tail=0.01 (size 0/max 0/flush 0/auto 6/age 0), partial_origin=normal 6/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 6/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 6/cached 0/mixed 0/unknown 0, freeze=size 86/flush 4/auto 1055/max 25/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=760 | GET=284.66 ms, PUT=322.54 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1170/reuse 4310/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 86/flush 4/auto 1055/max 25/age 0 |
| fio-randrw-post-write-runner-drained | 86.7% (43232/49854) | 12404.0 MiB | 5480.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=2714, PUT=1373 | GET=284.66 ms, PUT=351.22 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=1, full=2712, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1170 ops/5480.0 MiB/263718.9 ms, foreground=stage 263.72s/commit_wait 24.31s, flush_wait=4 ops/0.12s/4 slices, stage_fail=0, commit_before_stage=1033, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1170/reuse 4310/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1170 avg=4.7 MiB blocks=1.17/batch partial_tail=0.01 (size 0/max 0/flush 0/auto 6/age 0), partial_origin=normal 6/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 6/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 6/cached 0/mixed 0/unknown 0, freeze=size 86/flush 4/auto 1055/max 25/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1856.0 MiB | 128.0 MiB | 1728.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=62 | GET=0.00 ms, PUT=166.31 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=24 ops/1536.0 MiB/14599.4 ms, foreground=stage 14.60s/commit_wait 0.00s, flush_wait=2 ops/0.08s/2 slices, stage_fail=0, commit_before_stage=30, remote_inflight=1856.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=2, max_buffer_dirty=1022.0 MiB, max_live_dirty=318.0 MiB, max_live_slices=8, max_recent_pending=704.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=768.0 MiB, max_remote_inflight=960.0 MiB, last_buffer_dirty=1022.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=2 avg=64.0 MiB, origin=normal 2/cached 0/mixed 0/unknown 0, upload_batch=32 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=450 | GET=0.00 ms, PUT=200.56 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=8 ops/512.0 MiB/1762.1 ms, foreground=stage 1.76s/commit_wait 0.00s, flush_wait=2 ops/0.08s/2 slices, stage_fail=0, commit_before_stage=2, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=196.08 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/16378.5 ms, foreground=stage 16.38s/commit_wait 0.00s, flush_wait=5 ops/0.18s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=14.6x (10.4ms→152ms). Likely S3 retry or cache miss.
- **fio-randrw**: Write stall p50=8.0ms p99=2198ms — auto_flush/buffer-limit triggers S3 upload backpressure.
