# BrewFS：设计证据、实验开关与运行准备

状态：deferred。用户已要求先准备其他基线和阿里云测试，BrewFS 接口与优化暂缓。以下是已完成的源码审计，不列入本阶段执行要求。

2026-10-23，源码只读审计；没有在此处修改 BrewFS/AX、部署或运行性能测试。用户已确认 AX 集成和多 Agent 运行可用。本文件区分“这份源码提供什么”与“实际集群已接入什么”，不把本地旧说明中的待集成状态重新当作前提。

源码版本：BrewFS `f80e7148c82e9f1ef235ab19318b89cb41a093a2`；AX `4ef115f88f3e0eedd2c2d9653fe04a25c1e158c6`。审计时两树干净。当前 BrewFS 包含 Packed v3 / wire 005。可追溯路径、行号、文件 SHA-256 见 [design-evidence.json](design-evidence.json)；以下路径以对应源码仓库为根。

## 1. 每个 RQ 必须同时有三类证据

外部方案回答“整套系统是否值得用”；内部对照回答“哪个设计产生差异”；任务计时回答“少了这些开销，Agent 是否真的更早完成”。缺任何一类，就限定结论，不能用一次底层微测替代整条证据。

| RQ | 外部主对照 | 内部同语义对照 | 从机制到任务的证据 |
|---|---|---|---|
| RQ1：固定资源管理多少工作区 | JuiceFS clone、CephFS clone、本地 XFS reflink；云盘单列 | D1 共同引用同一基线 / 同内容但物理独立基线；D3 Packed / native-tikv 只读索引，另测 Entity memo | 每成功工作区新增元数据/对象字节、读请求、客户端 RSS → 创建排队、首次正确读 p95、按期成功数 |
| RQ2：共享、修改和历史的代价 | XFS reflink＋restic、JuiceFS、CephFS，允许共享只读版本和增量保存 | D1 单一发布版本 / 等内容独立版本；D2 保存私有修改 / 完整物化后保存；D4 新写对象的不同真实布局 | PUT/GET 次数及字节、唯一物理字节、版本元数据 → 发布/保存/分叉完成、全部消费者结束、恢复正确性 |
| RQ3：真实 Agent 是否更早完成 | 相同 L16、模型与资源的 B0/B1/B2/B3 | 先在同一工具调用轨迹回放 D1/D3/D4；实现等价开关后，再对预先冻结的 L16 子集做成对检查 | 工具的文件操作等待与上传尾部 → 工具结果返回 → 固定窗正确完成数；保留模型等待和限流，不简单相减 |
| RQ4：选中 Agent 单独迁走 | JuiceFS/CephFS 原路径重挂、restic 恢复、云盘 XFS 原盘换挂 | D5 同一个已保存状态直接重开 / 先完整物化为另一工作区再重开；另有不迁移组 | 搬运字节、撤销/排空/授权/挂载/首读时间 → 停顿、额外任务时间、48 个邻居 p95；所有权安全单独检查 |

内部对照固定内容、路径、读写顺序、会话、权限、完整性校验、可恢复确认点、缓存总预算和 CPU/内存。某方案不支持操作，记不支持及原因；不换一个接口却继续称单因素。上表的完整物化和 Entity 开关目前是待适配实验臂，不能标成现成命令。

## 2. D1：共同只读基线与身份复用

**实际机制。** Entity 从带 SHA-256 地址的 manifest 建立 lower binding，保留 manifest key/digest、根 inode 和 namespace 范围（`entity/experiment.rs:105–151`）。`PackedLower::open/reopen` 再校验这些身份（`entity/packed_lower.rs:84–116,161–194`）。每个工作区有独立 upper 与持久 lower owner；owner 在完成删除前不能因租约过期而回收（`stores/kv_store/packed_reader_pins/entity_owners.rs:1–12`）。共享基线不意味着不同挂载进程共享一个内存缓存。

