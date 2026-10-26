# BrewFS：研究问题、实验设计与环境协议

> 历史方案（2026-10-23）。本轮五 RQ、三组作者配置、Packed 数据库减压与构建成本、七份回放和迁移设计，以 [2026-10-26 实验方案](../experiments/ax-multi-agent/paper-design/plan.zh.md) 及其 `design.json` 为准。下文的旧 RQ 编号、对照和预算不用于新批次。

版本：2026-10-23。**E00–E16 均为待执行计划，不含本轮新产生的系统性能结果。** 已有 8 个任务、1,138,884 条 FUSE 请求的重分析仍仅支持已报告的负载结构观察。建议硬件、样本规模和参数不是已使用环境。当前原理审计固定 BrewFS `242af5f7e9bba7afc0c61778f1084fcb20e083a0`，见 `evidence/principles-code/`；原 `implementation_inventory.csv` 保留 `518e7cc` 历史检查点，RustFS 扩展仍限定于研究 fork `cc4926fac110`。实际执行后必须生成新的 immutable manifest。

## 1. 实验目的与执行原则

实验需要回答四类不同问题：真实负载为什么需要某项机制；各项独立使用是否有效；所选机制组合的收益与代价是什么；语义、故障和长期资源代价是否可接受。三项机制均可选，组合不是单机制实验的前提，也不预设必须同时开启才能获益。不同问题需要不同证据，不能用单个吞吐图替代。

三项机制统一称为基于增量状态的多智能体工作区（Delta-Based Multi-Agent Workspaces，简称增量工作区）、打包式元数据（Packed Metadata）和可变粒度对象存储（Variable-Granularity Object Storage）。增量工作区由共享不可变下层（shared immutable lower）与私有可写上层（private writable upper，简称私有上层）组合；打包式元数据机制的产物称为打包式不可变快照（packed immutable snapshot，简称 packed snapshot）。中英文名称沿用[已确认术语表](terminology.zh.md)，单独使用、省略与组合关系见 [机制关系框架](03-mechanism-relationships.zh.md)。增量工作区可使用原生下层；Packed 可独立只读使用；普通 flat 卷具有独立对象路径。

对象粒度分层记录：g 为客户端块大小，控制普通路径的 Slice 数据对象划分；L_o 为含适用压缩与成帧的最终上传对象长度；B 表示纠删码编码块大小（erasure-coding block size），q 表示物理校验／读取粒度（physical verification/read quantum）。布局提示（layout hint）主要请求 B；自动提示策略（automatic hint policy）以 L_o 为依据，不改变 g。Packed 数据帧属于快照格式，与 g、B、q 分属不同层。当前 g 默认 4 MiB 并提供固定配置；PPT 的 1/4/8/16 MiB 是研究档位，尚无在线动态选档实现。

当前主要风险不是样本数不足，而是处理组定义不清：legacy 与 Entity 的 I/O 路径不同，布局提示会牵动客户端策略，q 默认关闭且可能不改变实际布局，打包式不可变快照构建器和 Entity 挂载尚有布局提示传参缺口。因此，先执行 E00，再开展性能实验。所有试验均保留负面结果、失败、超时、layout fallback 和未支持操作。

## 2. 研究问题总表

| RQ | 可证伪的问题 | 对应实验 | 主要终点 |
|---|---|---|---|
| RQ1 | 选定 agent 工作负载是否共享足够多的不可变基线，并形成重复元数据及局部 I/O？ | E01、E02 | shared/read-set overlap、mutation footprint、metadata/data 比例与访问局部性 |
| RQ2 | 工作区的增量状态表示能否在相同持久化和缓存条件下降低创建、首次可用及存储成本？ | E03、E04 | provision-to-ready、first-tool latency、private metadata bytes、lifecycle cost |
| RQ3 | 打包式不可变快照独立读取或作为下层时，是否减少元数据库查询及总成本，何时被 overfetch、索引冷启动和构建代价抵消？ | E05、E06、E07 | lower-KV calls、GET/HEAD/bytes、lookup/readdir P99、amortization |
| RQ4 | 固定客户端 g、取数策略、对象长度与后端 B/q 分别如何影响请求、传输和物理 I/O？ | E08、E09、E10 | object count/bytes、persisted geometry、physical bytes、checksum/EC CPU、durable write/repair cost |
| RQ5 | D、P、G 的组合是否产生可解释的额外收益？ | E11 | 交互效应、同资源 Pareto 曲线、限制与反例 |
| RQ6 | 系统改进能否改善真实多 agent 的就绪、任务时间、吞吐和资源消耗？ | E12、E13 | job makespan、provisioning、storage critical path、success/control metrics |
| RQ7 | 隔离、完整性、发布/恢复和回收的承诺在故障与长期负载下是否成立？ | E14、E15、E16 | semantic mismatches、corruption detection、recovery、reclamation backlog、tail latency |

## 3. 环境：三个层次，不能互相替代

### 3.1 T0：单机功能与插桩台架

用途：验证 build、挂载、操作契约、对象 header、layout 和故障注入点，不支持“集群可扩展性”或跨故障域耐久性结论。

- 一台 Linux 物理机或可控 VM；建议至少 16 个物理核心、64 GiB RAM、独立可写测试磁盘。
- RustFS 使用四个独立测试 drive 组成一个明确配置的 EC set，目标几何 d=2、p=2；不得把四个同盘目录描述为四个独立物理故障域。
- 使用隔离目录、端口、bucket 与元数据前缀；不与已有用户数据混用。
- 只校验小数据路径和确定性结果，避免把容器共享资源的数字升级为正式性能结论。

### 3.2 T1：建议的正式研究环境

下面是资源需求建议，实际型号、容量和版本待获取机器后填入 manifest。

