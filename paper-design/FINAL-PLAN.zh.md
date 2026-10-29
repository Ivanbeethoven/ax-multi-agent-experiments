# BrewFS 实验方案

**范围更新：本页保留基础机制与资格配置。主实验已扩展为 [64/128 Agent 方案](../experiments/retest-2026-10-29/SCALE.zh.md)，VM/Docker 分开测；本页的 9/3 台、342 项不再是总体规模上限。规模计划使用 scale_plan.py，基础资格仍用 final_plan.py。**

操作命令见 [分步执行手册](../experiments/retest-2026-10-29/runbook/RUN.zh.md)，四集群归属与初始 prompt 见 [Agent 分工](../experiments/retest-2026-10-29/runbook/CLUSTERS.zh.md)。本轮只准备方案与脚本，尚未启动正式测试。

## 1. 实验设置

| 项目 | 设置 |
|---|---|
| 测试机 | 阿里云参考规格 8 vCPU、32 GiB、4×250 GiB PL1；自备机使用同组一致的 CPU/内存/四盘规格，独立 campaign。推荐 9 台并行，最低 3 台顺序复用，见 [拓扑](../experiments/retest-2026-10-29/runbook/CLUSTERS.zh.md) |
| 后端 | G/A/P/D/F 每台独立 RustFS，单机四盘 EC2+2；M 使用独立持久服务节点＋两个执行节点；均用专用 DB/bucket/缓存。四分区/四目录不能替代四盘 |
| 资源预算 | 客户端与元数据服务合计 4 vCPU / 8 GiB；RustFS 3 vCPU / 16 GiB；其余留给系统。用 cgroup 核对实际限额，缓存和 buffer 计入总预算 |
| 并行 | 四集群、每集群一个负责 Agent：C1=G/A（两个独立池各两机），C2=P，C3=D，C4=F/M。C 是实验资源组，baseline 在组内比较 |
| 重复 | 每个条件 3 个完整区组；同一挂载下的短测、两机换位周期不作为额外独立样本 |
| 数据与日志 | 测试数据、缓存、元数据均放数据盘；日志放独立结果目录。计时期间不编译、上传大文件或执行其他测试 |
| 版本 | 开批前同步并冻结 BrewFS 源码/二进制；RustFS 固定 `Ivanbeethoven/rustfs:feat/dynamic-block-size-rebased` 并解析当时 SHA、镜像 digest；JuiceFS 1.4.2，记录实际二进制 SHA |

同一源码、features、工具链和 profile 只构建一次。配置、文件清单、二进制和镜像摘要保存在各批次目录，不在本文反复复制。

## 2. 实验配置

### G：BrewFS 与 JuiceFS 产品比较

| 项目 | 配置 |
|---|---|
| 两个 baseline | BrewFS Overlay：workspace-v1 + native lower；JuiceFS：Redis 元数据 + 同版 RustFS S3 |
| 共同设置 | 正常内核缓存；4 MiB 客户端数据块；codec none；关闭 JuiceFS writeback；不启用 Pack 或动态块 hint；相同总资源预算 |
| G1 输入 | [全部 7 份 trace](TRACE-WORKLOADS.zh.md)：4 道 SWE-bench Verified 题的 5 次采集＋ripgrep 构建测试＋archive-I/O；共 85,665,904 回调，逐 case 比较，保留 xattr |
| G2 输入 | 同内容的 4×512 MiB 文件；fio 4 KiB randread，4 jobs，libaio，iodepth=32，direct=1，固定种子 20261029 |
| 数量 | G1：7 case×3 区组×2 baseline×2 换位周期＝84 次；G2：12 次；共 96 次 |

### A：BrewFS 内部 Overlay 消融

| 项目 | 配置 |
|---|---|
| 两个 baseline | 同一 BrewFS 二进制：flat-v1 与 workspace-v1 + native lower；两者都使用 Redis 元数据和同版 RustFS |
| A1 / A2 | 分别使用 G1 的 trace、G2 的暖读输入；保持资源、块大小、codec 和初态一致 |
| 比较目标 | Overlay 整体增量成本；单独缓存优化的开关/提交对照另列，不混进此组 |
| 数量 | A1：7 case 共 84 次；A2：12 次；共 96 次，与 G 独立执行、独立报告 |

