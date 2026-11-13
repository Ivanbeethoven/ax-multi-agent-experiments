# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randwrite | pass | 20 | tools/fio-randwrite.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randwrite | 36 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randwrite | randwrite | 1 | 4m | 4 | 0.00 B/s | 0.00 | 249.33 MiB/s | 62.33 | 0.000 ms | 0.022 ms | results/fio-randwrite.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 1 | 20 s | 80.078 s | -60.078 s | 0.25x | 20.022 s | -0.022 s | 1.00x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 20.022 s | 36.000 s | 56.022 s | 0.00 MiB/s | 89.11 MiB/s | 89.11 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randwrite | 0.000 ms | 0.000 ms | 0.000 ms | 0.003 ms | 0.000 ms | 0.000 ms | 0.006 ms | 0.022 ms | 0.071 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randwrite | evalsha: calls=716, usec/call=49.22<br>exec: calls=1252, usec/call=3.79<br>get: calls=1951, usec/call=1.50<br>rpush: calls=687, usec/call=4.10<br>incrby: calls=687, usec/call=3.44 | diagnostics/redis-fio-randwrite-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 0.0% (0/0) | 0.0 MiB | 4992.0 MiB | 3136.0 MiB | 0.0 MiB | 3136.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=469 | GET=0.00 ms, PUT=243.37 ms | diagnostics/stats-fio-randwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=688 ops/4992.0 MiB/34201.6 ms, foreground=stage 34.20s/commit_wait 2.15s, flush_wait=5 ops/0.08s/24 slices, stage_fail=0, commit_before_stage=500, remote_inflight=3136.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=16, max_buffer_dirty=3729.0 MiB, max_live_dirty=950.0 MiB, max_live_slices=143, max_recent_pending=3705.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=248.0 MiB, max_remote_inflight=3705.0 MiB, last_buffer_dirty=3111.0 MiB, slices=create 688/reuse 4305/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=687 avg=7.3 MiB blocks=1.83/batch partial_tail=0.02 (size 1/max 2/flush 3/auto 10/age 0), partial_origin=normal 16/cached 0/mixed 0/unknown 0, auto_detail=age 2/idle 8/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 10/cached 0/mixed 0/unknown 0, freeze=size 78/flush 24/auto 426/max 160/age 0 |
| fio-randwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=787 | GET=0.00 ms, PUT=239.86 ms | diagnostics/stats-fio-randwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 688/reuse 4305/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 78/flush 24/auto 426/max 160/age 0 |
| fio-randwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 4992.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1257 | GET=0.00 ms, PUT=242.25 ms | diagnostics/stats-fio-randwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=688 ops/4992.0 MiB/34201.6 ms, foreground=stage 34.20s/commit_wait 2.15s, flush_wait=5 ops/0.08s/24 slices, stage_fail=0, commit_before_stage=500, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 688/reuse 4305/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=688 avg=7.3 MiB blocks=1.83/batch partial_tail=0.02 (size 1/max 2/flush 4/auto 10/age 0), partial_origin=normal 17/cached 0/mixed 0/unknown 0, auto_detail=age 2/idle 8/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 10/cached 0/mixed 0/unknown 0, freeze=size 78/flush 24/auto 426/max 160/age 0 |
