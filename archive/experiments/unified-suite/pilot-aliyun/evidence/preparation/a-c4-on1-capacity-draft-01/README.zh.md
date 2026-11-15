# A C4 On1：真实来源容量草案，尚未准入

原 Off1 已完成完整 100k 父/子冷验、归档和退出；原父已回收 exit 0、遇到空 `trial-2/` 的 EEXIST，并完成三角色释放。此次准备以这些真实终态为前提，续接原 **On1（step 2）**；仍保留 Off1、On1、On2、Off2、Off3、On3 的原顺序、`touched-cold-v1` 和全部 code-19 工具 SHA。没有部署、运行测量或执行清理。

**容量尚不成立。** 原 `off1_observer_timing()` 从完成回执、cohort、drain 和完整冷验重算最短观察窗口为 **13,372 秒**。即使只用较低的 A 正式 C3 原始 series（80 样本、385.74 秒），该窗口对象观察原文下限仍为 **20,594,522,588 B**。00:03:55 的对象盘 0 实际可用 **20,345,561,088 B**；扣除 20% 保留量 **12,014,120,960 B** 和原单轮数据增量 **1,249,935,360 B**，尚缺 **13,513,017,820 B**，此数还未计归档。

00:08:58 独立清理 dry-run 实测四棵旧 C1 原文 allocated 合计 **8,902,164,480 B**，盘 0 可用 **20,285,997,056 B**，`applied=false`。即使随后完整回收该 allocated 量，静态预计可用 29,188,161,536 B；以上最低窗口仍缺 **4,670,417,372 B**，未计归档。见 [dry-run 后空间推算](dry-run-space-followup.json)。实际回收量和新可用空间必须来自真实 apply 及其后快照。

本草案采用 **18,000 秒**整轮窗口（比实测下界多 4,628 秒），每角色取 A 已闭合正式 C3 和 C1 混合读写两组 series 的最大原生块分配速率/峰值，再给原文及归档预算各加 25%。这两组仅决定速率，未用其短时长替代 A Off1 时长。对象原文预算因此为 **48,805,345,240 B**。`closed-observer-direct` 只省 worker 第二份 observer staging，仍计完整原文、tar、其它暂存和协调者解包。

以下是清理前的只读观测及本草案预算，单位为十进制 GB。它不是清理后的最终快照。

| 实际盘 | 当前可用 | 数据增长 | 观察原文 | 归档峰值 | 扣预算及 20% 保留后余量 |
| --- | ---: | ---: | ---: | ---: | ---: |
| client | 82.961 | 0.022 | 1.653 | 0.165 | 61.109 |
| metadata | 58.734 | 11.270 | 0.918 | 0.115 | 34.416 |
| objects 0 | 20.346 | 1.562 | 48.805 | 1.084 | **−43.120** |
| objects 1 | 40.554 | 1.562 | 0 | 0 | 26.978 |
| objects 2 | 40.555 | 1.562 | 0 | 0 | 26.978 |
| objects 3 | 40.554 | 1.562 | 0 | 0 | 26.978 |
| coordinator | 85.191 | 0.067 | 51.377 | 1.365 | 12.371 |

元数据预算保留原 **7,272,353,792 B** Redis rewrite headroom，再加数据增长及余量。AOF 增长预算 **1,221,567,927 B** 已计入元数据盘；Redis 内存另留 **110,136,070 B** 增长。每盘 inode 也有逐行预算，本次只有对象盘 0 的字节预算不满足条件。

可审查材料：

- [容量草案](capacity.draft.json)：`passed=false`、`reviewed=false`，`cleanup`、`snapshot`、`placement`、`at` 留空；已填真实历史 pins、逐盘/inode/Redis/coordinator 预算。
- [On1 续跑请求草案](continuation.request.draft.json)：`reviewed=false`、`capacity=null`，固定原 batch 请求、父 invocation、step 2、空续跑 prefix 和归档 overlay。
- [实测速率及归档推导](measured-basis-calculation.json)、[清理前只读空间](advisory-before-cleanup.json)、[逐行空间核算及最低窗口比较](capacity-advisory-evaluation.json)、[原输入及 pins](original-inputs.observed.json)。
- [部署方案](deployment-plan.json) 和 `code-overlay/`：只准备本地五个新增/更新入口，计划在协调者新代码树中部署；原 code-19 不动，worker 不部署代码。三个 A worker 的七个 `CODE_FILES` 已分别读取实际 SHA，三角色完全一致，见 [worker-code.actual.json](worker-code.actual.json)。
- [最终收尾步骤](FINALIZE.zh.md)：由主协调者在真实清理及容量方案成立后完成。

既有 C4 续跑、关闭观察归档和正式轮 overlay 的 **22 项离线测试通过**。未运行 `--validate-only`，因为最终清理与新快照尚未发生，且当前空间门槛已明确不满足。旧回执及总进度 README 均保持原样。
