# 负载、计时与输出

各子项的工作区格式、构建 feature、运行开关与开跑条件见[统一对照表](FEATURES.zh.md)。

参数见[design.json](design.json)，调度单位见[并行流程](../MAIN-SCALE.zh.md)。每条件3个配对批次，32档5个；按整批估计不确定性，Agent不是独立重复。提前冻结平衡顺序，保留全部失败和超时。

## C1：基本 I/O 与动态块（RQ1）

- 主系统对照：BrewFS 基础版、JuiceFS。
- 输入：8 个独立的 4 GiB 文件，先写入 CRC fixture 并完整校验。顺序和随机模式分别准备正确块格式。
- 5 个模式：read、write、randread、randwrite、randrw。顺序块 1 MiB，每 job 测 4 GiB；随机块 4 KiB，每 job 测 256 MiB，偏移覆盖完整 4 GiB。randrw 读写比 75:25。
- 固定 psync、iodepth=1、direct=false、time_based=false；写操作逐次 fsync。8 个 job 是 1 个执行客户端中的线程负载，不叫 8 个 Agent。
- 顺序共测 32 GiB；随机共测 2 GiB、524,288 次操作。初始化、测量、排空、冷重挂 CRC 校验分别计时。
- 输出：原始 fio JSON、各 job 完成字节、带宽/IOPS、读/写/fsync 延迟直方图及 P50/P95/P99、后端 GET/PUT/Range、物理读写字节和每盘负载。

动态块补充条件：客户端对象固定 4 MiB，同样的外置对象与校验语义，比较 enabled+auto 与 disabled+off；另比较固定 hint=64 KiB/256 KiB/1 MiB/4 MiB。单独拆 q 时比较 block-only 与 enabled，实测 B 必须相同。每种布局创建新前缀，保存实际 B/q。内联对象或 hint 未生效的对象标明 no-effect，不能计作布局收益。

## C2：完整 Agent 文件访问（RQ2，辅助 RQ1/RQ3）

| 条件 | 输入 | Agent 数 |
|---|---|---:|
| 单任务 | pytest-full-02、django、ripgrep-20261026、archive-io-20261026，各自完整一次 | 各 1 |
| 同质扩展 | pytest-full-02，每 Agent 完整一次 | 8、16、32 |
| 混合批次 | pytest 10、django 2、ripgrep 10、archive I/O 10 | 32 |
| 错峰补充 | pytest-full-02；固定 seed=9173，在 0–2 秒内分格启动 | 32 |

基础版、完整方案、JuiceFS使用相同seed、repo/HOME/XDG初态、操作及写入内容；每Agent独立工作区和2 CPU/8 GiB限额。events/blobs与输出位于挂载外。

主表 `pacing=off`，不传 `--simulate-llm`，不在每次 I/O 后随机 sleep。错峰只改变启动时刻。Rust 回放按记录顺序串行执行每份 trace，多个 Agent 的回放并发；它不重建原进程线程调度。

共同barrier释放到最后一个回放退出为整批耗时，包含JSON解析和blob读取。逐Agent保存计划/实际启动、完成及峰值重叠数；本地ext4只能作为回放器参考，不能用墙钟相减得出纯文件系统耗时。

