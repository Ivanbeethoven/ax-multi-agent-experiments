# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 21 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 38 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 303.23 MiB/s | 75.81 | 0.000 ms | 708.837 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 501.12 MiB/s | 125.28 | 225.54 MiB/s | 56.39 | 196.084 ms | 120.062 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 26.893 s | +0.000 s | - | 6.754 s | +0.000 s | - | - |
| fio-randrw | 0 | 21 s | 80.461 s | -59.461 s | 0.26x | 20.147 s | +0.853 s | 1.04x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.147 s | 38.000 s | 58.147 s | 173.63 MiB/s | 78.15 MiB/s | 251.78 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 12.517 ms | 0.000 ms | 0.000 ms | 83.362 ms | 196.084 ms | 501.219 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 17.433 ms | 0.000 ms | 0.000 ms | 250.610 ms | 708.837 ms | 1044.382 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 6.128 ms | 0.000 ms | 0.000 ms | 53.215 ms | 120.062 ms | 235.930 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=981, usec/call=62.16<br>exec: calls=2017, usec/call=5.65<br>get: calls=4045, usec/call=1.80<br>rpush: calls=957, usec/call=6.22<br>incrby: calls=957, usec/call=4.27 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 84.7% (42304/49955) | 10096.0 MiB | 4544.0 MiB | 2696.0 MiB | 0.0 MiB | 2696.0 MiB | 0.0 MiB | 0.0 MiB | GET=2355, PUT=462 | GET=13.92 ms, PUT=235.20 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=2355, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=957 ops/4544.0 MiB/39442.0 ms, foreground=stage 39.44s/commit_wait 5.14s, flush_wait=4 ops/0.48s/18 slices, stage_fail=0, commit_before_stage=826, remote_inflight=2696.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=16, max_buffer_dirty=2783.0 MiB, max_live_dirty=408.0 MiB, max_live_slices=90, max_recent_pending=2660.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=44.0 MiB, max_remote_inflight=2664.0 MiB, last_buffer_dirty=2733.0 MiB, slices=create 957/reuse 3587/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=957 avg=4.7 MiB blocks=1.19/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 73/flush 16/auto 849/max 19/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=674 | GET=13.92 ms, PUT=279.31 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 957/reuse 3587/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 73/flush 16/auto 849/max 19/age 0 |
| fio-randrw-post-write-runner-drained | 84.7% (42304/49955) | 10096.0 MiB | 4544.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=2355, PUT=1136 | GET=13.92 ms, PUT=261.37 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=2355, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=957 ops/4544.0 MiB/39442.0 ms, foreground=stage 39.44s/commit_wait 5.14s, flush_wait=4 ops/0.48s/18 slices, stage_fail=0, commit_before_stage=826, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 957/reuse 3587/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=957 avg=4.7 MiB blocks=1.19/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 73/flush 16/auto 849/max 19/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 2048.0 MiB | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=10 | GET=0.00 ms, PUT=2193.46 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=39 ops/2048.0 MiB/44541.4 ms, foreground=stage 44.54s/commit_wait 4.43s, flush_wait=5 ops/0.20s/5 slices, stage_fail=0, commit_before_stage=37, remote_inflight=2048.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=3, max_buffer_dirty=527.0 MiB, max_live_dirty=112.0 MiB, max_live_slices=4, max_recent_pending=485.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=481.0 MiB, max_remote_inflight=485.0 MiB, last_buffer_dirty=527.0 MiB, slices=create 39/reuse 2010/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=39 avg=52.5 MiB blocks=13.31/batch partial_tail=0.33 (size 6/max 0/flush 1/auto 6/age 0), partial_origin=normal 13/cached 0/mixed 0/unknown 0, auto_detail=age 6/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 6/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 6/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=509 | GET=0.00 ms, PUT=263.93 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 39/reuse 2010/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 6/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=519 | GET=0.00 ms, PUT=301.11 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=39 ops/2048.0 MiB/44541.4 ms, foreground=stage 44.54s/commit_wait 4.43s, flush_wait=5 ops/0.20s/5 slices, stage_fail=0, commit_before_stage=37, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 39/reuse 2010/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=39 avg=52.5 MiB blocks=13.31/batch partial_tail=0.33 (size 6/max 0/flush 1/auto 6/age 0), partial_origin=normal 13/cached 0/mixed 0/unknown 0, auto_detail=age 6/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 6/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 6/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw-prefill**: Write P99=709ms > 500ms — inspect writeback samples and tune upload queue depth, slice age, or write buffer.
- **fio-randrw**: Read tail latency p99/p50=15.7x (12.5ms→196ms). Likely S3 retry or cache miss.
