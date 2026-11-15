# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-randrw | pass | 21 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-randrw | 39 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 770.50 MiB/s | 192.63 | 0.000 ms | 58.982 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 546.26 MiB/s | 136.57 | 244.73 MiB/s | 61.18 | 158.335 ms | 84.410 ms | results/fio-randrw.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 10.528 s | +0.000 s | - | 2.658 s | +0.000 s | - | - |
| fio-randrw | 0 | 21 s | 80.155 s | -59.155 s | 0.26x | 20.071 s | +0.929 s | 1.05x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.071 s | 39.000 s | 59.071 s | 185.61 MiB/s | 83.15 MiB/s | 268.76 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 11.338 ms | 0.000 ms | 0.000 ms | 66.322 ms | 158.335 ms | 442.499 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 19.005 ms | 0.000 ms | 0.000 ms | 40.632 ms | 58.982 ms | 87.556 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 6.980 ms | 0.000 ms | 0.000 ms | 35.914 ms | 84.410 ms | 2399.142 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=1055, usec/call=58.12<br>exec: calls=2300, usec/call=5.41<br>get: calls=4490, usec/call=1.71<br>rpush: calls=1031, usec/call=5.54<br>incrby: calls=1031, usec/call=4.00 | diagnostics/redis-fio-randrw-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 87.1% (43573/50029) | 10964.0 MiB | 4912.0 MiB | 3096.0 MiB | 0.0 MiB | 3096.0 MiB | 0.0 MiB | 0.0 MiB | GET=2531, PUT=455 | GET=12.63 ms, PUT=243.32 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=2531, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1031 ops/4912.0 MiB/31131.0 ms, foreground=stage 31.13s/commit_wait 5.46s, flush_wait=4 ops/0.36s/24 slices, stage_fail=0, commit_before_stage=882, remote_inflight=3096.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=16, max_buffer_dirty=3311.0 MiB, max_live_dirty=439.0 MiB, max_live_slices=95, max_recent_pending=3193.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=28.0 MiB, max_remote_inflight=3193.0 MiB, last_buffer_dirty=3076.0 MiB, slices=create 1031/reuse 3881/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1031 avg=4.8 MiB blocks=1.19/batch partial_tail=0.00 (size 0/max 0/flush 1/auto 1/age 0), partial_origin=normal 2/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 1/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 1/cached 0/mixed 0/unknown 0, freeze=size 76/flush 24/auto 905/max 26/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=774 | GET=12.63 ms, PUT=257.96 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1031/reuse 3881/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 76/flush 24/auto 905/max 26/age 0 |
| fio-randrw-post-write-runner-drained | 87.1% (43573/50029) | 10964.0 MiB | 4912.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=2531, PUT=1229 | GET=12.63 ms, PUT=252.54 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=2531, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=1031 ops/4912.0 MiB/31131.0 ms, foreground=stage 31.13s/commit_wait 5.46s, flush_wait=4 ops/0.36s/24 slices, stage_fail=0, commit_before_stage=882, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 1031/reuse 3881/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=1031 avg=4.8 MiB blocks=1.19/batch partial_tail=0.00 (size 0/max 0/flush 1/auto 1/age 0), partial_origin=normal 2/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 1/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 1/cached 0/mixed 0/unknown 0, freeze=size 76/flush 24/auto 905/max 26/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 1792.0 MiB | 0.0 MiB | 1792.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=79 | GET=0.00 ms, PUT=181.37 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/24550.6 ms, foreground=stage 24.55s/commit_wait 0.30s, flush_wait=5 ops/0.23s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=1792.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=3, max_buffer_dirty=1574.0 MiB, max_live_dirty=233.0 MiB, max_live_slices=7, max_recent_pending=1408.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=768.0 MiB, max_remote_inflight=1536.0 MiB, last_buffer_dirty=1574.0 MiB, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=434 | GET=0.00 ms, PUT=202.31 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=513 | GET=0.00 ms, PUT=199.09 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=33 ops/2048.0 MiB/24550.6 ms, foreground=stage 24.55s/commit_wait 0.30s, flush_wait=5 ops/0.23s/5 slices, stage_fail=0, commit_before_stage=33, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 33/reuse 2016/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=33 avg=62.1 MiB blocks=15.55/batch partial_tail=0.03 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 32/flush 1/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw**: Read tail latency p99/p50=14.0x (11.3ms→158ms). Likely S3 retry or cache miss.
