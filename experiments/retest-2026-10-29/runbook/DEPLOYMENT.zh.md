# C1–C4：阿里云 ECS 与自备 Linux 服务器

**当前阶段：适配代码与离线检查完成后，逐组做实际资格；尚未启动正式实验。** 四组共用[挂载/并发/迁移入口](ADAPTERS.zh.md)和[观测验收](OBSERVABILITY.zh.md)，provider只改变资源取得、身份、网络与收尾。无需Kubernetes。默认一Agent一Docker；独立VM另立计划，不合并成绩。

**本次C1按最新要求改为8 Agent、两个执行节点各4个**，另加RustFS、Redis、协调节点，共5台168vCPU/672GiB。使用 `c1-8`（120 cohort）和 `pilot-8`（4 cohort）；两provider均支持。下表main/expanded保留原扩展设计，不代表本轮运行范围。C2–C4规模暂不变。

## 1. 四组设置、baseline 与主矩阵

| 负责组 | 场景和 baseline | main 条件 | 方法与主要指标 |
|---|---|---|---|
| C1 | Overlay + native lower / JuiceFS，均 Redis；flat-v1 / Overlay 单独对照 | 主线 132 + 内部对照 54 cohort；1–64 Agent；pilot 单列 6 | 完整 trace、统一屏障、三区组；任务/s、makespan、尾延迟、远端确认与后端负载 |
| C2 | Pack packed-metadata-v3 / Native flat-v1 + TiKV | 8M 文件×1/8/16/32/64；另测 1M×64；stat/固定随机读集×客户端冷/相同读集热；144 cohort | 同一真实只读 ML 语料与读集；文件/s、访问延迟、RPC/GET、空间与发布/导入成本 |
| C3 | RustFS 无 hint / B=64 KiB、256 KiB、1 MiB、4 MiB | 全局在途 1/16/64；8 MiB+123 B 对象、128 GiB 池；full GET/4 KiB Range×冷/热；180 cohort | 五臂完整区组，实际 external/B/q 验证；延迟、服务速率、应用/HTTP/后端/设备读放大 |
| C4 | F：BrewFS fork / JuiceFS clone / Native 全量 cp；M：两文件系统各自 stay/move | F 108：1M×4 KiB / 400 GiB、1/8/64 子副本、serial/burst；M 84：活跃1/16/64及既定迁移子集 | F 创建/全部完成/首用/空间/隔离；M 暂停/首正确工具恢复、move−stay、邻居影响与会话完整性 |

三区组内方法顺序随机，输入、位置和缓存条件配对。C1 详情见 [C1-ALIYUN](../C1-ALIYUN.zh.md)；C2/C3/C4 的完整条件继续由 [SCALE](../SCALE.zh.md) 定义。新 campaign 的 main 合计 **702 cohort**，expanded 合计 **1086**；计数是计划范围，不代表全部可运行。128 Agent、C2 32M、C3 大对象/1 TiB 池属于 expanded。

expanded包含主条件但部分服务容量不同，使用独立cell ID和批次，不能直接拿main结果填入；若只做增量，另冻结保持相同服务预算的子计划。

上传策略用 **study=writeback 单独生成**：C1 **186**、C4 **90**。它与 C1 baseline 的“132+54=186”是不同实验、不同 protocol 和 cell ID。native-v2 异步能力与远端就绪未通过前保持阻塞。Pack 只在 C2；C3 无 FUSE/Redis/Agent 进程要求。合成 LLM 等待默认关闭；新增 campaign 暂拒绝直接追加等待，待按固定总量独立配对合同细化，不沿用旧生成器全矩阵倍增。

## 2. 角色、节点和容量

| 角色 | 阿里云候选 | 自备服务器交付目标 | 数据盘 / 约束 |
|---|---|---|---|
| Docker worker / C3 requester | g8ine.8xlarge | 32 vCPU、128 GiB；记录真实核/线程/NUMA | worker 输入1 TiB＋缓存1 TiB；requester 临时1 TiB；每 worker最多8 Agent，每 Agent＋FUSE＋回放器2 vCPU/8 GiB，无swap |
| 独立 Agent VM | g8i.xlarge | 4 vCPU、16 GiB VM，实际 workload仍2/8 | 输入256 GiB＋缓存128 GiB；核宿主超卖和隔离 |
| RustFS | g8i.16xlarge | 64 vCPU、256 GiB、4独立设备 | 服务 cgroup **56 vCPU/224 GiB**，余量给宿主；单机 EC2+2 |
| C1/C4 Redis | g8ine.8xlarge | 32 vCPU、128 GiB | 1 TiB；私网直接连接、AOF always、noeviction；C1无SQLite fallback |
| C2 PD/TiKV | **3×g8ine.8xlarge** | **3个独立节点，各32 vCPU/128 GiB** | 每节点1 PD＋1 TiKV，各有独立128 GiB/1 TiB盘；三副本；冻结版本、WAL/fsync、compaction与进程限额 |
| 协调/观测 | g8i.2xlarge | 8 vCPU、32 GiB | 结果2 TiB；只有一个获批的公网管理入口，其余协调节点可经其跳转 |