**可控制。** `workspace init-packed-volume --manifest-key … --data-config …`、`workspace create --from …` 和 `workspace fork <REVISION> --count …` 是真实 CLI（`config.rs:425–458`）。它们属于 workspace CLI；不能直接当作 Entity API。Entity 实验挂载可传 manifest、namespace/volume 身份，现有 `create_fresh_from_manifest` 只创建新状态。

**内部实验。** 先保持 Packed 和缓存不变，对固定 16 个工作区比较：一个 manifest 被共同引用；16 个文件字节相同但具有独立身份和物理对象的 manifest。必须核对对象引用图，确认后端没有重新去重到同一组对象；若做不到，不能命名为“关闭共享”。首次构建和后续派生分开计时，但总账同时给出。随后用 1、16、64 个不同基线版本检验复用程度下降时的边界。

**先准备/优化。** 在正式批次前建立一次基线并保存输入清单、manifest digest、根 inode、对象数量和构建成本；每个工作区仅分配独立身份。冷缓存主组不能偷偷预热；预热组按各方案相同预算另列。记录每个挂载的 memo/RSS，再汇总到节点，防止 N 增大带来 N 倍缓存被误报为设计收益。

**容量边界。** `PACKED_READER_SLOT_COUNT=256` 位于 `packed_reader_pins.rs:19`，是 reader pin 槽位，不是普遍的“Agent 上限”。Entity 使用另外的持久 owner。先记录实际路径占用的 pin 和 owner，再决定 255/256/257 的边界测试；不能改大常量后把新旧结果混为同一系统。

## 3. D2：私有修改、保存与父状态分叉

**实际机制。** Entity `ChunkSource` 区分 `Data`、`LowerRange`、`Hole`（`entity/model.rs:76–86`）；未覆盖区域继续引用 lower，修改上传新对象，提交元数据决定可见范围（`entity/data.rs:301–465`）。它是独立写入和引用复用，不是任意字节差分压缩。数据 fsync 与关闭的 drain 必须计入（`entity/data.rs:953`；`entity/experiment.rs:342`）。

**已有接口的范围。** workspace CLI 可从**已有不可变 revision** fork；其帮助明确不把一个 live workspace 自动 seal（`config.rs:449–450`）。Operator 有 `publish_clean_packed_snapshot`、恢复后发布和 `pin_clean_packed_snapshot` 路径（`operator/.../packed_admin.rs:108–224,560–606`）。AX 的 `ForkWorkspace` 经 Kubernetes `BrewFSWorkspaceFork` 等待 Ready 和返回身份（`internal/server/fork.go:18–30,97–158`）。这些可作为适配入口；不能仅凭接口名称证明“修改后的 Entity 父状态已可 fork”。

**先做一个可核验样例。** 父任务改固定 1 MiB并写步骤号；保存确认后派生两个孩子；孩子分别覆盖同名文件；三份输出及原始基线各自核验；新节点重开父、子状态再次核验。将 AX workspace、BrewFS workspace/revision/manifest、会话文件摘要写进一个 run manifest。样例失败时保留 T1 可运行部分，T2 对应臂标未就绪。

**内部实验。** 同一份最终内容，用“保留 lower＋upper”和“完整物化到新状态”保存；都等到远端可恢复。1/16/128/512 MiB 修改及重复覆盖保持同样位置；统计前台写入全过程、保存尾部和后台维护。完整物化臂须新增适配并验证 hardlink、稀疏区、权限、删除、rename 与会话，而不是 `cp` 结束即成功。历史规模测已关闭但可恢复版本，另计残留引用和待回收对象。

**先优化。** 用现成 read/write smoke 排除错误；采集每 4 KiB 修改对应的 PUT 数/大小、Redis 命令数和 save 尾部，确定是否过多碎片/提交。仅在诊断指向瓶颈后调整批量提交或并发；不放宽 fsync、不隐藏 close/drain、不跳过校验、不删掉大修改负例。