### P：Pack 海量只读小文件

| 项目 | 配置 |
|---|---|
| 两个 baseline | Pack：packed-metadata-v3 只读挂载；Native+TiKV：相同 BrewFS 的普通 flat-v1 文件，TiKV 保存元数据 |
| 数据 | 1,048,576 条独立 TinyStories 记录，一条一文件，约 805 MiB；文件名、大小、内容 SHA 固定 |
| 工作负载 | ML-agent 语料发现/属性检查、预处理与训练数据加载的固定读集；读者数 1 / 16；发现属性 / 打乱读取分别测量 |
| 冷暖 | client-cold：新客户端、空客户端缓存；warm：同一读集覆盖并预热。后端缓存状态单独记录 |
| Pack 参数 | metadata/data codec=raw；inline-data=off；frame-policy=size-only；access-profile=sequential-small-file；解码工作区 32 MiB、metadata cache 64 MiB |
| 资源 | 两臂都按“客户端＋元数据服务”合计预算；RustFS 预算相同；训练输出、日志放被测只读挂载之外 |
| 数量 | 2 baseline×2 并发×2 工作负载×2 缓存状态×3 区组＝48 次 |

先以 1,024 文件检查目标路径的内容、GET/读、索引/数据缓存及 Range。真实 ML-agent 任务与读集须在正式执行前冻结；现有扫描器属于文件访问代理，不是完整 Agent 成绩。

### D：RustFS 动态块

| 项目 | 配置 |
|---|---|
| 五个 baseline | 默认无 hint；完整启用 B=64 KiB / 256 KiB / 1 MiB / 4 MiB |
| 启用条件 | block-hint enable/fleet-confirmed、read-quantum enable/fleet-confirmed 四开关全开；以 PUT/HEAD 和磁盘实际 B/q 验证 |
| 相同输入 | 每对象 8 MiB+123 B，普通单 PUT、相同内容；各组均须实际存为 external |
| 读取 | 完整 GET / 随机 4 KiB Range；已验证的冷启动 / 同对象暖态；固定对象清单、请求次数和随机种子；首轮单并发逐请求测量，不称饱和吞吐 |
| 数量 | 5 baseline×2 读取方式×2 缓存状态×3 区组＝60 次 |

对象池总量与请求次数在部署容量核查后写入本批配置，计时前冻结。参数源为 [layout-matrix.json](../experiments/retest-2026-10-29/layout-matrix.json)。1 MiB/EC2+2 保留为读帧 no-op 对照；暂不增加 4 KiB EC block。

### F：BrewFS fork 与 JuiceFS clone

| 项目 | 配置 |
|---|---|
| 三个 baseline | BrewFS 从不可变 revision fork；JuiceFS 在同一挂载内 clone；BrewFS Native flat-v1 挂载中的全量复制参考（不是本地 ext4/XFS） |
| 相同输入 | 100,000×4 KiB 文件，静止且内容/目录结构一致；分别创建 1 / 8 个子副本 |
| 复制参考 | `cp -a --reflink=never --sparse=never`；核查实际读取/写入字节 |
| 语义检查 | 先做 1,024 文件：修改、追加、截断、删除、重命名、新建、fsync、重挂后检查源与兄弟副本不变 |
| 数量 | 3 baseline×2 副本数×3 区组＝18 次 |

### M：Agent 跨节点流转

| 项目 | 配置 |
|---|---|
| baseline | BrewFS Overlay / JuiceFS，各自 stay（源节点恢复）与 move（目标节点恢复）；固定两个执行节点和独立持久服务节点 |
| 输入 | Pytest-10356、SymPy-13878；同一 filesystem 的 stay/move 从同一完成工具边界 checkpoint 续跑，固定模型、预算、恢复提示与评分器 |
| 初态 | 源退出并确认写者释放；两去向均冷客户端恢复。共享数据留在服务节点，HOME/XDG/会话状态按实际需要保全、传输和验证 |
| 数量 | 2 任务×2 filesystem×2 去向×3 区组＝24 次；先完成两节点原生会话恢复资格验证 |
| 方法 | [M 分步协议与检查脚本](../experiments/retest-2026-10-29/runbook/MIGRATION.zh.md)；不做进程内存/工具中途迁移，不新增多级共享目录 |

