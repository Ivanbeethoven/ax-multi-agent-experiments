## 当前进展与下一步

最新结果见已推送提交 `8a0530d` 的 [g8i 三种磁盘配置、四方法完整回放摘要](disk-matrix/summary/summary.zh.md)。本次 trace 中 native 在已有工作区上的回放明显更快，overlay 的额外开销需优先定位；packed 减少了 Redis 命令计数，但回放更慢，尚不能由此证明数据库瓶颈下的收益。主图只计回放，不代表包括准备、fork、保存和回收的全生命周期成本，也不证明严格正确性；C4 性能验收仍待完成。三台各一次且 CPU 型号不同，不能将差异全部归因于磁盘。

**新增代码审查限制：**配置文件的 TTL=0 不保证有效 FUSE TTL 相同。native 的旧策略可能仍取环境变量默认 1 秒，overlay 则使用挂载配置的 0；历史运行未记录有效回复 TTL，尚待核实。输入约 91% 为 getattr，因此不能把全部耗时差距归为 overlay 的虚拟化成本。调用链与验证顺序见 [issue #364](https://github.com/brewfs/brewfs/issues/364)。

新 g8i 批次中，100 GiB PL0、500 GiB PL0 已核验释放；500 GiB PL1 上的[原版 overlay 补测](baseline-overlay-01/README.md)也已完整结束，实例为剩余等量负载的 compose 对照保留，尚未释放。下文“已释放”仅指旧 e 系列批次。

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

当前候选版本为 C4：源码包 SHA256 `d609a429dfce0943114717495a76c164598df36a4564768fc346156548ea2783`，基于 `e61ab89448c1482d11f5baca2a5ad448f5818213`。C4 把缓存清理与目录版本失效分离，17 项本地检查及 `git diff --check` 已通过，包括严格 Clippy。packed 库测试 2,497 项通过、451 项忽略；默认库测试 1,261 项通过、262 项忽略；相关二进制测试也通过。最终 release 构建与两配置各 16 次真实挂载短回归通过，两个挂载进程均自然退出 0。

**C4 尚未通过性能验收。** [本地两轮 compose 原始证据](compose/summary.json)保留改善与回退：纯写对顺序敏感，直接随机写额外墙钟开销待归因。命令成功不等于无性能回退；本次发布的是带限制的实验报告，不是已接受的 Rust 优化。图及报告已完成自动与人工 QA，[生成脚本和数据约定](reporting/README.md)可追溯全部数据。

原始归档、构建日志和资源清单保存在仓库忽略目录 `runs/aliyun-{native,packed,juicefs}-fix-20261027/`；私有配置和临时签名地址不随公开摘要提交。
