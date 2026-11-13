# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 40 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 50 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 667.10 MiB/s | 166.78 | 0.000 ms | 119.013 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 284.31 MiB/s | 71.08 | 125.52 MiB/s | 31.38 | 308.281 ms | 599.785 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 12.227 s | +0.000 s | - | 3.070 s | +0.000 s | - | - |
| fio-randrw | 0 | 40 s | 160.055 s | -120.055 s | 0.25x | 40.027 s | -0.027 s | 1.00x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 40.027 s | 50.000 s | 90.027 s | 126.41 MiB/s | 55.81 MiB/s | 182.21 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 12.255 ms | 0.000 ms | 0.000 ms | 89.653 ms | 308.281 ms | 2021.655 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 18.481 ms | 0.000 ms | 0.000 ms | 60.555 ms | 119.013 ms | 196.084 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 8.454 ms | 0.000 ms | 0.000 ms | 82.313 ms | 599.785 ms | 8220.836 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=1109, usec/call=58.71<br>exec: calls=2429, usec/call=5.70<br>get: calls=4852, usec/call=1.61<br>rpush: calls=1076, usec/call=4.68<br>incrby: calls=1076, usec/call=3.64 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 86.9% (42301/48674) | 11380.0 MiB | 5024.0 MiB | 3200.0 MiB | 0.0 MiB | 3200.0 MiB | 0.0 MiB | 0.0 MiB | GET=2440, PUT=458 | GET=29.45 ms, PUT=502.78 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=2440, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1076 ops/5024.0 MiB/239134.6 ms, foreground=stage 239.13s/commit_wait 29.37s, flush_wait=4 ops/0.11s/36 slices, stage_fail=0, commit_before_stage=965, remote_inflight=3200.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=23, max_buffer_dirty=3283.0 MiB, max_live_dirty=448.0 MiB, max_live_slices=91, max_recent_pending=3283.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=224.0 MiB, max_remote_inflight=3283.0 MiB, last_buffer_dirty=3158.0 MiB, slices=create 1076/reuse 3948/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1076 avg=4.7 MiB blocks=1.17/batch partial_tail=0.01 (size 1/max 0/flush 0/auto 5/age 0), partial_origin=normal 6/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 5/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 5/cached 0/mixed 0/unknown 0, freeze=size 77/flush 34/auto 944/max 21/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=798 | GET=29.45 ms, PUT=316.73 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1076/reuse 3948/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 77/flush 34/auto 944/max 21/age 0 |
| fio-randrw-post-write-runner-drained | 86.9% (42301/48674) | 11380.0 MiB | 5024.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=2440, PUT=1259 | GET=29.45 ms, PUT=384.16 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=2440, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1076 ops/5024.0 MiB/239134.6 ms, foreground=stage 239.13s/commit_wait 29.37s, flush_wait=4 ops/0.11s/36 slices, stage_fail=0, commit_before_stage=965, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1076/reuse 3948/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1076 avg=4.7 MiB blocks=1.17/batch partial_tail=0.01 (size 1/max 0/flush 0/auto 5/age 0), partial_origin=normal 6/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 5/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 5/cached 0/mixed 0/unknown 0, freeze=size 77/flush 34/auto 944/max 21/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1792.0 MiB | 0.0 MiB | 1792.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=72 | GET=0.00 ms, PUT=228.87 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=25 ops/1536.0 MiB/25706.5 ms, foreground=stage 25.71s/commit_wait 0.32s, flush_wait=5 ops/0.16s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=1792.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=3, max_buffer_dirty=1247.0 MiB, max_live_dirty=235.0 MiB, max_live_slices=4, max_recent_pending=1088.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=768.0 MiB, max_remote_inflight=1088.0 MiB, last_buffer_dirty=1247.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=441 | GET=0.00 ms, PUT=206.13 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=8 ops/512.0 MiB/2064.9 ms, foreground=stage 2.06s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=209.32 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/27771.4 ms, foreground=stage 27.77s/commit_wait 0.32s, flush_wait=5 ops/0.16s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=25.2x (12.3ms→308ms). Likely S3 retry or cache miss.
- **fio-randrw**: Write stall p50=8.5ms p99=600ms — auto_flush/buffer-limit triggers S3 upload backpressure.
