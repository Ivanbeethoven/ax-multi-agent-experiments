# 多机 baseline 与机制消融（执行版）

**预注册方案已确定，执行资格逐项通过后放行。** 本轮先做三台机器上的独立对照，不能把它写成共享后端的分布式性能结果。原 [最终方案](../../paper-design/FINAL-PLAN.zh.md) 的五个 RQ 继续有效；本文件只规定机器分配、最小任务和放行条件。

## 本轮：三机、四方法、12 格

三台同规格 ECS 各有独立 RustFS、Redis、数据盘和命名空间。复用同一份核验过的二进制/镜像，测量时停止编译。每台是一个配对区组，每个方法使用全新的状态。

| 顺序 | 机器 A | 机器 B | 机器 C |
|---|---|---|---|
| 1 | native | native-overlay | native-overlay-pack |
| 2 | native-overlay | native-overlay-pack | JuiceFS |
| 3 | native-overlay-pack | JuiceFS | native |
| 4 | JuiceFS | native | native-overlay |

这使每个方法在每台机器各跑一次；不能固定“一台机器只测一个方法”。先各做 smoke，再跑同一份完整 `pytest-full-02`，共 12 个完整回放。准备/复制、Pack 构建上传、回放、排空/自然卸载分别保留，另直接测总墙钟。三个区组报告原值、中位数、范围和机内配对差，不宣称统计上普遍胜出。七份 trace、56 任务批次属于后续 RQ5。

- **版本**：BrewFS `350dc108a3bffcb5e7e27cc4279dfb5729febe60`，RustFS `Ivanbeethoven/rustfs:feat/dynamic-block-size-rebased` 的 `a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed`；固定 producer/replayer/JuiceFS 的源码及二进制摘要，核验运行中镜像 digest。新版本必须另起批次。
- **资源**：相同实例规格、500 GiB PL1 数据盘、服务 CPU/内存上限；逐机标定磁盘和实例级 IOPS/带宽，记录盘与缓存竞争。读取/写入方向分别标定，不能把高于单盘标定的成绩自动作废。
- **正常缓存主组**：四方法使用各自正常的内核缓存默认策略，固定其余资源/codec/持久性。BrewFS 不传 TTL/KEEP_CACHE 覆盖且清除相应继承环境变量；JuiceFS 不覆盖 attr/entry/dir-entry TTL。保存配置值、实际回复与缓存计数；不能用 TTL=0、KEEP_CACHE=false 的强制诊断组作为正常主性能结论。
- **缓存预算**：主对照统一客户端进程资源上限，并登记读/写内存缓存、磁盘缓存和内核缓存条件。BrewFS 读/写内存缓存各 512 MiB，`memory_budget_bytes=1 GiB`；读/写磁盘缓存各 4 GiB，总 8 GiB。JuiceFS 使用 `--buffer-size=1024`、`--cache-size=8192`（MiB）。两者完整 job 使用相同 cgroup CPU/内存上限；缓存预算不等于总 RSS，同数值也不代表缓存语义相同，实际配置与占用都保存。新分臂驱动将落实这些参数，实机证据通过前不能标为已统一。
- **codec**：四方法数据 payload 压缩统一关闭，必须用各自真实支持的参数验证；Pack 的 metadata codec 固定并登记，inline 关闭。数据压缩/校验策略不能随方法悄悄变化，默认 codec 结果另列。
- **数据与持久性**：四方法走各自装置中同版 RustFS S3；BrewFS 显式 `upload_before_commit`，JuiceFS 关闭 writeback；先做 fsync/正常卸载/独立重挂读回资格验证。Pack 的 trace-root 必须在 lower，不能复制到 upper 遮盖机制。
- **缓存状态**：初始树核验会预热，当前 trace 记为“校验后运行、后端缓存未控制”。若要报告冷态，需在验证后实际重挂/清客户端缓存并记录动作；不要把它称为全栈冷读。
- **有效性**：全部原始 trace 操作及 xattr 保留，Rust `--performance` 不设总时限。完成须核对操作数、读写字节及零句柄；性能模式不宣称严格正确性。失败/中断保留，指标缺测为 null。

旧六臂驱动只是 native/overlay/Pack 的默认值和 TTL=0，**不包含 JuiceFS，也没有完成上述 12 格轮换**；新分臂驱动补齐 JuiceFS 和每机任务清单，先做离线测试和实机 smoke，不能把旧六臂原样复制三次就称作主对照。

