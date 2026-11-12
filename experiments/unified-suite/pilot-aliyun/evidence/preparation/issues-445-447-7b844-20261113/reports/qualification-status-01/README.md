# Three-Issue qualification: publication held

The integrated candidate is 7b844504 (tree e72618af) against clean ca07159. Full CI passed, and the actual targeted cache/writer evidence now shows useful improvements. The combined PR remains unqualified because the mandatory ordinary-volume random-write guard exposes different bytes before and after a fresh-cache remount in both binaries.

| Target | Valid evidence | Limit |
|---|---|---|
| #445 independent fills | Six controlled actual Shared workspace pairs: 418.046 → 68.154 ms; peak target concurrency 1 → 8; exactly one fetch per target | Includes leaf lookup/open, 4KiB read and close; exact GroupMeta Range delay is controlled50ms; not a natural-S3 throughput claim |
| #446 row byte cap | Retained max524046B within512KiB; all32KiB xattr values correct. Longer128key follow-up, 65536 operations/thread, six pairs:1thread3.700→3.643s;8threads13.315→12.843s;0 timed misses, no pair >5% slower | Earlier short rounds showed+11.7/+7.5% median and remain preserved. Main gain is retained memory budgeting, not an RSS/CPU claim |
| #447 cached-write accounting | Three new EC4 actual4096×4KiB pairs: complete write+strictdrain BrewFS CPU falls20.4%,15.7%,18.0%; all remote coldSHA gates pass | Wall remains about5.26s; do not claim wall speedup. Older single-disk numbers were invalidated |

The source, compiler, feature/profile, binary and tools/service identities are pinned in status.json and the archived receipts. Both arms use the same configured byte budgets and fixed data/seed. The new valid service topology uses four RustFS directories on one physical disk with the explicit bypass; it is a local correctness/performance fixture, not a four-disk reliability test.

The original baseline regression passed buffered/direct sequential-write cold checks, then failed four-job buffered random write. Exact isolated candidate402 → baseline403 repeats both fail. Each file is512MiB;20-second workload,4MiB operations,io_uring depth1,seed20261031,kernel writeback disabled. Client upload/recovery logs and service5xx/ERROR gates are clean. No explicit end_fsync is enabled here; the observed issue is normal close + strict drain + remount visibility inconsistency.

Read-only inspection found three4MiB candidate and two4MiB baseline sealed staging records remaining under the old cache. Their1MiB hashes exactly cover every warm/cold difference and equal the warm view. They all sit at offset60MiB within64MiB chunks. fio's stamped start times are earlier in the warm/sealed bytes than in the cold view, consistent with a stale local overlay. These are evidence, not a declaration that either warm or cold is a complete oracle. The likely commit/upload cleanup race is recorded as a hypothesis pending a deterministic regression.

Frozen source7b was not modified to chase this failure. Both diagnostic service projects are stopped; their volumes and stopped perf containers remain for inspection. Parent owns all final cloud cleanup. Do not publish the integrated performance PR until the unresolved standard guards are handled and the resulting source is requalified.
