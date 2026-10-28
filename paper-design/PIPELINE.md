# Reproducible test pipeline

This protocol reruns the 2026-10-28 four-machine comparison: BrewFS flat-v1,
BrewFS overlay with a native lower layer, BrewFS overlay with a packed lower
layer, and JuiceFS. It is parameterized so a new machine changes command-line
paths rather than script logic.

## Preconditions

Use the RustFS repository and branch required by `AGENTS.md`:
`https://github.com/Ivanbeethoven/rustfs`,
`feat/dynamic-block-size-rebased`. Install `bash`, `python3`, `fio 3.36`,
`docker`, Redis, the BrewFS and/or JuiceFS clients, and `fusermount3`. The
machine must have four mounted disks and a raw ext4 data disk reserved for G2.
Do not use the system disk for G2. The mount point and all data/cache paths
must be writable. Do not use `sqlx` or `sqlite::memory:`.

Before each batch, freeze the branch and image together. The following is an
example; replace the image reference only with an image built from this exact
SHA, then record its immutable digest and BrewFS client SHA in
`backend-pin.json`:

```bash
repo=https://github.com/Ivanbeethoven/rustfs
branch=feat/dynamic-block-size-rebased
sha=$(git ls-remote "$repo" "refs/heads/$branch" | awk 'NF {print $1; exit}')
docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image"
# The output above must equal sha. Fill all required backend-pin.json fields.
```

`backend-pin.json` must contain `repository`, `branch`, `frozen_commit`,
`image_reference`, `image_digest`, `built_from_commit`, `resolved_at`,
`brewfs_client_sha`, and `resolved_by`. `gates.sh pin` repeats
`git ls-remote` at batch start and aborts if the branch moved. Missing fields,
placeholders (`REPLACE_WITH` or `REGISTRY/`), a build mismatch, or a blocked
pin stops the batch.

## One configuration

Run from the repository root. The command below is a complete dry run and is
safe on a non-experiment machine:

```bash
paper-design/pipeline/run-all.sh --config flat-v1 \
  --pin /path/to/backend-pin.json --output-dir /work/results/flat-v1 \
  --mount-point /work/mnt-flat --data-dir /work/d1/flat-data \
  --data-disk /work/d2 --image "$image" --dry-run
```

Expected output lists the exact Redis db0 mount command, the G1 sequence, the
two G2 fio commands, the G3 evidence input, four workload commands, and the
expected products. No binary, Docker, fio, Redis, or FUSE operation is run.

For a real BrewFS flat run:

```bash
paper-design/pipeline/run-all.sh --config flat-v1 \
  --pin /path/to/backend-pin.json --image "$image" \
  --output-dir /work/results/flat-v1 --mount-point /work/mnt-flat \
  --data-dir /work/d1/flat-data --data-disk /work/d2 \
  --rustfs-evidence /work/evidence/ec-facts.json
```

The other configurations use the following additional arguments:

```bash
# overlay + native lower layer
--config overlay-native --owner exp-native

# overlay + packed lower layer; init-cwd contains ./data/<manifest-key>
--config overlay-packed --owner exp-packed \
  --manifest-key packed-wire005-fixture/objects/0/<sha256> \
  --data-config /work/d1/packed-data.yaml --init-cwd /work

# JuiceFS (Redis db1; cache is cleared for W2 cold runs)
--config juicefs --cache-dir /work/d1/jfscache \
  --bucket http://rustfs:9000/jfs-exp --juicefs-name jfs-exp
```

`mount.sh` is the only source of mount commands. It uses Redis db0 for all
BrewFS forms and db1 for JuiceFS, parses the workspace UUID, and returns the
literal command for the manifest. A packed capability error is an expected
blocked result: the manifest says `unmeasured(G1: ...)`, and W1–W4 are skipped.

## Gates and expected output