输出：任务/整批耗时、成功任务/分钟、失败数、回调和读写字节、CPU秒、元数据/对象请求及传输量。逐操作延迟和版本感知重复读取率的实现缺口统一见[观测验收](EXECUTION.zh.md#验收与交接)；重复率定义为同一文件版本已读区间的重叠字节占成功读取字节的比例。

运行开销分别报告：

- 整套方案：`T(full) / T(basic) - 1`，动态策略相同；包含 Overlay 与 Pack 两条路径的差异。
- 纯 Overlay：同一 BrewFS 二进制与 workspace-v1，Pack、动态块、payload、初态和缓存预算相同，只切换 Overlay。冻结计时段会触达的全部 lower 路径和读取范围；两臂在各自正式测量的挂载上只读预热两遍。先用冷读核实计数确实能看到 lower 访问；末遍要求 lower 的后端 Range 请求与读取字节增量均为 0，且操作仍进入待测的 BrewFS 路径。只命中 Linux 页缓存、没有进入 BrewFS，或末遍仍从后端取数，都不算 lower 热态。预热后不重挂、不清缓存。超过共同缓存预算就事先冻结较小的热子序列，完整 trace 仍单独做系统对照。
- 每臂通过本次热态验证后立即计时；两臂执行同一批文件操作，计算 `T(overlay-on) / T(overlay-off) - 1`，报告总耗时、查找/读延迟和首次修改/同步耗时。输入准备、预热和排空单列，不把它们计入 Overlay 运行开销；同时保留 `T_run + T_drain`。

每次完整回放核实际操作数、读写量、剩余句柄=0；再核最终树、链接、权限、数据库和冷重挂。性能模式的 `completed=true` 不是严格验证通过。首次资格另做严格回放。

## C3：百万共享小文件（RQ3）

正式输入 1,000,000 个 4 KiB 文件、1,024 个目录；热集分别为 1,024 和 16,384。Agent 数 1/8/16/32，各一个新工作区并发执行，工作区内 8 个线程。BrewFS 共享 Packed lower，JuiceFS 使用原生 clone。初态准备在计时外，单列成本。

| 顺序 | 每工作区操作 | 主要指标 |
|---|---|---|
| cold-read | 全范围随机读 65,536 次 | 耗时、每读延迟、元数据与对象请求 |
| lower-prewarm | 热集完整读 2 遍 | 预热耗时和字节，单列 |
| hot-read | 热集读 65,536 次 | 耗时、命中/miss/eviction |
| cow | 首次修改 256 个不同文件并逐次 fsync | 首次修改延迟、写入/空间放大 |
| upper-prewarm | 256 个文件读 2 遍 | 预热耗时、正确性 |
| hot-write | 随机覆盖 2,048 次并逐次 fsync | 写入和 fsync 延迟 |
| upper-rewarm | 256 个文件读 2 遍 | 最新内容校验 |
| mixed | 6,144 次读 + 2,048 次写 | 完成时间、读写延迟与后端负载 |

保存每工作区/阶段JSON及每线程CSV；逐读核内容，写入版本由workspace ID和seed决定。共用初始barrier后，各工作区依次运行8阶段；阶段间不加全局barrier，后端计数归整批。父基线完整校验一次；子工作区冷重挂核全部touched文件、命名空间及父/兄弟隔离。

空间报告分开列：共享基线、每个新增工作区、元数据/AOF、对象物理占用和日志。用“相同存活工作区数下相对基线的增量”比较，不把预先创建的 Pack 成本隐藏。Pack-off 单变量仅在相同 payload 布局的适配通过后执行。

## C4：派生与干净迁移（RQ4）

**派生**：独立生成百万文件 fixture；1/8/16/32 个 child 同时请求。两个对照是 BrewFS 从已验证的不可变 revision 执行原生 fork，以及 JuiceFS 从同一输入内容执行原生 clone。父基线准备和核验在计时前完成。当前 workspace-v1 的 fork 不能当作“自动封存一个活动 upper”；需要新内容时先独立发布并核验 base。开云机前先在非空 Packed lower 上做资格：父目录完整可读，原生 fork 返回的 child 绑定同一 lower，child 首读和重挂后内容正确，修改也不改变父目录或兄弟。仅有命令退出 0 或空 revision 派生回执不够。[历史直接 fork 检查](../pilot-aliyun/evidence/preparation/c4-workspace-v1-packed-fork-01/README.zh.md)出现过空子目录；现有[候选发布路线复验](../pilot-aliyun/evidence/preparation/rbm-c4-published-20261112/README.zh.md)已通过子读、修改隔离和冷重挂。该结果使用候选补丁和两文件输入，不代替主线或规模性能资格。

JuiceFS 的[本机资格](../pilot-aliyun/evidence/preparation/c4-local-juicefs-clone-01/README.zh.md)已用同一份两文件输入验证：两个 clone 均可读，修改其中一个不影响父和兄弟，冷重挂后读回正确。新版命令驱动也[实机验证](../pilot-aliyun/evidence/preparation/c4-local-juicefs-burst-v2-01/receipt.json)了 4 线程的单子目录 clone。两个结果都只用于功能资格。

从发出派生请求开始，分别报告首次正确读取、全部子工作区就绪、任务结束、修改后 fsync 与后台排空；首次读到不等于任务完成。每 child 做 overwrite/append/truncate/unlink/rename/new-file，冷重挂核父/兄弟/child，保存上传量和物理空间增长。少量 child 给逐项值和样本数，不强报 P99。

**迁移**：8/16/32个在线工作区移动2/4/8个，完整方案和JuiceFS各做stay/move。stay执行同样停写/排空/卸载/重开步骤，留在原机；其余工作区继续固定trace。邻居先运行10秒建立基线，再触发迁移，恢复后继续观测10秒。预检选足够长的操作序列并冻结；邻居提前结束则该批邻居影响指标不验收，保留失败记录。1 Agent pilot不含邻居指标。

[rbm 的 stay 功能检查](../pilot-aliyun/evidence/preparation/rbm-c4-stay-20261112/README.zh.md)已在 BrewFS 当前工作区格式和 JuiceFS 上通过：完整工作区冷重挂、空 HOME、原会话导出一致。尚不代表 move、旧写者拒绝或模型续跑通过。

1. 从提前核验的同一 checkpoint 和后续操作序列开始，记录源节点和 workspace/session ID。
2. 停止新工具并 checkpoint，排空、正常卸载、原进程退出，释放原写者。
3. 目标必须是另一宿主，挂载相同 workspace；挂载外 HOME 为空，HOME/XDG 指向工作区中的状态。
4. 全部迁移工作区验证状态、继续固定文件操作；每臂每批只挑预先登记的 1 个 session 做真实 OpenCode resume，节省 token。
5. 同一后端上核源身份失效。若 JuiceFS 的互斥靠协调脚本，明确记为协调端保护；不能写成文件系统原生 fencing。

分别记录源停写到目标首读、首个持久写及新工具完成，模型等待单列；保存session/tool ID、丢失/重复操作、旧写者结果和邻居吞吐变化。本条件只测干净迁移，异常恢复及原缓存盘依赖另立实验。

## 小规模资格与正式数量

pilot：1 Agent，1 次；C1 五模式各 64 MiB；C2 用真实完整 pytest；C3 和 C4 派生按用户最新要求改为 10,000×4 KiB（约 39 MiB）。现有 C3 读 8,192、首次写 256、热写 256、混合 1,024（75% 读）、热集 1,024、预热两遍的定义仍是待执行方案；[两套 1 万文件输入](TENK-INPUTS.zh.md)已在本机生成并全量核验，尚未挂载执行；已有十万文件父数据的抽样保留原口径。C4 迁移仍为 1 个 Agent、原 894 项 checkpoint。耗时超过小规模窗口的项保存实际进度后跳过，不视为通过。

系统矩阵为 pilot 21 个单元、正式 257 个（含stay/move，不含消融、校准、导入及重试），由规划检查器计算。按就绪条件分批冻结与计费，旧“28项”仍按旧计划统计。
