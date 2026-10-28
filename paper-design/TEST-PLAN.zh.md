# 实验方案（执行前冻结版，2026-10-28）

> 当前研究与执行口径以 [最终方案](FINAL-PLAN.zh.md) 为准。本文冲突的超时、性能阈值作废及拓扑解释不再适用；现有流水线尚未通过机制/测量资格，具体问题见 [执行器审阅](REVIEW-20261028.md)。

本文件是交给执行 agent 的方案。它只定义装置、资格门、负载、判据和产物；本轮不启动云实例，不运行长实验。

## 目的和共同装置

研究对象是 BrewFS 的 flat-v1、Overlay 加原生封存下层、Overlay 加 Packed 下层，外部对照是 JuiceFS。每个研究小节都必须包含 JuiceFS 格子；JuiceFS 只在能力相同的指标上排名，缺失能力按“无等价能力”保留可比替代测量。

设计参数的权威来源是仓库的 [`design.json`](design.json)；本入口由 [`plan.zh.md`](plan.zh.md) 指向现行的 [`TEST-PLAN.zh.md`](TEST-PLAN.zh.md)、[`EXPERIMENT-CONFIG.zh.md`](EXPERIMENT-CONFIG.zh.md) 和 [`design.json`](design.json)。历史实测依据 `/home/hxy/us-artifacts/` 只作外部只读证据，不替代本批 pin。

正式批次使用四台对象服务、worker、Redis 和监控资源，资源上限以每批 manifest 为准；同一配对批次固定物理节点、CPU/内存、缓存和时段。小型 smoke 只能使用已有主机，且每条命令不超过两分钟。

共同装置硬约束见 [`EXPERIMENT-CONFIG.zh.md`](EXPERIMENT-CONFIG.zh.md)，开跑前必须通过 [`tools/check-disk-layout.sh`](tools/check-disk-layout.sh)；脚本退出码作为四盘与裸盘落点资格门。

版本合同：

1. 开批前执行 `git ls-remote https://github.com/Ivanbeethoven/rustfs refs/heads/feat/dynamic-block-size-rebased`，将完整输出写入该批 `backend-pin.json`。必须填写 `repository`、`branch`、`frozen_commit`、`image_reference`、`image_digest`、`built_from_commit`、`resolved_at`、`brewfs_client_sha`、`resolved_by`。
2. 从冻结 SHA 构建或验证 RustFS 镜像，并以 `docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' IMAGE` 机械核对 revision。出现 `REPLACE_WITH` 或 `REGISTRY/` 时生成 spec 直接失败；分支不可读、构建失败或镜像来源不匹配时整批阻断并写 `blocked_reason`，不换分支、不使用 `latest`。
3. 已有 `/home/hxy/images/rustfs-exp-a87e8f94.tar.zst` 只能作为候选来源证据：报告中的 revision 是 `a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed`，不能替代本批重新解析的冻结 SHA。已知候选镜像 digest 为 `sha256:24a9d230cb22a4610e6d24e6e9f1923f6df03964699f2d8b405928b9016294c7`，执行 agent 必须重新 inspect 并写入本批 pin。
4. BrewFS 客户端固定为 `/home/hxy/bin/brewfs-975deac`（191 MB，commit `975deac`，带 `workspace-overlay`）；单独记录其 SHA-256 和版本输出。旧报告使用的 `6ac8d508…` 二进制在两组 Overlay 上返回 `feature not compiled: workspace-overlay`，只作为失败证据。
5. Packed lower 只使用 `/home/hxy/packed_v3_snapshot_fixture`（92 MB）生成并记录 manifest、构建命令和输入摘要；不能把 fixture 文件本身写成已挂载 Packed 卷。

JuiceFS 对照的共同条件是同一 RustFS 后端、同一 Redis、同一 payload/语料、相同 CPU/内存和总缓存预算、同一物理机器与时段、相同发令表和重复次数。JuiceFS 版本在开批冻结；仓库权威参数中计划版本为 CE 1.4.2，pilot 实测为 `1.4.2+2026-10-22.46af7af`，以执行时实际二进制为准并记录。JuiceFS 没有的开关或表示必须写“无等价能力”，仍执行可比替代测量。

## 资格门和停止规则

资格门严格先于性能组：

