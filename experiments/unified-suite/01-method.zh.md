# 方法、配对与成本

## 固定设计

| 主线 | pilot | main | 主要观测 |
|---|---|---|---|
| C1 | 5 种模式，1 job，每 job 64 MiB | 5 种模式，8 jobs，每 job 4 GiB；顺序 1 MiB、随机 4 KiB | 全体完成 wall、IOPS、带宽、原始延迟分布、fsync、后续排空 |
| C2 | pytest-full-02，N=1 | 四份完整 trace，N=1/4/8 | 每任务/全体完成时间、完成率、每层读写放大 |
| C3 | 1M×4 KiB，1 个新工作区，热集 1024 文件 | 1M×4 KiB，每单元 4 个新工作区；热集 1024/16384 文件 | 8 个读写阶段、创建、排空、退出、字节正确性 |
| C4 fork | 1 child | 1/8/32/64 children | 批量创建、每 child 完成、首次可用、隔离、物理增长 |
| C4 handoff | 1 Agent，移动 1 个 | N=1/8/32，移动 max(1,N/2) | stay/move 配对、首个新工具完成、任务完成、邻居吞吐 |

每个 Agent 默认独立 2 CPU、8 GiB、swap=0；主机为 OS/服务/观测留空间。同一 worker 的线程数不等于 Agent 数：C1 的 8 个 fio jobs 在一个 client 内，C3 的 8 个读写线程也是如此。不能把 `agent_slots=64` 模板值当成已提供 64 份资源。

默认同步上传语义、每次变更 fsync、强排空后普通卸载和真实进程退出；clean SSD cache=0。客户端 payload 预算上限 512 MiB，实际命中率、有效预算、元数据缓存及 kernel page cache 分开记录。entity-native 当前没有 packed payload cache，使用 0；默认系统路径比较保留这个差异，纯缓存/格式消融按专门条件对齐。

## 配对单位与并行

一个 block 包含同一条件、同一重复轮次的全部 arms，作为**跨集群结果分组**。默认 `baseline-parallel` 按product/arm显式映射集群：BrewFS在pool-a、JuiceFS在pool-b并行运行；内部native/packed/消融可再拆池。各池独立领取单个cell，较快的池可先进入后续条件，不等另一池关闭整组。不同集群不要求同一时刻启动；需要同一时间窗口时另冻结窗口策略，不能从同轮ID推断同步屏障。

每个池的 CPU/内存/盘型/IOPS/带宽/服务拓扑和 metadata backend 必须匹配。跨池条件增加 `cross-pool-hardware-equivalence` 资格，引用各池实际inventory和校准证据；同名hardware_class不足以证明机器等价。固定方法→集群映射保留可能的集群差异，报告标明。需要轮换时显式给出相反顺序的pool列表，按轮次轮换并保留每池准备成本；不自动迁移base。详见 [配置与并行例子](12-parallel-baselines.zh.md)。

`resource_ids` 列出实际共享主机、盘、RustFS/TiKV/Redis endpoint。相同服务即使有不同 DNS/SSH 别名也用同一 ID；冲突cell串行。真正同时测量的集群须有独立服务和盘。跨云不自动视为匹配硬件。元数据默认 TiKV，可在独立配置选择 Redis，但同一配对保持一致。

main 的三个 block 是**操作重复**，不是三次独立部署。重复部署必须新 campaign、重新记录导入/准备。多arm在同池队列中的位置做循环轮换；跨池完成先后不改变配对身份。三轮无法完全平衡五个arm的位置，保留实际顺序，在报告说明。不要把4个workspace或数万次IO当成独立实验重复。历史无placement配置保持原同池整组顺序模式，冻结plan不重写；新配置如需此模式须显式选择paired-pool。

比较只用完整、无中断、通过校验和观测的 block。失败、慢值、中断恢复数据逐项保留；不足三轮就报告实际完整轮数。不要为了补齐三轮重放已开始单元。吞吐按总字节/共同 wall 计算；不要平均 p99 得到总体 p99，原始操作按实际分组计算。

## 缓存与冷热

冷启动指新进程、空私有应用缓存和已声明的 kernel cache 条件；不能由一个参数推断全链路冷。热集按事先冻结的规则预热两遍，保留预热时长及字节。C3 lower hot-read 和 upper mixed 分开，防止把读完 lower 后首次 COW 叫成热写。4 MiB / 64 MiB 是逻辑热集，packed 帧与对象大小会放大实际驻留，不能仅据逻辑容量声称全部命中。

冷读、超过有效缓存的工作集、同步写慢项都保留。若想增加大于 512 MiB 的热集、异步写、think time、预取开关，使用独立预注册配置/版本，不能看到结果后修改本轮。

## 计时账本

| 记录 | 计入位置 |
|---|---|
| 源文件生成、下载、导入、producer 临时盘、首次完整 base 校验 | 每个实际 pool/dataset/representation/layout 的 asset，一次记账 |
| 新工作区、挂载与 ready、预热、读写、COW/fsync | 每个 cell / workspace 的原始阶段 |
| 上传债务强排空、普通卸载、真实退出、冷读回/隔离校验 | 独立阶段；不能只给前台读写数字 |
| 维护、中断、人工诊断、等待、接管、采集、归档下载与校验 | campaign 时间线；恢复样本单列 |
| 全部实例、盘、网络与账单调整 | 创建至实际释放；共享资源不按每个 arm 重复相加 |

报告同时列操作性能、验证开销和全程成本。并发 wall 区间不能相加称全程 wall；asset 的一次导入不能在每个 workspace 重复收费，也不能从总成本删掉。完整 base 扫描由本协议移到每个不可变 asset 一次，性能段保留逐读字节验证；每个 child 仍必须验证 touched 数据、父/兄弟隔离和远端持久化。旧百万文件数据维持原校验方法和真实耗时。
