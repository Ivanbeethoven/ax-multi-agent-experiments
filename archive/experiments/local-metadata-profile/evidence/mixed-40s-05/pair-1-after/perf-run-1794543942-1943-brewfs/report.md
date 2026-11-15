# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 41 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 40 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 911.03 MiB/s | 227.76 | 0.000 ms | 37.487 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 321.02 MiB/s | 80.25 | 142.22 MiB/s | 35.55 | 225.444 ms | 1501.561 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 8.888 s | +0.000 s | - | 2.248 s | +0.000 s | - | - |
| fio-randrw | 0 | 41 s | 160.039 s | -119.039 s | 0.26x | 40.023 s | +0.977 s | 1.02x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 40.023 s | 40.000 s | 80.023 s | 160.55 MiB/s | 71.13 MiB/s | 231.68 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 9.765 ms | 0.000 ms | 0.000 ms | 65.274 ms | 225.444 ms | 1132.462 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 16.908 ms | 0.000 ms | 0.000 ms | 24.248 ms | 37.487 ms | 106.430 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 19.005 ms | 0.000 ms | 0.000 ms | 70.779 ms | 1501.561 ms | 5133.828 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=1262, usec/call=53.79<br>exec: calls=2508, usec/call=5.50<br>get: calls=5121, usec/call=1.59<br>rpush: calls=1224, usec/call=5.67<br>incrby: calls=1224, usec/call=3.82 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 86.4% (42168/48783) | 12848.0 MiB | 5692.0 MiB | 3080.0 MiB | 0.0 MiB | 3080.0 MiB | 0.0 MiB | 0.0 MiB | GET=2753, PUT=657 | GET=93.62 ms, PUT=351.90 ms | diagnostics/stats-fio-randrw-after.txt; range=4, full=2745, bg_prefetch=1, persistent_slice=0 ops/0.0 MiB, stage=1224 ops/5692.0 MiB/93152.6 ms, foreground=stage 93.15s/commit_wait 29.70s, flush_wait=4 ops/0.09s/20 slices, stage_fail=0, commit_before_stage=1078, remote_inflight=3080.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=28, max_buffer_dirty=3338.0 MiB, max_live_dirty=472.0 MiB, max_live_slices=98, max_recent_pending=3294.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=428.0 MiB, max_remote_inflight=3296.0 MiB, last_buffer_dirty=3062.0 MiB, slices=create 1224/reuse 4468/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1224 avg=4.7 MiB blocks=1.17/batch partial_tail=0.01 (size 0/max 0/flush 0/auto 12/age 0), partial_origin=normal 12/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 12/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 12/cached 0/mixed 0/unknown 0, freeze=size 88/flush 19/auto 1089/max 28/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=770 | GET=93.62 ms, PUT=262.33 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1224/reuse 4468/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 88/flush 19/auto 1089/max 28/age 0 |
| fio-randrw-post-write-runner-drained | 86.4% (42168/48783) | 12848.0 MiB | 5692.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=2753, PUT=1429 | GET=93.62 ms, PUT=303.76 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=4, full=2745, bg_prefetch=1, persistent_slice=0 ops/0.0 MiB, stage=1224 ops/5692.0 MiB/93152.6 ms, foreground=stage 93.15s/commit_wait 29.70s, flush_wait=4 ops/0.09s/20 slices, stage_fail=0, commit_before_stage=1078, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1224/reuse 4468/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1224 avg=4.7 MiB blocks=1.17/batch partial_tail=0.01 (size 0/max 0/flush 0/auto 12/age 0), partial_origin=normal 12/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 12/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 12/cached 0/mixed 0/unknown 0, freeze=size 88/flush 19/auto 1089/max 28/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1792.0 MiB | 0.0 MiB | 1792.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=68 | GET=0.00 ms, PUT=175.82 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=32 ops/2048.0 MiB/18708.9 ms, foreground=stage 18.71s/commit_wait 0.00s, flush_wait=4 ops/0.14s/4 slices, stage_fail=0, commit_before_stage=32, remote_inflight=1792.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=2, max_buffer_dirty=857.0 MiB, max_live_dirty=89.0 MiB, max_live_slices=4, max_recent_pending=768.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=768.0 MiB, max_remote_inflight=704.0 MiB, last_buffer_dirty=857.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=32 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=444 | GET=0.00 ms, PUT=219.10 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=212.98 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/18721.5 ms, foreground=stage 18.72s/commit_wait 0.00s, flush_wait=5 ops/0.16s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=23.1x (9.8ms→225ms). Likely S3 retry or cache miss.
- **fio-randrw**: Write P99=1502ms > 500ms — inspect writeback samples and tune upload queue depth, slice age, or write buffer.