- **版本和来源门**：RustFS 分支可读、SHA/image label/digest 对上、BrewFS SHA 固定、spec 无占位符。失败：写 `backend-pin.json` 的 `blocked_reason`，所有性能格 `status=void(backend-pin)`。
- **基础连通门**：每种配置真实完成写、sync、读回、stat、ls、rename、unlink；独立客户端读回且 namespace digest 一致。失败：该配置所有后续格 `void(qualification)`。
- **语义门**：父修改可由子继承；父/子/兄弟隔离；rename、unlink、hardlink、truncate、重开和 GC 引用正确；新权威建立后旧 writer 的迟到写和发布被存储层拒绝。JuiceFS 无 BrewFS fork/fencing 时写确切错误并保留 smoke，标为 `unmeasured(no-equivalent)`，不伪造能力。
- **观测门**：能同时采集 FUSE 字节/操作、应用返回、远端确认、Redis/对象请求、客户端 CPU 和设备计数；采不到的字段为 null，不能用估计值替代。
- **布局门**：每个对象记录请求 hint、实际 B/q、数据/校验片数、inline/protected 状态及持久元数据解码；只有实际布局改变才进入动态 block 性能组。配置值未改变布局时写 `void(layout-unchanged)`。
- **缓存和资源门**：预注册缓存冷热条件、总预算和 `ceilings.json`；热/冷比低于 2×的缓存格作废并保留原始数据。超过 `ceilings.json`、内存/磁盘上限、截止时间或队列持续增长的格作废，不当作 0。

所有失败保留命令、返回码、stderr、版本、配置、部分数据、日志和校验；修复后开新批次，不能回填旧批次。

## 配置和统一计时

配置名称直接写全称：

- BrewFS flat-v1：基础路径和原生元数据。
- BrewFS Overlay + 原生封存下层：workspace Overlay，lower 为普通原生封存。
- BrewFS Overlay + Packed 下层：相同 Overlay，lower 由固定 Packed v3 manifest 提供。
- JuiceFS：Redis 元数据加同一 RustFS S3。

每格按准备、发令、首个正确操作、应用返回、远端确认、另一客户端恢复读、排空、独立终检分别计时。流水线阶段直接测总关键路径，不能把重叠阶段相加。每格产出 `status=ok`、`void(原因)` 或 `unmeasured(原因)`。

## 基础能力和语义资格

负载顺序为创建、读取、覆盖、truncate、rename、unlink、sync；先修改父状态再确认版本，从该版本建立两个子状态；父、子、兄弟各自修改；关闭一个 worker 后由另一 worker 重开已确认版本；删除一半引用并观察回收；新 writer 获得权威后让旧 writer 发出迟到写和发布。

JuiceFS 测同一顺序、同一 payload 和独立 readback。它没有 BrewFS Packed lower、同一 fork 或 fencing 语义：这些字段写“不适用/无等价能力”，但仍测其原生目录、clone/snapshot（若版本支持）、重开、删除和一致性错误。JuiceFS 不参加 BrewFS 专有能力排名。

判据是每个操作的内容、属性、namespace digest、GC 可达性和独立 readback；任一失败标记 `unsupported` 或 `blocked`，不进入性能组。产物为 `qualification.json`、操作绑定、版本/镜像 pin 和证据清单。

## 属性缓存和重复打开

属性缓存对固定文件执行 100,000 次 stat，并轮转 1,000、10,000、100,000 inode；BrewFS 测 TTL 0 和 1000 ms，另测 Overlay 默认有效 TTL。记录 FUSE getattr/lookup、运行时 TTL、row-cache 命中、p50/p95/p99、CPU 和墙钟，并逐次验证修改、lease/generation 和删除重建边界。JuiceFS 使用相同工作集和 stat 序列，记录其 `--attr-cache`、`--entry-cache`、`--open-cache` 和默认行为；没有 BrewFS TTL 旋钮的等价能力，仍报告其实际 TTL 和 stat/FUSE 延迟。

重复打开对 4 KiB、64 KiB、1 MiB 文件各执行 200 轮 `open→read→close→open→read→close`，只改变 BrewFS `KEEP_CACHE`。判据只能用第二次读的 FUSE 字节数和操作数：不能用吞吐替代。JuiceFS 没有 `--fuse-keep-cache` 的等价开关，保留其默认缓存并以相同序列测第二次 FUSE/设备字节、内容正确性和 CPU；KEEP_CACHE 归因标为不适用。Packed lower 对该指标不作专属排名。

实测依据：OVERLAY-OPT-REPORT 报告 flat keep-cache=true 第二次读 FUSE 字节为 0、false 为 25.6 GiB，Overlay keep-cache=true 为 0；这些是方法证据，不是新批次结果。旧 pilot 的 JuiceFS 4 KiB/64 KiB/1 MiB 第二次设备读均为 0，来源 PILOT-REPORT。

## 同机四配置写入成本

用固定 ABBA、多个小文件、单大文件、随机覆盖和写入中删除负载，四种配置随机化配对区组；`pytest-full-02` 只在归档存在并通过校验时使用，否则整格 `unmeasured(trace-missing)`。每次记录 dirty 字节、队列长度、应用返回、writeback 上传、远端确认、排空、终检、另一客户端读回、Redis/对象请求和设备字节；累计 close 不重复加到墙钟。

