The complete twelve-scene corrected trial601 passes correctness. Performance qualification remains on hold pending the three direct1 concerns and the newly measured target workloads.

The independent review rehashed 1,236 payload files plus the manifest (1,237 archive members) and passed 2,235 checks. It reconstructed CPU from the three raw process snapshots, elapsed times from phase boundaries, bytes and p99 from FIO, and checked cold SHA, normal remount, strict drain, service errors, capacity and identity. Runtime sources remain baseline `4f982f45` and candidate `c8f5f16a`; publication source `e2c964c5` differs only by the separately verified test change.

| Scene | Full CPU/GiB change | Drained logical throughput change | Write total-latency p99 change |
|---|---:|---:|---:|
| seqwrite/direct0 | +0.66% | -2.25% | -2.72% |
| seqwrite/direct1 | +0.38% | +1.58% | +12.03% |
| randwrite/direct0 | +1.73% | +0.72% | -4.89% |
| randwrite/direct1 | +28.60% | -11.62% | -25.94% |
| randrw/direct0 | +4.63% | +1.33% | -24.12% |
| randrw/direct1 | +0.24% | +1.83% | +28.57% |

For randwrite/direct1, CPU rises from 34.76 to 38.63 seconds while logical writes fall from 4,940,890,112 to 4,269,801,472 bytes. Active CPU falls from 12.56 to 10.20 seconds; post-return drain CPU rises from 22.20 to 28.43 seconds. This is a measured difference of seconds, not a few CPU ticks. The shorter total elapsed time alone does not establish improvement. No cause is assigned from this single pair.

The original seqwrite profile uses one 512MiB job; randwrite and randrw use four. All use the same 4MiB block size, depth1, io_uring, 20-second time-based policy and seed. Completed bytes vary. Mixed-workload CPU is normalized by combined logical read and write bytes; these are repeated operation bytes, not unique file size or S3 transfer bytes. Cold durability verification is outside the timed boundaries.

The new disk is common to both arms: 512GiB, advertised 27,400 IOPS and throughput 350 in the API's units. Minimum sampled free space was 306,096,398,336 bytes for baseline and 309,263,568,896 for candidate, above the 20% floor. These are sampled minima, not continuous peaks.

A reduced direct1-only follow-up changes preceding workload history. Evaluate each matched pair within that condition and preserve the original six-scene result. Do not combine their absolute timings or average away a regression. Workspace and long-row qualification remain pending; legacy row cache byte-budget enforcement must not be claimed.
