## 当前进展与下一步

最新结果见 [g8i 三种磁盘配置、四方法完整回放摘要](disk-matrix/summary/summary.zh.md)。本次 trace 中 native 在已有工作区上的回放明显更快，overlay 的额外开销需优先定位；packed 减少了 Redis 命令计数，但回放更慢，尚不能由此证明数据库瓶颈下的收益。主图只计回放，不代表包括准备、fork、保存和回收的全生命周期成本，也不证明严格正确性。三台各一次且 CPU 型号不同，不能将差异全部归因于磁盘。

**新增代码审查限制：**配置文件的 TTL=0 不保证有效 FUSE TTL 相同。native 的旧策略可能仍取环境变量默认 1 秒，overlay 则使用挂载配置的 0；历史运行未记录有效回复 TTL，尚待核实。输入约 91% 为 getattr，因此不能把全部耗时差距归为 overlay 的虚拟化成本。调用链与验证顺序见 [issue #364](https://github.com/brewfs/brewfs/issues/364)。

新 g8i 批次的三台实例、六块盘、三个安全组和三个临时桶均已核验释放；本轮专用交换机也已删除，原有 VPC、交换机与 trace 数据保留。资源核验见 [cleanup.json](cleanup.json)。旧 `10-ai-trace` 定时任务已删除。500 GiB PL1 的[原版 overlay 补测](baseline-overlay-01/README.md)和[等量写入 B–C–C–B 对照](compose/matched-abba-01/REPORT.md)均已完整保存。

各历史图报保留生成时的验收与资源状态；当前状态以本页和 cleanup.json 为准。

等量对照每版本两次、16 个作业全部完成。四项命令墙钟均值变化依次为 −1.03%、+0.66%、−0.04%、−0.08%，直接随机写没有复现额外数秒的收尾；但 C4 顺序写的多次 close 总耗时更高，不能仅按 write 延迟宣称提速。结合已有混合读写 compose 与 17 项 CI，本轮接受的是 **C4 冻结基线上的 #354 修复及受租约保护的缓存变更**，不是普遍无性能回退的结论。源码提交为 `2457de5`；合并最新 main 后仍须重新验证，历史 WSL 挂起根因也未被本组证明。

[修复 PR #365](https://github.com/brewfs/brewfs/pull/365) 已作为草稿提交；GitHub 检出与最新 main 的合并冲突，当前未合并。它保留已测源码，后续集成需保留两侧缓存与目录版本逻辑并重新验证。

后续新批次优先采用对象存储后端 [Ivanbeethoven/rustfs 的 feat/dynamic-block-size-rebased 分支](https://github.com/Ivanbeethoven/rustfs/tree/feat/dynamic-block-size-rebased)，本次查得 HEAD 为 `a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed`，尚未用于本批测量。开跑前分别固定 BrewFS 客户端 SHA、RustFS 后端 SHA 和镜像摘要；本轮收尾仍使用原冻结镜像。比较 overlay/packed 时固定后端，dynamic block 的实际布局另设控制组。

后续沿用[既有五个 RQ 与实验设计](../../ax-multi-agent/paper-design/plan.zh.md)，先做单因素小组，再按结果扩展，不铺开全部组合：

- **RQ1 Overlay：**先剖析查找、数据库访问、缓存与并发路径的开销，单独跟进 overlay 性能瓶颈；再比较语义等价的 fork 生命周期，计入父状态准备、首个可用子分支、持久写、保存及后台收尾。native 无分支接口时使用等价物化对照，不能记为零成本 fork。
- **RQ2 Packed Metadata：**重点测试固定版本的海量小文件、共享基底和多消费者，以及对同数据库上独立可写邻居的干扰。沿用主尺度 N=32,768 / 1,048,576，分别看数据库请求、CPU、内存、邻居延迟和对象侧新增工作；计入首次固定版本、pack、上传及后续复用成本。保留小树、单消费者和频繁更新的适用边界，不预设 packed 必须更快。
- **RQ3 Object granularity：**dynamic block 继续作为独立控制维度；比较 overlay 与 packed 时固定对象粒度等条件。三种机制独立可选。
- **RQ4 Migration、RQ5 Multi-agent：**在上述机制与成本边界明确后，按既有设计验证迁移及完整任务收益；已有工作区上的文件回放优势不直接等于 Agent 全任务提速。

## 旧 e 系列批次：C4 与 JuiceFS 重测（本批资源已释放）

本批重测已完成 `pytest-full-02` 的全部 1,747,109 次操作，使用原 Rust 回放器的 performance 模式，不设总时长上限。三组读写字节一致、剩余句柄 0、回放退出 0，本批云资源已释放。[完整诊断图与简短报告](summary/summary.zh.md)保留配置、准备成本、错误、费用和验收边界；这是单次开发诊断，不能作为跨主机公平对照或严格正确性证明。

| 方法 / 版本 | 当前结果 | 数据 |
|---|---|---|
| native-overlay / C4 | 完整回放 2187.17 s，读写字节一致、句柄归零、正常卸载与挂载自然退出；1048 项 probe errno 差异 | [candidate4.json](native-overlay/candidate4.json) |
| native-overlay-pack / C4 | 完整回放 1730.24 s，读写字节一致、句柄归零、正常卸载与挂载自然退出；1048 项 probe errno 差异 | [candidate4.json](native-overlay-pack/candidate4.json) |
| JuiceFS / attempt02 | 完整回放 1853.92 s，读写字节一致、句柄归零，probe errno 差异 0；本组云资源已全部释放 | [result.json](juicefs/result.json) |

JuiceFS 首次尝试被系统自动升级重启服务打断，证据见 [interruption.json](juicefs/interruption.json)。原实例暂停自动服务重启后，使用新目录完成第二次回放；不是冷宿主页缓存实验。

旧版 [C1 完整回放](native-overlay/candidate1.json)只保留为历史，不进入最终图。在其持久化状态上重新挂载并中断在途写请求的探针也自然退出，见 [candidate1-interrupted-write.json](native-overlay/candidate1-interrupted-write.json)。这没有排除先前长生命周期进程或 WSL 状态导致的挂起。

本轮测试版本为 C4：源码包 SHA256 `d609a429dfce0943114717495a76c164598df36a4564768fc346156548ea2783`，基于 `e61ab89448c1482d11f5baca2a5ad448f5818213`。C4 把缓存清理与目录版本失效分离，17 项本地检查及 `git diff --check` 已通过，包括严格 Clippy。packed 库测试 2,497 项通过、451 项忽略；默认库测试 1,261 项通过、262 项忽略；相关二进制测试也通过。最终 release 构建通过；两个真实挂载各执行 16 次 orphan-close/目录读取回归，两个挂载进程均自然退出 0，并非每配置进行了 16 次重新挂载。

[早期两轮 compose 原始证据](compose/summary.json)保留改善与回退：其定时纯写的前置写入量不同，不能作为等量历史对照。后续等量对照补上了这一控制，没有复现直接随机写的长收尾；早期失败和中断记录仍保留，不追溯改成成功结果。图及报告已完成自动与人工 QA，[生成脚本和数据约定](reporting/README.md)可追溯全部数据。

原始归档、构建日志和资源清单保存在仓库忽略目录 `runs/aliyun-{native,packed,juicefs}-fix-20261027/`；私有配置和临时签名地址不随公开摘要提交。
