# T1–T6 计时与指标定义

2026-10-23。以下为计划，不是测量结果。每批保存原始日志、events.jsonl、请求/任务结果表、资源采样和运行清单。未观察的值为 null，不填 0。输入、步骤和时限在正式采集前冻结。保存完成规则见 [baselines.md](baselines.md)，迁移流程见 [integration-and-migration.md](integration-and-migration.md)。

## 1. 共同事件与计时

端到端时长使用同一协调器记录指令发出、确认收到的单调时间，原始单位 ns，输出 ms/s。事件保存 event_id、batch_id、request_id、task_id、workspace_id、node_id、clock_domain、kind、status，适用时加 checkpoint_id、版本摘要、源/目标、写者身份及基线/迁移方式。本机时钟只能计算本机 span，不跨主机直接相减。

| 事件 | 定义 |
|---|---|
| request_scheduled | 固定到达表中应发时间，不能因系统变慢后移 |
| request_sent / operation_started | 实际发出 / 操作开始，区分发生器延迟与服务队列 |
| operation_finished | 指定操作终止并记录内容检查结果；失败也有终止事件 |
| workspace_ready | 输入和可写目录就绪，规定内容检查通过，不是仅挂载成功 |
| first_useful_result | 工作负载预定义的首个实际工具结果通过检查 |
| checkpoint_started / checkpoint_confirmed | 开始保存 / 共同保存合同及完成记录均成功 |
| task_finished / grader_finished | 任务终止 / 固定判题器完成；退出与正确分开 |
| retention_released / gc_observed | 指定版本允许删除 / 一次实际空间与引用检查 |

时钟不连续、事件缺失或日志丢失均保留原运行和原因。无法计算时长的样本不补造，但任务成败仍进入结果；替换运行保留原运行 ID。额外实验采样日志外置，运行所需的原生会话和工具结果留在工作区并计入 I/O；记录采样率与丢事件数，使用计量开/关配对运行评估扰动。

持续请求按发送计划标记为热身或测量。起点为首次热身请求，热身区间 `[0,300)` 秒，测量区间 `[300,1200)` 秒，之后最多收尾 600 秒。窗口吞吐只统计测量区间内实际正确完成的请求数除以 900，同时列出其中来自热身的请求数。按时成功率使用测量期间到达的全部请求作分母，按各自计划到达时刻计算期限；收尾阶段的完成不能倒填窗口吞吐。两种指标分开报告，所有请求保留所属阶段。

## 2. T1：容量、到达率和队列

| 指标 | 定义 |
|---|---|
| requested_workspaces / usable_workspaces | 请求规模 / t0+120 s 前完成准备、规定读改保存及核对，保持打开和 owner/引用至 t0+180 s、且两次核验均通过的数量 |
| on_time_success_ratio | 时限内成功数 / 全部计划请求数；拒绝、不支持、排队超时、错误和未完成都在分母 |
| generator_lag_ms | request_sent − request_scheduled；未发出请求仍计计划数，单列发生器受限 |
| queue_wait_ms | operation_started − request_sent；未开始者报告等待至超时/观察截止 |
| service_ms | operation_finished − operation_started；成功与错误分开，快速拒绝不能拉低成功延迟 |
| request_elapsed_ms | operation_finished − request_scheduled，包含发生器延迟与全部排队 |
| completed_per_s | 固定窗口内通过检查的完成数 / 窗口秒数；同步列计划、发出、开始、拒绝及剩余队列 |
| resource_use | 全部客户端/服务端分别计 CPU 秒、总内存/RSS/缓存、元数据行数/字节、对象数、网络；给总量与峰值 |

N=16/64/256/1024 是请求规模，不是支持承诺。owner、内存、挂载、API 配额等限制均保留为容量结果，标出实际可用数。固定到达表与“完成一条再发下一条”的负载分开；后者因变慢而发出更少请求，不代表后端更省。排空时间和剩余队列单列，不能延长吞吐分母掩盖积压。

