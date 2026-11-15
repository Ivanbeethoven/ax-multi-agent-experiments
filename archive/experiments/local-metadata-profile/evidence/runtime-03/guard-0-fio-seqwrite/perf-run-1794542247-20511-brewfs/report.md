# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-seqwrite | pass | 21 | tools/fio-seqwrite.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-seqwrite | 39 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-seqwrite | write | 0 | 4m | 1 | 0.00 B/s | 0.00 | 244.93 MiB/s | 61.23 | 0.000 ms | 39.584 ms | results/fio-seqwrite.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-seqwrite | 0 | 21 s | 20.005 s | +0.995 s | 1.05x | 20.006 s | +0.994 s | 1.05x | - |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-seqwrite | 20.006 s | 39.000 s | 59.006 s | 0.00 MiB/s | 83.04 MiB/s | 83.04 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-seqwrite | 0.000 ms | 0.000 ms | 0.000 ms | 11.600 ms | 0.000 ms | 0.000 ms | 34.865 ms | 39.584 ms | 58.458 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-seqwrite | exec: calls=1226, usec/call=7.25<br>evalsha: calls=99, usec/call=71.63<br>get: calls=1306, usec/call=3.69<br>multi: calls=1226, usec/call=1.76<br>hget: calls=1245, usec/call=1.17 | diagnostics/redis-fio-seqwrite-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-seqwrite | 0.0% (0/0) | 0.0 MiB | 4900.0 MiB | 2980.0 MiB | 0.0 MiB | 2980.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=481 | GET=0.00 ms, PUT=242.80 ms | diagnostics/stats-fio-seqwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=78 ops/4900.0 MiB/10711.9 ms, foreground=stage 10.71s/commit_wait 0.00s, flush_wait=2 ops/0.04s/2 slices, stage_fail=0, commit_before_stage=78, remote_inflight=2980.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=15, max_buffer_dirty=2968.0 MiB, max_live_dirty=90.0 MiB, max_live_slices=2, max_recent_pending=2944.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=128.0 MiB, max_remote_inflight=2944.0 MiB, last_buffer_dirty=2933.0 MiB, slices=create 78/reuse 4823/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=78 avg=62.8 MiB blocks=15.72/batch partial_tail=0.01 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 76/flush 2/auto 0/max 0/age 0 |
| fio-seqwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=743 | GET=0.00 ms, PUT=269.90 ms | diagnostics/stats-fio-seqwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 78/reuse 4823/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 76/flush 2/auto 0/max 0/age 0 |
| fio-seqwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 4900.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1226 | GET=0.00 ms, PUT=259.62 ms | diagnostics/stats-fio-seqwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=78 ops/4900.0 MiB/10711.9 ms, foreground=stage 10.71s/commit_wait 0.00s, flush_wait=2 ops/0.04s/2 slices, stage_fail=0, commit_before_stage=78, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 78/reuse 4823/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=78 avg=62.8 MiB blocks=15.72/batch partial_tail=0.01 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 76/flush 2/auto 0/max 0/age 0 |