本轮开跑资格门是：运行版本与构建来源；TTL/KEEP_CACHE、上述缓存预算和 codec；持久确认及独立重开读回；相同实例上限、每盘及并发盘标定；trace、观测、回收链完整。每项保存实测证据，缺测为 null、未通过保持 not_run/invalid，不因已有脚本就算完成。

## 缓存语义与单因素消融

这几项必须分别记录，不能用“缓存开/关”一个标签概括：

| 层次 | 控制什么 | 冻结版本的事实 |
|---|---|---|
| 内核 metadata | 属性与正向路径项（attr/dentry）的回复有效期，减少部分 FUSE 请求 | TTL=0 不代表用户态元数据缓存关闭。BrewFS workspace 配置默认 1000 ms，但只有受 fence 保护的 LOOKUP/GETATTR 可返回正 TTL，创建等路径仍可能为 0；flat 配置默认 0，实际非 ACL 路径可走 legacy 1s 策略，须看实际回复。JuiceFS CE 的 attr/entry/dir-entry 默认各 1s。 |
| 用户态 metadata | client 内部的属性、打开文件、row、索引/解析结果复用 | BrewFS native open-file cache 默认 1s；workspace 的 lease-bound row cache 默认 60s。JuiceFS CE open-cache 默认 0，并非其所有元数据缓存均关闭。各机制保留正常默认，不能靠修改内核 TTL 冒充消融。 |
| 内核 data page cache | 打开文件后读到的数据页以及重复打开时是否继续保留 | BrewFS KEEP_CACHE 默认 false，只控制 FOPEN_KEEP_CACHE 的跨 open 保留，不是 metadata 开关，也不关闭用户态/磁盘数据缓存。JuiceFS 会在重新打开时检查文件是否修改后决定是否保留页缓存，不能假造同义 KEEP_CACHE 参数。 |
| 用户态/磁盘 data cache | 读写缓冲、对象块磁盘缓存、预取等 | 保持上面的资源预算及相同压缩/持久性策略；单独记录请求、命中与占用，不能用应用读取字节数当缓存未命中字节。 |

组别预注册如下，不做所有缓存层的全笛卡尔积：

| 组别 | 配置与用途 |
|---|---|
| `kernel-default` 主组 | 本轮 12 格采用上面的正常内核默认策略；记录实际差异，结论是该资源预算下的系统表现，不把全部差异直接归因某一个机制。 |
| `cache-enabled-aligned` 对齐组 | 先验证单 writer/单挂载、不可变 lower 或合格失效机制，再为 BrewFS 显式 TTL=1000 ms、KEEP_CACHE=true；JuiceFS 显式三个 kernel TTL=1s，保留其正常页缓存校验机制。必须用重复打开/实际 FUSE 回复和读取计数核验，不能仅凭配置声称完全等价；该组单独汇总，不能与主组混池。 |
| 内核 metadata TTL 消融 | 以合格对齐配置为基础，仅把 kernel TTL 从 1s 改为 0；KEEP_CACHE、用户态缓存、预算和数据保持不变。它用于量化绕过内核 metadata cache 的影响。 |
| data page retention 消融 | BrewFS 三方法固定 kernel TTL=1s，仅比较 KEEP_CACHE=true/false；不称“关掉全部缓存”。JuiceFS 的对应项记无同义开关，可报告其重复打开行为，不能凭空构造匹配臂。 |
| 用户态 metadata 消融 | 仅在实际可控接口和失效语义通过资格后，单独改变 native open-file cache、workspace row cache 或 JFS open-cache；其余层固定。需要让请求实际到达该层，必要时双方固定相同的 kernel TTL=0 诊断条件。不是正常主组。 |

BrewFS KEEP_CACHE=true 的源码说明限制了外部修改场景：同尺寸/mtime 的外部内容修改可能保留旧页。跨 client 共享可写文件或迁移/故障实验必须先做一致性与 fencing 资格；不能把单 writer 的性能配置无条件套过去。初始树验证带来的预热、重新挂载和真正的冷/暖状态也分别记录。

