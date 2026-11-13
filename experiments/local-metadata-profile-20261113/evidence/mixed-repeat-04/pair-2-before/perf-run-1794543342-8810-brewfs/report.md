# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 20 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 32 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 714.34 MiB/s | 178.58 | 0.000 ms | 62.652 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 420.89 MiB/s | 105.22 | 188.37 MiB/s | 47.09 | 238.027 ms | 127.402 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 11.308 s | +0.000 s | - | 2.867 s | +0.000 s | - | - |
| fio-randrw | 0 | 20 s | 80.087 s | -60.087 s | 0.25x | 20.024 s | -0.024 s | 1.00x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.024 s | 32.000 s | 52.024 s | 162.00 MiB/s | 72.50 MiB/s | 234.51 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 13.566 ms | 0.000 ms | 0.000 ms | 77.070 ms | 238.027 ms | 1166.017 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 21.365 ms | 0.000 ms | 0.000 ms | 37.487 ms | 62.652 ms | 74.973 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 7.897 ms | 0.000 ms | 0.000 ms | 66.322 ms | 127.402 ms | 404.750 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=823, usec/call=60.12<br>exec: calls=1627, usec/call=5.57<br>get: calls=3216, usec/call=1.81<br>rpush: calls=803, usec/call=5.62<br>incrby: calls=803, usec/call=3.99 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 82.3% (28798/34977) | 8428.0 MiB | 3772.0 MiB | 2948.0 MiB | 0.0 MiB | 2948.0 MiB | 0.0 MiB | 0.0 MiB | GET=1715, PUT=208 | GET=164.15 ms, PUT=540.94 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=1715, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=803 ops/3772.0 MiB/628277.9 ms, foreground=stage 628.28s/commit_wait 5.75s, flush_wait=4 ops/0.16s/25 slices, stage_fail=0, commit_before_stage=738, remote_inflight=2948.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=12, max_buffer_dirty=3073.0 MiB, max_live_dirty=479.0 MiB, max_live_slices=100, max_recent_pending=2972.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=900.0 MiB, max_remote_inflight=2984.0 MiB, last_buffer_dirty=2986.0 MiB, slices=create 803/reuse 2969/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=803 avg=4.7 MiB blocks=1.17/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 62/flush 24/auto 696/max 21/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=732 | GET=164.15 ms, PUT=206.91 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 803/reuse 2969/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 62/flush 24/auto 696/max 21/age 0 |
| fio-randrw-post-write-runner-drained | 82.3% (28798/34977) | 8428.0 MiB | 3772.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=1715, PUT=943 | GET=164.15 ms, PUT=280.52 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=1715, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=803 ops/3772.0 MiB/628277.9 ms, foreground=stage 628.28s/commit_wait 5.75s, flush_wait=4 ops/0.16s/25 slices, stage_fail=0, commit_before_stage=738, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 803/reuse 2969/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=803 avg=4.7 MiB blocks=1.17/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 62/flush 24/auto 696/max 21/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1792.0 MiB | 0.0 MiB | 1792.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=75 | GET=0.00 ms, PUT=205.70 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=32 ops/2048.0 MiB/25132.6 ms, foreground=stage 25.13s/commit_wait 0.20s, flush_wait=4 ops/0.21s/4 slices, stage_fail=0, commit_before_stage=32, remote_inflight=1792.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=3, max_buffer_dirty=1582.0 MiB, max_live_dirty=288.0 MiB, max_live_slices=8, max_recent_pending=1344.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=768.0 MiB, max_remote_inflight=1344.0 MiB, last_buffer_dirty=1582.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=32 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=438 | GET=0.00 ms, PUT=217.18 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=215.50 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/25148.0 ms, foreground=stage 25.15s/commit_wait 0.20s, flush_wait=5 ops/0.23s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=17.5x (13.6ms→238ms). Likely S3 retry or cache miss.
