# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 21 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 24 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 881.24 MiB/s | 220.31 | 0.000 ms | 26.870 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 300.90 MiB/s | 75.23 | 141.17 MiB/s | 35.29 | 526.385 ms | 488.636 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 9.131 s | +0.000 s | - | 2.324 s | +0.000 s | - | - |
| fio-randrw | 0 | 21 s | 80.122 s | -59.122 s | 0.26x | 20.033 s | +0.967 s | 1.05x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.033 s | 24.000 s | 44.033 s | 136.90 MiB/s | 64.22 MiB/s | 201.12 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 10.027 ms | 0.000 ms | 0.000 ms | 77.070 ms | 526.385 ms | 4328.522 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 17.957 ms | 0.000 ms | 0.000 ms | 24.510 ms | 26.870 ms | 66.322 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 4.948 ms | 0.000 ms | 0.000 ms | 27.918 ms | 488.636 ms | 2399.142 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=619, usec/call=48.10<br>exec: calls=1410, usec/call=4.89<br>get: calls=2945, usec/call=1.53<br>rpush: calls=602, usec/call=4.10<br>incrby: calls=602, usec/call=2.63 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 84.7% (31051/36663) | 6028.0 MiB | 2828.0 MiB | 2088.0 MiB | 0.0 MiB | 2088.0 MiB | 0.0 MiB | 0.0 MiB | GET=1367, PUT=185 | GET=51.34 ms, PUT=586.23 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=1367, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=602 ops/2828.0 MiB/392678.5 ms, foreground=stage 392.68s/commit_wait 19.45s, flush_wait=4 ops/0.02s/5 slices, stage_fail=0, commit_before_stage=538, remote_inflight=2088.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=9, max_buffer_dirty=2146.0 MiB, max_live_dirty=460.0 MiB, max_live_slices=95, max_recent_pending=1740.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=12.0 MiB, max_remote_inflight=1768.0 MiB, last_buffer_dirty=2146.0 MiB, slices=create 602/reuse 2226/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=602 avg=4.7 MiB blocks=1.18/batch partial_tail=0.01 (size 0/max 0/flush 0/auto 4/age 0), partial_origin=normal 4/cached 0/mixed 0/unknown 0, auto_detail=age 1/idle 3/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 4/cached 0/mixed 0/unknown 0, freeze=size 50/flush 5/auto 525/max 22/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=520 | GET=51.34 ms, PUT=201.90 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 602/reuse 2226/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 50/flush 5/auto 525/max 22/age 0 |
| fio-randrw-post-write-runner-drained | 84.7% (31051/36663) | 6028.0 MiB | 2828.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=1367, PUT=709 | GET=51.34 ms, PUT=302.26 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=1367, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=602 ops/2828.0 MiB/392678.5 ms, foreground=stage 392.68s/commit_wait 19.45s, flush_wait=4 ops/0.02s/5 slices, stage_fail=0, commit_before_stage=538, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 602/reuse 2226/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=602 avg=4.7 MiB blocks=1.18/batch partial_tail=0.01 (size 0/max 0/flush 0/auto 4/age 0), partial_origin=normal 4/cached 0/mixed 0/unknown 0, auto_detail=age 1/idle 3/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 4/cached 0/mixed 0/unknown 0, freeze=size 50/flush 5/auto 525/max 22/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1856.0 MiB | 0.0 MiB | 1856.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=62 | GET=0.00 ms, PUT=188.38 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=32 ops/2048.0 MiB/19496.9 ms, foreground=stage 19.50s/commit_wait 0.10s, flush_wait=4 ops/0.15s/4 slices, stage_fail=0, commit_before_stage=32, remote_inflight=1856.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=2, max_buffer_dirty=764.0 MiB, max_live_dirty=252.0 MiB, max_live_slices=5, max_recent_pending=512.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=576.0 MiB, max_remote_inflight=512.0 MiB, last_buffer_dirty=764.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=32 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=451 | GET=0.00 ms, PUT=205.90 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=203.78 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/19509.4 ms, foreground=stage 19.51s/commit_wait 0.10s, flush_wait=5 ops/0.16s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=52.5x (10.0ms→526ms). Likely S3 retry or cache miss.
- **fio-randrw**: Write stall p50=4.9ms p99=489ms — auto_flush/buffer-limit triggers S3 upload backpressure.
