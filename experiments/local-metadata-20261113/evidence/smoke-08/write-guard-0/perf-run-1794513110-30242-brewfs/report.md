# BrewFS Perf Report

Meta backend: redis

## Summary

| Tool | Status | Seconds | Log |
| --- | --- | ---: | --- |
| fio-seqwrite | pass | 20 | tools/fio-seqwrite.log |
| fio-randwrite | pass | 21 | tools/fio-randwrite.log |
| fio-randrw | pass | 22 | tools/fio-randrw.log |

## Post-Write Drain

| Tool | Drain seconds | Pending bytes | Dirty bytes | Buffer dirty bytes |
| --- | ---: | ---: | ---: | ---: |
| fio-seqwrite | 32 | 0 | 0 | 0 |
| fio-randwrite | 14 | 0 | 0 | 0 |
| fio-randrw | 27 | 0 | 0 | 0 |

## Fio

| Tool | Workload | Direct | BS | Jobs | Read BW | Read IOPS | Write BW | Write IOPS | Read P99 | Write P99 | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |---: | ---: | ---: |
| fio-randrw-prefill | write | 0 | 4m | 4 | 0.00 B/s | 0.00 | 80.73 MiB/s | 20.18 | 0.000 ms | 1400.898 ms | results/fio-randrw-prefill.json |
| fio-randrw | randrw | 0 | 4m | 4 | 227.14 MiB/s | 56.78 | 110.18 MiB/s | 27.55 | 1010.827 ms | 396.362 ms | results/fio-randrw.json |
| fio-randwrite | randwrite | 0 | 4m | 4 | 0.00 B/s | 0.00 | 88.59 MiB/s | 22.15 | 0.000 ms | 3204.448 ms | results/fio-randwrite.json |
| fio-seqwrite | write | 0 | 4m | 1 | 0.00 B/s | 0.00 | 233.49 MiB/s | 58.37 | 0.000 ms | 42.205 ms | results/fio-seqwrite.json |

## Fio Runtime Accounting

Use `active_io_runtime` for close/flush tail detection; fio `job_runtime_ms` can aggregate multiple jobs under group reporting.

| Tool | Direct | Script wall | fio job_runtime | wall-job_runtime | wall/job_runtime | active_io_runtime | wall-active_io | wall/active_io | Tail marker |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw-prefill | 0 | 0 s | 100.064 s | +0.000 s | - | 25.370 s | +0.000 s | - | - |
| fio-randrw | 0 | 22 s | 82.404 s | -60.404 s | 0.27x | 20.657 s | +1.343 s | 1.07x | fio job_runtime aggregates jobs |
| fio-randwrite | 0 | 21 s | 80.533 s | -59.533 s | 0.26x | 20.138 s | +0.862 s | 1.04x | fio job_runtime aggregates jobs |
| fio-seqwrite | 0 | 20 s | 20.060 s | -0.060 s | 1.00x | 20.061 s | -0.061 s | 1.00x | fio job_runtime aggregates jobs |

## Fully Drained Write Throughput

Actual fio bytes divided by `active_io_runtime + post_write_drain`; unlike foreground bandwidth, this includes the time required to empty the filesystem writeback queue.

| Tool | Active I/O | Drain | Complete | Read | Write | Total | Raw |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 20.657 s | 27.000 s | 47.657 s | 98.45 MiB/s | 47.76 MiB/s | 146.21 MiB/s | fully-drained-throughput.tsv |
| fio-randwrite | 20.138 s | 14.000 s | 34.138 s | 0.00 MiB/s | 52.26 MiB/s | 52.26 MiB/s | fully-drained-throughput.tsv |
| fio-seqwrite | 20.061 s | 32.000 s | 52.061 s | 0.00 MiB/s | 89.97 MiB/s | 89.97 MiB/s | fully-drained-throughput.tsv |

## Latency Percentiles

### Read

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 13.697 ms | 0.000 ms | 0.000 ms | 198.181 ms | 1010.827 ms | 1904.214 ms |

### Write

