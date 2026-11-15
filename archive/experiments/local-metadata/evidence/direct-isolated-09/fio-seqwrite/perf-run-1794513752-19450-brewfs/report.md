# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-seqwrite | pass | 20 | tools/fio-seqwrite.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-seqwrite | 31 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-seqwrite | write | 1 | 4m | 1 | 0.00 B/s | 0.00 | 259.98 MiB/s | 65.00 | 0.000 ms | 0.007 ms | results/fio-seqwrite.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-seqwrite | 1 | 20 s | 20.231 s | -0.231 s | 0.99x | 20.232 s | -0.232 s | 0.99x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-seqwrite | 20.232 s | 31.000 s | 51.232 s | 0.00 MiB/s | 102.67 MiB/s | 102.67 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-seqwrite | 0.000 ms | 0.000 ms | 0.000 ms | 0.004 ms | 0.000 ms | 0.000 ms | 0.005 ms | 0.007 ms | 0.078 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-seqwrite | exec: calls=1316, usec/call=5.43<br>evalsha: calls=106, usec/call=55.41<br>get: calls=1402, usec/call=2.47<br>multi: calls=1316, usec/call=1.20<br>hget: calls=1336, usec/call=0.95 | diagnostics/redis-fio-seqwrite-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-seqwrite | 0.0% (0/0) | 0.0 MiB | 5260.0 MiB | 3020.0 MiB | 0.0 MiB | 3020.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=564 | GET=0.00 ms, PUT=213.02 ms | diagnostics/stats-fio-seqwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=83 ops/5260.0 MiB/14244.3 ms, foreground=stage 14.24s/commit_wait 0.71s, flush_wait=1 ops/0.02s/1 slices, stage_fail=0, commit_before_stage=83, remote_inflight=3020.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=17, max_buffer_dirty=3082.0 MiB, max_live_dirty=84.0 MiB, max_live_slices=2, max_recent_pending=3072.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=320.0 MiB, max_remote_inflight=3072.0 MiB, last_buffer_dirty=3082.0 MiB, slices=create 84/reuse 5177/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=83 avg=63.4 MiB blocks=15.84/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 82/flush 2/auto 0/max 0/age 0 |
| fio-seqwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=751 | GET=0.00 ms, PUT=193.06 ms | diagnostics/stats-fio-seqwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 84/reuse 5177/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 82/flush 2/auto 0/max 0/age 0 |
| fio-seqwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 5260.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1316 | GET=0.00 ms, PUT=201.48 ms | diagnostics/stats-fio-seqwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=84 ops/5260.0 MiB/14256.7 ms, foreground=stage 14.26s/commit_wait 0.71s, flush_wait=2 ops/0.03s/2 slices, stage_fail=0, commit_before_stage=84, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 84/reuse 5177/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=84 avg=62.6 MiB blocks=15.67/batch partial_tail=0.01 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 82/flush 2/auto 0/max 0/age 0 |