**容量组：**t0 同时提交全部 N 个请求，t0+120 s 为准入截止。在 t0+120 s、t0+180 s 分别记录 admitted_workspaces_120s、held_workspaces_180s 及读写/引用检查；成功工作区始终打开并持有 owner/所需引用，第二次核验后统一释放。晚于 120 s 才成功者不计按时准入，异常结果仍保留。禁止创建后立即 close、释放再创建以绕过上限。**持续压力创建组：**每个生命周期完成后才释放，固定到达率和队列独立计量；累计创建量不等于同时可用容量。

## 3. T2–T3：保存、分叉、历史、回收与发布

| 指标 | 定义 |
|---|---|
| save_ms | checkpoint_confirmed − checkpoint_started；暂停到继续工作另报，后台保存尾部计入 |
| branch_ready_ms | 父状态分叉请求到子工作区读回已保存的父修改、独立写入检查通过 |
| isolation_errors | 父/兄弟被错误修改或读到其他私有内容的次数；任一项为正确性失败 |
| retained_requested / retained_recoverable | 请求保留数 / 按冻结检查规则确认可恢复的数量；抽查数/通过数另报，不把抽查当全量验证 |
| retained_storage_bytes | 基线、私有/共享对象、历史、索引、控制记录和未回收对象的实际占用；共享物理对象只计一次 |
| gc_reclaimed_bytes | 允许删除后实际回收量，同时记新写入与待回收量；前后总占用差不自动等于 GC 回收 |
| gc_recovery_ms | retention_released 到预定回收目标达成；超出观察窗记未完成并报剩余字节 |
| publish_ms | 发布请求到完整版本可见且绑定成功；构建、上传分项保留 |
| all_consumers_finish_ms | producer 开始构建到全部指定消费者正确完成；未全成功为 null，并报成功数/截止时间 |
| version_errors | 半版本、未绑定版本、旧消费者意外换版本的次数 |

H=1k/10k/100k 表示要保留的历史。活跃挂载、仍保留引用的历史与已释放终止记录分别计数；容量/恢复/GC 失败不可从空间曲线消失。修改固定最终变化字节、写次数、文件数和位置；另记新增/删除字节、重复写入流量、PUT 数和元数据成本。写 10 次同一处不能只按最终改动量解释开销。

共享读取按同一内容版本的范围计量：各工作区集合 R_i 先自身去重，read_reuse = 1 − |union R_i| / sum |R_i|；分母 0 则 null。相同路径不同内容不算复用；内容可复用比例、缓存命中、真实网络量分开。T3 主比较是构建一次后用各方案正常共享/分发，不能仅对比每个消费者重复构建。

## 4. T4：固定工具回放与真实任务

默认主对比是 16 份固定工具轨迹，见 [回放方案](replay-experiments.zh.md)。报告 replay_correct_count（退出码、输出和产物核验均符合的轨迹数）、replay_batch_ms（所有指定轨迹都正确完成时才有值）、replay_diverged_count 和 first_divergent_step。失败和未开始仍在 16 的分母中；回放的正确只表示复现记录，不等同真实任务解题成功。模型等待的固定间隔组与零等待压力组分开。

以下指标只用于另列的真实 Agent 运行。

各系统执行同一组 16 个题目、代码版本、提示词和模型预算，只改变并发 C=1/4/16。批次从首任务提交开始，包括其余任务排队、准备、模型等待、工具、重试和保存尾部。

- correct_completed_by_t：时刻 t 前已结束且最终被固定判题器判正确的指定任务数，主图范围 0–16；固定截止时报告正确数/16。
- batch_terminal_ms：从批次开始至全部指定任务终止，含失败；与正确数并列。batch_all_correct_ms 仅在 16 个都正确时有值，否则 null，不能用失败快证明更好。
- task_elapsed_ms：任务提交到终止。退出码、补丁产出、测试通过、模型请求完成和 task_success 分别保存；task_success 来自独立 grader，无结果为 ungraded。
- tool_wall_ms、model_wait_ms：记录各 span；同类并发区间取并集，重叠单列，不直接求和当全程。保留模型限流、重试、token 数，不从实际任务时间扣除。

失败、模型错误和超时都留在 16 个任务分母。模型随机性会改变工具路径，所以 T4 回答实际任务效果；T1–T3/T6 的固定操作解释设计机制。

## 5. T5：目标范围、暂停、整体代价与邻居

