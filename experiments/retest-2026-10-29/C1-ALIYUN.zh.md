# 历史参考：C1 阿里云固定角色方案

本页保存已暂停采购的5台、168核方案。当前方法见[C1](C1.zh.md)，实际环境按[SSH-RUN](runbook/SSH-RUN.zh.md)交付；本页硬件表用于追溯旧报价。

## 1. 实验设置与对照

| 组别 | 对照 | 回答的问题 |
|---|---|---|
| 文件系统主对照 | BrewFS Overlay + native lower + **Redis**；JuiceFS + **Redis** | 同样的任务、客户端配额和共享服务预算下，任务吞吐、完成时间与扩展性如何？ |
| BrewFS 内部对照，独立运行 | BrewFS flat-v1 + Redis；BrewFS Overlay + native lower + Redis | 两条产品路径的代价有多大？若格式不同，结论限定为路径比较，不归因于单一开关 |
| 暂不纳入本轮 | TiKV 元数据、Pack、动态 block 消融、fork/clone、跨节点流转 | TiKV 后续独立定义服务拓扑与预算；Pack 留给 C2，布局留给 C3，生命周期性能留给 C4 |

**不使用 SQLite 作为任何被测文件系统的元数据后端，不设置 SQLite fallback。** Manager #394 的 catalog 已支持 Redis/TiKV，但操作 journal 仍使用本地 SQLite，见[源码](https://github.com/brewfs/brewfs/blob/cba857b567fa6e94d50edd1d2e1c47c25c7b2e50/src/manager/mod.rs)。本轮 C1 默认不部署 Manager，卷与 workspace 的准备拆成原生命令步骤，并保存 JSON 回执。原生命令的非空 lower、fork、挂载链路同样必须先验证；不能把“绕开 Manager”当作已完成适配。

这里的“不用 SQLite”针对存储服务与控制组件；原始 trace 中应用自身访问的 SQLite 文件仍保留，否则会改变任务。

所有 Agent 访问本组同一 Redis 和 RustFS 服务池。一 Agent 一独立可写 workspace、HOME/XDG、挂载、会话和缓存；不能给每个 Agent 配独立后端后声称共享扩展性。两种文件系统默认顺序使用同一套机器；如并行运行两个 baseline，须配置两个独立同规格服务池，并跨区组交换池的分配。

## 2. 实验配置

| 项目 | 本轮约定 |
|---|---|
| BrewFS | 执行前核最新 main，冻结完整 SHA、工具链、features、产物 SHA；目标为 `workspace-native-v2` + `native_p1_catalog` immutable lower + 独立 writable head |
| 格式资格 | 非空语料必须经生产路径生成可用 revision；空 root 或测试专用 sealing helper 不算。不得换成 workspace-v1、packed catalog 或 SQLite 来通过检查 |
| JuiceFS | v1.4.2，固定二进制 SHA；同一 Redis 版本与持久性设置、同一 RustFS 构建 |
| Redis | 自建单实例、独立节点、只走私网；AOF 开启、`appendfsync=always`、`maxmemory-policy=noeviction`；内存上限先定 64 GiB，节点留 128 GiB；两臂同值。启动配置、有效 CONFIG 与 AOF 重写峰值均留证 |
| 元数据隔离 | 每次 cohort 的专用 DB/namespace 和真实 volume/workspace 身份入清单；换臂按精确归属重建初态，禁止全库 FLUSHALL |
| 对象服务 | RustFS：`Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased`；开批解析 full SHA，核 OCI revision、digest、实际运行镜像；单机四独立数据盘，实际 EC2+2 须核验 |
| 对象命名空间 | 同一服务池内隔离两臂及轮次；native-v2 使用共享对象池与隔离 key，不要求每 workspace 独立 bucket |
| 上传策略 | BrewFS `upload_before_commit`；JuiceFS 客户端 `--writeback` 关闭。两边实际提交/上传边界分别核实，不把普通 write 返回等同远端持久化 |
| 异步消融 | 保留为后续独立组；#394 的 native-v2 不支持 `commit_before_upload`。同版本、同格式配对能力完成前不运行，不用 flat 替代 workspace 异步臂 |
| 缓存与资源 | 正常缓存，禁止套用旧 TTL0 诊断；每 Agent + FUSE + 回放器合计 2 vCPU / 8 GiB、无 swap，磁盘 read cache 上限 64 GiB；所有实际 TTL、内存缓存、预读、上传并发与内核 FUSE 参数完整冻结 |
| codec / 布局 | 客户端压缩关闭，C1 不启用动态 block hint 或 Pack；记录实际对象布局。编译包含 feature 不等于运行时启用，不为对齐参数名强制两边内部块大小相同 |
| 版本变化 | 同一批对照不更新代码；任何资格修复产生新构建，则冻结新批，不能拼接旧版成绩 |

