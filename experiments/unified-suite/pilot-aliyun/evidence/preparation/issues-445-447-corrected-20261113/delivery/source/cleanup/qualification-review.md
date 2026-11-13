# Writeback commit cleanup qualification

Recorded on 2026-11-13 (Asia/Shanghai). Raw log timestamps are UTC.

Correctness commit `c8f5f16aefe811540782b952481982179a1bfd6f` fixes a reproducible commit-before-upload race: metadata can finish after the uploader has skipped stage cleanup, leaving an obsolete sealed record visible to the SSD overlay. The fix atomically assigns cleanup to one completion path. When upload has already finished, cleanup runs before Committed publication; a marker prevents a late uploader from promoting the same record again.

Four original-code runs failed the actual residual-stage assertion. The unchanged regression and two enhanced real FileWriter/FsWriteBackCache tests pass, including three additional runs of the same test executable. The enhanced tests hold actual cleanup, verify flush remains pending and payload stays charged, then verify known A/B remote data and the SSD overlay.

Default full tests: **1,362 passed, 266 ignored**. Superset full tests: **2,858 passed, 485 ignored**. The official strict default all-targets Clippy gate passes after separate test-only commit `e2c964c55fafdcced34af58e840ebddda1cf9ea4`. That commit changes one ignored benchmark verification loop after all timing samples; all other 2,001 tracked modes and blob IDs are identical. The runtime binaries and future measurements remain identified as c8f5f16a. The extra superset all-targets Clippy gate passes; its command does not use `-D warnings`. It retains warnings in frozen pre-cleanup workspace code: row_cache.rs fetch_update deprecation, a collapsible if and two manual slice-size calculations, and wire005/read_cache.rs type complexity. Raw warning blocks are included in the JSON alongside vendor warnings in the full logs.

The superset enables default features plus workspace-overlay, observability and rustfs-ec-block-size-hint. It is distinct from the no-default-features runtime build. Both corrected heads still require the exact runtime-feature full tests and actual FUSE fresh-cache durability/performance validation managed by agent 447. This receipt does not approve runtime performance or a PR.

All failures remain archived: the initial zero-test RED invocation, four meaningful RED failures, the earlier native-export errno12 full-suite failure, and the strict benchmark lint failure. The native-export error was not attributed to OS memory or cleanup without evidence; its five isolated passes were diagnostic, and the later complete suite passed. No budget or assertions were relaxed.

Existing filesystem promotion/remove error handling remains best effort. Epoch-skip and orphan paths are unchanged. Unit tests invoke the real SSD overlay method explicitly; they do not substitute for production cfg(not(test)) FUSE fallback verification.

Gate elapsed times include compilation and execution and are not performance measurements:

| Gate | Source | Elapsed | Result |
|---|---|---:|---|
| focused | green-02 | 905 s | exit 0 |
| actual-features | green-02 | 547 s | exit 0 |
| check | green-02 | 74 s | exit 0 |
| build | green-02 | 159 s | exit 0 |
| tokio | green-02 | 69 s | exit 0 |
| uring | green-02 | 59 s | exit 0 |
| tests | green-02 | 415 s | exit 0 |
| clippy | green-02 | 59 s | exit 0 |
| strict-default-clippy | testfix-gates-01 | 87 s | exit 0 |
| superset-clippy | testfix-gates-01 | 204 s | exit 0 |

See `ci-final.json` for commands, exact counts, per-log SHA256 and source identities; `testfix-release-equivalence.json` and `testfix-tree-manifests.json` prove the release-source equivalence. Historical receipts retain their state as originally recorded.
