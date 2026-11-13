# randwrite/direct1 drain diagnosis: frozen 601 and 603

Date: 2026-11-13 Asia/Shanghai. Raw timestamps remain Unix nanoseconds/UTC.
This is a read-only diagnosis, not a new performance qualification.
The original full-six-scene AB 601 and BA 603 regressions remain recorded separately.

The repeated regression is real in the recorded accounting: candidate full BrewFS process CPU/GiB is +28.60% in 601 and +11.42% in 603. No timestamp/PID mismatch was found. The strongest next hypothesis is interaction with background work inherited across scene remounts. The current artifacts establish the overlap but do not yet attribute the additional user CPU to a particular function.

## Frozen inputs and reproducibility

- Raw roots: `/home/dev/ax-perf-issues-validation/corrected-runtime-review-{06,08}`.
- Baseline: `4f982f45c153a0c31e3e9cd8ff642ec7659c98fd`.
- Candidate runtime: `c8f5f16aefe811540782b952481982179a1bfd6f` (the mechanically captured full HEAD in `facts.json` is authoritative; no source was changed).
- Run `GIT_OPTIONAL_LOCKS=0 python3 /home/dev/ax-randwrite-drain-diagnosis-01/capture.py` to regenerate `facts.json` from the frozen files. Its input map records every consumed file SHA256, both source HEADs, and source file hashes.
- The capture asserts that each prior randwrite/direct0 remount created the exact PID/starttime measured by randwrite/direct1, that the configuration hashes join, that all nine debt metrics stay zero after their first all-zero sample, and that writer uploaded logical bytes equal fio's actual bytes.

## Observed accounting

CPU seconds are for BrewFS and all its threads, excluding fio and RustFS. The native PUT counter records successful ObjectBlockStore operations and body bytes; it is not an independent HTTP trace and does not enumerate SDK-internal retries.

| Matrix / arm | Logical write MiB | Active CPU s | Drain user / system CPU s | Full CPU s | PUT delta ops | PUT delta payload beyond logical writes, MiB¹ |
|---|---:|---:|---:|---:|---:|---:|
| 601 baseline | 4712 | 12.56 | 18.63 / 3.57 | 34.76 | 1581 | 1600 |
| 601 candidate | 4072 | 10.20 | 24.42 / 4.01 | 38.63 | 1415 | 1584 |
| 603 baseline | 4476 | 11.36 | 21.29 / 4.00 | 36.65 | 1558 | 1724 |
| 603 candidate | 4184 | 10.94 | 23.47 / 3.76 | 38.17 | 1462 | 1664 |

¹ Bookkeeping residual = native PUT body bytes − logical writer upload bytes − 4 bytes × PUT operations. The configured raw object format has a four-byte header. This residual is not itself proof that every extra object was a compaction object.

In all four arms, the first all-zero sample of the nine writer debt metrics occurs about 25 seconds into drain. Successful PUTs continue after that:

| Matrix / arm | Subsequent PUTs | Subsequent payload MiB² | Remaining time to strict drain end |
|---|---:|---:|---:|
| 601 baseline | 224 | 896 | 25.333 s |
| 601 candidate | 240 | 960 | 29.912 s |
| 603 baseline | 272 | 1088 | 31.688 s |
| 603 candidate | 272 | 1088 | 31.777 s |

² Each also carries its four-byte raw-object header. The exact body-byte values and sample timestamps are preserved in `facts.json`.

The current process already has 320/336/324/384 MiB of successful PUT body payload before the four respective randwrite/direct1 tools begin, while its FUSE write bytes and writeback stage bytes are zero. Its PID is the new process started by the preceding randwrite/direct0 cold durability check. Only 0.1105/0.1216/0.1096/0.1104 seconds separate that check's completion from the next tool's start.

Candidate has fewer stage/upload batches in both pairs (642→511 and 621→535), fewer uploaded blocks (1181→1019 and 1127→1046), and fewer partial-tail operations (6→2 and 16→0). Therefore, “candidate created more partial tails or more upload batches” is contradicted by these counters. Redis commandstats cover only the active window: evalsha calls and server microseconds are lower in candidate in both pairs. They do not measure drain metadata CPU.

The increased `writeback_stage_lat_us_total` is not a pure disk latency measurement. `writer.rs:4430–4455` measures the awaited persist → seal → `try_commit_before_upload_if_front` chain, summed over overlapping tasks. It cannot be equated to CPU seconds or attributed to storage alone.

## Source and lifecycle chain

All source files below, except the containing writer file, are byte-identical between the two measured heads; the relevant normal writer/cleanup spans were separately verified in `/home/dev/ax-standard-write-path-review-01`.