| 角色 | 数量 | 建议资源 | 用途与隔离 |
|---|---:|---|---|
| Agent / replay clients | 2 | 每台 24–32 个物理核心，128 GiB RAM，1 块本地 NVMe，25 GbE | 固定 CPU/NUMA；缓存分区；两台独立客户端可测共享与 fan-out |
| Metadata server | 1 | 16 个物理核心，64 GiB RAM，独立 NVMe，25 GbE | 主实验使用统一 durable KV 配置；禁用混跑其他服务 |
| RustFS storage servers | 4 | 每台 16 个物理核心，64 GiB RAM，至少 1 块独立 NVMe，25 GbE | 一台一 drive 形成四盘 d=2,p=2 机制实验；启动时核验实际几何 |
| Coordinator / model endpoint | 外部或独立资源 | 管理进程可以轻量；模型服务不与存储混跑 | 记录模型版本、并发/速率限制、请求时序；协调开销分离 |

此配置优先保留和当前 q 实现相符的四盘 2+2 几何。它不是硬件上限，也不代表 RustFS 的唯一可用配置。四服务器拓扑不能触发当前要求所有在线 shard 本地的 batch-pread 快路径；该优化应在 T0 四本地盘上单独测量，不能将其效果无条件移植到 T1。需要研究 8/16 drive 或其他 data/parity 组合时，作为单独的敏感性实验，并对每个布局重新核验 q；d 变化会改变 shard size 和 q 是否真正缩小。

如果只有单机 T0，先完成机制和正确性计划，明确无法支持的分布式结论。若做元数据服务高可用或进程失效跨节点恢复，可另加两个元数据节点，形成实际引擎支持的部署；不要把单节点 kill/restart 描述为 HA failover。

### 3.3 T2：可选真实云环境

在 T1 找到可重复的机制效果后，才用云主机/云盘检验外部有效性。云盘类型、配额、burst credit、可用区、网络上限和计费模式分别记录。云实验不能与本地 NVMe 实验混在一张无分面的吞吐图中。成本首先报告请求数、传输字节、盘时和核时；货币换算使用执行日期、区域及公开价格链接，当前计划不填写价格。

### 3.4 必须固定或记录的环境变量

| 类别 | 必填字段 |
|---|---|
| 主机 | CPU 型号/物理核/SMT、NUMA、频率策略、RAM、SSD 型号/固件/数量、文件系统与 mount options、NIC/switch/link speed |
| OS | 发行版、kernel、FUSE/fuse3 版本、容器运行时、cgroup 配额、ulimit、I/O scheduler、时间同步方式 |
| BrewFS | git SHA、dirty diff hash、Cargo features、Rust toolchain、mount entry、完整配置、logical chunk、客户端 g、slice 参数及其适用数据路径 |
| RustFS | fork URL/SHA、build flags、d/p/drive placement、inline 阈值、B hint gate、q gates、mmap/read reclaim/write reclaim、checksum/protected modes |
| KV | 引擎和版本、资源预算、WAL/AOF/fsync 策略、持久化完成语义、备份/清理后台任务 |
| 客户端 | memory/SSD cache 上限、admission/eviction、SingleFlight、readahead、attr/entry TTL、direct I/O、kernel writeback、write budget |
| 负载 | task/repo/image/dataset revision、seed、操作流哈希、object compression、稀疏文件/重复内容比例 |
| Agent | DSH/AX/harness SHA、模型实际返回的 ID、provider、温度、工具、token/时间/轮次预算、并发、retry/rate-limit policy |
| 持久化 | 前台返回、fsync、元数据 commit、对象 remote-complete、后台 queue-drained 的分别定义 |

建议先使用可用的稳定 Linux LTS 与固定容器 digest；本计划不声称某个未来软件版本已安装。禁止在最终论文环境表里留下“latest”。

## 4. 基线与公平比较

### 4.1 外部基线

| ID | 基线 | 为什么需要 | 对齐范围和限制 |
|---|---|---|---|
| B0 | 同机 ext4/XFS 原生目录 | 本地开销参照 | 无分布式共享耐久性，不作为等价服务的唯一对手 |
| B1 | 目录完整复制 | 显示物化成本 | 包括元数据和文件内容；只是必要参照，不能作为唯一强基线 |
| B2 | XFS/Btrfs reflink + 预置只读基线 | 现有共享物理数据能力 | 验证实际 reflink 支持；共享数据不必免去所有目录元数据构建 |
| B3 | Linux OverlayFS | 最直接的共享 lower/writable upper 强基线 | lower 预置与 cold-stage 两组；统一 upper 持久化边界、copy-up 和 kernel options |
| B4 | BranchFS | 近期 agent COW 工作区 | 固定 revision；ephemeral fsync 是 no-op，持久性比较包含实际 commit/flush。本地 commit 不自动等同远端 EC 完成；不匹配时分场景报告 |
| B5 | JuiceFS Community + 同一 RustFS | 同类对象存储+元数据服务架构 | 同资源、缓存、压缩、logical block、KV 持久化配置；无法完全一致的设置逐项报告 |
| B6 | 不可变 sorted-KV/SSTable lower 参考实现 | 检验打包式元数据的具体布局，相对于 DeltaFS 等已有组织方式 | 同一 overlay、相同记录内容/缓存/对象 backend；明确是机制参考实现，不能标作“DeltaFS”实测 |
| B7 | 真实 DeltaFS（可行性门槛） | 最接近的 namespace/snapshot 概念 | 先核验操作和工作流可比性；若不兼容，提供定性分析和 B6，不虚构基准数值 |
| B8 | Nydus 或 CernVM-FS immutable lower + 合适 upper（至少一个必需） | 镜像/快照按需分发近邻 | 核心 E03/E05/E07 及一组 E13 包含至少一个可行实现；限定共同契约，计入构建和必要远端 checkpoint |

CephFS、云共享文件服务可作外部有效性补充，但不能用不同硬件、可靠性和预算得出的绝对速度替代同后端消融。YoloFS 必须在 related work 中讨论；其权限与用户纠错目标和本文不同，不要求为了基线数量强行比较。

### 4.2 机制消融，避免改变整条实现路径