| Workload | p1 | p5 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fio-randrw-prefill | 0.000 ms | 0.000 ms | 0.000 ms | 109.576 ms | 0.000 ms | 0.000 ms | 683.672 ms | 1400.898 ms | 1887.437 ms |
| fio-randrw | 0.000 ms | 0.000 ms | 0.000 ms | 6.062 ms | 0.000 ms | 0.000 ms | 79.167 ms | 396.362 ms | 1786.774 ms |
| fio-randwrite | 0.000 ms | 0.000 ms | 0.000 ms | 66.322 ms | 0.000 ms | 0.000 ms | 591.397 ms | 3204.448 ms | 4009.755 ms |
| fio-seqwrite | 0.000 ms | 0.000 ms | 0.000 ms | 10.682 ms | 0.000 ms | 0.000 ms | 33.161 ms | 42.205 ms | 206.569 ms |

## Redis Diagnostics

| Tool | Top commandstats | Details |
| --- | --- | --- |
| fio-randrw | evalsha: calls=525, usec/call=147.56<br>incrby: calls=505, usec/call=151.88<br>rpush: calls=505, usec/call=106.65<br>exec: calls=1302, usec/call=5.08<br>get: calls=2820, usec/call=1.60 | diagnostics/redis-fio-randrw-after.txt |
| fio-randwrite | evalsha: calls=375, usec/call=84.97<br>rpush: calls=355, usec/call=7.98<br>exec: calls=450, usec/call=5.94<br>get: calls=817, usec/call=2.01<br>incrby: calls=355, usec/call=2.66 | diagnostics/redis-fio-randwrite-after.txt |
| fio-seqwrite | exec: calls=1172, usec/call=6.38<br>evalsha: calls=96, usec/call=65.91<br>get: calls=1249, usec/call=3.06<br>multi: calls=1172, usec/call=1.57<br>hget: calls=1191, usec/call=1.23 | diagnostics/redis-fio-seqwrite-after.txt |

## BrewFS Stats

Counters are per-tool deltas when matching before/after snapshots exist; dirty, live, and inflight values are after-tool gauges.

