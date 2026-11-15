# C4：fork、私有增量与结果合并

**RQ：共享父版本和私有增量，能降低多少工作区派生、局部修改及结果合并的时间和存储开销？**

配置：[experiment.json](experiment.json)。接口与版本：[adapters.json](adapters.json)。执行说明：[HANDOFF-C4](../../docs/RUNNING.md)。C4 独立保存结果，与 C1～C3 错开性能测量时间。

## 对照与实际接口

| 系统 | 派生 | 合并 |
|---|---|---|
| BrewFS Native Overlay | 从同一不可变 Native 版本 fork | `workspace merge CHILD TARGET --operation UUID`，元数据事务与数据对象引用复用 |
| 原生 JuiceFS | 普通 clone | `merge_posix.py BASE CHILD TARGET`，完整目录枚举、直接内容比较、复制改动 |
| JuiceFS Overlay | lazy fork | 同一个 POSIX 三方工具 |

BrewFS 已检查实现为 `aacfc6db`，AX 接入为 `0bf2d0d7`；本目录已接入 `bench.cluster.workspace_merge`。当前格式为 `workspace`：共同 base 是 depth=1 的不可变 Native 层，child 和 target 是 depth=2 的可写层，使用相同对象桶，调用 merge 时两者均已卸载。源代码与六机部署使用同一个记录明确的构建版本。

原功能验证使用 Redis/local-fs。四节点 RustFS 的准备首先验证共享桶：默认 S3 fork 的独立桶配置需要改为通过 catalog API 创建共享桶的工作区，保留全卷的 inode/slice 分配及对象引用管理。父版本必须含真实文件与数据对象；文件数、内容与层关系在导入后检查。

## 输入和主实验

普通文件为 4 KiB，每目录 1,000 个文件，内容由固定种子和文件编号生成。数据、操作序列、主机安排在三个系统间相同。父数据准备单独计时，每次重复使用新的 child、target 和操作 ID。

| 条件族 | 输入与变量 | 固定项 | 首要指标 |
|---|---|---|---|
| `fork`、`fork-scale` | 父目录 1 万/10 万文件；1/16 个子工作区；10 万文件另测 64 个 | 父版本已可读，分散六机 | fork/clone、挂载、首读时间；新增记录、元数据字节 |
| `same-host` | 10 万文件，16 个子工作区在 A366 | 与分散条件相同数据与缓存总预算 | 冷读、重复读、重复下载、主机缓存占用 |
| `partial-update` | 8 个 64 MiB 文件，16 个子工作区，每文件覆盖 4 KiB/1 MiB | 相同偏移和写入内容 | 逻辑写入、上传、物理写入量及上传完成时间 |
| `merge-size` | 父目录 1 万/10 万文件 | child 100 项修改，target 未修改 | 父目录规模是否影响合并；实际扫描记录与时间 |
| `merge-dirty` | child 100/1,000/10,000 项修改 | 父目录 10 万，target 未修改 | 合并随子增量增长的时间、请求与记录数；100 项复用 merge-size |
| `merge-parent-progress`、`merge-target-delta` | target 100/1,000/10,000 项独立修改 | 父目录 10 万，child 100 项修改 | 父侧增量扫描、冲突检查 CPU、合并时间 |
| `merge-fan-in` | 4/16/64 个 child，各 100 项互不重叠修改 | 父目录 10 万，按 child 编号顺序合入同一 target | 每次及累计合并时间、target 记录增长、最终可读时间 |
| `merge-metadata-only` | 100 次 chmod | 父目录 10 万，单 child | 纯元数据合并时间与元数据请求 |
| `merge-large-file` | 8 个 64 MiB 文件，各覆盖 4 KiB/1 MiB 后合并 | 单 child，target 未修改 | 合并阶段对象 GET/PUT/COPY 字节、引用复用成本 |
| `merge-empty` | child 与 target 均未修改 | 父目录 10 万 | 命令启动、事务与回执的固定成本 |

混合修改中，50% 覆盖文件、20% 新增、10% 删除、10% 改名、10% chmod，源文件互不重叠；K 表示逻辑修改次数。target 的独立修改采用另外一组文件，禁止与 child 的源、目标路径重叠。多子归并的各子目录在 base 中预先创建。原始记录另列实际 dentry/inode/extent 数，K 不代替元数据记录数。

已完成的每个 child 在归并前固定状态；归并后 target 不重新建立版本，继续接收下一个 child。这样能测到 target 私有记录累积的真实成本。达到扫描或事务容量限制时，保存当时的修改次数、各类记录数、内存和错误输出，作为容量结果呈现。

## 计时与指标

保存以下事件的单调时间和 UTC：停止新写入、子任务上传完成、卸载完成、合并开始、合并返回、重新挂载可用、首次读回、完整检查结束。