- **D0/D1**：同一 namespace/data API 上，完整物化元数据下层与共享下层+私有增量状态。D0 需要实验适配器；不是直接用 flat 代替 Entity。
- **P0/P1/P2**：相同逻辑下层内容，分别为逐文件 KV、immutable sorted-KV、按目录组织的打包式元数据；可附 P3（打包式元数据去掉 hot-attribute/data-locator 共置）以验证具体布局，P3 是待实现控制。
- **客户端 g 因子**：在普通数据路径上，以独立数据集比较固定 1/4/8/16 MiB 档位；先固定 B/q、压缩与取数策略，确认相同逻辑内容和持久化契约。不得在旧对象集上直接改几何，也不得将固定档位 sweep 称为在线动态策略。
- **G0/G1/G2**：保留原编号，专指后端子因子：固定 B + legacy q、按对象策略 B + legacy q、按对象策略 B + q selector。所有记录使用有效布局；自动提示策略与 G0 完全相同时是零处理差异的对照，不能证明 B 策略有效。显式固定 B sweep 只证明粒度敏感性；若提出新 selector，先在独立调优集冻结规则，再以 holdout 对比默认和各固定 B。
- **C0/C1**：Range-oriented 与 full-object+SingleFlight 客户端策略；cache eviction/admission 另作控制或在两组一致。
- **W0/W1**：remote-complete 后写预算释放，作为单独优化控制；不得混入 B 因子。
- **R0/R1**：shared-bytes reply 作为单独控制，区分内存复制减少与后端 I/O 减少。

D0、P0/P1/P3 及 C 策略隔离可能需要新增研究适配器，不能视为现成 CLI flag。普通 flat 上的 g/B/q 实验与独立 Packed 读取可分别开展，不要求先建立 D1。组合实验再选择小规模、合法的全因子子集；若所选适配器仅支持 P1/P2 与 D1 搭配，使用嵌套设计，不补造 D0P1/D0P2 数据。该接线限制不应被写成机制本身的必然依赖。

## 5. 工作负载与抽样

### 5.1 历史 pilot 的使用边界

已有记录使用 DSH `0.1.5-rc.1` 和实际配置 `deepseek-v4-flash`，在 WSL 的 passthrough FUSE 上运行 SWE-bench Verified 任务。本包已对 8 个任务、1,138,884 条 FUSE 请求完成重分析，Core 与 Exploratory 分开报告，见 [工作负载分析说明](../experiments/agent-fuse-observations/README.md)。这些观察描述请求结构、大小与时间变化，不是 BrewFS/RustFS 吞吐或延迟结果，也未完成 E01 的共享性、局部性及正式分阶段采集。

若扩展或回放该 pilot，必须继续核验 `events.jsonl`、`runs.json`、`tasks.json` 和可用性报告中所需字段；现有聚合计数不能替代路径、写入内容、句柄生命周期与操作顺序。检查事件丢失、脚本版本、时间戳单位、任务退出条件和质量评估；累计 handler time 与 wall-clock 不得混用。

### 5.2 正式样本设计

采用三层负载，避免所有实验被模型方差支配：

1. **机制微基准**：受控 namespace、明确 request size/offset 和已知结果，用于 RQ2–RQ4、RQ7。
2. **冻结操作/工具轨迹回放**：来自真实任务，但冻结工具调用与输入，用于因果性能比较。此实验保持文件操作相同，不用于宣称 agent 自适应决策效果。
3. **真实闭环 agent**：固定 harness/model/prompt/budget，允许结果随反馈变化，用于 RQ6 和外部有效性。独立记录任务成功与存储指标。

建议的正式 workload panel：

- SWE-bench Verified 取至少 20 个兼容实例，用于与历史 pilot 的可比性；分层选择仓库规模、任务阶段和修改量，不按已观察的加速比选择。
- SWE-bench Pro 的**一个固定公开版本与数据 revision**取至少 20 个任务；当前不同版本的默认配置和实例数不同，执行前写入版本、实例清单和镜像 digest，禁止仅写“最新 Pro”。
- Multi-SWE-bench 取至少 20 个覆盖 Rust/Go/C++/JavaScript 等环境可用的实例，至少三个语言族。每一实际支持子集单独标识，不能暗示覆盖整个数据集。
- 同一批仓库增加传统 build/test/grep/包安装脚本与受控修改，用作 agent 专属性的反例。
- 为相同基线、不同分支探索/重试构建 4/16/64/128 个工作区的 workload waves；128 以内的主设计也避免超过当前 Entity owner 256 上限，仍需预留 owner 余量并核验实际计数。

这是扩展研究用的 60-task panel。必需闭环核心先冻结 20 个分层任务，选 B5、B8 和预先声明的 BrewFS 组合三条件，每条件三次，即 180 runs；扩展至 60 个任务为 540 runs，尚不含其他并发或模型。B3/B4 先用于机制实验，再选共同任务作外部验证。以暂定 30 分钟 run 上限计算，核心最多 90 个串行 run-hours，扩展最多 270；这是预算上界，不是预计完成时间，模型费用用 pilot 和执行日价格另算。并发 sweep 优先用 replay，闭环仅选代表点并重算预算。具体任务和正式样本在兼容性筛查后、看到处理效果前冻结。筛查只依据预先声明的能力与依赖条件，保留排除清单；不得删去慢任务。

SWE-bench Verified 的测试质量和污染风险已在 2026 年官方审计中被讨论。本研究把它作为可重现的文件系统负载来源，不据其分数宣称更强模型；增加 Pro、多语言和受控任务以扩大外部有效性。[W1–W4]

### 5.3 合成数据参数

| 维度 | 初始 sweep | 控制目的 |
|---|---|---|
| lower 文件数 N | 10^4、10^5、10^6；10^7 仅资源允许时扩展 | 区分 namespace 规模与工作区数 |
| 工作区数 K | 1、4、16、64、128 | 独立视图并发及创建风暴 |
| 修改比例 μ | 0、0.1%、1%、10%、50% | 稀疏变化与增量状态优势消失区域 |
| 文件尺寸 | 4/16/64/256 KiB、1/4/16 MiB，以及经验分布 | 小文件密度及长尾 |
| 请求长度 r | 4/16/64/128/256 KiB、1 MiB | FUSE 与 Range 大小分开记录 |
| 目录形状 | 深度 1/4/8；Entity fanout 100/1,000/4,096；打包式不可变快照的独立只读挂载可扩展至 100,000 | Entity 完整枚举另受 4 MiB retained-directory bytes 限制；更大目录需分页适配后才进入系统实验 |
| 访问分布 | uniform、Zipf α=1.1、热点迁移、顺序遍历 | cache 与 locality 影响 |
| 客户端块 g | 普通路径固定 1/4/8/16 MiB，默认档为 4 MiB | 对象数、实际 L_o、请求数与搬运字节；不代表已实现动态选择 |
| 内容 | 不可压缩、可压缩、真实仓库；各自固定 seed | 最终上传对象长度与逻辑尺寸的区别 |
| RTT（可选受控仿真） | 实测局域网、1/5/20 ms | 对象请求数和 round-trip 敏感性 |

