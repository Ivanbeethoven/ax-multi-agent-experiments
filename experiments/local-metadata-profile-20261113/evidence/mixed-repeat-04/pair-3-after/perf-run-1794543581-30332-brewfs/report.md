# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 21 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 42 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 636.82 MiB/s | 159.20 | 0.000 ms | 94.896 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 535.03 MiB/s | 133.76 | 239.57 MiB/s | 59.89 | 204.472 ms | 96.993 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 12.730 s | +0.000 s | - | 3.216 s | +0.000 s | - | - |
| fio-randrw | 0 | 21 s | 80.950 s | -59.950 s | 0.26x | 20.253 s | +0.747 s | 1.04x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.253 s | 42.000 s | 62.253 s | 174.06 MiB/s | 77.94 MiB/s | 252.00 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 12.124 ms | 0.000 ms | 0.000 ms | 82.313 ms | 204.472 ms | 413.139 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 19.005 ms | 0.000 ms | 0.000 ms | 63.177 ms | 94.896 ms | 114.819 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 7.242 ms | 0.000 ms | 0.000 ms | 47.972 ms | 96.993 ms | 212.861 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=1048, usec/call=60.34<br>exec: calls=2424, usec/call=5.25<br>get: calls=4846, usec/call=1.50<br>rpush: calls=1024, usec/call=5.56<br>incrby: calls=1024, usec/call=3.97 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 87.4% (45172/51688) | 10836.0 MiB | 4852.0 MiB | 3096.0 MiB | 0.0 MiB | 3096.0 MiB | 0.0 MiB | 0.0 MiB | GET=2586, PUT=441 | GET=12.36 ms, PUT=252.33 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=2586, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1024 ops/4852.0 MiB/35837.1 ms, foreground=stage 35.84s/commit_wait 6.27s, flush_wait=4 ops/0.11s/17 slices, stage_fail=0, commit_before_stage=884, remote_inflight=3096.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=16, max_buffer_dirty=3144.0 MiB, max_live_dirty=432.0 MiB, max_live_slices=90, max_recent_pending=3000.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=64.0 MiB, max_remote_inflight=3024.0 MiB, last_buffer_dirty=3127.0 MiB, slices=create 1024/reuse 3828/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1024 avg=4.7 MiB blocks=1.18/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 76/flush 10/auto 913/max 25/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=769 | GET=12.36 ms, PUT=283.83 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1024/reuse 3828/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 76/flush 10/auto 913/max 25/age 0 |
| fio-randrw-post-write-runner-drained | 87.4% (45172/51688) | 10836.0 MiB | 4852.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=2586, PUT=1213 | GET=12.36 ms, PUT=272.90 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=2586, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1024 ops/4852.0 MiB/35837.1 ms, foreground=stage 35.84s/commit_wait 6.27s, flush_wait=4 ops/0.11s/17 slices, stage_fail=0, commit_before_stage=884, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1024/reuse 3828/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1024 avg=4.7 MiB blocks=1.18/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 76/flush 10/auto 913/max 25/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1728.0 MiB | 0.0 MiB | 1728.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=85 | GET=0.00 ms, PUT=202.94 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=32 ops/2048.0 MiB/29169.5 ms, foreground=stage 29.17s/commit_wait 0.32s, flush_wait=4 ops/0.32s/4 slices, stage_fail=0, commit_before_stage=32, remote_inflight=1728.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=3, max_buffer_dirty=1323.0 MiB, max_live_dirty=235.0 MiB, max_live_slices=4, max_recent_pending=1088.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=576.0 MiB, max_remote_inflight=1088.0 MiB, last_buffer_dirty=1323.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=32 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=427 | GET=0.00 ms, PUT=313.87 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=294.95 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/29191.9 ms, foreground=stage 29.19s/commit_wait 0.32s, flush_wait=5 ops/0.34s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=16.9x (12.1ms→204ms). Likely S3 retry or cache miss.