规划合计 **342 次测量**：基础 318＋通过资格门后执行的 M 24（C1 192、C2 48、C3 60、C4 42）。生成 C4 计划必须带 `--include-migration`；不带时仅 F 18。P 的共享 payload 消融和 D 的 block-only 组另有 96 项条件诊断，非自动追加。8M/32M 文件、64/128 Agent、400 GiB fork 现进入独立扩展矩阵；EC4+2 仍另议。

## 3. 实验方法

### 3.1 多机配对

G 与 A 各自使用以下安排，运行 3 个完整区组；第一个周期的配置顺序由固定种子决定：

| 周期 | 机器 1 | 机器 2 |
|---|---|---|
| 1 | baseline X | baseline Y |
| 2 | baseline Y | baseline X |

两机都完成准备和检查后，AI 才启动该周期。周期结束先核对结果，再重建初态、互换配置。每台机器同时只运行一个计时任务。P/D/F 在同一 worker 内完成全部 baseline 配对，不同完整区组可分机并行。

### 3.2 每次测量的步骤

1. **准备**：核源码/产物摘要、资源限额和数据清单；创建本次专用命名空间、结果目录和挂载。
2. **检查**：核对挂载方式、缓存、对象布局和初始内容。内容检查会预热数据，冷测需随后重新建立规定初态。
3. **执行**：只运行当前一个 baseline、一个条件。G1/A1 回放全部操作且不设总时限；G2/A2 完整覆盖一次、逐遍检查预热，再测 3×30 s。
4. **验收**：检查退出码、实际操作/字节、内容和重挂结果；读取资源与请求计数。AI 据此决定是否执行下一步。
5. **收集**：保存原始结果、日志和 SHA，确认进程结束，再卸载；本机结果下载核验后另行清理本批资源。

暖读预热最多 6 遍，连续两遍吞吐差小于 10% 后由 AI 检查缓存/GET 状态；测量中仍观察缓存淘汰和 GET 回升。未达到条件的记录标为未收敛，不称稳态。

## 4. 测量指标

| 实验 | 主指标 | 同时记录 |
|---|---|---|
| G1 / A1 | 完整 replay 墙钟秒数；计时包含解析和实际回放，排除准备与计时外内容核验 | 操作数、读写字节、errno 差异、剩余句柄、内容/重挂检查、CPU、内存、Redis/S3 请求 |
| G2 / A2 | 吞吐 MiB/s、读延迟 p50/p95/p99 | 每遍原值、FUSE/Redis/GET 每读开销、缓存命中/淘汰、CPU、内存；预热成本单列 |
| P | 文件访问吞吐、任务文件访问墙钟、访问延迟 | TiKV RPC、对象/GET 数、inline 比例、总内存/磁盘、构建发布/导入时间；训练计算与 LLM 等待分报 |
| D | GET/Range 延迟、吞吐、读放大 | 应用返回字节、HTTP 字节、后端读取字节、设备 I/O 字节分别统计；实际 B/q、inline/external、占用空间 |
| F | 命令返回、挂载就绪、首个正确读、首次持久写的耗时 | 基底准备时间、drain、对象/元数据净增量、隔离检查；同时报告从普通文件树到可用副本的总成本 |
| M | 总暂停、首条正确工具恢复延迟、move−stay 增量 | 边界等待/drain/释放/挂载/会话恢复、传输字节、token、剩余预算、历史/内容/独立任务评分；协调端同一时钟 |

报告全部区组原值、中位数、范围和配对差值。G1/A1 逐 case 展示，不将七份不同负载合成一个吞吐；同题两次 Pytest 不当作两个独立题目。G2/A2 的三个短测先归并为该次测量；延迟百分位用原始直方图合并或按 job 分列，不平均 p99。错误、故障和缺测单列，不填零。