不跑所有维度的笛卡尔积。先以 N=10^5、K=16、μ=1% 固定点做机制对照，再按预先指定的单因素/二维 sweep 分解。保留 N/K/μ/RTT 边界组合用于负面结果。

## 6. 逐项实验协议

### E00：实现接线、有效布局与插桩资格检查（所有 RQ 的前置条件）

**目的。** 证明实验比较的每个标签对应真实可运行路径。

**步骤。** 固定 code SHA 和 clean diff；分别列出普通卷、legacy、Entity、打包式不可变快照的只读挂载入口，以及每项处理选择的机制。先验证省略可选机制的默认路径及拟测单机制路径，再验证相应组合的下层查询、上层写入、布局提示、持久化布局和 read/repair。只有实验选择 Entity/快照构建与 hint 的组合时，才需增补相应 S3Config 传参及 strict durable create-only PUT 的提示元数据，并保留 If-None-Match、412 校验语义和完整 diff。按路径检查：普通上传／打包式不可变快照构建路径生成最终上传对象长度跨过 1 MiB 的对象；在支持压缩的路径改变压缩率而保持逻辑长度相同。EntityFuse MAX_WRITE=1 MiB，直接分片 PUT，当前挂载的上层不能触发自动提示策略的 >1 MiB 分支；应验证其对象分布和无效处理，可另测试固定 B。更大对象的聚合方案属于新实现。

**检查。** 记录客户端 g、实际 Slice/Object 长度分布、请求 header、服务端 effective B、q/marker、inline/multipart/protected 状态；验证旧对象在开关更改后按原布局解释。对 upper 和 lower 的一次 4 KiB read，记录实际 Range 或整对象长度。证明 metadata counter 能区分 upper、control 和 lower。

**停止条件。** 若所选组合未接通或其关键计数器无法确定性验证，不发布该组合的系统结果；其他通过资格检查的默认配置、独立组件或组合仍可按各自范围执行。该检查通过不意味着完整正确性通过。

### E01：真实 agent 文件系统负载画像（RQ1）

**假设。** 仓库导航、依赖探索、编辑、build/test 阶段产生不同的 metadata/data 比例和共享访问。

**设计。** 在相同基线 snapshot 上采集正式 workload panel；每任务先做一个 instrumented run，抽取至少 10 个任务比较 logging on/off 的扰动，必要时采用异步批量记录并测丢失。用受控任务确认记录覆盖子进程与 mmap 相关读行为；仅凭 FUSE 事件无法还原所有 syscall，应注明层次。

**指标。** 各操作数量/延迟/请求与返回字节；lookup miss、getxattr probe 等预期错误与真实 I/O error 分开；每阶段读写热度、路径深度、请求长度/offset、read-after-write、重复 metadata 查询、打开文件数、修改文件和字节比例。共享集合用文件内容/基线身份判定，避免不同绝对路径导致错判。报告 per-task 分布，不让一个长任务支配总体比例。

**图。** 阶段堆叠分布、per-task metadata fraction、请求长度 CDF、read-set overlap 与 mutation footprint；这些图完成后才进入 Motivation。

### E02：共享性与访问组织适配性（RQ1）

**设计。** 对同仓库不同任务、同任务重试、多仓库三类 pair 分别计算基线内容重合、访问集合交集/并集以及 reuse distance。测量同目录名称区间被连续访问的概率，与“随机相邻”及按 inode 排序对照。避免把名称字典序局部性当作未经验证的事实。

**指标。** Jaccard/read-byte weighted overlap、相同索引页／组元数据页（GroupMeta page）复用、跨工作区 cache reuse、增量状态内容分布、跨任务时间间隔。结果若显示随机点查为主，应在 E05 加重点查和 overfetch 反例，重新选择布局参数。

### E03：工作区创建与首次可用（RQ2）

**设计。** B1/B2/B3/B4/B5/B8 与同路径 D0/D1；sweep N、K。分别测：基线已预置；基线需从对象存储首次获取。同步创建 wave 和 Poisson 到达可分两组，但分别报告 offered load。本地 upper fsync 与远端 EC 完成使用不同服务场景；等价远端比较必须包含必要 checkpoint/发布成本，并验证相同故障模型。

**计时。** t0=请求开始；t1=控制面创建完成；t2=mount 可执行首个查找；t3=固定导航工具完成；t4=一次 edit+durability completion。避免把“返回一个 workspace ID”当作可用时间。

**指标。** 每工作区 P50/P95/P99 就绪、wave makespan、服务端写入条目/字节、对象上传/下载、客户端 memory/CPU、错误率。预置成本和首次获取成本分栏；不能让 BrewFS 预热而让其他系统冷启动。

**判据。** 仅在观测范围讨论随 N/K 增长的趋势；不因拟合近似常数就证明渐近 O(1)。如果共享基线已在本地且 OverlayFS 更快，应解释为适用范围。

### E04：修改、生命周期与 churn（RQ2）

**实现门。** Entity 目前 fresh-only，没有完整 reopen/resume/discard wrapper。先完成并验证所需 cleanup/lifecycle 研究实现，或限制为已有 fresh-create、受支持修改和 orderly unmount，并明确没有验证完整回收。

**设计。** 固定初始内容，扫 μ；混合 create/update/delete/rename/truncate 与整目录删除。持续创建—修改—退出—回收，至少 100 个生命周期或达到预注册时长。区分工作区内部多进程与跨工作区隔离。

