# C1 阿里云重测方案：Redis 元数据，64 Agent 主规模

更新：2026-11-01。**当前只供审阅，不购机、不部署、不启动回放。**

本页已接入四组统一的 [ECS/自备服务器脚本规划](runbook/DEPLOYMENT.zh.md)。`scale_plan.py --campaign ecs-20261101` 已能生成本文精确矩阵和角色清单；提供方不同只切换 provider。主组132＋内部54与独立 `study=writeback` 的186是不同计划，不混用ID或结果。

本页是本轮 **C1 / Aliyun / Docker** 的具体方案，覆盖旧 C1 入口中的元数据选择、硬件、矩阵和生命周期准备方式。C2/C3/C4 与自备服务器方案不变。C1 测共享后端上的 Agent 文件访问性能；fork/clone 耗时、跨节点流转另属 C4。

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

| 角色 | 试跑 / 64 Agent / 128 Agent | 单机目标与候选 SKU | 数据盘，不含系统盘 |
|---|---:|---|---|
| Docker 执行节点 | 2 / 8 / 16 | 32 vCPU / 128 GiB，`ecs.g8ine.8xlarge`；最多 8 Agent/节点 | 输入 1 TiB PL2 + 缓存/临时 1 TiB PL2 |
| RustFS | 1 / 1 / 1 | **64 vCPU / 256 GiB**，`ecs.g8i.16xlarge` | **4 × 1 TiB PL2 独立设备**，不以目录模拟四盘 |
| Redis | 1 / 1 / 1 | **32 vCPU / 128 GiB**，`ecs.g8ine.8xlarge` | 1 TiB PL2，AOF 与重写空间单独核算 |
| 协调、跳板与结果 | 1 / 1 / 1 | 8 vCPU / 32 GiB，`ecs.g8i.2xlarge` | 2 TiB PL1 |
| **合计** | **5 / 11 / 19 台** | **168 / 360 / 616 vCPU；672 / 1440 / 2464 GiB RAM** | 每台另配至少 100 GiB 系统盘 |

这次调大共享服务节点，替代旧 C1 的 312 vCPU / 1248 GiB 合计。Agent 配额仍为 2/8；执行节点只分给 8 个任务 16 vCPU / 64 GiB，余量用于宿主、缓存和观测。N≤8 使用一个执行节点，N=16/32/64/128 使用 2/4/8/16 个，配对时位置完全一致。

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
| P：流程试跑 | 完整 pytest-full-02；N=1/8/16 | Overlay / JuiceFS；各 1 个配对区组 | 6，仅资格 |
| M1：弱扩展，主线 | 三份扩展负载分别测；N=1/8/16/32/64；每 Agent 1 个完整任务 | Overlay / JuiceFS × 3 区组 | 90 |
| M2：输入覆盖，主线 | sklearn、sympy、pytest-full-02、pytest-20261026；N=1 | Overlay / JuiceFS × 3 区组 | 24 |
| M3：固定总工作量，主线 | 256 个完整任务实例：ripgrep 128 + archive-I/O 128；N=16/32/64 | Overlay / JuiceFS × 3 区组 | 18 |
| A：BrewFS 内部对照 | 三份扩展负载；N=1/32/64 | flat-v1 / Overlay × 3 区组，单独完整配对 | 54 |
| X：128 扩展，另选 | 三份弱扩展 N=128；固定队列 N=128 | Overlay / JuiceFS × 3 区组 | 24 |

**先完成 P，再做主线 132 cohort 和内部对照 54 cohort；X 不默认执行。** P 的结果不充作正式重复；A 不从别轮抽取一个 Overlay 成绩拼成配对。以上数量与旧通用生成器的 618/990 等总数不同，不直接运行其全部输出。

M3 预先冻结任务实例、每个 worker 的有序队列和路径映射；各 worker 均衡分配两类任务，两臂复用同一分配。不按运行速度动态换题或重分队列。每个任务从独立相同初态开始，任务环境预先准备，计时内只含固定的启动/调度与执行。必须保留实际活跃数曲线；256 次完成不代表始终 64 个活跃 Agent。

可选项单独列计划，不乘到全部主矩阵：

