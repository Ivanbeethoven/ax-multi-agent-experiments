# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 20 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 24 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 874.47 MiB/s | 218.62 | 0.000 ms | 34.865 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 348.57 MiB/s | 87.14 | 160.17 MiB/s | 40.04 | 509.608 ms | 152.044 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 9.112 s | +0.000 s | - | 2.342 s | +0.000 s | - | - |
| fio-randrw | 0 | 20 s | 80.464 s | -60.464 s | 0.25x | 20.128 s | -0.128 s | 0.99x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.128 s | 24.000 s | 44.128 s | 158.99 MiB/s | 73.06 MiB/s | 232.05 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 10.813 ms | 0.000 ms | 0.000 ms | 99.090 ms | 509.608 ms | 3439.329 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 17.170 ms | 0.000 ms | 0.000 ms | 28.705 ms | 34.865 ms | 41.681 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 5.669 ms | 0.000 ms | 0.000 ms | 48.497 ms | 152.044 ms | 2264.924 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=700, usec/call=49.90<br>exec: calls=1436, usec/call=5.28<br>get: calls=2933, usec/call=1.73<br>rpush: calls=682, usec/call=4.49<br>incrby: calls=682, usec/call=3.23 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 85.9% (34670/40363) | 7016.0 MiB | 3224.0 MiB | 2228.0 MiB | 0.0 MiB | 2228.0 MiB | 0.0 MiB | 0.0 MiB | GET=1511, PUT=249 | GET=386.36 ms, PUT=450.23 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=1511, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=682 ops/3224.0 MiB/279864.5 ms, foreground=stage 279.86s/commit_wait 47.99s, flush_wait=4 ops/0.05s/6 slices, stage_fail=0, commit_before_stage=597, remote_inflight=2228.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=9, max_buffer_dirty=2307.0 MiB, max_live_dirty=464.0 MiB, max_live_slices=96, max_recent_pending=2084.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=424.0 MiB, max_remote_inflight=2088.0 MiB, last_buffer_dirty=2307.0 MiB, slices=create 682/reuse 2542/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=682 avg=4.7 MiB blocks=1.18/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 54/flush 5/auto 611/max 12/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=551 | GET=386.36 ms, PUT=201.34 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 682/reuse 2542/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 54/flush 5/auto 611/max 12/age 0 |
| fio-randrw-post-write-runner-drained | 85.9% (34670/40363) | 7016.0 MiB | 3224.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=1511, PUT=806 | GET=386.36 ms, PUT=278.32 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=1511, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=682 ops/3224.0 MiB/279864.5 ms, foreground=stage 279.86s/commit_wait 47.99s, flush_wait=4 ops/0.05s/6 slices, stage_fail=0, commit_before_stage=597, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 682/reuse 2542/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=682 avg=4.7 MiB blocks=1.18/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 54/flush 5/auto 611/max 12/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1792.0 MiB | 64.0 MiB | 1728.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=67 | GET=0.00 ms, PUT=179.15 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=25 ops/1536.0 MiB/17795.3 ms, foreground=stage 17.80s/commit_wait 0.00s, flush_wait=4 ops/0.03s/4 slices, stage_fail=0, commit_before_stage=32, remote_inflight=1792.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=2, max_buffer_dirty=896.0 MiB, max_live_dirty=128.0 MiB, max_live_slices=4, max_recent_pending=768.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=768.0 MiB, max_remote_inflight=704.0 MiB, last_buffer_dirty=896.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=1 avg=64.0 MiB, origin=normal 1/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=446 | GET=0.00 ms, PUT=216.78 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=8 ops/512.0 MiB/2114.8 ms, foreground=stage 2.11s/commit_wait 0.00s, flush_wait=1 ops/0.10s/1 slices, stage_fail=0, commit_before_stage=1, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=211.86 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/19910.1 ms, foreground=stage 19.91s/commit_wait 0.00s, flush_wait=5 ops/0.13s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=47.1x (10.8ms→510ms). Likely S3 retry or cache miss.
