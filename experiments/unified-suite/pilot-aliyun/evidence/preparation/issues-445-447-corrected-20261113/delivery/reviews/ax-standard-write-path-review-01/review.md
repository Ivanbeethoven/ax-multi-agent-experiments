# Standard601 write-path review

The original direct=1 regression remains unexplained; the unchanged-source findings below do not negate the measurements. Trial603 is the complete original six-scene matrix in candidate→baseline order, with the same direct=0 predecessors. Prepared subset602 was neither deployed nor executed.

The exact corrected runtime heads are baseline4f982f45 and candidatec8f5f16a. Both already contain the same writeback commit-cleanup fix. Publicatione2 changes only an ignored test expression and is not the measured binary identity.

## What is established

- Original601 manifests and trial receipts select an ordinary Redis/S3 volume, leave volume-format/workspace selectors empty, and set `BREWFS_FUSE_WRITEBACK=0`, `BREWFS_FUSE_OP_LOG=0`. The recorded backend uses commit-before-upload, 64MiB chunks, 4MiB blocks, the same cache/upload budgets and RustFS hint. No new runtime branch trace was collected.
- `src/fuse/mount.rs:38,76` derives kernel writeback-cache negotiation from that environment value. The actual write dispatch nevertheless checks `write_flags & FUSE_WRITE_CACHE` at `src/fuse/mod.rs:992`; neither fio's direct setting nor a nonzero request ID substitutes for this check.
- If that flag is absent and a handle exists, the source chain is `fuse/mod.rs:1028` → VFS `write_with_unique` → `vfs/handles.rs:518` → `FileWriter::write_at_with_unique` (`writer.rs:2836`). That method passes **creation_unique=0**, while carrying `request_unique` separately for observation. The stateless normal path reaches the same method. This is a source-conditional fact, not a measurement of every601 opcode.
- If WRITE_CACHE is present, `write_cached_ino` passes the actual request unique to `write_at_cached` (`writer.rs:2851`), which calls `allowed_cached_write_ranges`. Only its nonzero creation-unique branch reaches the changed watermark algorithm (`writer.rs:2451,2956`). Therefore there is no justification to label cached_unique as measured zero; no raw counter/trace records every such value in601.
- The sampled cached-only wait/partial-tail, cached-fragmentation, and older-unique rejection fields are zero in all archived direct1 snapshots. These are event-specific counters and end-point gauges. They support the ordinary-path interpretation but are not a complete branch-coverage proof.

## Changed code versus standard drain

The full `4f→c8` diff places #445 in retained Packed wire005 metadata and #446 in WorkspaceMetaLayer row caching. They are not selected by the ordinary volume configuration. #447 changes the cached-write watermark representation and `Inner::allowed_cached_write_ranges`; it does not add a normal-write or upload/commit scan. The container's full binary can still differ in generated code/layout, so this call-chain observation cannot establish performance equivalence.

Mechanical byte comparisons confirm that six source spans are identical between4f andc8: normal/cached entry methods, strict flush wait, staging/upload task, cleanup helpers, commit-before-upload completion handoff, and commit task. `fuse/mod.rs`, `fuse/mount.rs`, `vfs/handles.rs`, and `vfs/fs/mod.rs` are also byte-identical. The receipt records the exact delimiters and SHA values. `clear()` clears the same empty-or-populated watermark map but its value type changed; there is no observed map-entry count for601.

The common staging/upload/metadata/promotion machinery still does real work during post-tool drain. For randwrite/direct1, source-independent601 accounting is:

| Metric | Baseline | Candidate |
| --- | ---: | ---: |
| Active CPU | 12.56s | 10.20s |
| Post-tool drain CPU | 22.20s | 28.43s |
| Total CPU | 34.76s | 38.63s |
| Actual fio write bytes | 4,940,890,112 | 4,269,801,472 |

The candidate completes fewer bytes while spending more total CPU; the CPU/GiB increase and drained-throughput reduction cannot be dismissed as a small tick effect or inferred away because the suspected optimized method is probably inactive. If603 preserves the regression, the next diagnosis should keep active/drain boundaries and compare actual stage/upload/object counts and a targeted profile of the common drain path. No root cause is asserted here, and no extra cloud workload was started.