固定 64 个工作区，id=0…63，源节点 node=floor(id/16)，主线目标 target_node=(source_node+1)%4；其他目标另做范围探测。全部 64 个在第 10 轮保存确认点进入短屏障，协调器确认全部到齐后确定 t0。t0 统一给 16 个选中者 migration_requested、pause_requested，它们保持暂停至取得迁移槽位并正确恢复；排队期间不得改变文件或会话。迁移并发为 1/4/16，与同输入、同屏障的不迁移批次配对。

48 个邻居在 t0 继续，每 5 s 一轮，共 120 轮，计划时刻 t0+5k s（k=0…119），持续观察 600 s；每轮期限为计划时刻+120 s。t0+600 s 停止新请求，最多收尾 300 s，不延长单轮期限。迁移恢复统一截止 t0+600 s，配对批次统一截止 t0+900 s；所有 C、基线和不迁移对照使用相同时间表。屏障前成本仍计全任务时间，准备失败保留。正常迁移、源故障、同区原卷换挂、跨区快照新卷分组。

| 指标 | 定义与分母 |
|---|---|
| target_candidate_count | 运行前固定候选目标数，满足相同计算/镜像条件，未按存储可达性筛选，源节点不计 |
| target_supported_ratio | 有配置/接口证据支持指定方式的目标数 / 候选数，只描述支持范围 |
| target_verified_ratio | 实际验证状态正确、任务继续且不要求暂停未选中邻居的目标数 / 候选数；未知/未测单列，不算成功 |
| migration_success_ratio | t0+600 s 前恢复正确状态并继续任务数 / 16 个指定请求；不支持、错误、超时都在分母 |
| migration_dispatch_queue_ms | migration_started − pause_requested；16 个均从 t0 暂停，此项是获得迁移槽位前的排队，属于暂停的一部分 |
| migration_pause_ms | target_first_useful_result − pause_requested；主线 pause_requested=t0，包含排队、必要控制保存、源释放、目标接入、会话及首个实际工具 |
| migration_request_elapsed_ms | target_first_useful_result − migration_requested；主线 migration_requested=pause_requested=t0，因此与暂停相等，不能从暂停中扣去排队 |
| migration_batch_resume_ms | t0 到 16 个全部有效恢复；t0+600 s 尚未全恢复为 null，报截止时恢复数/16 |
| task_extra_completion_ms | 迁移批次同一任务“提交到正确完成”时长 − 不迁移配对时长；任一未正确完成则 null，并保留成败对 |
| all64_extra_completion_ms | 全 64 个任务正确完成时间的配对差；t0+900 s 未全完成则报各组正确数与剩余任务 |
| neighbor_pause_count | t0 之后 48 个邻居中收到暂停、失去存储或因整卷操作中断的数量；共同第 10 轮屏障不计迁移导致的邻居暂停 |
| neighbor_extra_completion_ms | 48 个邻居完成各自 120 轮的配对时长差，给全部原始点；另报相同 600 s 窗口内正确操作数差、每轮按时完成率及工具延迟变化 |
| redo_operations / redo_bytes | 恢复后重做操作数/字节，已确认与未确认分开；目标额外读入不算应用重做 |
| stale_write_accepted / stale_commit_accepted | 目标取得当前写权限后旧身份的文件写入/当前状态提交是否成功；给尝试、拒绝数及目标读回 |
| confirmed_state_loss | 源故障后相对共同确认记录缺失/改变的文件、会话、工具结果数量；任何已确认状态丢失均为正确性失败 |

目标明细按“源工作区—目标—迁移方式”保存，同可用区/跨可用区分表；不能混算原卷换挂与快照新卷，也不把 BrewFS 未验证网络或权限范围当成功。逐源报告比例；汇总用成功配对总数/候选配对总数，不无说明平均百分比。

整卷移动若恢复了选中任务，可以在 migration_success_ratio 中计成功，但需中断邻居的目标不进入“单独迁移”的 target_verified_ratio 分子。该规则对 B0–B4 相同。旧本地文件可写与旧身份可提交当前状态分开；仅协调器停止派任务不等于存储已拒绝旧写。

