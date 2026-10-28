# Known issues and hard assertions (2026-10-28)

These are the eight failure classes observed in the four-machine run. Every
one is represented by a check, a status value, or an explicit prohibition.

1. **The metadata store was process-local.** `sqlx` and `sqlite::memory:` made
   remounts see different catalogs and violated the shared-store comparison.
   BrewFS must use `--meta-backend redis --meta-url redis://127.0.0.1:6379/0`;
   JuiceFS uses Redis database 1. `run-all.sh` writes `meta_backend=redis` and
   the database number to the manifest, and rejects a blocked/incomplete pin.
2. **Overlay mounts were attempted with workspace 0.** A workspace must be
   initialized first and the printed UUID parsed (32 hexadecimal characters
   after removing hyphens). `mount_overlay_native` and
   `mount_overlay_packed` refuse to mount without that parsed UUID.
3. **Packed workspaces can fail after init.** The binary may print
   `packed workspace binding requires complete catalog permission/ID/lifecycle capabilities`.
   The packed mount function captures the complete output, sets `blocked`, and
   run-all writes `unmeasured(G1: ...)`; it never silently falls back to flat.
4. **`data-config.dir` is ignored by the packed initializer.** It looks under
   `./data/<manifest-key>`. The packed function runs init from `--init-cwd`
   (documented as the directory containing that path) and records the command
   and output, so a misplaced fixture is diagnosable.
5. **fio 3.36 does not accept `seqread`.** W1/G2 use `rw=read`, `rw=write`,
   `rw=randread`, and `rw=randwrite`, with `direct=1`, `numjobs=8`,
   `iodepth=32`, and 20 seconds. W1 compares each operation's throughput with
   G2 ceilings and labels an over-ceiling cell `void(cache-hit)`.
6. **Cold and hot cache runs were confused.** A cold W2 run does
   `sync`/`drop_caches` and a remount; JuiceFS additionally clears its own
   `--cache-dir`. A hot run is prewarmed and must have `read_bytes` near zero.
   A hot/cold ratio below 2 is recorded as `void(heat/cold <2x)` by analysis.
7. **Repeated-open benefit was inferred from the wrong counter.** W3's
   criterion is the second read's application byte count. BrewFS runs
   `keep_cache=false` and `true`; JuiceFS records `equivalent_keep_cache=none`
   because it has no equivalent switch.
8. **Qualification evidence was mistaken for a configuration switch.** G2
   runs fio on the raw data disk (not the system disk) and writes
   `ceilings.json`; missing ceilings invalidate W1. G3 requires measured RustFS
   occupancy ratios, the actual `x-rustfs-internal-ec-read-quantum` header
   result, and the response value of `x-rustfs-effective-ec-block-size`.
   W4 marks `stat` IOPS above 100,000 as `void(kernel-cache)` and reports
   `fuse_getattr_ops`, `attr_valid_ns`, `entry_valid_ns`, and row-cache counts.
