# C4：10 万文件原生派生

在同一个 A 池串行跑一次 BrewFS 原生 fork 和一次 JuiceFS 原生 clone。每臂 1 个子工作区，客户端同一 2 CPU / 8 GiB / swap=0 槽、root 身份，Redis 和 RustFS 共用同一套服务，各臂使用各自空 Redis DB 和对象桶。保持两臂的完整输入相同，保留准备、计时、冷验各自结果。当前 BrewFS 候选二进制为诊断构建（`opt-level=0`），原始耗时只作本次资格排查，不进入性能结果。

## 开机前和开机后的核对

- A 客户端已有 `/srv/ax-client/inputs/pilot-100k-01/fork-source` 和同批 `receipts/c4-fixture-tree.json`；[原始输入收尾](evidence/preparation/pool-a-next-01/collected-client/input-closure.json)确认 100,000 × 4096 B，树清单 SHA-256 `02aeda272edbab9512595f7b968d840a9cfdc0e8302ed036399bf4151f08bd13`。C3 的同规模输入不是这棵树。
- [BrewFS 候选](evidence/preparation/c4-local-packed-fork-candidate-01/root-nlink-build-receipt.json) SHA-256 `3ecde1a2a4a55d450b7680661c655fd8d08491afc6aebacef84b58cea76bd6ee`，分发到新的 `…/bin/brewfs`，不要覆盖 A 池已有版本。Packed producer SHA-256 `aa45b1ee9640da92051ead72b17ed41836b57812e05623f860b963e09373b3ab`；JuiceFS 原生 clone 二进制 SHA-256 `6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc`。
- [A 池历史只读盘点](evidence/preparation/a-c3-cold-capacity-01/statvfs.json)四盘空闲约 28.1/39.4/39.4/39.4 GB（每盘约 60.1 GB），B 池历史盘 0 [仅剩约 4.0 GB](evidence/preparation/b-c1-post-gc-capacity-01/statvfs.json)，低于 20% 预留。A 是候选；开机后重新核四盘读写挂载、容量/ inode、Redis 内存及 AOF、客户端磁盘、运行镜像 commit/digest、两种二进制 SHA、cgroup 和冲突 leases。按新源与校验工作量留增量后，仍须保住 20% 空间和 inode。过去的盘点不是本次准入。
- 从第一次写入准备到最后一个挂载关闭并拉回回执，协调者使用同一个既有 `runner-locks`、真实实例/磁盘资源 ID 和 SQLite 租约检查；B 池旧 JuiceFS 回执不与 A 池拼对。没有可核验的当前 RustFS 版本或已发布 Packed carrier 时，停在准备阶段。

## 准备源目录

以下命令在 A 客户端、固定的执行槽及已核验的绝对路径下运行。`RUN` 为本轮新证据目录，`TREE` 为上面的原始清单。所有凭证来自本机私有文件，不放进命令或归档。Redis DB 在写入前逐个核对 `DBSIZE=0`，对象桶在创建前核对不存在。

**BrewFS。** 先用 `bench/fs_step.py check` 从本地源核对 `TREE`。通过已验证的 Packed producer 上传同一棵树：

```bash
"$PRODUCER" --source-directory "$LOCAL_SOURCE" \
  --source-consistency best-effort-detected --source-hardlink-policy visible-links \
  --output-dir "$RUN/producer" --manifest-output "$RUN/manifest-key" \
  --bucket "$PACKED_BUCKET" --endpoint "$OBJECTS" --region us-east-1 \
  --force-path-style true --prefix base --metadata-codec raw --data-codec raw \
  --inline-data off --chunk-size 4194304 --block-size 4194304 \
  --s3-rustfs-ec-block-size-hint auto
BF=("$BREWFS" workspace --meta-backend redis --meta-url "$BREW_REDIS" --workspace-namespace "$NAMESPACE")
"${BF[@]}" init-packed-volume --manifest-key "$(cat "$RUN/manifest-key")" \
  --data-config "$DATA" > "$RUN/import.json"
```

`DATA` 使用实际 S3 bucket/endpoint/region、`force_path_style=true` 与 `rustfs_ec_block_size_hint=auto`。按 `import.json` 的 workspace ID 配置 `workspace-v1`，挂载并正常卸载父工作区，核原进程退出；再发布：

```bash
"${BF[@]}" publish-packed-snapshot "$PARENT_WORKSPACE" --config "$PARENT_CONFIG" \
  --scratch "$RUN" --name c4-pilot-100k --snapshot-id "$SNAPSHOT_ID" \
  --journal-id "$JOURNAL_ID" --lease-id "$LEASE_ID" --next-head "$NEXT_HEAD" \
  > "$RUN/publication.json"
```

四个 ID 各用新的 UUID。原生 fork 的输入必须是 `publication.json` 的 `fork_source`。导入时的 revision 不能代替它。脚本生成一个独立的原生 fork 资格请求，执行后把其唯一 child 挂载，才读取全树：

```bash
P=experiments/unified-suite/pilot-aliyun/scripts/draft-c4-native-100k.py
python3 "$P" carrier-step --cell "$CELL" --tree "$TREE" --binary "$BREWFS" \
  --redis-url "$BREW_REDIS" --namespace "$NAMESPACE" --publication "$RUN/publication.json" \
  --data-config "$DATA" --aws-environment "$PRIVATE_AWS_JSON" --cwd "$RUN" \
  --out "$RUN/carrier-step.draft.json"
python3 -m bench.experiment_step run --spec "$CARRIER_STEP_REVIEWED" \
  --spec-sha256 "$(sha256sum "$CARRIER_STEP_REVIEWED" | cut -d' ' -f1)" --output "$RUN/carrier-probe"
python3 "$P" source-brewfs --cell "$CELL" --tree "$TREE" --binary "$BREWFS" \
  --redis-url "$BREW_REDIS" --namespace "$NAMESPACE" --publication "$RUN/publication.json" \
  --data-config "$DATA" --carrier-step "$CARRIER_STEP_REVIEWED" --carrier-output "$RUN/carrier-probe" \
  --mount-spec "$PROBE_MOUNT_SPEC" --ready "$PROBE/ready.json" --out "$RUN/source.draft.json"
```