分项事件为 tools_quiesced、checkpoint_started/confirmed、migration_started、source_release_started/released、target_restore_started、target_storage_ready、target_session_ready、target_first_useful_result。主线第 10 轮 checkpoint 在 t0 前已确认，t0 后复用同一版本；其成本保留在全任务计时，不重复算作迁移内任务写入。B4 加记卸载、detach、attach、设备出现和挂载的 API/等待；跨区增加 snapshot 与新卷阶段。重叠阶段不能简单求和。故障另记 failure_injected → failure_detected → target_first_useful_result，报告完整故障恢复时间，不遗漏检测。

迁移流量分此次新增保存/传输、已有远端状态、目标首次读取、控制 API 四项；ESSD 配置容量不是传输量。正常迁移不强制额外历史快照；要求精确旧版本回滚时，对所有方案施加相同保存合同并计前置成本。

## 6. T6 与资源计量

T6 每组同时给工具结果与机制量：Packed 为工具时间及 lower 索引请求/字节；私有修改为保存时间与新增对象/字节；B/q 为工具时间与设备实际读量。总 Redis 命令不等于 lower 查询，FUSE 短读不等于 S3 短 Range。

| 层 | 必采字段 |
|---|---|
| 文件请求 | method、errno、请求/返回 bytes、offset、稳定路径/内容 ID；不存在与不支持分开 |
| 元数据/数据路径 | 共享基线、私有修改、控制记录分类；lower GET/bytes、缓存、mapping/删除记录 |
| 对象请求 | HEAD/GET/PUT、Range、响应 bytes、对象长度/版本、重试 |
| 存储服务 | 实际持久化 B/q/d/p、设备读写、校验/EC CPU、后台修复与回收 |
| 全部节点/云资源 | CPU 秒、总内存/缓存、网络/磁盘；ESSD 卷数、配置容量/IOPS/吞吐、实例限制与使用时长 |

S3_read_amplification = S3 response bytes / successful FUSE returned read bytes；backend_read_amplification = backend physical read bytes / S3 response bytes。按批次先加总分子分母，分母为 0 则 null，缓存/预取/重复读另列。上传量与 EC 冗余率分开。保存结束、后台尾部结束、历史保留期间及 GC 后分别计空间，既不重复共享对象，也不遗漏无引用未回收对象。

单位标明 bytes、KiB、MiB、GiB，KiB=1024 bytes。费用按实际区域、日期、配置和时长计算。不可观察的托管后端 CPU/物理 I/O 为 unobserved，不估成 0，不伪称与自建系统完全等资源。

## 7. 重复、失败与图表

独立重复单位是全新批次；同批的 1024 个工作区、数百万 I/O 不能充当独立重复。3 批试跑、5 批正式初定时，给每批原始点、中位数和范围；按任务/输入/顺序区块配对并随机交错。不用 3–5 批估计精确总体 p99；请求足够多可给该批内经验 p95/p99，但标注请求数、窗口与相关性，不冒充跨部署稳定尾部。

每个计划请求、指定任务都保留最终状态：成功、错误、拒绝、超时、不支持、未完成、未评分。成功耗时分布明确以成功为条件，旁边给全部请求的按时成功比例及失败表。失败不记 0 ms，也不在删去失败后宣称总体更快。

T1 画规模/到达率对可用数、按时完成率、队列；T2 画修改/历史数量对保存、空间、GC；T3 画消费者数量对完成时间与传输；T4 画正确任务数随时间；T5 画目标范围、暂停/整体延迟和 48 个邻居的原始影响点；T6 画工具时间与机制量。所有失败点保留在图或相邻结果表，计划不填假数据。

## 本轮新增的三个记录要求

- 未保存修改迁移从统一暂停请求开始计时，包含随后保存、等待、挂载和首次正确工具结果。记录上次保存后写入字节，以及暂停后的实际同步/上传字节，两者不能互换。0 MiB 对照不修改会话。
- A4 的分散小读与连续小读均为 1024 条、4 MiB；另外报告实际触及的分片/对象数和读取范围。不能仅凭读 4 MiB 就称为随机或离散负载。
- 结果汇总必须读取生成计划的 index.json。未收到的结果列为 not_run，未知成功数和用时留空；原始文件与审核记录的摘要不一致，不计算该批的性能提升。审核文件只记录检查结论，程序核对文件不能替代看原始结果。
