# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 41 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 33 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 877.46 MiB/s | 219.37 | 0.000 ms | 67.633 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 309.43 MiB/s | 77.36 | 136.27 MiB/s | 34.07 | 304.087 ms | 252.707 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 9.239 s | +0.000 s | - | 2.334 s | +0.000 s | - | - |
| fio-randrw | 0 | 41 s | 160.397 s | -119.397 s | 0.26x | 40.126 s | +0.874 s | 1.02x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 40.126 s | 33.000 s | 73.126 s | 169.79 MiB/s | 74.78 MiB/s | 244.56 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 13.828 ms | 0.000 ms | 0.000 ms | 92.799 ms | 304.087 ms | 876.610 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 16.908 ms | 0.000 ms | 0.000 ms | 26.345 ms | 67.633 ms | 100.139 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 8.454 ms | 0.000 ms | 0.000 ms | 80.216 ms | 252.707 ms | 8489.271 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=1199, usec/call=58.15<br>exec: calls=2648, usec/call=6.01<br>get: calls=5341, usec/call=1.73<br>rpush: calls=1165, usec/call=5.29<br>lrange: calls=1281, usec/call=3.86 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 86.9% (44783/51532) | 12416.0 MiB | 5468.0 MiB | 2880.0 MiB | 0.0 MiB | 2880.0 MiB | 0.0 MiB | 0.0 MiB | GET=2942, PUT=647 | GET=24.05 ms, PUT=348.85 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=2940, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1165 ops/5468.0 MiB/89776.2 ms, foreground=stage 89.78s/commit_wait 15.61s, flush_wait=4 ops/0.07s/2 slices, stage_fail=0, commit_before_stage=1018, remote_inflight=2880.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=24, max_buffer_dirty=3208.0 MiB, max_live_dirty=448.0 MiB, max_live_slices=93, max_recent_pending=3208.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=244.0 MiB, max_remote_inflight=3208.0 MiB, last_buffer_dirty=2951.0 MiB, slices=create 1165/reuse 4303/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1165 avg=4.7 MiB blocks=1.18/batch partial_tail=0.00 (size 1/max 0/flush 0/auto 3/age 0), partial_origin=normal 4/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 3/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 3/cached 0/mixed 0/unknown 0, freeze=size 86/flush 2/auto 1060/max 17/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=718 | GET=24.05 ms, PUT=211.40 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1165/reuse 4303/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 86/flush 2/auto 1060/max 17/age 0 |
| fio-randrw-post-write-runner-drained | 86.9% (44783/51532) | 12416.0 MiB | 5468.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=2942, PUT=1369 | GET=24.05 ms, PUT=276.69 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=2940, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1165 ops/5468.0 MiB/89776.2 ms, foreground=stage 89.78s/commit_wait 15.61s, flush_wait=4 ops/0.07s/2 slices, stage_fail=0, commit_before_stage=1018, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1165/reuse 4303/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1165 avg=4.7 MiB blocks=1.18/batch partial_tail=0.00 (size 1/max 0/flush 0/auto 3/age 0), partial_origin=normal 4/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 3/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 3/cached 0/mixed 0/unknown 0, freeze=size 86/flush 2/auto 1060/max 17/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1856.0 MiB | 0.0 MiB | 1856.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=61 | GET=0.00 ms, PUT=204.20 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=29 ops/1856.0 MiB/18769.2 ms, foreground=stage 18.77s/commit_wait 0.10s, flush_wait=4 ops/0.19s/4 slices, stage_fail=0, commit_before_stage=32, remote_inflight=1856.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=2, max_buffer_dirty=771.0 MiB, max_live_dirty=259.0 MiB, max_live_slices=7, max_recent_pending=512.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=704.0 MiB, max_remote_inflight=704.0 MiB, last_buffer_dirty=771.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=32 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=451 | GET=0.00 ms, PUT=324.83 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=3 ops/192.0 MiB/401.7 ms, foreground=stage 0.40s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=309.90 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/19184.2 ms, foreground=stage 19.18s/commit_wait 0.10s, flush_wait=5 ops/0.21s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=22.0x (13.8ms→304ms). Likely S3 retry or cache miss.
- **fio-randrw**: Write stall p50=8.5ms p99=253ms — auto_flush/buffer-limit triggers S3 upload backpressure.