## 5. 注意事项

- Pack 仅用于只读小文件主实验，不进入通用读写组；外部大文件小读异常只作为路径诊断依据。
- G/A/P/F 保持默认后端布局；D 独立切换布局。小对象可能进入 RustFS `xl.meta`，不能根据名义 B 推算空间浪费。
- G/A 使用各自同时期的基准；不借用异机旧成绩。有效慢轮保留，故障按各臂一致的规则标注。
- `--performance` 只表示性能回放；内容、持久性和所测语义分别验收，不宣称完整 POSIX 正确性。
- AI 根据当前进程、挂载和结果决定下一步；脚本失败后不自动重跑、切配置或删除资源。

## 6. 操作脚本

| 步骤 | 入口 | 使用范围 |
|---|---|---|
| 生成任务表 | [final_plan.py](../bench/final_plan.py) | 只写计划，不启动任务 |
| 两类服务器清单 / 节点检查 | [experiment_inventory.py](../bench/experiment_inventory.py) · [host_preflight.py](../bench/host_preflight.py) | 静态资源分配与 Linux 只读核查分开；不配置服务器 |
| 单条命令计时 / 保全 | [experiment_step.py](../bench/experiment_step.py) | 固定输入/可执行文件 SHA，执行一次；另行 seal/verify，不自动重试或清理 |
| 固定源码和镜像 | [freeze_backend.py](../infra/rustfs/freeze_backend.py) | 只校验并保存 pin |
| 后端配置 / 检查 / 启动 | [generate_rustfs.py](../infra/rustfs/generate_rustfs.py) · [rustfs_node.py](../infra/rustfs/rustfs_node.py) | 生成、检查与 `--start` 分开调用 |
| OSS trace 目录核查 | [trace_oss_audit.py](../bench/trace_oss_audit.py) | 只读整桶目录、归档 HEAD，下载核验 metadata/sidecar；不是完整包校验 |
| trace 输入 / 结果 / 树检查 | [trace_checks.py](../bench/trace_checks.py) | 每次只检查一项；不挂载、不回放 |
| trace 单次回放 | [Rust full-fuse-replay](../tools/full-fuse-replay/README.md) | 对已准备的专用根目录回放一次 |
| fio 单步任务 / 结果检查 | [warm_same_files.py](../bench/warm_same_files.py) | `prepare` 生成一个 job；AI 执行 fio 后再调用 `check` |
| ML 文件清单 / 单次扫描 | [ml_corpus_inventory.py](../bench/ml_corpus_inventory.py) · [ml-corpus-scan](../tools/ml-corpus-scan/src/main.rs) | 清单检查与 verify/stat/read 分开执行 |
| 布局解析 | [inspect-layout.py](../experiments/retest-2026-10-29/scripts/inspect-layout.py) | 检查持久化布局；不是性能运行器 |
| P 原生导入 | [ml_import.py](../bench/ml_import.py) | 已挂载空目录上导入一次；不挂载/切臂 |
| D 固定请求 | [s3_requests.py](../bench/s3_requests.py) | plan/upload/read 分开，逐请求时间和内容校验；不管理桶/服务/冷暖切换 |
| F 首用 / 隔离；M 文件状态 | [fs_step.py](../bench/fs_step.py) | fixture/snapshot/check/first-read/durable-write/mutate 各独立调用 |
| M 会话历史 | [session_check.py](../bench/session_check.py) | checkpoint/restored/resumed；校验原生 export，不调用模型 |

[分步执行手册](../experiments/retest-2026-10-29/runbook/RUN.zh.md) 给出命令与每步检查点；[阿里云](../infra/alicloud/PROVIDER.zh.md) / [自备服务器](../infra/servers/README.zh.md) 只区分资源准备。脚本可供 Agent 分步调用；正式 ready 仍需当前服务器上验证原生 CLI、挂载/限额、真实 ML 读集、冷暖与计数器、fork/clone 及两节点会话恢复，不能把本地测试通过当作这些验证已完成。