## 4. D3：Packed 索引、缓存与目录 memo

**可以直接复用的内部对照。** readonly Packed v3 mount 存在 `BREWFS_PACKED_METADATA_ARM=packed|native-tikv`，相同认证 manifest、数据对象、请求轨迹及缓存预算，仅改变元数据放置（`cli.rs:912–978`）。`tools/perf/run_packed_paired.sh` 已交替运行两臂、核验内容并保留失败。导入 native 元数据是独立阶段；它不能被永久从 T1 初次建立成本中删除。

**不可混用的开关。** readonly 路径支持 `BREWFS_PACKED_METADATA_CACHE_BYTES`，以及从环境读取的 v3 预算。当前 Entity `PackedLower` 使用固定 **4 MiB** 索引缓存（`entity/packed_lower.rs:104,182`）和合计 **64 MiB** memo 上限（`entity/packed_lower_cache.rs:10–13`）。设置 memo 容量和清空只出现在 `cfg(test)` 方法（`entity/packed_lower.rs:139–156`），不是可部署开关。Entity 也没有现成 frozen-native lower adapter；`run_entity_mount_comparison.sh:19–20` 明确跳过 overlay-native。因此 readonly Packed/native 对照能解释只读元数据成本，不能冒充 writable Entity 的严格消融。

**应补的最小实验接口。** 在相同 Entity 代码路径增加显式 index-cache 字节数、memo 字节数、目录 memo 使用开关及只读计数器；默认保持原值。只切换缓存命中/未命中的实现，不跳过绑定、权限和 writer admission。计数器包括索引后端请求/字节、memo 命中/填充/逐出/占用、目录命中避免的点查数、并发填充合并数。若未实现，实验清单必须写 `needs_adapter`，不能通过设置未被读取的环境变量产生虚假消融。

**固定请求顺序。** 同样的正/负查名清单，分“冷态直接随机查名”“先 readdir 再按名字读”“同目录重复访问”三组，组间重建挂载/缓存状态。代码中已有目录 memo→二分查名路径（`entity/packed_lower.rs:469–496`）和合并并发填充机制（`entity/packed_lower_cache.rs:99–115`）。分别开关索引缓存和 memo，最后再做 2×2；不把两项收益直接相加。

**要看什么。** 固定总缓存下的 lookup/stat/readdir 请求时间、工具启动/搜索耗时、RSS、每任务元数据网络。发生预算拒绝时把请求计失败，不能扩大预算后仍写“相同资源”。当前 readonly wire 005 明确要求 payload cache/prefetch 为零（`cli.rs:931–936`）；不能声称通过这些旧环境变量已调开该版本的数据预取。

## 5. D4：对象读取布局 B/q

这里严格区分三件事：文件系统 chunk/block，Packed frame，以及 RustFS 每对象 EC block。它们不是同一个参数。当前客户端可设置 `--s3-rustfs-ec-block-size-hint auto|65536|262144|1048576|4194304`，要求构建启用 `rustfs-ec-block-size-hint`，只对新上传对象生效（`config.rs:265–267`；`cadapter/s3.rs:22–91`）。auto 按**压缩/封装后的对象大小**判断；当前大于 1 MiB 时建议 1 MiB，小对象不发送建议。不能把 auto 解释为自动发现最佳 q，更不能单独设置一个源码没有暴露的 q。

**接线检查。** 普通 S3 构造器传递 hint（`cli.rs:1481,1522`）；Entity mount 的 S3Config 使用若干字段加 `Default`，未传 hint（`cli/entity_mount.rs:35–47`）。因此 Entity upper 的 hint 实验当前需适配；lower 可由明确带参数的生产者预先生成，必须记录该生产者和对象实际布局。仅解析 CLI 成功不等于生效。