**指标。** 私有 dentry/inode/mapping 字节、actual object bytes、删除标记数、持久化延迟、回收 backlog、live bytes 与 retained bytes；报告峰值和稳态。删除操作需验证基线对象仍被其他工作区引用时没有误删。

**反例。** μ=50%、频繁 rename 大目录、反复覆盖同文件和小批多次发布。若优化只适用于稀疏修改，明示其条件。

### E05：打包式不可变快照下层查询路径与布局效果（RQ3）

**处理。** P0/P1/P2，必要时 P3；先在独立只读查询接口上比较，再以可运行适配器验证工作区下层组合，两者分别标记。另对 B8 做共同操作契约的外部验证。保持同 namespace 内容、同返回属性和相同 data locator、相同总内存上限。使用同一个 lower API adapter，避免比较不同 FUSE 参数。

**工作负载。** 正/负 lookup、lookup+getattr、readdir/readdirplus、完整目录遍历、lookup+小 Range read；宽目录、深路径、long name、热点目录和跨页边界。扫描与随机点查分别报告。

**计数。** 逐请求区分 control/upper/lower KV；index/GroupMeta/data 对象 GET、HEAD、range byte、有效负载、重复 locator 查询；CPU 与峰值 resident memory。

**验证下层查询独立性。** 在确认 mount/control 已就绪后，对下层查询注入计数或访问拒绝，执行查找与数据定位；仍允许必要 upper/control 操作。查询不得因缓存碰巧命中才通过；从独立新进程或清除对应页缓存重复。报告 cold root/bootstrap 对象请求，不声称整个文件系统为零数据库。

**指标。** 各操作延迟分位数、查询放大、bytes per useful entry、cache hit rate、吞吐/核时。页面预算 sweep 仅使用已实现可配置范围；尚无控制参数的方案列为研究分支而非现成 flag。

### E06：缓存和冷启动分解（RQ3）

设置四种明确状态：COLD=新客户端+空应用 cache+经验证冷对象层；INDEX-WARM=仅 manifest/index 缓存；META-WARM=metadata 页热、文件内容冷；DATA-WARM=重复访问后内容热。服务端 page cache 状态独立成轴，无法清除时准确标为“client-cold/server-warm”。

缓存按路径分别控制：先扫已支持的 metadata/index cache。当前打包式不可变快照的独立只读挂载 CLI 拒绝非零 payload-cache/prefetch，payload 的 0/256 MiB/1 GiB/4 GiB sweep 只能用于支持该参数的普通 VFS，或在研究分支新增并验证缓存后执行。Entity 不使用普通 VFS cache，不能套用其 flags。所有组固定总 RAM，压缩、prefetch、TTL、kernel/server page cache 分别固定或分层。不把共享主机 drop_caches 当作可靠清缓存方案；优先新进程、测试对象 namespace、工作集大于 cache，或在独占台架记录实际操作。

报告 cache 命中与字节变化，并检查 P0 在同样缓存预算下是否已消除大部分远程查询。若是，则贡献应表述为冷启动/受限缓存改善。

### E07：快照构建、发布与摊销（RQ3）

从一致的输入版本构建 snapshot，计入遍历、元数据获取、排序/分组、压缩、对象上传、index/manifest 和 publish。工作区数 K=1/4/16/64/128；快照使用次数和重建频率分别变化。

指标包括构建墙钟/CPU/RAM、临时磁盘、上传对象数/字节、已发布/不可见中间对象、首次访问时间及可回收成本。对于不同大小子树分别报告，不把整个仓库和依赖子树混算。

分别对 CPU-seconds、传输 bytes、持久写 bytes 或执行日货币成本定义同单位可加资源成本：C_total(K)=C_build+C_publish+Σ(C_provision_i+C_access_i)+C_reclaim，不跨单位相加。并发场景另测 batch makespan，不能把各 task wall-clock 求和当作 makespan。只有每工作区净节省为正时才计算经验 break-even K；若所有实测 K 均未摊销，报告“在观测范围未达到”。B8 构建成本也进入同一口径。

### E08：后端 (B,q) 布局微基准（RQ4）

先直接用同一个 S3 驱动调用研究 RustFS fork，再接普通 flat 等选定 BrewFS 路径，不要求增量工作区或 Packed。保持 S3 对象和请求流固定以隔离 B/q；客户端 g 的对象形成效应在 E09/E10 分开比较。

**对象。** 尺寸跨过 inline、1 MiB、B 边界及 multipart threshold；建议 4/16/64/256 KiB、1 MiB±1 byte、4 MiB±1 byte、16/64 MiB，分别用可压缩和不可压缩内容。大对象和小对象分层，inline 单列。

**B。** 默认无布局提示，固定 64 KiB、256 KiB、1 MiB、4 MiB，当前自动提示策略。布局提示只是请求，按有效 B 分组。尤其检查自动提示策略与默认模式实际 B 相同的组。

**q。** legacy 与现有 q-selector gate；不将 q 当作任意连续旋钮。四盘 d=2 下，当前 selector 的例子为：B=64 KiB→q=16 KiB；256 KiB→64 KiB；1 MiB→q 等于 shard=512 KiB；4 MiB→q=1 MiB。最后是否写 marker 还取决于 inline、protected mode 等条件。每组读取实际持久化 marker/frame 后才纳入处理组。其他 d/p 几何重新核验。

**请求。** aligned、跨 q 边界、跨 B 边界、对象尾部、顺序整对象、随机短 Range；r 扫 4 KiB 到 1 MiB。分别 healthy、缺一个 drive、缺可恢复数目 drives，不用故障组推断正常组。

**主要终点。** 应用/HTTP 有效字节、数据/parity 物理 pread 返回字节、checksum bytes/frames、EC decode CPU、pread 次数、P50/P99、总吞吐。前端 Range 长度不能代替后端磁盘字节。

### E09：客户端策略和对象布局的因子拆分（RQ4）