1. Archived container harness `run-measured-perf-in-container.sh:2128` gives each scene a distinct `.perf-<tool>` directory. Lines 2187–2189 remove only the current directory. Prior scene files remain in the same Redis database and S3 bucket.
2. The prior durability check restarts the same volume with a fresh local cache. Lines 2085–2088 deliberately carry that new cache into the next tool. The recorded prior `new_mount` and current CPU PID/starttime are identical. Thus local cache isolation does not isolate namespace history or background jobs between scenes.
3. `src/meta/config.rs:202` defaults `no_background_jobs=false`; `:550` defaults the compaction interval to 600 seconds. `src/vfs/fs/mod.rs:1237–1240` starts background jobs on mount. `src/chunk/compact/worker.rs:247–258` uses Tokio interval, whose first tick is immediate, and starts a compaction cycle. The worker scans existing namespace chunks (`:287–298`).
4. `src/chunk/compact/compactor.rs:224–258` reads and merges metadata-referenced slices, then uploads the merged data. `:368–381` writes blocks through the same BlockStore and upload semaphore. It does not enter FileWriter's stage/upload debt accounting. `src/chunk/store.rs:1373–1398` counts successful uploads in the same ObjectStoreMetrics later published to `.stats` (`src/vfs/fs/mod.rs:1620–1630`). Raw framing is `src/chunk/compress.rs:247`.
5. The archived strict-drain helper requires both clear writer debt and an unchanged global PUT operation counter across three observations (`:1179–1199`). Its process CPU endpoint is recorded only afterward (`:1200–1201`). Therefore any background compaction in this interval is intentionally included in the measured full process CPU and wall time.
6. Metrics are mirrored into `.stats` every 100 ms (`src/vfs/fs/mod.rs:1459`). This sampling cannot label the exact owner of each upload. The archive has neither compaction-specific native counts nor successful INFO compaction events; formal logs were at WARN level.

The chain explains how prior history can enter the current measurement and matches the observed pre-tool PUTs and post-writer-debt PUT tail. It does not prove which old files were compacted, nor that compaction explains the candidate's extra CPU. Some work may also reflect the current namespace scan or other common background mechanisms. CPU attribution needs the separately authorized profile.

## What has been excluded, and what remains open

- CPU snapshots retain the same PID/starttime and expected order. Their timestamp offsets from the paired phase boundaries range from −9.161 ms to +16.904 ms; no seconds-long window mismatch was found. CPU tick resolution is 10 ms.
- The normal FUSE write path passes `creation_unique=0` and bypasses the changed cached watermark algorithm. This is a source/config inference; formal 601/603 have no FUSE per-operation log to prove every request flag. It limits one hypothesis but does not excuse the measured regression.
- No post-drain system/cgroup series is recorded. Active-window memory pressure must not be extrapolated into the drain interval. Likewise three CPU endpoints cannot split drain CPU before versus after the writer debt reaches zero.
- No extra partial-tail/batch count or active Redis transaction count supports a candidate-only increase in work. Candidate's longer stage wall sum and larger drain user CPU still need explanation.
- Comparable aggregate PUT bytes do not imply identical physical work: different prior time-based operation counts can leave different slice histories, and compaction may reread superseded slices before writing a common-size merged range.

## Ranked tests and fair follow-up

1. **Authorized diagnostic profile, original four-scene prefix retained.** Keep default compaction, exact binaries/budgets, and both arms. Profile all process threads across active and strict drain, align samples to original phase timestamps and the first nine-debt-zero observation, and retain the global PUT trace. Check whether drain user samples concentrate in compactor merge/read/checksum/copy, regular writer cleanup/cache operations, or another subsystem. This run is attribution evidence, not a replacement acceptance number.
2. **Close provenance if the profile points to compaction.** Match compaction completion chunk IDs/timestamps, metadata slice history or object keys to the preceding scene's retained files. Limited diagnostic INFO logging may provide those events; it must be identical for both arms and its overhead disclosed. Existing WARN-only logs cannot supply them retroactively.
3. **If prior-history contamination is confirmed, add a separately named per-scene guard.** Use a fresh namespace/backend prefix and owned cache for each scene, or restore an identical frozen metadata/object fixture for both arms. Retain normal background mechanisms. Record setup and initial background work separately, then measure the scene and all its resulting drain/maintenance. Do not disable compaction to make the original regressions disappear. Preserve the original full-matrix result as the distinct cumulative-history workload it actually measures.

No frozen Rust code, cloud process, formal runner or raw evidence was modified in this review. The original standard guard remains HOLD pending attribution and a matched follow-up decided by the parent agent.

## Available existing diagnostic logging

An exact, bounded filter is `RUST_LOG=warn,brewfs::chunk::compact::worker=debug,brewfs::cadapter::s3=debug`. This is proposed only for the new diagnostic pair.

- `worker.rs:158–160` emits successful lock acquisition with chunk ID, TTL, slice count and sync mode. `:349` records an async lock; `:362/:368` records light/heavy completion with chunk ID. There is no existing inode/task ID/new slice ID field in those successful events.
- `s3.rs:760–765` instruments `put_object_vectored_simple` at DEBUG with object key and actual body size. The measured 4 MiB blocks plus header stay below the unchanged 16 MiB multipart cutoff (`:1219–1222`).
- `cli.rs:374` already uses `FmtSpan::CLOSE` for the non-profiling build's file logger, so enabling those spans exposes close timestamps and busy/idle duration without a Rust change. These are async span timing observations, not CPU samples or full HTTP retry traces.
- If needed, `brewfs::chunk::store=trace` adds the normal `ObjectBlockStore.write_fresh_vectored` span (`store.rs:1457`); compaction uses the non-instrumented `write_fresh_range` wrapper (`:1467`). This produces substantially more read-side logs, and absence of a parent span is not sufficient provenance proof on its own.

Use completed chunk metadata/object mapping plus the profile and event boundaries to establish provenance. A CRC/SHA leaf by itself cannot distinguish foreground data preparation from background compaction.