| Tool | Cache hit | FUSE read | FUSE write | Dirty | Live dirty | Recent pending | Recent uploaded | Read buffer | S3 ops | S3 avg latency | Details |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fio-randrw | 74.9% (23839/31817) | 4692.0 MiB | 2276.0 MiB | 1816.0 MiB | 0.0 MiB | 1816.0 MiB | 0.0 MiB | 0.0 MiB | GET=1029, PUT=115 | GET=84.76 ms, PUT=978.72 ms | diagnostics/stats-fio-randrw-after.txt; range=0, full=1029, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=505 ops/2276.0 MiB/190021.6 ms, foreground=stage 190.02s/commit_wait 23.17s, flush_wait=4 ops/0.65s/19 slices, stage_fail=0, commit_before_stage=462, remote_inflight=1816.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=11, max_buffer_dirty=1377.0 MiB, max_live_dirty=322.0 MiB, max_live_slices=71, max_recent_pending=1360.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=120.0 MiB, max_remote_inflight=1368.0 MiB, last_buffer_dirty=1377.0 MiB, slices=create 505/reuse 1771/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=505 avg=4.5 MiB blocks=1.13/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 41/flush 4/auto 456/max 4/age 0 |
| fio-randrw-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=454 | GET=84.76 ms, PUT=309.62 ms | diagnostics/stats-fio-randrw-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 505/reuse 1771/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 41/flush 4/auto 456/max 4/age 0 |
| fio-randrw-post-write-runner-drained | 74.9% (23839/31817) | 4692.0 MiB | 2276.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=1029, PUT=569 | GET=84.76 ms, PUT=444.85 ms | diagnostics/stats-fio-randrw-post-write-runner-drained-after.txt; range=0, full=1029, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=505 ops/2276.0 MiB/190021.6 ms, foreground=stage 190.02s/commit_wait 23.17s, flush_wait=4 ops/0.65s/19 slices, stage_fail=0, commit_before_stage=462, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 505/reuse 1771/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=505 avg=4.5 MiB blocks=1.13/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 41/flush 4/auto 456/max 4/age 0 |
| fio-randrw-prefill | 0.0% (0/0) | 0.0 MiB | 2048.0 MiB | 966.0 MiB | 0.0 MiB | 966.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=282 | GET=0.00 ms, PUT=483.36 ms | diagnostics/stats-fio-randrw-prefill-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=55 ops/1795.0 MiB/128908.9 ms, foreground=stage 128.91s/commit_wait 3.66s, flush_wait=4 ops/0.29s/4 slices, stage_fail=0, commit_before_stage=59, remote_inflight=966.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=9, max_buffer_dirty=1069.0 MiB, max_live_dirty=229.0 MiB, max_live_slices=6, max_recent_pending=1024.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=407.0 MiB, max_remote_inflight=1060.0 MiB, last_buffer_dirty=997.0 MiB, slices=create 489/reuse 8028/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=59 avg=34.7 MiB blocks=8.90/batch partial_tail=0.44 (size 11/max 0/flush 0/auto 15/age 0), partial_origin=normal 26/cached 0/mixed 0/unknown 0, auto_detail=age 15/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 15/cached 0/mixed 0/unknown 0, freeze=size 130/flush 21/auto 336/max 2/age 0 |
| fio-randrw-prefill-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=243 | GET=0.00 ms, PUT=257.19 ms | diagnostics/stats-fio-randrw-prefill-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=4 ops/253.0 MiB/7011.3 ms, foreground=stage 7.01s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 489/reuse 8028/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 130/flush 21/auto 336/max 2/age 0 |
| fio-randrw-prefill-runner-drained | 0.0% (0/0) | 0.0 MiB | 8516.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=2152 | GET=0.00 ms, PUT=289.66 ms | diagnostics/stats-fio-randrw-prefill-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=489 ops/8516.0 MiB/1384163.6 ms, foreground=stage 1384.16s/commit_wait 42.28s, flush_wait=10 ops/1.13s/29 slices, stage_fail=0, commit_before_stage=460, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 489/reuse 8028/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=489 avg=17.4 MiB blocks=4.40/batch partial_tail=0.09 (size 11/max 0/flush 2/auto 31/age 0), partial_origin=normal 44/cached 0/mixed 0/unknown 0, auto_detail=age 19/idle 12/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 31/cached 0/mixed 0/unknown 0, freeze=size 130/flush 21/auto 336/max 2/age 0 |
| fio-randwrite | 0.0% (0/0) | 0.0 MiB | 1784.0 MiB | 792.0 MiB | 0.0 MiB | 792.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=255 | GET=0.00 ms, PUT=400.03 ms | diagnostics/stats-fio-randwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=355 ops/1784.0 MiB/1239249.0 ms, foreground=stage 1239.25s/commit_wait 38.31s, flush_wait=4 ops/0.81s/23 slices, stage_fail=0, commit_before_stage=326, remote_inflight=792.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=8, max_buffer_dirty=812.0 MiB, max_live_dirty=615.0 MiB, max_live_slices=111, max_recent_pending=738.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=735.0 MiB, max_remote_inflight=707.0 MiB, last_buffer_dirty=812.0 MiB, slices=create 430/reuse 6039/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=355 avg=5.0 MiB blocks=1.28/batch partial_tail=0.05 (size 0/max 0/flush 1/auto 16/age 0), partial_origin=normal 17/cached 0/mixed 0/unknown 0, auto_detail=age 4/idle 12/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 16/cached 0/mixed 0/unknown 0, freeze=size 98/flush 21/auto 309/max 2/age 0 |
| fio-randwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=193 | GET=0.00 ms, PUT=232.86 ms | diagnostics/stats-fio-randwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 430/reuse 6039/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 98/flush 21/auto 309/max 2/age 0 |
| fio-randwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 6468.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1627 | GET=0.00 ms, PUT=260.94 ms | diagnostics/stats-fio-randwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=430 ops/6468.0 MiB/1248243.4 ms, foreground=stage 1248.24s/commit_wait 38.62s, flush_wait=6 ops/0.84s/25 slices, stage_fail=0, commit_before_stage=401, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 430/reuse 6039/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=430 avg=15.0 MiB blocks=3.78/batch partial_tail=0.04 (size 0/max 0/flush 2/auto 16/age 0), partial_origin=normal 18/cached 0/mixed 0/unknown 0, auto_detail=age 4/idle 12/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 16/cached 0/mixed 0/unknown 0, freeze=size 98/flush 21/auto 309/max 2/age 0 |
| fio-seqwrite | 0.0% (0/0) | 0.0 MiB | 4684.0 MiB | 3019.0 MiB | 11.0 MiB | 3008.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=417 | GET=0.00 ms, PUT=283.04 ms | diagnostics/stats-fio-seqwrite-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=73 ops/4672.0 MiB/8969.9 ms, foreground=stage 8.97s/commit_wait 0.31s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=73, remote_inflight=3008.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=16, max_buffer_dirty=3074.0 MiB, max_live_dirty=89.0 MiB, max_live_slices=2, max_recent_pending=3072.0 MiB, max_recent_uploaded=0.0 MiB, max_stage_inflight=128.0 MiB, max_remote_inflight=3072.0 MiB, last_buffer_dirty=3072.0 MiB, slices=create 75/reuse 4609/reject_unique 0/reject_prefix 0, live_slices=1 avg=11.0 MiB, origin=normal 1/cached 0/mixed 0/unknown 0, upload_batch=73 avg=64.0 MiB blocks=16.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 73/flush 1/auto 0/max 0/age 0 |
| fio-seqwrite-post-write-drained | 0.0% (0/0) | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=753 | GET=0.00 ms, PUT=208.78 ms | diagnostics/stats-fio-seqwrite-post-write-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=0 ops/0.0 MiB/0.0 ms, foreground=stage 0.00s/commit_wait 0.00s, flush_wait=0 ops/0.00s/0 slices, stage_fail=0, commit_before_stage=0, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 75/reuse 4610/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=0 avg=n/a blocks=0.00/batch partial_tail=0.00 (size 0/max 0/flush 0/auto 0/age 0), partial_origin=normal 0/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 73/flush 2/auto 0/max 0/age 0 |
| fio-seqwrite-post-write-runner-drained | 0.0% (0/0) | 0.0 MiB | 4684.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | GET=0, PUT=1172 | GET=0.00 ms, PUT=235.37 ms | diagnostics/stats-fio-seqwrite-post-write-runner-drained-after.txt; range=0, full=0, bg_prefetch=0, persistent_slice=0 ops/0.0 MiB, stage=75 ops/4684.0 MiB/8994.4 ms, foreground=stage 8.99s/commit_wait 0.31s, flush_wait=2 ops/0.03s/2 slices, stage_fail=0, commit_before_stage=75, remote_inflight=0.0 MiB, buffer_sleep=soft 0/0.0ms moderate 0/0.0ms hard 0/0.0ms, samples=n/a, slices=create 75/reuse 4610/reject_unique 0/reject_prefix 0, live_slices=0 avg=n/a, origin=normal 0/cached 0/mixed 0/unknown 0, upload_batch=75 avg=62.5 MiB blocks=15.63/batch partial_tail=0.01 (size 0/max 0/flush 1/auto 0/age 0), partial_origin=normal 1/cached 0/mixed 0/unknown 0, auto_detail=age 0/idle 0/pressure 0/too_many 0/buffer_high 0/flush_duration 0/unknown 0, auto_origin=normal 0/cached 0/mixed 0/unknown 0, freeze=size 73/flush 2/auto 0/max 0/age 0 |

## Bottleneck Analysis

- **fio-randrw-prefill**: Write P99=1401ms > 500ms — inspect writeback samples and tune upload queue depth, slice age, or write buffer.
- **fio-randrw**: Read tail latency p99/p50=73.8x (13.7ms→1011ms). Likely S3 retry or cache miss.
- **fio-randrw**: Write stall p50=6.1ms p99=396ms — auto_flush/buffer-limit triggers S3 upload backpressure.
- **fio-randwrite**: Write P99=3204ms > 500ms — inspect writeback samples and tune upload queue depth, slice age, or write buffer.
