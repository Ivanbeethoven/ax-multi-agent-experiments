# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-seqwrite | pass | 21 | tools/fio-seqwrite.log |
| fio-randwrite | fail(1) | 21 | tools/fio-randwrite.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-seqwrite | 30 | 0 | 0 | 0 |
| fio-randwrite | 24 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 1 | 4m | 4 | 0.00 B/s | 0.00 | 534.45 MiB/s | 133.61 | 0.000 ms | 0.009 ms | results/fio-randrw-prefill.json |
| fio-randwrite | randwrite | 1 | 4m | 4 | 0.00 B/s | 0.00 | 137.03 MiB/s | 34.26 | 0.000 ms | 0.016 ms | results/fio-randwrite.json |
| fio-seqwrite | write | 1 | 4m | 1 | 0.00 B/s | 0.00 | 242.78 MiB/s | 60.70 | 0.000 ms | 0.017 ms | results/fio-seqwrite.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 1 | 0 s | 14.869 s | +0.000 s | - | 3.832 s | +0.000 s | - | - |
| fio-randwrite | 1 | 21 s | 80.389 s | -59.389 s | 0.26x | 20.229 s | +0.771 s | 1.04x | fio job_runtime aggregates jobs |
| fio-seqwrite | 1 | 21 s | 20.017 s | +0.983 s | 1.05x | 20.018 s | +0.982 s | 1.05x | - |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 20.229 s | 24.000 s | 44.229 s | 0.00 MiB/s | 62.67 MiB/s | 62.67 MiB/s | fully-drained-throughput.tsv |
| fio-seqwrite | 20.018 s | 30.000 s | 50.018 s | 0.00 MiB/s | 97.17 MiB/s | 97.17 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 0.003 ms | 0.000 ms | 0.000 ms | 0.005 ms | 0.009 ms | 0.037 ms |
| fio-randwrite | 0.000 ms | 0.000 ms | 0.000 ms | 0.003 ms | 0.000 ms | 0.000 ms | 0.006 ms | 0.016 ms | 0.077 ms |
| fio-seqwrite | 0.000 ms | 0.000 ms | 0.000 ms | 0.004 ms | 0.000 ms | 0.000 ms | 0.006 ms | 0.017 ms | 0.036 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randwrite | evalsha: calls=414, usec/call=113.21<br>exec: calls=697, usec/call=3.57<br>get: calls=1109, usec/call=1.32<br>rpush: calls=399, usec/call=3.33<br>incrby: calls=399, usec/call=2.64 | diagnostics/redis-fio-randwrite-after.txt |
| fio-seqwrite | evalsha: calls=98, usec/call=78.08<br>exec: calls=1216, usec/call=6.01<br>get: calls=1295, usec/call=2.77<br>multi: calls=1216, usec/call=1.37<br>hget: calls=1235, usec/call=1.25 | diagnostics/redis-fio-seqwrite-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 0.0% (0/0) | 0.0 MiB | 2772.0 MiB | 2100.0 MiB | 0.0 MiB | 2100.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=171 | GET=0.00 ms, PUT=646.44 ms | diagnostics/stats-fio-randwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=399 ops/2772.0 MiB/672132.0 ms, foreground=stage 672.13s/commit_wait 61.70s, flush_wait=4 ops/0.31s/23 slices, stage_fail=0, commit_before_stage=321, remote_inflight=2100.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=3, max_buffer_dirty=1340.0 MiB, max_live_dirty=1168.0 MiB, max_live_slices=152, max_recent_pending=172.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=44.0 MiB, max_remote_inflight=264.0 MiB, last_buffer_dirty=0.0 MiB, slices=create 476/reuse 7157/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=399 avg=6.9 MiB blocks=1.74/batch partial_tail=0.01 (size 0/max 0/flush 0/auto 2/age 0), partial_origin=normal 2/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 2/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 2/cached 0/mixed 0/unknown 0, freeze=size 119/flush 21/auto 258/max 78/age 0 |
| fio-randwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=517 | GET=0.00 ms, PUT=214.26 ms | diagnostics/stats-fio-randwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 476/reuse 7157/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 119/flush 21/auto 258/max 78/age 0 |
| fio-randwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 7632.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1910 | GET=0.00 ms, PUT=258.49 ms | diagnostics/stats-fio-randwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=476 ops/7632.0 MiB/688897.7 ms, foreground=stage 688.90s/commit_wait 61.89s, flush_wait=6 ops/0.36s/25 slices, stage_fail=0, commit_before_stage=398, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 476/reuse 7157/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=476 avg=16.0 MiB blocks=4.01/batch partial_tail=0.01 (size 0/max 0/flush 1/auto 2/age 0), partial_origin=normal 3/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 2/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 2/cached 0/mixed 0/unknown 0, freeze=size 119/flush 21/auto 258/max 78/age 0 |
| fio-seqwrite | 0.0% (0/0) | 0.0 MiB | 4860.0 MiB | 3068.0 MiB | 60.0 MiB | 3008.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=453 | GET=0.00 ms, PUT=259.60 ms | diagnostics/stats-fio-seqwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=75 ops/4800.0 MiB/16686.3 ms, foreground=stage 16.69s/commit_wait 0.18s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=75, remote_inflight=3068.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=16, max_buffer_dirty=3072.0 MiB, max_live_dirty=74.0 MiB, max_live_slices=2, max_recent_pending=3008.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=320.0 MiB, max_remote_inflight=3072.0 MiB, last_buffer_dirty=3072.0 MiB, slices=create 77/reuse 4784/reject_unique 0/reject_prefix 0, live_slices=1 avg=60.0 MiB, origin=normal 1/cached 0/mixed 0/unknown 0, upload_batch=76 avg=63.9 MiB blocks=15.99/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 75/flush 2/auto 0/max 0/age 0 |
| fio-seqwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=760 | GET=0.00 ms, PUT=200.39 ms | diagnostics/stats-fio-seqwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 77/reuse 4784/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 75/flush 2/auto 0/max 0/age 0 |
| fio-seqwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 4860.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1216 | GET=0.00 ms, PUT=222.75 ms | diagnostics/stats-fio-seqwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=77 ops/4860.0 MiB/16765.7 ms, foreground=stage 16.77s/commit_wait 0.18s, flush_wait=2 ops/0.05s/2 slices, stage_fail=0, commit_before_stage=77, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 77/reuse 4784/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=77 avg=63.1 MiB blocks=15.79/batch partial_tail=0.01 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 75/flush 2/auto 0/max 0/age 0 |