**JuiceFS。** 先核 A 池是否已有对应精确输入、同版本且可完整读取的 JuiceFS 源卷。找不到则用独立 DB 和桶做一次 `juicefs format --storage s3 --bucket "$OBJECTS/$JFS_BUCKET" --block-size 4M --compress none --trash-days 0 "$JFS_REDIS" "$VOLUME"`；凭证通过私有 `ACCESS_KEY` 和 `SECRET_KEY`。准备挂载只把本地树导入 `$MOUNT/parent`，创建同挂载的 `$MOUNT/children`，执行文件和目录 fsync、排空并正常关闭；这段导入不计时。再挂载同一卷做一次完整源核验：

```bash
python3 "$P" source-juicefs --cell "$CELL" --tree "$TREE" --binary "$JUICEFS" \
  --source "$MOUNT/parent" --destination-parent "$MOUNT/children" \
  --writer-ready "$WRITER/ready.json" --writer-stopped "$WRITER/stopped.json" \
  --mount-spec "$PROBE_MOUNT_SPEC" --ready "$PROBE/ready.json" --out "$RUN/source.draft.json"
```

所有挂载用 `bench.runtime.prepare mount --cluster C4` 生成草稿、核对实际配置并审阅 mount+command 两级摘要；`bench.runtime.node start --spec … --sha256 … --output …` 产生 `ready.json`，`node stop --output …` 产生同一 supervisor 的 `stopped.json`。BrewFS 使用 `layout.chunk_size=4194304`、`block_size=4194304`、`cache.writeback_mode=upload_before_commit`、`fuse.cache_ttl_ms=0`、`keep_cache=false`；JuiceFS 使用 512 MiB 缓存、512 MiB buffer、attr/entry TTL 为 0，其他参数见[当前配置](../current/design.json)。

BrewFS 计时前没有活动源挂载；JuiceFS 核验结束后换同路径、同后端、同设置的新缓存挂载。若复用旧 JuiceFS 卷，仍需真实准备 writer 的同 boot 关闭凭证，不能补写伪回执。已存在卷不能提供它时，创建独立卷并按上述流程导入。

## 计时与冷验

草稿默认 `reviewed=false`。逐项审阅后另存 reviewed 文件并重新计算 SHA；不要就地改动草稿或结果。两臂都执行：

```bash
sha() { sha256sum "$1" | cut -d' ' -f1; }
python3 -m bench.fork_source --request "$SOURCE_REVIEWED" --sha256 "$(sha "$SOURCE_REVIEWED")" \
  --out "$RUN/qualified.json"
python3 -m bench.runtime.node stop --output "$PROBE"
# BrewFS: OPT=(--aws-environment "$PRIVATE_AWS_JSON"); MOUNT_ARGS=()
# JuiceFS: OPT=(); MOUNT_ARGS=(--measurement-spec "$MEASURE_SPEC" --measurement-ready "$MEASURE/ready.json")
COMMON=(--qualification "$RUN/qualified.json" --probe-stopped "$PROBE/stopped.json" --child child-01)
python3 "$P" measure-step "${COMMON[@]}" "${MOUNT_ARGS[@]}" "${OPT[@]}" --cwd "$RUN" \
  --out "$RUN/child.draft.json"
python3 "$P" burst "${COMMON[@]}" "${MOUNT_ARGS[@]}" --step "$CHILD_REVIEWED" \
  --out "$RUN/burst.draft.json"
python3 -m bench.fork_burst --spec "$BURST_REVIEWED" --sha256 "$(sha "$BURST_REVIEWED")" \
  --output "$RUN/timed"
```

- `timed/result.json`：只含原生命令从统一放行至退出的耗时，BrewFS 一次 fork 1 个 child，JuiceFS 使用 `clone -p --threads 4`。同时保存 argv、退出码、源码树绑定、运行版本。Packed 构建、挂载、全树读回不纳入创建时间。
- 首次可用：创建返回后尽快读 `d000000/f00000000` 的 4096 B 并核对哈希。用 `bench/fs_step.py first-read` 单独记读取秒数，同时在同一客户端单调钟上记录从 `release_ns` 到首次正确内容返回；BrewFS 首挂、JuiceFS 子目录首次查找均落入这个区间。不要预读子工作区。若只拿到读取秒数，不能当作整个首次可用时间。
- 冷验：用 `fork-oracle.py --files 100000 --tag child-01` 生成独立预期树；对两臂各用 `bench/fs_step.py mutate` 执行同样的覆盖、追加、截断、删除、改名和新增。排空、关挂载、用新缓存重开，`fs_step.py check` 分别比对子树和父树清单。记录排空两次原生零上传债务（相隔 ≥1 秒）、退出和挂载消失。保留原始日志、资源/服务采样与前后容量；没有冷验的计时只算命令诊断。
- 小规模仅做一个完整配对。每个阶段使用新的输出路径，超时或首错就保留证据并关闭本轮挂载；不要原地重试、冲掉失败数据或重跑历史通过项。独立 Overlay 运行开销另测，只有该对照才要求验证相同 lower 的热态。