1. **G0**: `docker inspect` revision equals `backend-pin.json`'s
   `frozen_commit`, and the image's `RepoDigests` contains its pinned
   `image_digest`; `g0.txt` says `G0 pass`. A mismatch aborts the batch.
2. **G1**: the mounted path performs write, `sync`, readback, `stat`, `ls`,
   rename, and unlink. `g1-output.txt` contains each literal command output
   and exit code. Any failure records `unmeasured(G1: ...)` and skips W1–W4.
3. **G2**: `gates.sh g2 --data-disk /work/d2 --output ceilings.json` runs fio
   on bare ext4: 1 MiB sequential write and 4 KiB random read,
   `direct=1`, eight jobs, depth 32, 20 seconds. `ceilings.json` is required
   by W1; its absence makes W1 invalid.
4. **G3**: supply a measured evidence JSON to `--rustfs-evidence`. It must
   contain `object_occupancy`, boolean `internal_ec_read_quantum_present`, and
   numeric `effective_ec_block_size_bytes`. The generated `ec-facts.json`
   records these observations; a true feature flag alone is rejected.

## Workloads and artifacts

`run-w1.sh` executes read/write at 1 MiB and randread/randwrite at 4 KiB,
three repetitions by default, using `rw=read|write|randread|randwrite`,
`numjobs=8`, `iodepth=32`, `direct=1`, and 20 seconds. It appends each fio
JSON's byte increment to `read_bytes` and labels throughput above its G2
ceiling `void(cache-hit)`.

`run-w2.sh` reads an 8 GiB corpus with an 8192 MiB cache budget, three cold and
three hot repetitions. Cold means drop caches plus remount; JuiceFS also
removes its private cache. Hot means a full prewarm followed by timing, and
must have approximately zero `read_bytes`. A heat/cold ratio below 2 is void.

`run-w3.sh` performs open/read/close/open/read/close for 200 rounds at 4 KiB,
64 KiB, and 1 MiB. BrewFS has both `--fuse-keep-cache` settings; JuiceFS has
one run marked `equivalent_keep_cache=none`. Between BrewFS groups, remount
through `mount.sh` with `--no-fuse-keep-cache` and `--fuse-keep-cache`; dry-run
prints both exact variants. The second read byte count is the criterion.

`run-w4.sh` performs 100,000 `stat` calls for 1k, 10k, and 100k inode working
sets at TTL 0 and 1000 ms. It records `fuse_getattr_ops`, `.stats`
`attr_valid_ns`/`entry_valid_ns`, and row-cache hit/miss. IOPS above 100k is
`void(kernel-cache)`.

Every CSV has exactly this header and exactly eleven columns:

```text
cell_id,config,workload,params,rep,throughput_mibps,iops,p99_us,read_bytes,status,note
```

Blank numeric values remain blank. `validate.py` uses Python's `csv` module,
so commas in `params` are quoted correctly. Run the regression against the
read-only real samples with:

```bash
python3 paper-design/pipeline/validate.py --self-test
python3 paper-design/pipeline/validate.py paper-design/pipeline/fixtures/*.csv
```

The first command must report both 11-column acceptance and 10-column
rejection. `summarize.py` also uses `csv`, reports min/median/max per cell and
parallel configurations, and writes cache-hit/kernel-cache labels:

```bash
python3 paper-design/pipeline/summarize.py /work/results/flat-v1/*.csv \
  --ceilings /work/results/flat-v1/ceilings.json \
  --output-md /work/results/flat-v1/summary.md \
  --output-json /work/results/flat-v1/summary.json
```

A successful run leaves `backend-pin.json` (or its remote check), `g0.txt`,
`g1-output.txt`, `ceilings.json`, `ec-facts.json`, `w1.csv`–`w4.csv`,
`run-manifest.json`, `summary.md`, and `summary.json`. The manifest includes
the Redis backend/db, original mount command, workspace/packed error output,
actual runtime facts, and the pinned backend. Historical results are retained
as-is; never rewrite their SHA to today's pin.