依据：[BrewFS 冻结配置源码](https://github.com/brewfs/brewfs/blob/350dc108a3bffcb5e7e27cc4279dfb5729febe60/src/config.rs)、[FUSE 回复路径](https://github.com/brewfs/brewfs/blob/350dc108a3bffcb5e7e27cc4279dfb5729febe60/src/fuse/mod.rs)、[JuiceFS CE 缓存语义](https://juicefs.com/docs/community/guide/cache/)、[JuiceFS CE 参数默认值](https://juicefs.com/docs/community/command_reference/)。固定的 JuiceFS 二进制还须保存 `version` 和 `mount --help`，以实测版本为准，不套用企业版参数。

## 后续：一次只改变一个机制

Overlay、Packed Metadata、动态粒度独立可选，不要求拼成八种运行模式；缺少的组合记能力边界。

| 问题 | 最小成对实验 | 状态/开跑条件 |
|---|---|---|
| RQ1 Overlay | 上述四方法日常代价；F1 父先修改，fork 1/16/64 子，每子写 1 MiB；native 物化、JuiceFS 真 clone 或明确物化 | 日常 trace 可接续。`fork-base` 只能测已有固定版本、串行挂载；修改后 seal/save 和持久性未接通，不能冒充 F1。 |
| RQ2 Packed 减压 | 同一固定父版本，32,768/1,048,576 个 1 KiB 文件；native lower 对 packed lower，消费者 1/16，lookup/stat/readdir；无背景/可持续/过载背景下同一可写邻居 | Rust 查询及固定到达表已有入口。native lower 的等价版本来源、跨客户端消费者、缓存证据、持续速率标定和分层 DB/对象指标仍需资格验证。TTL、g、B/q 全部固定。 |
| RQ2 固定成本与回本 | 分别计版本冻结、打包、上传、校验、发布；K=1/4/16/64；每版更新 0.1%/1%/10%，每版复用 4/64 | producer 总时间可测，但 `packed-build` 要求真实只读 Btrfs snapshot；当前 XFS 静态副本仅为诊断，未包含数据库工作区 seal 成本。CPU秒/请求/字节分别累计，墙钟用直接 makespan；不回本也保留。源 DB 记录仍保留时只能声称请求减压。 |
| RQ3 客户端 g | 固定 Overlay/Pack 和后端布局，仅变 g=1/4/8/16 MiB；相同文件工作量，稀疏 4 KiB 读、顺序读、固定覆盖写 | `object-io` 可做应用诊断；须补对象长度/请求及字节证据后才归因于对象形成。JuiceFS 只比较相同文件工作量，不要求对象长度相同。 |
| RQ3 后端 B/q | 固定对象内容与 S3 请求序列，先分别改变 B、q，再做必要交互；默认/no-op 留作控制 | 当前 runner 的实际 B/q 为 null；需有 EC 几何的装置和持久元数据核验。单盘实例不能用四个目录假装四盘；可复用 BrewFS `tools/perf/rustfs-hint` 的布局审计思路。 |
| RQ4 fork 后迁移 | 固定后端，源/目标必须不同机器；pending=0/16/128 MiB，冷/暖目标；迟到旧 writer、确认前后故障各 20 次 | 修改后 save/open、租约转移和 fencing 实际接口未资格验证，暂不放行。`workspace migrate` 是格式迁移；JuiceFS 重开可作性能参照，缺 fencing 列 N/A。正式规模仍按最终方案 4 源/4 目标、16 workspace。 |
| RQ5 多 Agent trace | 同一共享 RustFS/Redis，七份 trace 各八次共 56 任务，4 worker，并发 1/4/16；按任务族报告完成曲线、makespan和失败 | 需放开 runner 的“仅本机 S3 endpoint”限制并用跨主机部署证据替代本机容器检查；当前 SSH 派发器不完成这项资格。真实 Agent 两道题的 continue/fork/migrate 另等 session API，不用 trace 冒充。 |

## 共享后端实验与并行测试的区别

本轮三机独立后端用于加快收集对照、隔离资源干扰。Packed 的数据库分担、共享后端扩展和跨机迁移则必须让多个 worker 访问**同一个固定后端与数据库**，分别记录请求者与后端资源。正式拓扑仍采用最终方案的四个 RustFS 服务节点；四个卷/目录与四个物理节点分别登记。不要并行运行不同 baseline 去竞争同一后端后再称它们是公平基线；共享压力本身应是明确预注册的因素。

先完成三机 12 格，处理失败和资格缺口，再扩共享后端与机制消融。任何新机器都要有本轮精确资源清单、SSH/云助手双通道、低频 checkpoint、已验证的收集器与清理器。只在结果保存且本轮进程确已结束后释放本轮资源；结束后删除定时跟进。