JuiceFS 完全复用负载、payload、机器、Redis/RustFS、缓存预算和发令表，分别记录 writeback 与远端确认。它没有 Overlay/Packed lower，故只参加共同写入等待和生命周期列，专属列写“不适用”。判据是独立读回正确、远端确认存在且不超过资源上限；应用提前返回但远端未确认的格不得判为成功。

## Overlay 继承、分支和历史保留

使用 F1：约 1 GiB、32,896 文件的确定性输入。父先做 1 MiB 数据修改、32 次 rename、32 次删除和属性变化；子数量 1、16、64，扩展 256；每子初始修改 1 MiB，扩展修改率按 0、0.1%、1%、10%、50%；分支形状为星型、四叉树、四条深 8 链，共 33 个状态。固定叶子查询和 4 KiB 校验读。

JuiceFS 在同一父 payload、子数量、树形和修改率运行原生 clone/snapshot；如果版本或单机后端不能提供可恢复 clone，保存确切错误并将该臂 `unmeasured(no-clone)`，不物化复制冒充 clone。BrewFS flat 的物化适配器若参加，必须把复制和构建成本单列。

判据使用“fork 到首个正确操作”“首次持久写”“save”和可恢复独立读回，以及只计一次的共享对象字节；父、子、兄弟内容/属性隔离失败则 `void(semantic)`。产物含状态清单、namespace digest 和成本分解。

历史保留先测 1,000、10,000 个已确认状态，100,000 仅扩展；删除一半引用，保护 600 s，观察 3,600 s。重开最早、最新和多代分支，记录逻辑删除、可回收对象、GC 排队、物理释放和 reader/历史引用保护。JuiceFS 同样测其 snapshot/目录版本保留和重开；没有历史版本能力时写“不适用”，仍测普通目录删除后的可恢复性，不参加历史保留排名。超过 `ceilings.json` 或无法重开则 `void(retention-limit)`。

## Packed 访问、共享数据库干扰和偿本

使用 M 的 32,768 和 1,048,576 个 1 KiB 文件；每 session 32,768 次查询，消费者 1、16、64，路径 shared/disjoint。查询族为随机正 lookup、50% 负 lookup、inode 属性、相邻名称、readdir 后属性、lookup 后读取；分别记录客户端冷、索引暖、元数据暖。Overlay 原生 lower 与 Packed lower 固定 payload、upper、缓存预算、g/B/q、inline/压缩条件。

共享数据库 probe 先做无背景 5 min 标定：队列不持续增长且计划到达后 2 s 内完成率至少 99%；两表示取较低可持续速率，探针为其 0.25 倍，背景为 0、1、4 倍，最多 64 个在途。记录计划/实际发令、admission/deadline miss、探针 80% 属性读和 20% 4 KiB 覆盖写+sync、DB/对象服务 CPU/请求/持久字节。发生器自身饱和的格写 `void(generator-saturated)`。

JuiceFS 对同一查询和 probe 运行原生目录/对象路径，记录 Redis 请求、对象请求、客户端解码和设备字节。它没有 Packed manifest、索引页/descriptor 和 Packed 构建偿本：这些列为“不适用”，但通用查询、probe 按时完成率和对象/客户端转移成本仍可比。判据使用按时完成率、p95、DB 与对象服务资源的联合账本，不能只凭 DB 请求下降判优。

Packed 生命周期按首次构建、上传、校验、发布、首读和复用 K=1、4、16、64、256；连续 8 个版本，变化率 0.1%、1%、10%，每版本使用 4 或 64 次，末次保留 0 或 600 s。JuiceFS 对相同版本内容使用其原生 snapshot/clone；若不支持，`unmeasured(no-clone)`，仍测普通上传和读取成本。判据是每个资源独立计算累计差值，报告观测到的交叉点，不外推普遍回本阈值。

## 对象粒度、RustFS 动态布局和获取策略

先在资格阶段确认实际布局。固定定义：客户端分块粒度 g；最终编码对象长度 L；RustFS EC 块 B；shard 内部读取/校验粒度 q。记录每个请求 hint、有效 B/q、持久元数据解码、数据/校验片和 inline/protected 状态。四个同设备目录只能功能验证，不能代替多盘/多节点性能。

第一层固定后端布局和读取策略，只改变 g=1、4、8、16 MiB；F2 负载为 4 KiB 随机索引读、64 KiB/1 MiB 读、顺序扫描和 64 MiB 总覆盖写。第二层固定对象内容和请求列表，只改变合法 B/q；第三层固定布局，只改变 Range、whole-object、预取、合并和缓存准入。每个格记录 HTTP/客户端、服务端 pread、设备字节和正确读回，三者不得互相替代。