| 项目 | 最小范围 | 前提 |
|---|---|---|
| 初态预热客户端 | 三份扩展负载、N=64、两臂、3 区组，共 18 cohort | 相同初态只读预热协议；不可重放一次有写任务后把修改后的树当热初态 |
| 模拟 LLM 等待 | archive-I/O、N=64；新建“关闭/开启等待 × 两臂 × 3 区组”，共 12 cohort | 默认关闭；真实工具/轮次边界；每任务固定总等待 T、每段 0–15s、配对计划同 SHA。不逐文件操作等待 |
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
| 整组主指标 | 应用完成 makespan、全部远端确认 makespan、成功任务/s、完成/失败/缺测数；弱扩展相对 N=1 吞吐与效率；固定队列相对 N=16 的加速比 |
| 单 Agent | 完成时间分布、相对同 trace 单 Agent 的慢化、每 worker 完成任务数、实际活跃数随时间变化；按任务种类分别统计 |
| 文件系统 | ops、读/写字节、errno 差异、handles、最终内容/应用状态；若采集调用延迟直方图，先验证采样开销与覆盖范围 |
| 持久性 | 应用完成到 drain/确认的间隔、待提交/待上传/inflight/重试队列、dirty/staging 峰值、独立读回结果 |
| 客户端及宿主 | CPU/RSS、cgroup 峰值/OOM/节流/PSI、输入解析与输入盘 I/O、缓存命中、网络、磁盘与宿主 steal |
| Redis / RustFS | CPU/RSS、请求量与延迟、EVALSHA/RPC、AOF/重写、GET/PUT/请求字节、EC 布局、云盘/网络限速和排队 |
| 成本 | 各角色实际保留时长、资源费用、每成功任务成本；准备/空闲费用单列 |

每个条件报告 3 个区组的原值、区组内比值、中位数和范围；三个重复不支持强置信区间结论。任务尾延迟注明样本数；N=1/8 等小样本不把最大值包装成稳定 p99。混合队列报告各任务类与总体值，不将不同工作量简单平均成“单任务延迟”。

## 7. 分步脚本与执行 Agent 的检查点

只使用明确的一步入口；不新增包办建机、全矩阵、重试和销毁的总脚本。每步记录命令、退出码、输出 SHA 和检查结论，输出集中在 `runs/<campaign>/C1/`。

| 步骤 | 已有入口 | 本轮必须检查 / 尚缺内容 |
|---|---|---|
| 01 冻结本轮计划 | [scale_plan.py](../../bench/scale_plan.py)、[新版部署](runbook/DEPLOYMENT.zh.md) | 使用 --campaign ecs-20261101，pilot/main/expanded已编码本页矩阵与角色；核6/132/54/24及execution=false；固定队列任务实例、真实版本/输入仍需冻结 |
| 02 资源准备 | [阿里云 provider](../../infra/alicloud/PROVIDER.zh.md)、[inventory](../../bench/experiment_inventory.py)、[host 检查](../../bench/host_preflight.py) | from-plan已生成准确角色模板，host按角色检查；实际ID/host-key/盘/费用/所有权与峰值验收待填写；旧9/3台模板不适用 |
| 03 冻结与构建 | [freeze_backend.py](../../infra/rustfs/freeze_backend.py)、[build-client.sh](scripts/build-client.sh) | 构建入口先适配冻结源码；每套产物只构建一次，内网分发，核 SHA/镜像来源 |
| 04 对象服务 | [RustFS 说明](../../infra/rustfs/README.md)、[配置生成](../../infra/rustfs/generate_rustfs.py)、[单节点检查/启动](../../infra/rustfs/rustfs_node.py) | 明确 single-node-four-disk、实际 EC、禁用 C1 无关 hint；配置生成、检查、启动分别执行 |
| 05 输入准备 | [下载](../../scenarios/k8s-containers/scripts/download_archives.py)、[归档检查](../../scenarios/k8s-containers/scripts/check_archives.py)、[trace_checks.py](../../bench/trace_checks.py) | 全包与文件级完整性；本次规划没有重新访问 OSS，旧清单不替代开批检查 |
| 06 元数据与挂载 | 冻结 BrewFS/JuiceFS 原生 CLI；[现有挂载注意事项](runbook/RUN.zh.md) | 需补 Redis 配置检查、非空 native P1 准备/身份回执/挂载适配；旧 Redis/workspace-v1 mount_command 不直接复用；Manager 本轮不启用 |
| 07 可选等待 | [trace_think_plan.py](../../bench/trace_think_plan.py)、[等待合同](../../tools/full-fuse-replay/THINK-TIME.zh.md) | 默认关；边界可信且 0≤T≤15×间隔数；总量精确、配对复用 |
| 08 一个 cohort | [full-fuse-replay](../../tools/full-fuse-replay/README.md)、[单步留证](../../bench/experiment_step.py) | 并发屏障、一次放行、活跃度与聚合适配仍需补齐，并验证缺 worker、重复放行、失败和结果丢失 |
| 09 收集与收尾 | [通用逐步手册](runbook/RUN.zh.md)及本轮精确资源清单 | 全体终态与完整归档核 SHA 后，再按归属清理；不由回放脚本删除资源 |

C1 负责人先交付：实际配置与精确计划、无 SQLite 检查结果、全量 trace 校验、非空 lower/FUSE 资格、硬件验收和上述适配的测试。当前不能将 Manager PR 合并、旧二进制可用或计划文件生成，记为实验 ready。**本轮到规划为止，后续实现与正式执行分别调度。**
