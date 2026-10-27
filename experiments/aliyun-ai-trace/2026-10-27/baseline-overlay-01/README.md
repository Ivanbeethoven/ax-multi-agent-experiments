原版 native-overlay 已在保留的同一台 500 GiB PL1 节点上完成全部 trace，无整体超时。**本组是单次开发诊断，尚不构成性能验收。**

| 版本 | 完整回放秒数 | ops | 剩余句柄 | 回放／自然挂载退出 |
|---|---:|---:|---:|---|
| 原版 8c9db597 | 1621.413 | 1,747,109 | 0 | 0／0 |
| C4（同节点先前结果） | 561.169 | 1,747,109 | 0 | 0／0 |

原版单次耗时为 C4 的 **2.89 倍**。两者读取 281,774,624 B、写入 194,706,661 B，expected errors 均为 45,525、probe errno differences 均为 1,048；`valid=false`，完整工作量不等于严格正确性。

同一 ecs.g8i.2xlarge／Xeon 8575C、500 GiB PL1 XFS 数据盘、同 trace 与 replayer，使用独立新后端／目录。C4 先运行，原版在矩阵之后运行；n=1，未清宿主页缓存，也没有顺序交叉复测，不能把该耗时比视为排除了缓存、顺序和观察器影响的修复因果收益。准备与卸载不计入回放秒数；原先超时记录仍保留。

[结构化结果](summary.json) · [原版原始报告](original/baseline-overlay-replay.json) · [同节点 C4 报告](c4/candidate-overlay-replay.json) · [来源与 SHA](manifest.json) · [独立三节点矩阵](../disk-matrix/summary/summary.zh.md)

两组 prepare／final mount 均自然退出 0，后台清理无错误。三份最终归档均保留并通过哈希及凭据扫描；本目录仅保留证明所需的小份原始证据。当前实例为剩余等量前置 B-C-C-B compose 验收评估继续保留，**尚未释放**；未因此另建实例。
