# 四集群实验总览

**状态：方案与代码准备，尚未开跑。** C1–C4 表示四个实验资源组，各由一个 Agent 负责；不是四种 baseline。每组在组内完成对照。阿里云和自备服务器采用同一实验方法、输入与指标，分别建 campaign、分别报告。

## 1. 每组测什么

| 组 / 负责人 | 场景与输入 | 对比 baseline | 推荐机器数与分配 | 主要测量指标 | 计划单元 |
|---|---|---|---|---|---|
| **C1 / c1-owner** | G 产品比较、A Overlay 消融。两组均用 7 份完整 Agent trace；另测相同 4×512 MiB 文件的 4 KiB 暖态随机读 | **G：BrewFS Overlay（native lower）vs JuiceFS**；**A：同一 BrewFS 的 flat-v1 vs Overlay**。G/A 独立建初态、独立报告 | **4 台**：n01/n02 做 G，n03/n04 做 A；每对先同时跑不同臂，再交换机器，完成 3 个配对区组 | trace 完整回放墙钟、实际操作/读写字节、errno 差异、剩余句柄；暖读吞吐、p50/p95/p99；FUSE/Redis/S3 请求、CPU/内存、最终内容及重挂验证 | G 96＋A 96＝**192** |
| **C2 / c2-owner** | P：海量只读 ML 小文件。1,048,576 条独立 TinyStories 记录、一条一文件；发现属性/打乱读取，1/16 读者，client-cold/warm | **BrewFS Pack（packed-metadata-v3 只读挂载）vs BrewFS Native flat-v1＋TiKV**；两个臂使用相同源文件和固定 ML 读集 | **1 台** n05；同机顺序完成两臂的整个配对区组，共 3 区组 | 文件访问吞吐/墙钟；访问延迟；TiKV RPC、GET/对象数、inline 比例、内存/磁盘；Pack 构建发布与 Native 导入成本单列 | **48** |
| **C3 / c3-owner** | D：RustFS 动态块。相同 8 MiB＋123 B 外置对象，完整 GET / 随机 4 KiB Range，冷/暖分别测；首轮单并发 | **默认无 hint vs 完整启用 B=64 KiB / 256 KiB / 1 MiB / 4 MiB**；固定 EC2+2 | **1 台** n06；同机顺序完成五种布局的配对组，共 3 区组 | 逐请求延迟、单并发服务速率；应用/HTTP/后端/设备各层字节与读放大；实际 B/q、inline/external、空间占用 | **60** |
| **C4 / c4-owner** | F：100,000×4 KiB 文件，创建 1/8 个子副本。M：Pytest-10356、SymPy-13878 在完成工具边界后跨节点续跑 | **F：BrewFS revision fork vs JuiceFS clone vs BrewFS Native flat-v1 全量 cp**。**M：BrewFS Overlay vs JuiceFS，各自有 stay / move 对照** | **3 台**：F 在 n09 单机比较三臂；F 结束后，M 使用 n07 源执行、n08 目标执行、n09 持久服务。F/M 不同时运行 | F：创建返回、挂载就绪、首次正确读/持久写、总准备成本、空间增量、源/兄弟隔离。M：总暂停、首工具恢复、move−stay、传输字节、token/剩余预算、会话/内容完整性、独立任务评分 | F 18＋M 24＝**42** |

共 **342 个计划测量单元**，其中 M 的 24 项必须先通过两节点会话恢复资格验证。基础组共 318；另外 96 项 P 元数据消融/D block-only 诊断只按需要追加。计数中的换位周期、同挂载短测不是额外独立样本；每个条件报告 3 个完整配对区组的原值、中位数、范围与配对差值。

## 2. 机器与共同配置

| 项目 | 规划 |
|---|---|
| 并行推荐 | **9 台 Linux**：C1 4 台＋C2 1 台＋C3 1 台＋C4 3 台；管理使用现有工作站 |
| 最低配置 | **3 台顺序复用**：C1 的 G/A、C2、C3、C4 依次独占，交接前确认进程/FUSE/服务结束及结果保全；不能同时计时 |
| 7 台折中 | 先并行 G/A/P/D/F；完成后将 C1 释放的两台移交 C4 做 M 的源/目标 |
| 参考规格 | 每台 8 vCPU / 32 GiB；RustFS 主机 4×250 GiB 独立数据盘、EC2+2。四目录/同盘四分区不能替代四盘；M 的两台纯执行节点可只配专用工作盘 |
| 工作区 | 输入、缓存、元数据、日志与结果使用已批准的目录，不能写进 RustFS volume；建议另留至少 100 GiB 本地空间，按完整 trace 解包和语料清单复核 |
| 资源预算 | 客户端＋元数据合计 4 CPU/8 GiB，RustFS 3 CPU/16 GiB；M 跨机分配仍按合计预算，两臂一致。核真实 cgroup，不只核配置文本 |
| 固定版本 | 开批前同步冻结 BrewFS；RustFS 使用 `Ivanbeethoven/rustfs:feat/dynamic-block-size-rebased`，冻结源码 SHA＋镜像 digest 并核 OCI revision；JuiceFS 1.4.2 核二进制 SHA。相同构建只做一次 |
| 数据与缓存 | 相同源内容、顺序/种子和持久性口径；正常内核缓存、codec none、JuiceFS writeback 关闭；冷/暖条件分别留运行证据 |