每节点另加100 GiB系统盘。数据盘默认PL2，协调结果盘PL1；自备使用独立已挂载设备，不能用四目录冒充四盘。候选 SKU 的基线网络/云盘限额见[阿里云规格](https://help.aliyun.com/zh/ecs/user-guide/general-purpose-instance-families)，实际地域库存、价格与链路仍待验收；不用突发上限当持续能力。TiKV 的3 PD＋3 TiKV拓扑参考[官方说明](https://github.com/tikv/tikv/blob/master/README.md)，本实验按三个节点共置各一对进程，不称六台独立服务器，也不部署无关 TiDB SQL 服务。

| 集群 | 64 Docker节点 | 128 Docker节点 | 64独立VM节点 | 128独立VM节点 | RustFS原始容量目标 |
|---|---:|---:|---:|---:|---|
| C1 | 11 | 19 | 67 | 131 | 4×1 TiB |
| C2 | **13** | **21** | **69** | **133** | main 4×2 TiB；expanded 4×8 TiB；真实语料/索引/暂存峰值决定是否足够 |
| C3 | 4 | 4 | 4 | 4 | 4×1 TiB；HTTP并发不增加Agent VM |
| C4 | 19 | 35 | 131 | 259 | 完整F主组4×20 TiB；128扩展4×40 TiB；M和F不同时争用 |
| **四组并行** | **47** | **79** | **271** | **527** | 服务池各组独立 |

64 Docker 合计 **1536 vCPU / 6144 GiB**，128合计2560/10240；是规划配额，未采购、未证明机器充足。顺序复用按C4峰值19/35节点，但C2仍要落实3个PD/TiKV角色，切换前完整保全、释放旧服务并重新验收，不复用旧资格。全部计划按独立池生成，顺序复用需额外实际映射。

C4容量保留全量cp对照：400 GiB源＋64/128副本，EC2+2并留25%空闲，数据部分至少67.71/134.38 TiB，故目标80/160 TiB。所有方法用共同预算；不能因费用只删cp臂。仅选择M时可另定4 TiB池，但必须形成独立子计划；当前完整C4生成器不自动删减F。writeback仅8副本条件生成4×3 TiB目标，仍需额外物理开销验收。

所有卷按实测峰值留25%容量与inode；元数据盘另算AOF/RAFT/WAL/快照/compaction，云盘吞吐受实例总限额约束。C2语料来源、平均大小与读取集合未冻结时，32M/8M仍不得标ready。

## 3. 两种环境的脚本规划

不维护两套实验逻辑。调用下面同一个计划入口，明确provider；每次命令只做一件事，AI检查输出再继续。路径由独立执行方自己提供，输出目录须事先存在。

阿里云ECS版本，示例C1；C2/C3/C4仅替换cluster与输出文件名：

```bash
python3 bench/scale_plan.py --campaign ecs-20261101 --provider aliyun --isolation docker --profile c1-8 --cluster C1 --output "$RUN_DIR/plan.json"
```

纯服务器版本：

```bash
python3 bench/scale_plan.py --campaign ecs-20261101 --provider provided --isolation docker --profile c1-8 --cluster C1 --output "$RUN_DIR/plan.json"
```

C1首次流程资格用 `--profile pilot-8 --cluster C1`，正式用c1-8；两个执行节点不使用vm profile。C2/C3/C4用main；保留64/128设计的main/expanded仅另行调度。独立VM用vm。C1/C4上传消融另指定 `--study writeback --profile main`，本轮不运行。省略cluster仅生成四组原扩展计划。

从新计划生成准确节点模板：

```bash
python3 bench/experiment_inventory.py from-plan --plan "$RUN_DIR/plan.json" --output "$RUN_DIR/inventory.json"
```

执行方填入真实资产、私网、可信host-key、独占目录和容量后，单独静态检查：

```bash
python3 bench/experiment_inventory.py check --inventory "$RUN_DIR/inventory.json" --output "$RUN_DIR/inventory-check.json"
```

随后在每台实际Linux节点执行一次：

```bash
python3 bench/host_preflight.py --inventory "$INVENTORY" --node "$NODE_ID" --output "$HOST_RECEIPT"
```

新模板按角色核CPU/内存/盘数量与名义容量，不再用默认8核/32GiB代替所有角色验收。静态检查不验证价格、空闲状态、网络、实际盘归属或峰值容量，始终不授权运行；真正readiness由原始证据组成。

| 分步 | 已有脚本 / 文件 | 完成边界 |
|---|---|---|
| 资源取得 | [ECS准备](../../../infra/alicloud/PROVIDER.zh.md) / [自备准备](../../../infra/servers/README.zh.md) | ECS报价/配额/创建、格式化、释放由明确调度独立执行；自备只映射交付资产，不包含云API |
| 固定产物 | [freeze_backend](../../../infra/rustfs/freeze_backend.py)、[构建](../scripts/build-client.sh) | 真实SHA/OCI/digest一致，一套构建经私网分发；不逐worker编译 |
| RustFS配置 | [generate_rustfs](../../../infra/rustfs/generate_rustfs.py) → [rustfs_node](../../../infra/rustfs/rustfs_node.py) | 生成、只检查、启动分别调用；配置中必须选新版protocol；生成器不部署ECS |
| Redis / TiKV / mount | 原生服务CLI；[prepare/node](ADAPTERS.zh.md) | SSH与前台mount监督已实现；服务有效配置、真实非空lower和三节点TiKV仍需现场资格 |
| 输入 | [数据交接](DATA.zh.md)、[trace检查](../../../bench/trace_checks.py)、[ML清单](../../../bench/ml_corpus_inventory.py) | 全包下载和完整内容/来源检查在计时外 |
| C1负载 | [Rust回放器](../../../tools/full-fuse-replay/README.md)、[cohort](../../../bench/runtime/cohort.py) | 全量任务/固定队列、全体ready后一次释放、每任务结果与ACK时间；内核I/O并发由观测证实 |
| C2负载 | [ml_import](../../../bench/ml_import.py)、[ml-corpus-scan](../../../tools/ml-corpus-scan/src/main.rs)、[热读](../../../bench/warm_same_files.py) | 并发cohort和逐访问延迟桶已实现；真实ML读集、8M/32M来源及实际服务资格仍必需 |
| C3负载 | [s3_requests](../../../bench/s3_requests.py)、[布局核验](../scripts/inspect-layout.py) | 有界并发、请求分片、完整逐请求记录；两requester份额之和等于全局目标；布局和原生分层指标现场验收 |
| C4负载 | [Manager](MANAGER.zh.md)、[handoff](../../../bench/runtime/handoff.py)、[迁移步骤](MIGRATION.zh.md) | clean stay/move适配已实现，源卸载/lease失败不启动目标；原生非空fork、真实会话及邻居持续负载须现场验证 |
| 可观测性 | [observe](OBSERVABILITY.zh.md) | 全角色原始指标、阶段采样/断档/SHA检查、至少三对on/off开销资格；未验收不能进正式测量 |
| 单步证据、保全 | [experiment_step](../../../bench/experiment_step.py) | 一条命令、原始输出、seal/verify；不是全实验runner，也不清云资源 |

新版RustFS experiment字段示例，仅展示协议部分，节点、镜像、盘和凭据路径仍需真实配置：

```json
{"protocol_profile":"ecs-20261101","project_name":"ax-c1-attempt1","stage":"G","layout_profile":"disabled","cpus":56,"memory_bytes":240518168576}
```

C1的G/A、C2的P、C4的F/M均disabled；C3的D按无hint/启用组选择，逐项核四开关与实际B/q。旧qualification-20261029仍固定3CPU/16GiB，不能套用到新版；不删旧协议以保留复现。

## 4. 各组开跑前的关键检查

- **C1**：Redis元数据，无SQLite服务依赖；Manager暂不部署。nonempty native P1、原生挂载和回放内容先通过；性能模式完成不能代替正确性。pilot与正式三区组分开。
- **C2**：普通Native基准明确TiKV，Pack保留同等服务预算、报告实际使用量；raw/inline-data off/32MiB解码工作区等沿用冻结配置。已有1M语料不等于8M/32M已就绪，不复制文件冒充新样本。只读，不加上传策略或LLM等待。
- **C3**：必须证明对象external及特性实际生效；4KiB是Range长度，不新增4KiB EC block。1MiB读帧no-op对照保留。无FUSE/Redis，不套用其他组Agent挂载检查。
- **C4**：数据路径元数据Redis；若选择Manager，单独披露其本地SQLite journal，仅作控制日志。#394不等于真实挂载/交接ready。fork丢ACK保持原请求/key/ID，不能回退已推进head；128请求按固定64+64分批，202不等于完成。停写、drain、卸载、释放lease并保全后才逻辑删除；move不能用DELETE代替交接。stay与move保留相同备用资源，邻居持续运行；实例ID不证明物理跨机。

## 5. 执行与交付

[四组负责人](CLUSTERS.zh.md)分别接收自己的prompt和调度单，phase=prepare，先补配置/缺口/离线测试。获得明确执行范围后也须先通过实际readiness；不能把execution_enabled改true当证据。一次执行一个cohort，保留全部有效慢值、失败和未完成记录。

每组交付plan+SHA、实际inventory+身份/归属、版本/输入SHA、有效配置、步骤与全部结果、简洁报告、下载保全回执。ECS按精确本轮ID释放并查询确认；自备保留主机、设备和原有数据。费用与下载权限不授予新增资源、改盘或删机权限。