首先固定后端 B/q 和客户端取数/缓存策略，比较普通路径的固定 g 档位；每档创建独立对象集，保持同一逻辑文件内容和应用操作流，测对象数、L_o、GET/PUT 数、网络字节及解码/校验成本。当前 range 阈值按 g 的比例计算，因而 g 变化还可能改变取数决策；若隔离对象划分效应，应匹配绝对阈值，所需研究适配器先通过 E00，不能假称已有 CLI 开关。随后固定 g 进入后端因子比较，避免把对象划分变化当作 B/q 的效果。

legacy-q 与 selector-q 影响新对象写入：先分别创建内容一致、key/manifest 明确隔离的对象集并核验布局。在每个固定对象集内用相同 read trace 比较 Range/full-object 策略，构成 q×客户端策略比较；再固定 B 或在少量合法 B 下重复。LRU/TinyLFU 和 SingleFlight 必须一致或作为额外因素；W/R 优化先冻结。

现有 Entity 上层可能为了整体 SHA-256 获取全对象，它不等于可以直接变成短 Range 的数据路径。对私有上层与打包式不可变快照下层分别做图；如果上层仍取全对象，后端小 q 不产生预期收益是有效结果。若需要修改完整性粒度才能利用 Range，应作为新的设计变化和正确性成本说明，不能默默跳过校验。

用输出 SHA-256 和逐次读结果检查语义相等；记录 GET 去重率、overfetch、cache churn。不要将改用整对象缓存的收益写成“EC 小块减少物理读取”。

### E10：写入、校验和修复代价（RQ4）

采用 append、覆盖、truncate、频繁 fsync、完整文件写入、small-tail objects、multipart+重试。固定 g 对照与 B/q 对照分开：新 Slice 覆盖不自动意味着重写旧完整对象，实际上传量须计入 flush、compaction 和重试。记录 syscall accepted、local staged、remote upload complete、metadata committed、所有后台债务清空五个时间；统一主要完成条件。

将 write amplification 分为：上传字节/用户提交字节、后端持久写字节/上传字节、包含 parity 的物理占用/有效存活对象字节。重复覆盖可导致用户提交字节与最终文件长度不同，分母都保留。压缩后比率小于 1 不意味着没有冗余。

在对象完全持久化后移除一个测试 drive，再测 degraded reads 与 healing；验证修复内容和布局，记录读写/网络/CPU、恢复时间和正常业务 P99。保护模式与不同 q marker 兼容性单列。**只有 E10 支持后，正文才可以使用“减少写放大”；否则第三项贡献仅陈述降低部分读取代价的目标和实测边界。**

### E11：整体机制与组合效应（RQ5）

在 E00 已通过的统一实现上，以少量代表性 workload 执行 D×P×G 的合法配置，固定硬件、客户端 g、cache、持久化及取数策略；G0/G1/G2 继续表示后端子因子。原候选 D0P0G0、D1P0G0、D1P1G0、D1P2G0、D1P2G1、D1P2G2 仅是比较点清单，不表示必须逐项开启；根据入口能力选择并报告独立使用与组合点，实现允许时再补齐其他格子。所有 label 映射到 manifest 中明确的 adapter/flags，不接受含糊的“BrewFS without optimization”。

对可完整交叉的因素，在每个任务上分析 log latency 的 difference-in-differences；对受结构限制的因素只报告 conditional effect，不称为正交全因子。既报告机制增量，也报告全系统相对于 B3/B5 的净收益。若组合没有额外正交效应，可解释互补瓶颈；不能创造“协同倍增”结论。

### E12：确定性工具/操作轨迹回放（RQ6）

优先冻结带路径、输入内容和同步信息的工具调用（如 grep、读取、edit patch、test command）并从同一初始 snapshot 重执行。旧 FUSE JSONL 如果只有 operation ID、inode、长度与 handler time，通常不足以重放创建、rename 和写入：必须补路径/handle、payload 或内容引用、错误码、同步/依赖和 initial image。

两种模式分别标记：闭环 replay 保持依赖和完成触发；到达驱动 replay 保留原始 arrival schedule 并报告排队/饱和。不要将各请求 handler latency 直接相加成任务时间；也不要让 open-loop 工具遗漏 coordinated omission 问题。

验证重放后的文件树、内容哈希、允许的输出和每步错误码。任一语义不匹配时，该 pair 进入 failure 表而非只留下快的一侧。测 task wall-clock、工具 critical path、metadata/data 瓶颈和资源消耗，不评价模型能力。

### E13：AX/DSH 闭环多 agent 端到端（RQ6）

**先证明接入。** 检查 pod/container mountinfo、工作目录、子进程 cwd、依赖/临时目录和实际 I/O 计数；以独特 canary 文件和 tracing 验证 task workspace 没落回 durableDir 或宿主本地目录。容器层、模型下载和编译缓存置于控制路径，或全部计入服务边界。

**实验组。** 同一任务、基线、model endpoint 与预算，在 B5/B8/完整 BrewFS 上做核心配对随机顺序运行，另以共同契约子集验证 B3/B4。20-task 核心与 60-task 扩展的预算见 5.2，不能默认全维度交叉。每任务每条件初始 3 次独立闭环运行；pilot 仅用于确定方差和可承担范围，正式样本量冻结后不按显著性停止。若资源不足，提前缩小任务族并明确外部有效性，不悄悄减少失败样本。

**场景。** 独立同仓库任务、同任务多路径探索、失败后重试、warm pool 与 cold start、并发 1/4/16/64；128 作为系统资源允许时的扩展。AX 的具体版本、模型和多 agent 拓扑在试验前固定，避免在不同后端上改变 agent 数量或策略。

**终点。** provision-to-ready、batch makespan、tasks/hour、per-task wall-clock、模型等待/工具执行/存储关键路径、CPU/network/storage、测试实际通过率、token/工具调用/重试次数。退出码只是过程状态，resolved 必须由独立测试 harness 判定。

**解释。** 调用模型的时间与 I/O 可以重叠，使用 spans/dependencies 识别关键路径，不能简单作减法分解。分析配对任务、语言、初始大小和 read/write 类别；保留不显著、劣化及失败。不要将某个后端上的短轨迹/失败导致的低耗时误判为加速。

### E14：操作契约、差分与并发隔离（RQ7）