Redis 采用直接连接单数据节点；本组研究共享服务争用，不声称 Redis 高可用或后端横向扩容。若测试 TiKV，应另建完整 PD/TiKV 拓扑并报告节点数、总预算和持久性，不能将不同元数据架构的差异混入本组文件系统排名。

## 3. 阿里云硬件

同地域、同可用区、私网互通；只协调节点保留公网入口。正式测量选按量实例，避免抢占回收干扰完整区组。具体地域、库存、配额与费用上限在采购前填入调度单。

| 角色 | 本轮台数 | 单机目标与候选 SKU | 数据盘，不含系统盘 |
|---|---:|---|---|
| Docker 执行节点 | 2 | 32 vCPU / 128 GiB，`ecs.g8ine.8xlarge`；本轮最多4 Agent/节点 | 输入 1 TiB PL2 + 缓存/临时 1 TiB PL2 |
| RustFS | 1 | **64 vCPU / 256 GiB**，`ecs.g8i.16xlarge` | **4 × 1 TiB PL2 独立设备**，不以目录模拟四盘 |
| Redis | 1 | **32 vCPU / 128 GiB**，`ecs.g8ine.8xlarge` | 1 TiB PL2，AOF 与重写空间单独核算 |
| 协调、跳板与结果 | 1 | 8 vCPU / 32 GiB，`ecs.g8i.2xlarge` | 2 TiB PL1 |
| **合计** | **5 台** | **168 vCPU / 672 GiB RAM** | 每台另配至少100 GiB系统盘 |

Agent配额仍为2/8；每执行节点4个任务合计8 vCPU/32 GiB，宿主余量用于解析、缓存、采集和验证发压能力。最大8个环境明确映射为4+4；N=4时选2+2，N=1按区组轮换节点，两臂位置一致。机器与共享服务预算在不同N保持不变。自备版交付相同角色预算和独立设备，额外记录物理宿主、NUMA与超卖，不执行云API。

