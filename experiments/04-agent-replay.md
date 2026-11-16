# 4. 多 Agent trace 回放

状态：已确认。统一参数与 Dynamic 比较方法见 [完整方案](../docs/PLAN-REVIEW.md)。

使用 [trace-inputs.json](../configs/trace-inputs.json) 中现有八类主负载，以及现有 Rust `full-fuse-replay` 工具。

- Agent 数量：**1、8、16、64**。
- 1 个 Agent 顺序执行八类任务；其余规模按相同比例分配八类 trace。各系统在同一规模下执行相同任务集合。
- 工具调用边界之间加入固定种子的 **1～7 秒随机等待**，保留任务内部文件操作顺序。
- 主比较使用 BrewFS 工作区、JuiceFS Redis、JuiceFS Overlay。缓存与预读使用正常参数。
- **仅在 64 个 Agent 时增加一组关闭 Dynamic 的 BrewFS**，与开启组保持相同 trace、随机等待、资源和缓存设置。
- 指标：任务/小时、各类任务完成时间 P50/P95、文件操作数、实际读写量、重复读取比例、元数据请求、S3 流量及 CPU/内存占用。等待时间与文件操作执行时间分别保存。

主比较 12 组配置，Dynamic 增加 1 组，各运行一次，共 **13 次批量运行**。复用现有批量挂载、trace 边界计划和回放脚本。

单机也可以用 [多线程回放入口](../tools/full-fuse-replay/README.md#多条-trace-并发回放)，通过一份清单同时运行多条 trace。每条 trace 使用独立工作区。跨机器或需要逐 Agent 设置 cgroup 时使用原集群入口。
四台 RustFS 节点上的 16、64 Agent 测试使用 `configs/replay-placement-rustfs.json`。BrewFS 的 `truncate` 在 16 Agent 时最多等待待提交数据 30 秒，64 Agent 时为 60 秒。64 Agent 的 Dynamic 开、关使用相同上限。实际等待时间计入回放耗时；上传排空和跨机冷重挂内容检查均保持开启。
