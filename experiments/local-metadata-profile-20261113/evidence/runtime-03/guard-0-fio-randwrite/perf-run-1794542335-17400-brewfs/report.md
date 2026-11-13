# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randwrite | pass | 21 | tools/fio-randwrite.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randwrite | 39 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randwrite | randwrite | 0 | 4m | 4 | 0.00 B/s | 0.00 | 249.35 MiB/s | 62.34 | 0.000 ms | 333.447 ms | results/fio-randwrite.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 0 | 21 s | 80.100 s | -59.100 s | 0.26x | 20.036 s | +0.964 s | 1.05x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 20.036 s | 39.000 s | 59.036 s | 0.00 MiB/s | 84.63 MiB/s | 84.63 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randwrite | 0.000 ms | 0.000 ms | 0.000 ms | 23.200 ms | 0.000 ms | 0.000 ms | 66.322 ms | 333.447 ms | 4278.190 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randwrite | evalsha: calls=793, usec/call=54.76<br>exec: calls=1253, usec/call=4.84<br>rpush: calls=765, usec/call=5.03<br>get: calls=2030, usec/call=1.87<br>incrby: calls=765, usec/call=4.03 | diagnostics/redis-fio-randwrite-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 0.0% (0/0) | 0.0 MiB | 4996.0 MiB | 3075.0 MiB | 0.0 MiB | 3075.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=485 | GET=0.00 ms, PUT=235.13 ms | diagnostics/stats-fio-randwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=766 ops/4996.0 MiB/71100.6 ms, foreground=stage 71.10s/commit_wait 2.25s, flush_wait=5 ops/0.05s/12 slices, stage_fail=0, commit_before_stage=606, remote_inflight=3075.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=16, max_buffer_dirty=3496.0 MiB, max_live_dirty=914.0 MiB, max_live_slices=144, max_recent_pending=3387.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=184.0 MiB, max_remote_inflight=3423.0 MiB, last_buffer_dirty=3182.0 MiB, slices=create 766/reuse 4231/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=766 avg=6.5 MiB blocks=1.64/batch partial_tail=0.02 (size 0/max 1/flush 4/auto 12/age 0), partial_origin=normal 17/cached 0/mixed 0/unknown 0, auto_detail=age 1/idle 11/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 12/cached 0/mixed 0/unknown 0, freeze=size 78/flush 12/auto 565/max 111/age 0 |
| fio-randwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=770 | GET=0.00 ms, PUT=254.63 ms | diagnostics/stats-fio-randwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 766/reuse 4231/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 78/flush 12/auto 565/max 111/age 0 |
| fio-randwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 4996.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1258 | GET=0.00 ms, PUT=246.87 ms | diagnostics/stats-fio-randwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=766 ops/4996.0 MiB/71100.6 ms, foreground=stage 71.10s/commit_wait 2.25s, flush_wait=5 ops/0.05s/12 slices, stage_fail=0, commit_before_stage=606, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 766/reuse 4231/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=766 avg=6.5 MiB blocks=1.64/batch partial_tail=0.02 (size 0/max 1/flush 4/auto 12/age 0), partial_origin=normal 17/cached 0/mixed 0/unknown 0, auto_detail=age 1/idle 11/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 12/cached 0/mixed 0/unknown 0, freeze=size 78/flush 12/auto 565/max 111/age 0 |
