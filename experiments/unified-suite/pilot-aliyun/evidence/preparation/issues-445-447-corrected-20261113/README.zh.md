# Issues #445–#447：修复暂存清理后的重新验证

两位独立审阅者已批准**限定范围验收**，修复已提交 [BrewFS PR #451](https://github.com/brewfs/brewfs/pull/451)。确定收益为不同 Packed 元数据对象的并发填充、行缓存保留字节上界及缓存小写入的 CPU 开销；同固定写入量的三对完整工具与排空检查未重现明显 CPU 回退。短负载成本和旧累计场景的回退均保留，不主张所有访问形状都加速。原 [7b844 候选的失败与 HOLD 记录](../issues-445-447-7b844-20261113/README.zh.md)保持原样。

[完整交付与独立复核](delivery/README.zh.md) · [266 项交付清单](delivery/delivery-manifest.json)。实际二进制与含栈内存的 raw perf 保存在 RBM，不进入 Git。PR 与本轮 [Packed 整包缓存 PR #449](https://github.com/brewfs/brewfs/pull/449)基于同一旧主线独立验证，合并二者后的源码树仍需处理相邻缓存代码冲突并另验；本轮未自动合并 PR。

两份最终意见：[运行与证据终审](delivery/reviews/independent-final/final-limited-qualification-review-01.md)、[源码与同工作量复核](delivery/reviews/source-and-fixed-work/final-scope-review/limited-pr-opinion.md)。[后续一轮源码走查](delivery/reviews/ax-next-review-445-01/review.md)也已完成，检查填充取消与代际、行缓存记账与读者持有、写回清理交接及 Packed 持久缓存身份；没有新增可确认的问题。已知冷 HEAD 锁竞争和属性缓存刷新边界仍保留为后续项，不写成此次已经解决。

[PR 触发的官方 CI](https://github.com/brewfs/brewfs/actions/runs/43351717736)已在同一 `e2c964c5` head 通过：构建、`cargo test --workspace --lib`（1,360 通过、266 忽略）及 `cargo clippy --workspace --all-targets -- -D warnings` 全部成功。官方只运行 lib 测试；下文的本地默认 1,362 数包含另两个 bin 测试，口径分别保留。[原始 CI 包](pr-ci.tar.gz)、[API 回执](pr-ci-receipt.json)、[协调者核验](pr-ci-coordinator.json)作为追加记录，不修改冻结交付清单。

## 实际源码与构建

两臂都包含相同的元数据最后完成时的暂存清理修复。基线为 `4f982f45c153a0c31e3e9cd8ff642ec7659c98fd`，候选为 `c8f5f16aefe811540782b952481982179a1bfd6f`；整树重建验证候选相对基线恰为原三项性能补丁。发布用子提交 `e2c964c55fafdcced34af58e840ebddda1cf9ea4` 只调整忽略 benchmark 在计时结束后的测试迭代器，其余 2,001 个条目的 mode/OID 相同。实测始终记录实际 c8 二进制身份。

| 产物 | SHA-256 |
| --- | --- |
| 基线 release | `0b0a2743aa968f497782d823cb068cd421eb840f82d5739a363d3bc3623c8679` |
| 候选 release | `905c4e4ca35162ae6a1ab2de03f4fa18194ef64d9afcbe20cff9bbefd570ce90` |
| 两臂共用的 ca07159 fixture producer | `8c4d65dbe8daaeaffb7f6084474a9a839df418c66d2aced6226842a5b8997d72` |

两份 release 固定 Rust 1.99.1、opt-level 3、关闭默认 features，并启用 `fuse-io-uring-runtime,workspace-overlay,observability,rustfs-ec-block-size-hint`。精确 feature 的完整 workspace lib/bins 测试：基线 2,770 通过、483 忽略；候选发布树 2,792 通过、485 忽略。候选默认和 superset 测试、实际 RED/GREEN、官方严格 Clippy 与测试子提交的生产等价证明见[本地 CI 归档](../writeback-commit-cleanup-20261113/README.zh.md)。

编译、精确测试、来源与激活证据见 [46 成员原始包](compilation.tar.gz)、[独立复核](compilation-independent.json)和[协调者复核](compilation-coordinator.json)。大二进制保存在 RBM，未放进 Git。

## 已通过的真实内容门

两臂使用各自冻结 release，通过标准随机写 trial 500 的暖读与空缓存冷重挂四文件摘要核验。负载为 512 MiB × 4、20 秒、4 MiB I/O、固定随机种子；严格排空、正常卸载、原挂载进程退出及服务错误检查均通过。该检查只证明此负载的内容一致性，其墙钟耗时不作为性能收益。

已知内容 trial 502 在两臂各核验四次完整 64 MiB 文件：写入 A 后、覆盖 B 后、使用原缓存重挂后、使用新空缓存重挂后。每次均核验完整字节、EOF、inode、大小、mode 和 mtime，且确认实际 FUSE `open → GETATTR → 129 个连续 READ（含 EOF）→ GETATTR` 顺序、PID、文件句柄、属性值与调用时间窗口。两位独立检查者与云端重放验证器结果一致。

原 trial 500 的已知 A 检查及 v3 trial 501 失败保留。v3 已确认两臂 A 的全部字节正确，仅前后 mtime 相差约 13–15 ms：写入更新后端属性，但 lookup 的旧缓存属性与 open 后刷新的属性基线不一致。相关源文件在原始与修复两臂相同。v4 在读取前后显式执行 fd `statx(AT_EMPTY_PATH | AT_STATX_FORCE_SYNC)`，并核对 statx 与 fstat；不支持、缺字段或属性不匹配均失败。它保留原全部内容与身份断言，并由真实 GETATTR trace 证明刷新发生；没有修改生产代码、TTL、写入或 fsync 协议。此非计时内容检查的修订不进入性能测量。

v4 原始阶段包冻结时仍等待 trace，因此其外层 `qualified=false` 保留；后续 joined correctness checkpoint 引用原始包及 trace 证明，记录正确性阶段通过，未改写旧回执。

## 新性能结果与历史回退

512 GiB 盘上的 trial 601 按基线→候选、trial 603 按候选→基线执行；每臂均保持原六场景和 direct0→direct1 的前驱顺序。两轮共 24 项内容、严格排空、客户端/服务端错误、正常重挂和容量检查通过。顺序写是原 profile 的单 job；随机写和混合读写为四个 job，各文件 512 MiB，不将所有场景笼统写成四 job。

原累计场景中，随机写 direct1 的观测如下；新诊断证明此类窗口混入前序工作，但没有重建或扣减这两轮未采样的 CPU：

| 指标 | 601 基线 / 候选 | 603 基线 / 候选 |
| --- | --- | --- |
| 工具执行及 strict drain 的 CPU 秒 | 34.76 / 38.63 | 36.65 / 38.17 |
| 实际逻辑写入字节 | 4,940,890,112 / 4,269,801,472 | 4,693,426,176 / 4,387,241,984 |
| drain CPU 秒 | 22.20 / 28.43 | 25.29 / 27.23 |
| CPU / 逻辑 GiB 的候选变化 | +28.60% | +11.42% |
| 包含排空的有效带宽变化 | -11.62% | -1.75% |

这是 20 秒 time-based 负载，完成的实际字节不同；不能只凭候选总墙钟更短或 active 带宽提高就宣称通过。三项初始 direct1 疑点中的顺序写和混合读写 p99 在反序时方向改变；原值与新出现的波动全部保留，不跨批平均抵消随机写的 CPU 疑点。未执行的 602 单独 direct1 方案保留为准备记录，未用它替代原完整上下文。

这些 CPU 边界不覆盖每次冷验证、卸载前尾段和最后冷重挂进程的全部生命周期。旧回执缺少对应 CPU 快照，不能把六个工具窗口相加称为总生命周期成本，也不能据此论证前后场景的成本转移已经抵消。写回债务归零后的额外 PUT 和前驱文件碎片量已确认存在，归属于哪些后台任务仍需实际采样与日志绑定。

固定 4,096 × 4 KiB 的缓存写入小样本已完成两臂触达检查和三对 AB/BA/AB。BrewFS 进程在“写入 + fio 内部 pattern 读回 + strict drain”全阶段的 CPU 秒分别为 `0.53→0.45`、`0.54→0.41`、`0.53→0.43`，降低 15.09%、24.07%、18.87%。全阶段墙钟约 5.26 秒，不主张吞吐或延迟收益。空缓存冷重挂整文件 SHA 检查在这些计时边界之后另计。

八臂的 fio `write.short_ios=4095` 保持原样。未修改的上游 3.39 源码、真实头文件 ABI、本地 syscall 跟踪及实际 tools 镜像的普通文件诊断共同确认：range-sync 的 direction 为 5，却用于仅四项的 `total_io_u`，越界位置恰为 `short_io_u[WRITE]`。实际镜像的两组各 4,096 次 pwrite 全部请求和返回 4,096 字节；开启 range-sync 后 4,095 次同步成功并出现同值计数，关闭后为零，文件均为完整已知内容。云端诊断的读证据为 fio verify 与整文件 SHA，本地另有逐次 pread trace；不混用两份证据的范围。

新 4f/c8 二进制的 114 次 workspace 挂载和 24 臂 long-row 已实际完成并通过独立核验。协调者从原始结果重算：在仅对 GroupMeta 注入 50 ms 延迟、八对象八线程的六对并发冷读中，中位耗时从 428.284 ms 降至 74.356 ms，降低 82.64%。这是受控填充竞争测试，不是对任意 S3 环境的延迟承诺。128-key 热点长轮每线程固定 65,536 次操作：单线程六对全部更快，八线程五对更快、另一对慢 0.129%；两种形状的中位耗时分别降低 3.07% 和 1.94%。

候选组合也有短负载代价：同一个 GroupMeta、单线程的中位耗时为 86.955→92.321 ms（+6.17%，六对中五对较慢），八线程为 85.445→88.064 ms（+3.07%）；短轮 128-key、八线程的行缓存调用中位耗时增加 18.51%。长轮结果不能删去这些短轮成本，因此不主张所有访问形状都加速。这里统一按“各臂耗时中位数的比值”报告百分比，原始逐对值另存。

行缓存独立压力检查的 65 次采样中，候选最大记账保留量为 524,046 B，不超过 524,288 B 配额。基线只有原条目上限，不执行新的字节配额，也没有对应字节计数；这些数值不是进程 RSS。新 metadata 归档的 114+24 次挂载、逐值内容、原生命中与零 miss、预算配置和退出检查已经独立核验通过。

## 同工作量检查与最终准入

605/606 诊断两臂的四场景前缀及八次冷 SHA 均通过，保留默认 compaction。每臂的 32 个 compaction chunk 都对应实际 stat 确认的前驱 randwrite/direct0 文件 inode；目标 direct1 的写回债务归零后，旧文件整理仍继续完成。CPU 栈也出现 `ObjectBlockStore::read_range → compact::worker::run_compaction_cycle`。采样存在覆盖缺口，S3 span 没有实际记录对象 key，因此只确认前驱任务进入测量窗口，不把所有额外 PUT 或 CPU 都归于它，也不按样本比例扣减 CPU。

固定工作量 610/611/612 使用各自全新项目、相同工作集和种子，每臂写入固定 4 GiB / 1,024 次 I/O，按 AB/BA/AB 顺序执行。每臂保留同一原运行器的 28 B sanity 写读删除预检；不是绝对零先前 I/O，残余最多为一次 32 B PUT。六臂均通过严格排空、服务错误检查、全量冷重挂 SHA 和容量门。

| 对次 | 写入及排空 CPU：基线 / 候选（秒） | 写入至排空：基线 / 候选（秒） |
| --- | --- | --- |
| 610，AB | 10.84 / 10.78 | 42.566 / 41.829 |
| 611，BA | 10.77 / 10.93 | 42.371 / 42.330 |
| 612，AB | 10.88 / 10.86 | 42.240 / 42.495 |

三对 CPU 比分别为 0.994465、1.014856、0.998162，耗时比为 0.982690、0.999040、1.006041。610 的 active 阶段仍为 4.96→14.70 秒，随后排空缩短；原始分段保留，不将整轮接近写成前台普遍加速。该门只覆盖当前工具与 strict drain，不包括全部冷验证、卸载或后续周期维护。两位审阅者据定向收益、字节上界、同工作量完整窗口及正确性结果批准限定 PR，不改写历史回执中的 HOLD 或 `performance_accepted=false`；最终准入由独立终审文件给出。

604 在测试入口的固定路径校验处退出，未执行 FIO；其失败保留。605 的负载和 raw perf 已完整保存，但离线质量检查因未转换 Rust v0 名称而拒绝。原始 mangled 栈、失败回执及后续离线转换分别保留，未改写原状态；606 仅延后离线解码，两臂采样参数不变。raw perf 含栈内存，仅保存在 RBM 私有目录，Git 不包含它。编译和云端归档传输与测量串行；这些诊断耗时不用于性能准入。

标准 trial 600 的基线通过四个顺序/随机写场景后，进入 buffered randrw 时剩余容量降至约 6.94 GB，低于 20% 门槛。四个文件的暖读、冷读 SHA 全部相同；验收失败来自重新挂载日志的 `upload_or_recovery_failure`。RustFS 原始日志明确记录四个目录的 `DiskFull`、`drive path full` 和一次 PUT 的 HTTP 503，因此不能把本轮拒绝写成文件内容不一致。原先 44 GiB 的经验预留低估了累计缓存与对象开销。既有 runner 在失败后自动执行该 project 的 `down -v`，日志与原始回执保留，但不能声称其后端临时卷也保留。候选 trial 600 未启动，原 402/403 现场未删除。

本任务的系统盘已由 160 GiB 在线扩至 512 GiB，同一精确磁盘 ID 的 API 回读及 guest 扩分区、ext4 扩容均已确认，可用空间为 439,991,136,256 字节。PL1 不变，API 的 IOPS 从 9,800 变为 27,400、Throughput 字段从 200 变为 350，因此这也改变了磁盘性能规格。新一轮完整标准和性能对照均使用扩容后的同一硬件，重新记录容量门和运行身份；不与旧 160 GiB 的绝对耗时混算，原内容门保留当时身份。增大的预留及逐场景监测作为经验防护，不宣称它是已证明的占用上界。

RustFS 使用用户指定分支 `feat/dynamic-block-size-rebased` 的 `f4ed202508f2153afb7956bae6f7799b60eccd00`，镜像 digest `sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806`。四个 EC 目录位于同一块 SSD，磁盘独立性检查被显式跳过；这是当前修复验证环境，不代表分布式部署或正式 C1–C4 suite 已获准执行。

本任务的临时实例、512 GiB 系统盘和独立安全组已经 API 回查确认不存在，只移除了 RBM 上本任务复制的连接密钥，原 keypair 和网络保留。原 402/403 的五份 sealed 差异样本共 20 MiB、日志、来源及本轮原始证据在 RBM 核验保存；旧完整 Redis/RustFS 临时卷未备份，未声称保全整个后端现场。清理回执记录了安全组首次因实例异步退役返回依赖错误、随后重试释放成功的完整过程。
