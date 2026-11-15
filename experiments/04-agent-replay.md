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
