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
| fio-randrw-prefill | write | 1 | 4m | 4 | 0.00 B/s | 0.00 | 833.20 MiB/s | 208.30 | 0.000 ms | 0.007 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 1 | 4m | 4 | 558.51 MiB/s | 139.63 | 248.12 MiB/s | 62.03 | 0.009 ms | 0.013 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 1 | 0 s | 9.751 s | +0.000 s | - | 2.458 s | +0.000 s | - | - |
| fio-randrw | 1 | 21 s | 80.049 s | -59.049 s | 0.26x | 20.039 s | +0.961 s | 1.05x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.039 s | 42.000 s | 62.039 s | 180.40 MiB/s | 80.14 MiB/s | 260.55 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 0.003 ms | 0.000 ms | 0.000 ms | 0.005 ms | 0.009 ms | 0.026 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 0.003 ms | 0.000 ms | 0.000 ms | 0.004 ms | 0.007 ms | 0.017 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 0.004 ms | 0.000 ms | 0.000 ms | 0.006 ms | 0.013 ms | 0.079 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=1056, usec/call=52.91<br>exec: calls=1821, usec/call=5.54<br>get: calls=3560, usec/call=1.68<br>rpush: calls=1032, usec/call=4.61<br>incrby: calls=1032, usec/call=3.40 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 69.7% (5449/7823) | 11192.0 MiB | 4972.0 MiB | 3160.0 MiB | 0.0 MiB | 3160.0 MiB | 0.0 MiB | 0.0 MiB | GET=1538, PUT=455 | GET=12.55 ms, PUT=246.16 ms | diagnostics/stats-fio-randrw-after.txt; range=1, full=1537, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1032 ops/4972.0 MiB/81302.9 ms, foreground=stage 81.30s/commit_wait 14.27s, flush_wait=4 ops/0.41s/39 slices, stage_fail=0, commit_before_stage=889, remote_inflight=3160.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=15, max_buffer_dirty=3382.0 MiB, max_live_dirty=545.0 MiB, max_live_slices=112, max_recent_pending=3222.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=68.0 MiB, max_remote_inflight=3222.0 MiB, last_buffer_dirty=3059.0 MiB, slices=create 1032/reuse 3940/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1032 avg=4.8 MiB blocks=1.21/batch partial_tail=0.01 (size 0/max 0/flush 0/auto 8/age 0), partial_origin=normal 8/cached 0/mixed 0/unknown 0, auto_detail=age 1/idle 7/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 8/cached 0/mixed 0/unknown 0, freeze=size 77/flush 38/auto 863/max 54/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=791 | GET=12.55 ms, PUT=259.23 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1032/reuse 3940/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 77/flush 38/auto 863/max 54/age 0 |
| fio-randrw-post-write-runner-drained | 69.7% (5449/7823) | 11192.0 MiB | 4972.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=1538, PUT=1247 | GET=12.55 ms, PUT=254.63 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=1, full=1537, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1032 ops/4972.0 MiB/81302.9 ms, foreground=stage 81.30s/commit_wait 14.27s, flush_wait=4 ops/0.41s/39 slices, stage_fail=0, commit_before_stage=889, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1032/reuse 3940/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1032 avg=4.8 MiB blocks=1.21/batch partial_tail=0.01 (size 0/max 0/flush 0/auto 8/age 0), partial_origin=normal 8/cached 0/mixed 0/unknown 0, auto_detail=age 1/idle 7/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 8/cached 0/mixed 0/unknown 0, freeze=size 77/flush 38/auto 863/max 54/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1792.0 MiB | 0.0 MiB | 1792.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=67 | GET=0.00 ms, PUT=203.15 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/25059.8 ms, foreground=stage 25.06s/commit_wait 0.32s, flush_wait=5 ops/0.35s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=1792.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=2, max_buffer_dirty=950.0 MiB, max_live_dirty=182.0 MiB, max_live_slices=4, max_recent_pending=768.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=768.0 MiB, max_remote_inflight=704.0 MiB, last_buffer_dirty=950.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=32 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=443 | GET=0.00 ms, PUT=280.87 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=270.17 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/25059.8 ms, foreground=stage 25.06s/commit_wait 0.32s, flush_wait=5 ops/0.35s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