JuiceFS 在同一对象长度、读请求、RustFS 后端和缓存预算执行 whole/range/随机/顺序读，记录其实际 block-size、GET range、客户端/服务端/设备字节。它没有 BrewFS 的 g、Packed frame、RustFS B/q hint 接线：这些字段“不适用”，但 common read latency、请求数、客户端和设备字节仍测。若 BrewFS hint 未改变持久布局，相关格 `void(layout-unchanged)`；JuiceFS 不参加 B/q 专属排名。

## 迁移、恢复和 fencing

四个源 worker、四个目标 worker、16 个工作区；迁移 0、1、4、8 个，pending 修改 0、1、16、128 MiB，目标缓存冷/暖。计时覆盖 drain、版本固定、必要 Packed、上传、校验、发布、目标取数和独立读回；记录邻居尾延迟、排队和 deadline。

故障屏障覆盖确认前、确认后、源失去协调但仍能访问存储、目标恢复中断；每个主要故障点每个表示 20 次，这是时序覆盖，不是性能重复。新 writer 获权后旧 writer 必须真正发出迟到写和发布，检查存储层拒绝及 readback 未污染。

JuiceFS 用相同保存点、冷/暖缓存、迁移数量、pending 修改和屏障测重挂、重开、首个正确读、所有权转移和迟到写拒绝。它没有 BrewFS Packed 构建和 Entity lease/fencing 证据：分别写“不适用/未资格”，但仍报告普通重挂和错误。没有跨节点 fencing 证据的格 `unmeasured(no-fencing)`，不把重挂耗时当安全能力。

## 固定任务回放、规模和真实 Agent

固定任务使用 design.json 的七条 trace、每条 8 份共 56 个任务，并发 1、4、16；每任务截止 1,800 s，共同观察截止 101,100 s。只有资格门通过的配置进入性能；准备只计一次但纳入生命周期。记录正确完成率、合格任务吞吐、任务族延迟、全成功 makespan 和服务资源。

JuiceFS 使用相同冻结 trace、模型、权限、worker 并发、payload 和 8192 MiB cache。没有 BrewFS fork/Packed/迁移能力的任务臂保留原始失败原因，并从专属排名剔除；共同读写路径仍与 JuiceFS 对照。已知昨天两台机器均未找到 `pytest-full-02` 归档；若执行前仍缺失，包含它的真实回放格必须 `unmeasured(trace-missing; 不执行)`，不能用 fio 或人工 trace 替代。

规模扩展只在固定后端资格通过后做 worker 4、16，64/128 为扩展；JuiceFS 同样使用相同任务表和资源上限。不能由单机 4/16 worker 外推无限扩展，超过资源上限的格直接作废。

真实 Agent 选择 live-tasks.json 中两个任务，在第三个完整工具结果后触发继续、四子 fork 或迁移；固定模型、权限、初态、评分器和 1,800 s 截止。父剩余预算四等分且不重置；保存历史、工具结果、环境、文件状态和剩余预算，并要求真实后续工具调用。JuiceFS 使用相同任务和工具权限，只在共同文件读写路径作对照；没有 BrewFS fork/Packed/迁移语义的部分记录原始 `unmeasured(no-equivalent)`。判据为正确完成、评分器结果、独立文件状态校验和实际后续工具调用。

## 统一产物

每批至少保存 `batch-manifest.json`、`backend-pin.json`、BrewFS/JuiceFS 版本、配置原文、输入摘要、命令和返回码、阶段事件、资源采样、服务指标、对象/版本清单、内容断言、namespace digest、失败原因和最终 readback。每格还必须有 `status`、`layer`、`judge`、`expected_artifact`；机器清单见 [run-grid.json](run-grid.json)。补充对照规则见 [`JUICEFS-PARITY.md`](JUICEFS-PARITY.md)，章节化说明见 [`REORG-EXPERIMENT-PLAN.zh.md`](REORG-EXPERIMENT-PLAN.zh.md)。

## 明确不做和不能回答什么

本轮不启动云实例、不删除 `/home/hxy/images`、`/home/hxy/us-artifacts`、`/home/hxy/bin`，不把旧二进制或官方 `latest` 当替代，不用缩小 pilot 伪装 8 GiB 正式结果，不把同设备四个目录称为多节点动态布局，不把一次 smoke 或一次重跑当统计结果。

单机单次不能回答跨硬件、跨地域、生产级可靠性或无限 worker 扩展；没有本批 pin 和运行时 B/q 不能声称动态布局生效；`valid=false`、操作数相等或最终树相等不能替代中途读语义、属性、目录视图和独立恢复；trace 的事件比例不能代表真实系统调用比例、运行时间占比或缓存命中率；客户端 PUT/GET、服务端 pread、页缓存和设备计数不能等同于物理磁盘字节或网络 wire 字节；一次查询结果不能推出所有查询；一次 Packed 结果不能推出所有查询都更快；没有跨节点协调、迟到写和 fencing 证据不能声称迁移安全；没有归档的真实回放不能用合成 trace 代替。

