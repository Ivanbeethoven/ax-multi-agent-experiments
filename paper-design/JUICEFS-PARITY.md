# JuiceFS 全实验对照矩阵

这张表与 [TEST-PLAN.zh.md](TEST-PLAN.zh.md) 和 [run-grid.json](run-grid.json) 同步。每一行都是一个实验小节；执行 agent 不得因为 JuiceFS 缺少某个 BrewFS 开关而删除该行。无等价能力写入结果字段并保留替代测量。所有行共享同一 RustFS、Redis、payload、机器、CPU/内存上限、总缓存预算、时段、发令表和重复次数；版本在批次开始冻结。

| 实验小节 | JuiceFS 测什么（同一负载/语料） | 公平性条件 | JuiceFS 没有的能力如何处理 | 不适用指标及原因 |
|---|---|---|---|---|
| 基础能力和语义资格 | 创建、读取、覆盖、truncate、rename、unlink、sync、重开、独立 readback 和 namespace digest；使用同一 smoke payload 与父子修改顺序 | 同一 RustFS、Redis、payload、worker、缓存预算、版本 pin 和阶段计时 | 没有 BrewFS fork、Packed lower、Entity fencing 时记录确切错误；使用原生 clone/snapshot（若支持）和普通重开作为替代，不把物化复制叫 clone | BrewFS 专有 fork 继承、Packed manifest、lease/fencing 证据不适用；内容、属性、删除和恢复读仍可比 |
| 属性缓存和重复打开 | 1,000/10,000/100,000 inode 的 100,000 次 stat；4 KiB/64 KiB/1 MiB 各 200 次双重 open-read-close；记录实际 TTL、FUSE/设备字节和 p50/p95/p99 | 同一文件内容、工作集、发令表、CPU/内存、Redis/RustFS 和缓存总量 | 没有 BrewFS TTL 旋钮、FOPEN_KEEP_CACHE 时用 JuiceFS 实际 `attr-cache`/`entry-cache`/`open-cache` 和默认缓存测同一序列；KEEP_CACHE 只标 no-equivalent | KEEP_CACHE 机制归因、Packed lower/manifest 指标不适用；第二次读字节、内容校验和延迟适用 |
| 同机四配置写入成本 | ABBA、小文件、大文件、随机覆盖、写入中删除；记录应用返回、writeback、远端确认、排空、另一客户端读回 | 同一 payload、dirty 字节、缓存、RustFS/Redis、机器和时段；阶段不重复计 close | 没有 Overlay/Packed lower 时仍执行共同 POSIX 写入和 writeback 生命周期；专属 lower 列写 no-equivalent | Overlay/Packed 路径拆分不适用；应用/远端等待、请求数、资源和正确性适用 |
| Overlay 继承、分支和历史保留 | F1 父修改、1/16/64（扩展 256）子、同样树形/修改率、clone 首读/首写、状态重开和删除半数引用 | 同一 1 GiB/32,896 文件 F1、父版本、子数量、缓存和保护窗口 | 有原生 clone/snapshot 就用；没有可恢复 clone 则写 no-clone，保留普通目录复制/删除/重开作为可比替代，不进入 clone 排名 | BrewFS Overlay/whiteout/共享下层和 Packed 私有元数据不适用；首正确操作、读回、逻辑/物理字节仍适用 |
| Packed 访问和数据库干扰 | M 的 32,768/1,048,576 个 1 KiB 文件，六类 query、1/16/64 消费者、shared/disjoint、冷/索引暖/元数据暖、0/1/4 倍背景 probe | 同一查询表、缓存预算、RustFS/Redis、CPU/内存、探针 0.25r 和 2 s deadline | 使用原生目录/对象路径；记录 Redis、对象请求、客户端解码、设备字节和 probe 完成率，不因无 Packed manifest 删格 | Packed manifest、索引页/descriptor、lower/upper 分层和 Packed 专属构建指标不适用；通用延迟、probe p95、DB/对象/客户端代价适用 |
| Packed 首次构建、复用和更新偿本 | 同一版本内容的首次可用、K=1/4/16/64/256 消费、8 周期、0.1%/1%/10% 修改、末次保留 0/600 s | 同一版本表、消费查询、写入、缓存、生命周期计时和资源上限 | 用 JuiceFS 原生 snapshot/clone；不支持时记录 no-clone，仍记录普通上传、读取、保留和回收成本 | Packed 扫描/manifest/descriptor 构建字段不适用；共同的首读、首写、上传、存储和累计资源可比 |
| 对象粒度和动态布局 | 相同对象长度、4 KiB/64 KiB/1 MiB、跨边界、顺序/随机/whole/range 读，记录 GET range、客户端/服务端/设备字节和实际 JuiceFS block-size | 同一 RustFS、对象内容、请求列表、缓存、并发和机器；设备计数单列 | JuiceFS 没有 BrewFS g、Packed frame 和 RustFS B/q hint；写 no-equivalent，但执行共同 read/写/范围负载 | g、Packed frame、B/q hint 和 RustFS 持久 q 不适用；请求/字节/延迟/内容正确性适用 |
| 迁移和恢复 fencing | 同一保存点、0/1/4/8 工作区迁移、0/1/16/128 MiB pending、冷/暖目标、确认前后和协调断连屏障 | 同一源/目标资源、payload、缓存、时段、故障屏障和 20 次时序覆盖 | 测 JuiceFS 重挂、重开、首读、所有权转移和迟到写；没有 Packed 或 Entity lease/fencing 证据则 no-equivalent/unmeasured，保留错误原文 | Packed 构建、BrewFS lease/entity fencing 不适用；普通重挂时间和读回正确性适用 |
| 固定任务回放 | 七条冻结 trace、每条 8 份、worker 并发 1/4/16、相同截止和 8192 MiB cache；记录正确完成与资源 | 同一模型、工具权限、初态、payload、机器、缓存和任务分配 | 缺少 fork/Packed/迁移的任务保留原始失败原因，从专属排名剔除；共同读写路径照常比较 | BrewFS 专属 fork/迁移/打包指标不适用；正确完成、任务延迟、资源和 readback 适用 |
| 固定后端规模扩展 | worker 4/16（64/128 仅扩展）和同七条 trace、相同 slots、截止和资源采样 | 同一固定后端、任务表、缓存预算、机器和到达表；超过资源上限作废 | JuiceFS 完整执行相同 worker 组；无 BrewFS 专属状态的列写 no-equivalent | Overlay/Packed 专属收益不适用；共同完成率、吞吐、尾延迟和服务瓶颈适用 |
| 真实 Agent 续接、fork、迁移 | 相同两个任务、第三个完整工具结果触发、四子 fork/继续、模型、权限、初态、评分器、剩余预算 | 同一 live-tasks manifest、任务截止、worker、缓存、RustFS/Redis 和观察窗口 | 只在共同文件读写路径运行；没有 BrewFS fork/Packed/迁移语义写 no-equivalent，保留真实工具失败和评分 | fork 隔离、Packed 构建和 fencing 证据不适用；正确完成、评分、文件状态和后续工具调用适用 |

判定规则：JuiceFS 的 `unmeasured(no-equivalent)` 不是零；替代测量仍必须写入格子产物。客户端 PUT/GET、服务端 pread、页缓存和设备字节分别记账，不互相代替。