M 源/目标只交接执行和必要的会话状态，持久服务留在 n09。JuiceFS 重新挂载原 namespace，不要求 clone 或整树复制；stay 也执行停源、drain、释放写者和冷客户端恢复。同一文件系统的 stay/move 使用同一 checkpoint。不同 VMID 只能证明跨实例，物理跨机需另有宿主/置放证据。

## 3. 阿里云与自备服务器的区别

| 项目 | 阿里云 `provider=aliyun` | 自备 `provider=provided` |
|---|---|---|
| 资源来源 | 后续在明确的数量、地域、规格、费用范围内分配 ECS/云盘；本轮尚未分配 | 只使用提供方交付的服务器、设备和目录；不足时不自行租补 |
| 磁盘 | 参考 4×250 GiB PL1 独立云盘；记录盘 ID、规格和实测限制 | 4 个独立块设备、已挂载 XFS/ext4；同组介质/容量/控制器条件一致，不默认允许格式化 |
| 连接与分发 | 优先一个管理入口＋私网/ProxyJump；来源受限；产物优先私网或同地域 OSS internal | 使用提供的 SSH 别名、可信 host-key、跳板/内网；可通过私网或预先下载的完整本地输入分发 |
| 运行风险记录 | 记录实例型号、可用区、抢占/限速事件；中断保全，禁止自动重购重跑 | 记录 CPU/NUMA/kernel/governor、链路和已有负载；共享机不得全局清缓存或停别人服务 |
| 收尾 | 完整结果下载核 SHA、确认所有本轮进程结束后，按精确 ID 释放本轮资源；预先存在资源保留 | 只撤销本轮创建的挂载/进程/容器/namespace/目录；**不删除服务器、不格式化盘、不清提供方数据** |
| 结果合并 | 与自备环境分开报告；同规格环境内做配对 | 与阿里云分开报告；不能将异构机器或不同环境轮次凑成一个配对区组 |
| 操作入口 | [阿里云准备](../../infra/alicloud/PROVIDER.zh.md) | [自备服务器准备](../../infra/servers/README.zh.md) |

## 4. 执行入口与当前待验证项

| 组 | Agent 初始 prompt | 单步脚本 |
|---|---|---|
| 共用 | [分工/调度单](../../infra/experiment/CLUSTERS.zh.md)；[完整操作手册](../../infra/experiment/RUN.zh.md) | [任务表](../../harness/final_plan.py)、[机器清单](../../harness/experiment_inventory.py)、[主机检查](../../harness/host_preflight.py)、[单条命令计时与保全](../../harness/experiment_step.py) |
| C1 | [C1](../../infra/experiment/agents/C1.prompt.md) | [trace 检查](../../harness/trace_checks.py)、[原始 Rust 回放](../../tools/full-fuse-replay/README.md)、[fio 单步](../../harness/warm_same_files.py) |
| C2 | [C2](../../infra/experiment/agents/C2.prompt.md) | [清单](../../harness/ml_corpus_inventory.py)、[Native 导入](../../harness/ml_import.py)、[只读扫描器](../../tools/ml-corpus-scan/src/main.rs)；Pack 发布单独调用已固定 fixture |
| C3 | [C3](../../infra/experiment/agents/C3.prompt.md) | [固定 S3 请求](../../harness/s3_requests.py)、[布局参数](layout-matrix.json)、[落盘解析](scripts/inspect-layout.py) |
| C4 | [C4](../../infra/experiment/agents/C4.prompt.md)；[流转协议](../../infra/experiment/MIGRATION.zh.md) | 原生 fork/clone/cp 各自留计时收据；[文件状态与首用](../../harness/fs_step.py)、[会话历史检查](../../harness/session_check.py) |

- **Pack 仅进 C2 的只读 ML 小文件组**，不进入 C1 通用读写、C4 fork/流转；真实 ML-agent 任务与 readset 仍需冻结。当前扫描器不是完整 Agent 成绩，延迟直方图仍待补齐。
- **D 必须验证功能实际启用**：四开关、PUT/HEAD、四 shard 的 B/q 和外置布局均留证；1 MiB/EC2+2 是读帧 no-op 对照。4 KiB 是请求粒度，本轮不新增 4 KiB EC block。对象池/请求数按服务器容量预试后冻结，应用字节不冒充后端 I/O。
- F 全量复制在 **BrewFS Native flat-v1 挂载内**执行 `cp -a --reflink=never --sparse=never`；不与本地 ext4/XFS 的复制速度混用。先做 1,024 文件资格验证，400 GiB 规模留后续。
- M 先完成原生会话导出/恢复、写者释放、同一协调端计时适配。计时中不插入 AI 思考/人工确认；只有人工演练时，不报文件系统暂停成绩。不新增多级共享目录实验。
- 单步脚本测试通过不代表真实服务已就绪。实际服务器还需核挂载、限额、冷暖态、计数器、fork/clone 隔离和续跑语义。已有慢值/失败证据保留，不择优删除；缺测不填零。

详细参数以 [主方案](../../paper-design/FINAL-PLAN.zh.md) 为准；trace 来源与完整性见 [trace 清单](../../paper-design/TRACE-WORKLOADS.zh.md)。本页供统一审阅，不是启动指令。