- **业务停顿**：停止新写入到 target 首次成功读取合并结果。包含各系统实际需要的排空、卸载、合并及挂载；JuiceFS 在停写后可保留工具所需挂载。
- **合并命令时间**：Native 包含差异读取、冲突检查、事务提交与元数据 flush；POSIX 包含枚举、内容比较和应用修改。CLI 结束后仍记录剩余上传及持久完成时间。
- **准备与完整任务时间**：父版本准备、子任务写入和上传分别记录，再与合并相加，防止把成本移到计时区间之外。
- **冷读检查**：统一使用新进程、新缓存目录读取合并改动与未修改样本，使用退出进程释放的槽位。全部文件直接内容检查放在该首次读取之后，并单列时间。

Native 的 `scanned_records` 只统计 child 和 target 的私有 delta 返回行，**不含 base 点查询及后续索引/压缩读取**；`applied_records` 不含派生索引、引用归属和回执。另采集 Redis 命令/Lua、读取/写入字节和进程 CPU。POSIX 的 `entries_scanned` 是目录项数；两者分列，不直接相除计算加速比。

POSIX 已提供 `scan_seconds/plan_seconds/apply_seconds`、`comparison_bytes_read/payload_bytes_copied`。当前 Native CLI 提供总结果和记录计数，内部阶段时间需增加计时后记录；尚未提供时保存 `null`，不通过总时间猜分段值。S3 字节分别统计子任务写入阶段、纯合并阶段和读回阶段。

## 消融与方法比较

| 技术 | 参照 | 消融 | 固定输入 |
|---|---|---|---|
| 共享名字空间 | 共享目录记录 | 派生时复制目录记录，数据引用相同 | 10 万文件、16 个 child |
| 范围增量 | 只改写对应范围 | 复制完整文件后覆盖相同内容 | 8 个 64 MiB 文件、4 KiB 修改、16 个 child |
| 行缓存 | 64 MiB | 0 | 10 万文件、16 个 child |
| 同机只读缓存 | 每挂载独立 | 共享不可变元数据和数据块 | A366 上 16 个 child，总缓存及并发上限相同 |
| 差异枚举 | 私有 delta 扫描 | 完整树枚举后产生相同合并输入 | 10 万文件、100 项修改 |
| 数据引用复用 | 直接引用现有对象 | 同一差异发现结果，重写修改文件后由同一事务提交 | 同上 |

后两项做 2×2 四组，具体配对见 [ABLATIONS](../../docs/ABLATIONS.md)。当前 merge CLI 的差异枚举和对象重写开关尚需接入：完整枚举必须替换发现阶段，不能只额外遍历一次；数据重写必须保持冲突规则和提交方式相同。

另加 `merge-posix-brewfs`：在 BrewFS 挂载上运行同一 POSIX 工具，与原生 merge 比较整个方法的成本。它同时改变差异查找与数据应用方式，作为方法比较单列。JuiceFS 对照不使用负载生成器提前提供的修改清单。

## 正确性与资源

公共检查包含新增、覆盖、删除、改名、权限、目录、符号链接、稀疏文件、双方独立修改和明确冲突。冲突用例令双方写入不同内容；预检失败后逐项确认 target 未改变。硬链接、xattr、ACL 属于 Native 专项，POSIX 工具支持的公共字段为类型、模式、普通文件内容和符号链接目标。

Native 专项还包括活动挂载拒绝、不同 base/桶拒绝、丢失响应后使用同一 operation 文件重试、重复提交、discard child 并回收后 target 冷重挂可读。不同重复使用新的操作 ID；重试时间单列。共享桶只在整组工作区与读取者结束后清理，child 回收不调用整桶删除。Native 的多记录原子事务与 POSIX apply 中途错误的恢复能力分别记录。

默认共 **30 个条件：22 个主条件、7 个消融、1 个方法比较；三次重复为 228 次系统运行**。100 万文件合并是独立扩展项。最多 64 个 child 加 base/target，共 66 个挂载、264 GiB 客户端内存上限；沿用每挂载 4 GiB 内存和 2 GiB 磁盘缓存。

64 个 child 的分配为 A366/A355/A672/A601/A635/A368：16/11/6/13/13/5；base 和 target 放 A366。主机预算包含已有 Redis/RustFS。同一后端一次一个计时条件，C4 与 C1～C3 使用分开的测量时段。

```powershell
python -m bench.cluster.plan --group C4 --out runs/c4-plan.json
```

结果位置：本地 `results/cluster-c4/run-NNN/`，服务器 `/srv/ax-cluster/runs/c4/run-NNN/`。原始数据结构与每步命令见 [HANDOFF-C4](../../docs/RUNNING.md)。
