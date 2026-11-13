# Next-round source review

Reviewed on 2026-11-13 Asia/Shanghai. The machine-readable receipt records the actual UTC capture time.

No additional confirmed finding was identified in this bounded read-only review. This is a source review, not a replacement for the ongoing matched performance gates or a claim that the repository has no other defects.

## Exact targets

- Issues runtime candidate: `c8f5f16aefe811540782b952481982179a1bfd6f`, tree `142c8d384fbb2939f2cde4e3e7497b122b2c0f53`, `/home/dev/ax-issue447-corrected-candidate`.
- Issues publication child: `e2c964c55fafdcced34af58e840ebddda1cf9ea4`. Its only difference is the previously reviewed post-timing ignored-test `chunks_exact` to `as_chunks` expression. Runtime qualification remains attributed to c8.
- Packed PR 449: `53c725f36db5bcd060ec2483a0abecb943bed4ea`, tree `57b1e56db3918e5e2082a17f7f0f72d1aedada2d`, `/home/dev/ax-packed-prefetch-test-drain`. Relative to measured `7093f141d8081e6b96cd0a80814876aed5a3b577`, only `src/chunk/store/read_amplification_tests.rs` changes. No performance result is relabelled as measured on 53.

Both inspected worktrees were clean. Source hashes and bounded numbered excerpts are recorded separately in `source-receipt.json` and `source-excerpts.txt`.

## Checked behavior

| Area | Source and conclusion |
| --- | --- |
| Metadata fill registration | `workspace_overlay/packed_v3/wire005/read_cache.rs:87`: epoch-and-digest entries are created under the registry mutex, with the Control permit acquired before allocation. The registry mutex ends before backend awaits. The eight-entry limit is per retained-cache instance. |
| Single-flight result lifetime | `read_cache.rs:29,194`: registered followers hold `FillGuard` references. A result too large to retain remains in the fill until those followers consume it; the final guard removes the registry entry before waking a slot contender. The per-key mutex is dropped before the fill guard. |
| Cancellation, errors, and fences | `read_cache.rs:117,195,209` and `budget.rs:205,221`: full-slot waiters, per-key waiters, and active fetches observe an epoch change; close also bumps that epoch. Error/cancellation leaves no successful completed value and allows an existing follower to retry. `index.rs` invalidates the shared epoch before clearing retained caches. No lost wake or lock-order cycle was identified in these paths. |
| Row payload ownership | `meta_layer/row_cache.rs:107,172`: all retained clones share one charge; the payload is dropped before the charge. The heap-bearing fields in InodeDelta, DentryDelta, XattrDelta and PermissionSnapshot match the capacity-based accounting. Eviction, replacement, or invalidation cannot release a payload charge still held by a reader. |
| Row index and pressure | `row_cache.rs:252,282,463`: index reservation tracks the high-water entry count until the backing table is replaced. Peer reclamation uses `try_write`, so it does not wait on a peer's row lock while holding the registry. Each successful pressure step removes an entry; second-chance probing is bounded at eight, and is not exact LRU. |
| Row hit/fence behavior | `row_cache.rs:475,512,525,634`: same-key hits share a read lock. Expiry cleanup rechecks the current entry under its write lock, preventing deletion of a fresh replacement. Insert publication rechecks disabled/generation state. Derived path copies are constructed only after byte admission. Cancellation of an unresolved commit permanently disables both caches through CacheCommitGuard. |
| Writeback completion handoff | `vfs/io/writer.rs:4592,4677,5025`: the metadata-last path checks upload completion under the same slice lock used to publish Committed in the opposite ordering. If upload is already complete, metadata owns retirement and publishes only after the retirement attempt; the marker prevents a late uploader from repeating promotion. If upload is incomplete, Committed is published under that lock and the later uploader owns retirement. There is no check/publish gap recreating the demonstrated permanent-residue race. |
| Writeback scope | The epoch-skip and orphan branches were not broadened into successful promotion. Promotion/removal remain best-effort on disk-I/O failure, as before. The fix does not claim to eliminate every pre-existing metadata-first retirement visibility window or make arbitrary disk failures durable. Existing real FileWriter RED/GREEN and actual FUSE known-A/B proofs remain the behavioral evidence. |
| Packed persistence identity | `cli.rs:2326` names the cache directory using the authenticated manifest digest; object filenames hash the object key within that snapshot directory. The lock protects directory loading/eviction. Complete entries are published through the existing temporary-file/hard-link path; reads still authenticate above this cache. |
| Packed failed-download ownership | `workspace/packed/readonly/pack_cache.rs:167`: a failed entry with live readers/worker stays owned and charged while a new request can use the Range path. Once the registry is the last owner, the failed entry is removed and a later fill is allowed. Its worker retains transport/request ownership through the body. No newly confirmed terminal-entry retry or drain ownership defect was found. |

## Evidence and limits

- Inspected the nearby regression tests for independent/same-key fills, over-capacity sharing, cancellation, failed leaders, close/epoch wakeups, retained payload pinning, shared row budget/peer pressure, TTL replacement, cancelled commits, and both writeback completion orders.
- Existing cleanup RED/GREEN and full-CI receipt: `/home/dev/ax-writeback-commit-cleanup-validation/ci-final.json`, SHA256 `2102568589d8a2608648c6342fb40f936f286e5c1eb5a78aa8a384ddc169755a`.
- Existing real FUSE A/B and forced-freshness trace proofs: `/home/dev/ax-known-tail-trace-01/receipt.json`, SHA256 `8d0be53e535dedbe6802a869f33768df2f09db70d541dfbb6c7fa9cfa5435cb0`. These are previously executed behavioral evidence, not tests rerun by this review.
- This round did not build Rust, run a cloud workload, alter production/source/flags, or create an Issue/PR. It did not fault-inject disk corruption or panics. Static checks are labelled as such.
- The already reproduced Packed registry lock held across HEAD remains a follow-up; this review does not re-report it or infer that it caused readdir timing. The already diagnosed lookup/fstat attribute-cache transition also remains a known item, not a new content-corruption finding.
- Overall performance acceptance stays with the paired workload results and the user's accepted E2E tradeoff. This source review does not turn an incomplete or failed workload gate into a pass.
