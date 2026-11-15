# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randwrite | pass | 22 | tools/fio-randwrite.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randwrite | 30 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randwrite | randwrite | 1 | 4m | 4 | 0.00 B/s | 0.00 | 244.91 MiB/s | 61.23 | 0.000 ms | 0.013 ms | results/fio-randwrite.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 1 | 22 s | 82.860 s | -60.860 s | 0.27x | 20.726 s | +1.274 s | 1.06x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 20.726 s | 30.000 s | 50.726 s | 0.00 MiB/s | 100.07 MiB/s | 100.07 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randwrite | 0.000 ms | 0.000 ms | 0.000 ms | 0.004 ms | 0.000 ms | 0.000 ms | 0.006 ms | 0.013 ms | 0.083 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randwrite | evalsha: calls=803, usec/call=49.63<br>exec: calls=1273, usec/call=3.98<br>rpush: calls=774, usec/call=4.26<br>get: calls=2059, usec/call=1.54<br>incrby: calls=774, usec/call=3.51 | diagnostics/redis-fio-randwrite-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randwrite | 0.0% (0/0) | 0.0 MiB | 5076.0 MiB | 3072.0 MiB | 0.0 MiB | 3072.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=508 | GET=0.00 ms, PUT=231.89 ms | diagnostics/stats-fio-randwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=775 ops/5076.0 MiB/68482.4 ms, foreground=stage 68.48s/commit_wait 1.97s, flush_wait=5 ops/0.04s/5 slices, stage_fail=0, commit_before_stage=596, remote_inflight=3072.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=17, max_buffer_dirty=3500.0 MiB, max_live_dirty=892.0 MiB, max_live_slices=131, max_recent_pending=3384.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=232.0 MiB, max_remote_inflight=3384.0 MiB, last_buffer_dirty=3092.0 MiB, slices=create 775/reuse 4302/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=775 avg=6.5 MiB blocks=1.65/batch partial_tail=0.02 (size 0/max 1/flush 5/auto 13/age 0), partial_origin=normal 19/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 13/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 13/cached 0/mixed 0/unknown 0, freeze=size 79/flush 5/auto 589/max 102/age 0 |
| fio-randwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=768 | GET=0.00 ms, PUT=192.46 ms | diagnostics/stats-fio-randwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 775/reuse 4302/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 79/flush 5/auto 589/max 102/age 0 |
| fio-randwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 5076.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1279 | GET=0.00 ms, PUT=208.13 ms | diagnostics/stats-fio-randwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=775 ops/5076.0 MiB/68482.4 ms, foreground=stage 68.48s/commit_wait 1.97s, flush_wait=5 ops/0.04s/5 slices, stage_fail=0, commit_before_stage=596, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 775/reuse 4302/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=775 avg=6.5 MiB blocks=1.65/batch partial_tail=0.02 (size 0/max 1/flush 5/auto 13/age 0), partial_origin=normal 19/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 13/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 13/cached 0/mixed 0/unknown 0, freeze=size 79/flush 5/auto 589/max 102/age 0 |