选型依据：g8ine.8xlarge 标称基线网络 44 Gbit/s；g8i.16xlarge 为 32 Gbit/s 网络、20 Gbit/s 云盘带宽。旧 g8i.8xlarge 的 25G 是突发上限，基线为 20G，因此不能当作持续 25G 使用。以上来自[阿里云实例规格](https://help.aliyun.com/en/ecs/user-guide/general-purpose-instance-families)，仍须核目标地域可购性与实际性能。1 TiB PL2 按当前公式为 53,000 IOPS、632 MB/s；实际还受实例总云盘限额约束，不能把四盘标称相加当作实测。见[ESSD 规格与实例限制](https://www.alibabacloud.com/help/en/ecs/user-guide/essds)。

**容量与压测能力通过实测验收后才算充足：**

- 逐卷按两臂较大峰值预算，容量与 inode 均至少保留 25% 空闲；RustFS 4 TiB 原始容量在 EC2+2、预留 25% 后，数据部分上限约 1.5 TiB，元数据/旧版本/暂存还要扣除。
- 每 Agent 64 GiB 缓存是上限，不预分配 64 GiB；8 个上限合计 512 GiB，临时输出和未验收终态另算。固定 256 任务队列按实际累计保留峰值预算，不能只乘并发 N。
- Django events 文件约 21.36 GiB；须验证回放器输入解析方式、RSS 和输入 I/O。输入在被测挂载之外，每宿主一份只读共享；不能假定每个 8 GiB 环境能将它全读入内存。
- 私网持续吞吐、RTT/PPS、云盘 IOPS/吞吐和限速均需测；输入分发在计时外。先排除解析器、输入盘、观测工具限制发压能力，再测文件系统。
- 服务池配置在所有 N 固定；正式规模组遇到 Redis/RustFS 饱和时报告瓶颈，不临时扩容某一臂。SKU 不可用或资格不足时先整体修订配置再冻结。
- 预算按“各角色实际单价 × 保留时长 + 云盘 + 公网/OSS”核算，不沿用旧抢占实例单价。这里的表不是采购指令。

## 4. 负载与实验矩阵

完整备份现有 **27份trace：20份扩展＋7份历史，约13.65GB**；下载/可信摘要/9分卷恢复见 [数据交接](runbook/DATA.zh.md)。本页预先指定的主矩阵仍选择历史7份，约8566万条FUSE回调，来源见 [TRACE-WORKLOADS](../../paper-design/TRACE-WORKLOADS.zh.md) 与 [archives.json](../../scenarios/k8s-containers/archives.json)。扩展20份另按catalog选择和资格验证，不自动混入。**重复分发不增加独立benchmark数；这7份最大初始树约255MiB，不称超缓存数据集。**

选择三份预先指定的扩展负载：`django`（metadata 密集）、`ripgrep-20261026`（构建/读写）、`archive-io-20261026`（解包/扫描/应用索引）。其他四份用于单 Agent 覆盖，不因结果快慢挑选输入。

一个 cohort 指“某一方法、规模、输入条件的一次整组执行”。正式条件均做 **3 个完整配对区组**；每区组两臂顺序随机，位置随区组轮换，随机种子在测量前固定。

| 阶段 | 输入与并发 | 对照与次数 | cohort 数 |
|---|---|---|---:|
| P：流程试跑 | 完整 pytest-full-02；N=1/8 | Overlay / JuiceFS；各1个配对区组 | 4，仅资格 |
| M1：弱扩展，主线 | 三份扩展负载分别测；N=1/4/8；每Agent 1个完整任务 | Overlay / JuiceFS × 3区组 | 54 |
| M2：输入覆盖，主线 | sklearn、sympy、pytest-full-02、pytest-20261026；N=1 | Overlay / JuiceFS × 3 区组 | 24 |
| M3：持续队列 | 256个完整任务：ripgrep 128 + archive-I/O 128；N=8，每Agent 32任务 | Overlay / JuiceFS × 3区组 | 6 |
| A：BrewFS内部对照 | 三份扩展负载；N=1/8 | flat-v1 / Overlay × 3区组，独立配对 | 36 |

**先完成P和观测资格，再做主线84＋内部36，共120 cohort。** P不充作正式重复；A不借用主线的Overlay成绩。64/128规模与异步writeback是将来独立调度，不属于这次8 Agent范围。

M3预先冻结任务实例、每worker有序队列和路径映射；每worker两类任务各16个，两臂复用分配。不按运行速度动态换题。每任务从独立相同初态开始，环境提前准备；计时包含固定启动/调度和执行。保存实际活跃曲线，256次完成不代表始终8个Agent活跃。

可选项单独列计划，不乘到全部主矩阵：

| 项目 | 最小范围 | 前提 |
|---|---|---|
| 初态预热客户端 | 后续独立计划，本轮不追加 | 相同初态只读预热；不可用有写任务修改后的树当热初态 |
| 模拟LLM等待 | 后续独立计划，本轮关闭 | 真实工具/轮次边界；每任务固定总等待T、每段0–15s、两臂同SHA；不逐文件操作等待 |
| TiKV、上传策略、真实模型 | 独立补充方案 | 能力/拓扑/任务与预算先冻结；不以本轮完成代表这些项目完成 |

## 5. 实验方法

1. **先证明数据路径正确。** 核完整归档 SHA、events、initial、全部 blobs、final tree；两臂导入相同初态。非空 native lower 要验证真实 S3 读回、head 写入、隔离、重挂和最终内容。复用旧结果时只作历史参考，不承接旧资格。
2. **准备与计时分开。** 导入、fork/clone、初态验证、下载、构建、挂载在计时外。BrewFS 与 JuiceFS 的初始文件内容/属性一致；本轮不测准备速度，不用提前做完任务的状态作为初态。
3. **主线缓存口径固定为“客户端冷、服务端统一准备”。** 每 cohort 新挂载、清本轮客户端磁盘缓存；在执行节点无其他任务时处理客户机内核页缓存。统一用独立准备客户端读取初始树，再关闭它；两臂采用相同逻辑访问序列并记录服务端缓存状态。这不是物理存储全冷，也不保证后续修改数据全热。禁止在活跃任务间全局 drop_caches。
4. **先就绪，再一次放行。** 所有 N 个环境确认配置、初态、挂载与独立目录，协调器一次释放屏障。保存实际起跑偏差和活跃曲线；顺序启动 N 个进程不能冒充 N 并发。AI 在步骤间检查，计时期间不插入监督等待。
5. **使用原 Rust 全量回放。** 全部操作、xattr、顺序和真实字节保留，不设总回放时限。pytest-full-02 应为 1,747,109 ops、读 281,774,624 B、写 194,706,661 B、handles=0；其他 trace 用各自冻结清单。
6. **结束分三段。** 记录应用完成、尾部提交/上传完成、远端完成确认；随后用独立客户端验证内容，验证耗时另列。fsync/close 或单一 dirty=0 不代替队列与内容证据。固定队列不在任务间额外插入全池 drain。
7. **区分正确性与性能。** 独立语义资格检查 errno、读内容、操作量、终态树及应用状态；performance 模式完成不等于严格正确性通过。旧 BFS 的 errno 差异需重新核对分类；若差异导致跳过实际工作，该条件不能作为等工作量排名。
8. **按预设规则处理异常。** 保存所有慢值、失败、OOM、缺测；基础设施故障的重试使用新 attempt，原值保留。修改配置须重新冻结整个配对条件，不能只删除对某一方不利的重复。

这些是 FUSE trace 回放，重现记录的文件访问，不是重新执行模型推理或原程序的线程并发。真实模型任务另立组；0–15s 随机等待只模拟间歇负载。

## 6. 测量指标与报告

| 层次 | 必报指标 |
|---|---|
| 整组主指标 | 应用与远端确认makespan、成功任务/s、完成/失败/缺测数；弱扩展相对N=1吞吐与效率；固定256队列的绝对完成时间和两臂配对比值 |
| 单 Agent | 完成时间分布、相对同 trace 单 Agent 的慢化、每 worker 完成任务数、实际活跃数随时间变化；按任务种类分别统计 |
| 文件系统 | ops、读/写字节、errno差异、handles、最终内容/应用状态；原生FUSE/metadata计数与延迟直方图，先验采集开销和覆盖 |
| 持久性 | 应用完成到 drain/确认的间隔、待提交/待上传/inflight/重试队列、dirty/staging 峰值、独立读回结果 |
| 客户端及宿主 | CPU/RSS、cgroup 峰值/OOM/节流/PSI、输入解析与输入盘 I/O、缓存命中、网络、磁盘与宿主 steal |
| Redis / RustFS | CPU/RSS、请求量与延迟、EVALSHA/RPC、AOF/重写、GET/PUT/请求字节、EC 布局、云盘/网络限速和排队 |
| 成本 | 各角色实际保留时长、资源费用、每成功任务成本；准备/空闲费用单列 |

每个条件报告 3 个区组的原值、区组内比值、中位数和范围；三个重复不支持强置信区间结论。任务尾延迟注明样本数；N=1/8 等小样本不把最大值包装成稳定 p99。混合队列报告各任务类与总体值，不将不同工作量简单平均成“单任务延迟”。

## 7. 分步脚本与执行 Agent 的检查点

只使用明确的一步入口；不新增包办建机、全矩阵、重试和销毁的总脚本。每步记录命令、退出码、输出 SHA 和检查结论，输出集中在 `runs/<campaign>/C1/`。

| 步骤 | 已有入口 | 本轮必须检查 / 尚缺内容 |
|---|---|---|
| 01 冻结计划 | [scale_plan.py](../../bench/scale_plan.py) | campaign=ecs-20261101，profile=pilot-8/c1-8，provider=aliyun/provided；核4/120 cohort、5角色节点与8个环境4+4；不启执行 |
| 02 资源准备 | [阿里云 provider](../../infra/alicloud/PROVIDER.zh.md)、[inventory](../../bench/experiment_inventory.py)、[host 检查](../../bench/host_preflight.py) | from-plan已生成准确角色模板，host按角色检查；实际ID/host-key/盘/费用/所有权与峰值验收待填写；旧9/3台模板不适用 |
| 03 冻结与构建 | [resolve_backend.py](../../infra/rustfs/resolve_backend.py)、[build-client.sh](scripts/build-client.sh) | 构建入口先适配冻结源码；每套产物只构建一次，内网分发，核 SHA/镜像来源 |
| 04 对象服务 | [RustFS 说明](../../infra/rustfs/README.md)、[配置生成](../../infra/rustfs/generate_rustfs.py)、[单节点检查/启动](../../infra/rustfs/rustfs_node.py) | 明确 single-node-four-disk、实际 EC、禁用 C1 无关 hint；配置生成、检查、启动分别执行 |
| 05 输入准备 | [下载](../../scenarios/k8s-containers/scripts/download_archives.py)、[归档检查](../../scenarios/k8s-containers/scripts/check_archives.py)、[trace_checks.py](../../bench/trace_checks.py) | 全包与文件级完整性；本次规划没有重新访问 OSS，旧清单不替代开批检查 |
| 06 元数据与挂载 | [prepare/node](runbook/ADAPTERS.zh.md) | 原生挂载与正常卸载适配已实现；Redis有效配置、非空lower、身份和实际FUSE内容资格仍须本批验证；不启Manager |
| 07 可选等待 | [trace_think_plan.py](../../bench/trace_think_plan.py)、[等待合同](../../tools/full-fuse-replay/THINK-TIME.zh.md) | 默认关；边界可信且 0≤T≤15×间隔数；总量精确、配对复用 |
| 08 观测和一个cohort | [cohort](../../bench/runtime/cohort.py)、[observe](runbook/OBSERVABILITY.zh.md) | 原始采样→开销配对资格→全体ready→一次放行→应用完成→drain/读回→停止采集并audit；每任务/失败/部分结果全部保留 |
| 09 收集与收尾 | [通用逐步手册](runbook/RUN.zh.md)及本轮精确资源清单 | 全体终态与完整归档核 SHA 后，再按归属清理；不由回放脚本删除资源 |

C1负责人先交付实际配置、无SQLite检查、完整trace核验、非空lower/FUSE资格、资源验收和观测资格。软件测试通过与实际实验ready分开记录。地域/报价/费用上限/保留时长未落实前不采购；没有原生指标、等工作量和内容资格就不进入正式排名。