**对照。** 固定 T3 查询、内容、编码和服务冗余，对默认/合法固定建议创建互不复用的新对象；读取 HEAD `x-rustfs-effective-ec-block-size` 及后端布局证明，冻结实际不同的布局组合。先测一消费者的 4 KiB 随机读、重复读和顺序全读，再测 16/64 消费者；保留 auto 与默认实际相同的负例。Packed frame 策略用另一个单因素试验，不与 EC 参数同时变化。

**因果链。** 应用请求字节 → BrewFS 实际 GET range/返回字节 → RustFS EC/磁盘读取 → 文件读/工具/消费者批次时间。`read_observer.rs:594–626` 是客户端字节账本；它不等于服务端物理磁盘字节。缺磁盘/EC计量时仅报告 HTTP 字节，不用放大系数推算磁盘节省。

**负例必须保留。** Entity upper `read_object` 读取整个 descriptor 并校验 SHA-256（`entity/data.rs:829–862`），小应用读不必然减少对象读。调整布局不许关掉这个完整性校验。旧 `tools/perf/rustfs-hint/agent-mixed/run_client.py:75` 对 auto 的 4 MiB 期望与当前源码 1 MiB 规则不符，不能原样跑并接受结果；该目录也包含旧云配置和破坏性清理，仅复用纯分析/计量思路，部署另做适配。

## 6. D5：工作区重开、迁移与所有权

**现有机制。** Entity `acquire` 只允许从未挂载的 Ready 状态，租约到期不自动允许新 writer（`entity/lifecycle.rs:453–455`）。`resume_after_maintenance` 需要已证明排空的维护 token；不会凭进程死亡制造安全退出（`:764–788`）。`PackedLower::reopen` 能重建 lower 引用，但不代表完整 FUSE/AX 会话恢复已接通。当前 Entity CLI 输出 `fresh_only: true`（`cli/entity_mount.rs:104–109`）。Operator workspace 路径另有 PVC 身份和恢复处理，不能和 Entity 混成同一条已验证路径。

**先准备。** 用实际已运行的 AX 接入记录一条完整迁移：暂停新工具→等当前工具结束→保存文件与会话→远端确认→源排空/撤销证明→目标获得新一代所有权→挂载→恢复会话→校验首个结果。分别记阶段开始/结束和失败，另记无需串行的重叠阶段。保存 `actor_id/task_id/workspace_id/revision/source_node/target_node/generation`，身份重开前后应可核对。进程内存、未完网络调用不是这份文件保存点的恢复范围。

**同语义内部对照。** 直接重开共享持久状态，与在同一保存点完整物化后重开比较；目标均已就绪，均正确撤销旧写者，均用冷缓存。两者可能改变多项成本，结论写“避免工作区搬运的整条路径”，不要写租约算法单独产生加速。fencing 是正确性条件，不能关闭 fencing 做性能对照。安全授权/排空时间照实计入停顿。

**指标。** 暂停至首个正确结果、16 个全部恢复、迁移额外任务时间、传输字节、重做步骤；48 个未迁移任务相对于不迁移组的吞吐/p95。每个阶段及等待队列是总停顿的解释，存在重叠时不能简单把各计时相加。真实云盘换挂属于整系统对照，API 排队不能作为 D1/D3 的独立收益。

## 7. 可复用脚本与明确不能直接跑的部分

以下命令在专用 Linux/FUSE 实验节点运行，均为准备好的入口，**本轮未执行**。先给二进制记录 `--version`、SHA-256、feature 和镜像 digest；只允许写本批次独占目录/桶/命名空间。不要将旧脚本里的路径、账号或清理逻辑直接带入共享集群。