先定义受支持操作集合与持久化保证，逐项检查 lookup/getattr/readdir/read/write、create/unlink/mkdir/rmdir、rename/rename-overwrite、truncate、symlink、hardlink、open-unlink-read、permission/xattr、跨目录边界和 sparse holes。未支持操作应给稳定且明确的错误，不借测试跳过暗示完整 POSIX。

以同操作序列对照 ext4/OverlayFS 可比语义，做已知答案用例、随机有状态序列及多进程历史。特意测试仅存在于下层的文件修改、添加删除标记（whiteout）后同名重建、opaque directory、目录合并重复名、EOF、rename 后 inode/data identity、工作区 A 修改而 B 仍读基线。

只在声明 linearizable 的操作范围检查历史；不无依据对整个系统宣称线性一致或跨工作区事务。记录 timeout/deadlock、invariant failure、误读/漏读/多读、error mismatch。

### E15：故障、发布和兼容性（RQ7）

**实现门。** Entity mount 当前不支持 reopen/resume/process-crash takeover。进程恢复实验在研究分支提供相应入口并通过资格检查后执行；此前只能验证已有库协议及明确拒绝/失败行为，不能计为自动恢复通过。

故障矩阵至少包括：对象上传中止；index 已写而 manifest 未发表；manifest 已持久而 control publication 失败；upper metadata commit 与数据上传交错；客户端/元数据/RustFS 重启；部分对象缺失；checksum/frame corruption；multipart retry；旧对象+新 reader、新对象+gate-off reader；healing 中断。

以代码实际支持的 publish protocol 确定 crash point；不虚构一个全局事务覆盖所有步骤。每点重复至少 20 个可重现 seed 或枚举所有确定性 crash window。检查恢复后看到合法旧/新状态还是明确失败、是否引用未完成对象、是否保留不可见垃圾、是否破坏其他工作区。对“不自动恢复但 fail closed”的行为单独报告，不能写成零恢复时间。

GC 与 lease/fence 的 crash 分析只适用于真实选定实现；Entity 与 legacy 的 lifecycle 模型不可混用。

### E16：稳态、资源上界和干扰（RQ7）

**实现门。** 连续生命周期、live publish 与 GC 干扰需选择已有完整管理路径，或先补齐 Entity 的相关接入；不能把 legacy 管理能力当作 Entity 能力。未接入时保留 pending，只报告可运行子集。

选定代表性 read-heavy、mixed、write/churn 负载连续运行至少 2 小时作为初始计划，覆盖多个 cache/GC 周期；实际持续时间在正式运行前冻结。加入背景 snapshot publish、repair、GC 和热点迁移，各自保持可记录负载。

报告 1/10/60 秒窗口 P99、吞吐、RSS、open fds、pending uploads、retained objects、owner count、write budget、GC lag。若资源随时间持续增长，不用最终平均值掩盖。观察 owner 256 上限和分片/分页内存限制，不用超出支持范围的压力试验得出“无限扩展”。

## 7. 度量定义：每个“放大”必须有分子和分母

| 名称 | 定义 | 说明 |
|---|---|---|
| namespace duplication | 所有工作区私有 namespace 字节 / 共享基线 namespace 字节 | 同时给绝对值和每工作区分布；zero denominator 单列 |
| lower query amplification | lower KV RPC 数 / 逻辑 lower 操作数 | upper/control KV 单列；应用操作不是所有内部 lookup 的简单总和 |
| object request amplification | GET+HEAD 数 / 逻辑有效读操作数 | GET 与 HEAD 也分别报告，重试计入且单列 |
| HTTP read amplification | 收到的 S3 payload 字节 / 返回应用的有效字节 | cache-hit 的分母纳入总服务需求时另标；另给 cold-miss 比率 |
| physical read amplification | 所有 data/parity drives 实际读回字节 / 完成请求所需有效字节 | 可包含 checksum bytes；明确 pread 层与块设备层的差别 |
| upload amplification | 上传对象 payload 总字节 / 成功提交的应用写字节 | fsync/writeback 合并和压缩均影响；另给最终存活数据分母 |
| backend write amplification | 后端物理写字节 / 上传对象 payload 字节 | parity、checksum、journal、compaction 分层；不要忽略编码可靠性因子 |
| repair amplification | 修复读/写/网络字节 / 丢失的有效 shard 字节 | 健康读和恢复读不能混算 |
| storage overhead | 物理 live+retained 字节 / 逻辑存活数据字节 | durable redundancy、metadata、garbage 分开给出 |
| latency | monotonic end-start，分别定义完成点 | 不能把 wall time、CPU time 和 handler time 混用 |

对重复读取，同一字节多次服务应多次计入应用需求；对工作集去重另给 unique bytes。对元数据，返回条目数和实际返回字节都应保留。所有单位使用明确的 bytes/KiB/MiB/GiB，网络速率与磁盘容量也注明十进制/二进制。

## 8. 分析模型：用于解释，不代替测量

### 8.1 工作区容量

活跃逻辑元数据的计量恒等式为 M_live-meta = M_base + ΣM_delta_i + M_control。M_base 对共享下层的活跃元数据只计一次；M_delta_i 统计工作区 i 的私有名称、属性、删除记录和数据映射；M_control 统计前两项未包含的 binding、ownership 等控制记录，三类不重计。完整复制会重复实例化基线元数据，但 reflink/OverlayFS 的实际元数据成本必须测量。增量状态存储实体当前状态时，M_delta 不等于“发生变化字段的字节数”。此式不包含上层数据负载、EC/校验和、构建中间对象、尚未回收对象或缓存；这些按物理空间和资源指标单独计量，不能将本式当作全部后端占用模型。

### 8.2 一次理想物理窗口读取

对某 shard 上偏移 a、所需区间长度 l、物理校验／读取粒度 q，理想帧数为 n=ceil((a+l)/q)-floor(a/q)。该 shard 的数据帧覆盖约为 n×q；若每帧校验和为 c，还需约 n×c。实际总读取为各被读 data/parity shards 的和，可能受尾部、批读上限、压缩、fallback、repair 和 cache 影响。不要直接以应用 r 套入一个 shard 的 l，也不要将这个理想模型画成实测结果。

### 8.3 构建摊销与端到端上限

