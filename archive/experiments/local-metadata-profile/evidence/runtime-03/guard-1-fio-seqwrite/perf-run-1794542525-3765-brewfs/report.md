# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-seqwrite | pass | 28 | tools/fio-seqwrite.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-seqwrite | 30 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-seqwrite | write | 1 | 4m | 1 | 0.00 B/s | 0.00 | 177.34 MiB/s | 44.34 | 0.000 ms | 0.009 ms | results/fio-seqwrite.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-seqwrite | 1 | 28 s | 27.855 s | +0.145 s | 1.01x | 27.856 s | +0.144 s | 1.01x | - |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-seqwrite | 27.856 s | 30.000 s | 57.856 s | 0.00 MiB/s | 85.38 MiB/s | 85.38 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-seqwrite | 0.000 ms | 0.000 ms | 0.000 ms | 0.004 ms | 0.000 ms | 0.000 ms | 0.005 ms | 0.009 ms | 0.047 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-seqwrite | exec: calls=1236, usec/call=6.35<br>evalsha: calls=106, usec/call=61.38<br>get: calls=1318, usec/call=3.20<br>multi: calls=1236, usec/call=1.54<br>hget: calls=1260, usec/call=1.01 | diagnostics/redis-fio-seqwrite-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-seqwrite | 0.0% (0/0) | 0.0 MiB | 4940.0 MiB | 3020.0 MiB | 0.0 MiB | 3020.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=483 | GET=0.00 ms, PUT=341.66 ms | diagnostics/stats-fio-seqwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=80 ops/4940.0 MiB/17908.9 ms, foreground=stage 17.91s/commit_wait 0.29s, flush_wait=2 ops/0.02s/2 slices, stage_fail=0, commit_before_stage=80, remote_inflight=3020.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=19, max_buffer_dirty=3083.0 MiB, max_live_dirty=91.0 MiB, max_live_slices=2, max_recent_pending=3083.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=384.0 MiB, max_remote_inflight=3083.0 MiB, last_buffer_dirty=3083.0 MiB, slices=create 80/reuse 4861/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=80 avg=61.8 MiB blocks=15.46/batch partial_tail=0.04 (size 0/max 0/flush 2/auto 1/age 0), partial_origin=normal 3/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 1/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 1/cached 0/mixed 0/unknown 0, freeze=size 77/flush 2/auto 1/max 0/age 0 |
| fio-seqwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=751 | GET=0.00 ms, PUT=189.93 ms | diagnostics/stats-fio-seqwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 80/reuse 4861/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 77/flush 2/auto 1/max 0/age 0 |
| fio-seqwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 4940.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1237 | GET=0.00 ms, PUT=249.42 ms | diagnostics/stats-fio-seqwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=80 ops/4940.0 MiB/17908.9 ms, foreground=stage 17.91s/commit_wait 0.29s, flush_wait=2 ops/0.02s/2 slices, stage_fail=0, commit_before_stage=80, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 80/reuse 4861/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=80 avg=61.8 MiB blocks=15.46/batch partial_tail=0.04 (size 0/max 0/flush 2/auto 1/age 0), partial_origin=normal 3/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 1/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 1/cached 0/mixed 0/unknown 0, freeze=size 77/flush 2/auto 1/max 0/age 0 |