```bash
# D3：已有 readonly Packed/native-tikv 配对。显式指定 release 二进制。
PAIRED_ARTIFACT_ROOT=/experiment-artifacts/d3 \
PAIRED_REPEATS=5 \
PACKED_LOCAL_BINARY=/opt/brewfs/bin/brewfs \
PACKED_LOCAL_FIXTURE=/opt/brewfs/bin/packed_v3_snapshot_fixture \
bash tools/perf/run_packed_paired.sh

# D3/D4：frame、inline、codec 的已有只读解释试验，不是 EC B/q 开关。
ABLATION_ARTIFACT_ROOT=/experiment-artifacts/packed-ablation \
ABLATION_ROWS='baseline metadata-raw data-raw static-1mib' \
PACKED_LOCAL_BINARY=/opt/brewfs/bin/brewfs \
PACKED_LOCAL_FIXTURE=/opt/brewfs/bin/packed_v3_snapshot_fixture \
bash tools/perf/run_packed_ablation.sh

# D2：Entity 新建、写、fsync、读回和退出 smoke；不是迁移/历史测试。
ENTITY_MOUNT_ARTIFACT_ROOT=/experiment-artifacts/entity-smoke \
ENTITY_MOUNT_BINARY=/opt/brewfs/bin/brewfs \
ENTITY_MOUNT_FIXTURE=/opt/brewfs/bin/packed_v3_snapshot_fixture \
ENTITY_MOUNT_SMOKE_ONLY=1 \
bash tools/perf/run_entity_mount_comparison.sh
```

补充入口：`smallfiles_scan.py` 做固定 seed 的目录/文件读；`smallfiles_rw.py` 做写、fsync、读回；`packed_run_manifest.py`/`packed_resource_journal.py` 保存输入、参数和清理证据；`packed_paired_summary.py` 汇总配对；`entity_redis_diagnostics.py` 可做 Redis 阶段采样，但 MONITOR 扰动另报。当前源码未找到 `BREWFS_ENTITY_DIAGNOSTICS`/SIGUSR1 timer 实现，虽然 Entity runner 的 diagnostic 模式要求它，故默认不启用该模式。

现有 runner 的 fixture 不是 F1/F2；只能先验证计量和机制，不能把它的数字直接填入 T1–T5。F1/F2、AX 事件和各方案命令由本实验目录的统一适配脚本提供。真实 Agent 运行和迁移所需的模型凭据、云资源、集群端点不应写入结果文件。

## 8. 按优先级准备，避免先调参再解释结果

1. **先固定实际路径。** 导出 AX/BrewFS build、volume format、接入方式、对象/元数据端点标识、缓存和资源，执行父子状态/远端重开样例。AX 集成已可用，无需重新论证；只需把可运行路径落到证据。
2. **先补计量。** 对每阶段记录单调时钟、请求/完成/失败数、GET/PUT、Redis 命令、RSS/cgroup、上传尾部、对象布局。计数缺失不能填 0；客户端字节不能代替物理读取。
3. **先测不改代码的内部臂。** readonly Packed/native、共享版本数量、已有合法 frame/codec 与 EC hint 路径。所有开关必须读回或用计数证明生效。
4. **再补最小等价接口。** Entity memo/index 控制、可写 native lower 对照、完整物化保存、Entity 目标重开与安全交接、hint 接线。缺失项写 `needs_adapter`，不会阻止已就绪的外部主对照和其他 RQ。
5. **只优化明确瓶颈。** 若目录点查重复多，先检查 memo 顺序；若保存 PUT 细碎，检查批量/上传；若迁移恢复慢，分清撤销、挂载、下载和模型恢复。优化前后同任务/资源/语义；不以成功样本筛选结果。
6. **冻结正式配置。** 各方案获得相同调优预算，校准结果不并入正式样本。任何 BrewFS 行为变更须按其 `AGENTS.md` 完成相关正确性和全工作区 CI/perf gate；不能把专门 microbenchmark 好看当作优化已被接受。

最终每项设计的结论只允许三种：任务与机制都改善；机制改善但任务未改善；未改善或存在代价。它们都能回答 RQ，不能预设五项设计必须全部有正加速。