用 E07 的实际 C_build 与每 workspace 净收益计算 break-even；若收益随并发改变，使用每个并发点独立估计。Amdahl 类估算只作 sanity check：存储不在关键路径时端到端收益有限；模型等待与 I/O overlap 由 tracing 决定，不能机械相加。

## 9. 重复、随机化和统计

1. 机制微基准采用至少 10 个独立 run/block 的初始计划，先根据 pilot 的方差与环境稳定性冻结正式次数；请求级百万样本不等于百万独立试验。
2. 处理顺序在 host/time block 内随机化或拉丁方交错，避免固定先跑 baseline 后跑 treatment。记录 seed 和完整运行顺序。
3. 每次运行设统一 warm-up 与测量窗口；冷启动实验不把冷段当 warm-up 丢弃。所有 outlier 排除规则在正式结果前指定，只能基于可解释的基础设施故障。
4. 对配对任务以 task/repository 为聚类单位 bootstrap 95% CI，报告 paired ratio/log ratio 和绝对变化；不能把同任务事件当独立样本夸大置信度。
5. 对成功率给任务数、重复次数与区间；重复按 task/repository 聚类分析，不能作为独立 McNemar pairs。若使用 McNemar，只对预定义的独立 task-level 二元 pairs 应用。主要速度终点是统一 deadline 下全体分配任务的成功完成吞吐和 batch makespan；快速失败不算快速完成。completed-only latency 是次要指标，并列失败/超时的 intention-to-treat 统计。
6. 超时预设为 censored/failed，并保留耗时下界；不得删掉超时后计算更漂亮的均值。记录 rate limit、model outage、backend error 各自原因。
7. Primary endpoints：RQ2 首次可用与总物化元数据；RQ3 lower-KV 与总字节/P99；RQ4 physical read bytes 与 durable write/repair 代价；RQ6 batch makespan 与质量控制。其余指标为解释性结果，避免到处择优报告。
8. 比较配置过多时把预设机制假设与探索性 sweep 分开报告；必要时控制多重检验。小样本无显著差异不等于等价，若要声称无劣化需预注册可接受界值和统计功效。

本包不运行显著性检验，因为尚无正式数据。提供的 schema/表头只是采集契约。

## 10. 可复现记录与执行门槛

每次运行至少生成：`run-manifest.json`、`events.jsonl`、`metrics.csv`、`layout-audit.jsonl`、`stdout.log`、`stderr.log`、任务输出及校验哈希、环境快照、版本/补丁、failure record。实验 bundle 的 manifest 对所有原始文件计算 SHA-256。私钥、访问 token、模型请求中的私人内容不得进入发布包；数据脱敏规则应保留操作统计的解释性。

`experiments/run-manifest.example.yaml` 中的 null 表示尚未获取，不是可直接运行配置。`tools/validate_plan.py` 只校验筹划包的一致性；没有任何工具能据空 manifest 生成真实结果。运行器与 workload adapter 仍是后续工程任务，本包不把伪命令包装成完整 benchmark harness。

### 最小可发表证据门槛

- 每项被主张机制的独立证据及能力边界；凡主张组合效果，提供该组合在同一路径中的集成证据。未接通的组合不能称为完整实现，但不否定其他合法配置。
- 与 OverlayFS/reflink、同后端对象文件系统及至少一种成熟 lazy immutable lower 的公平比较。
- 打包式元数据与不可变 sorted-KV/同缓存对照；正面回应 DeltaFS。
- 客户端 g 的对象形成与传输量证据，以及 (B,q) 的有效布局和物理字节测量；因子拆开缓存、全对象读、写预算与共享回复。
- 至少一类可复现闭环多 agent 场景，以及对应的确定性回放。
- 对只读 lower、私有修改、发布/恢复和 checksum/repair 的关键正确性结果。
- 包含构建/重建摊销、高修改比例、无局部性、大顺序读等负面区域。

若尚无“写放大降低”的证据，保留当前标题但在贡献表述中收缩为已有证据支持的请求与读取代价；若第三项在其适用配置和目标负载中仍没有独立证据，作者应重新评估论文主线和标题，而不是只选择有利 workload。独立有效不要求必须在三项全开的配置中进一步加速，组合成本与负面结果分别报告。

## 参考与协议依据

- [W1] [SWE-bench 官方 harness](https://www.swebench.com/SWE-bench/reference/harness/)
- [W2] [OpenAI 对 SWE-bench Verified 的审计说明，2026-02-23](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)
- [W3] [SWE-bench Pro 官方数据集](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro)；[官方 harness](https://github.com/scaleapi/SWE-bench_Pro-os)
- [W4] [Multi-SWE-bench，NeurIPS 2025](https://proceedings.neurips.cc/paper_files/paper/2025/hash/5afa9cb1e917b898ad418216dc726fbd-Abstract-Datasets_and_Benchmarks_Track.html)
- 当前原理依据：[机制关系框架](03-mechanism-relationships.zh.md)、[PPT 正文与讲稿](../evidence/ppt-principles/slides.json)、[工作区审计](../evidence/principles-code/workspaces.md)、[Packed 审计](../evidence/principles-code/packed.md)、[对象粒度审计](../evidence/principles-code/objects.md)。
- [S1] [BrewFS cooperative I/O design，历史检查点](https://github.com/brewfs/brewfs/blob/518e7cc39b2ff0b421ecf0f656de6d468cacb9b8/innovations/brewfs-rustfs/design.md)
- [S2] [BrewFS Entity mount，历史检查点](https://github.com/brewfs/brewfs/blob/518e7cc39b2ff0b421ecf0f656de6d468cacb9b8/src/cli/entity_mount.rs)
- [S3] [RustFS EC block layout](https://github.com/Ivanbeethoven/rustfs/blob/cc4926fac1107aee76540f28bf22dbd47fcbca24/crates/ecstore/src/object_api/ec_block_layout.rs)
- [B1] [JuiceFS 官方架构](https://juicefs.com/docs/community/architecture/)
- 近邻论文的完整 metadata、出版状态和 DOI 见根目录的 `references.bib` 与 `references/source_registry.csv`。
