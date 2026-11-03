# 统一 C1–C4 实验：执行入口

协议 `unified-c1-c4-v1`。外部 baseline 只保留 **JuiceFS**；BrewFS native、workspace native、entity native 和 packed 是内部路径对照。方法、准备、运行、校验分开：AI 维护环境并调用单步工具，每一步都有固定输入和保留结果。

| 主线 | 要回答的问题 | 执行文档 |
|---|---|---|
| C1 普通 fio | 固定工作量的顺序/随机读写和同步写代价 | [fio](06-fio.zh.md) |
| C2 AI trace | 完整真实任务文件访问及多 Agent 并发 | [trace](07-trace.zh.md) |
| C3 百万小文件 | 创建、冷读、热 lower 读、首次 COW、热 upper 读写 | [百万文件](08-smallfiles.zh.md) |
| C4 fork / 流转 | 批量派生、隔离、首次可用、原生会话跨机继续 | [fork / 流转](09-fork-handoff.zh.md) |

先读 [方法与统计单位](01-method.zh.md) 和 [baseline / 三技术点消融](02-ablations.zh.md)，再按顺序完成：

1. [环境与构建](03-environment.zh.md)：机器、磁盘、维护窗口、版本、SSH 和隔离。
2. [输入与一次导入](04-inputs.zh.md)：实际 trace / 固定小文件输入、不可变 base 和完整验证。
3. [baseline 拆分到集群](12-parallel-baselines.zh.md) 与 [调度与单步运行](05-execution.zh.md)：显式映射、冻结计划、草稿、资格、逐cell领取、运行、恢复。
4. 依对应 C1–C4 文档执行，在 [证据与收尾](10-evidence.zh.md) 核验并汇总。
5. 把 [AI 交接单](11-ai-handoff.zh.md) 交给下一个维护者，保留原阶段身份。

默认 `pilot` 验证接入，`main` 做三个操作配对区组；**pilot 的 C3 仍是百万文件**。本地脚本测试另用小样本，不能写成实验结果。正式部署/性能测试尚需实际资源、冻结二进制及资格证据；模板只生成计划，不创建资源。

默认把 BrewFS 方法放在 pool-a、JuiceFS 放在 pool-b，两个集群并行推进各自队列。native、packed 或消融臂可用 `arm_pools` 分到更多集群；`product_pools` 是未单列方法的回退映射。一个配对 block 是结果分组，执行锁按cell/pool与实际共享资源生效。草稿目录的 `queues.json` 列出各集群实际队列；[初始 prompt](START-HERE.prompt.zh.md) 可交给执行 AI。

```bash
# 在仓库根目录；RUN 是已有本轮控制目录的绝对路径，放在独立本地盘。
python3 -m bench.suite template --profile pilot --study systems --output "$RUN/config.json"
python3 bench/scale_plan.py --suite-config "$RUN/config.json" --output "$RUN/plan.json"
python3 -m bench.suite_support drafts --plan "$RUN/plan.json" --output "$RUN/drafts"
```

编辑 config 中实际资源、输入和构建摘要后重新生成最终 plan，随后才初始化状态库；规划草稿不能直接领取执行。消融使用独立 campaign/config，`--study ablations`。

本次 C1–C4 是**新编号**。旧 [2026-10-29 方案](../retest-2026-10-29/PLAN.zh.md) 中的 C1 trace、C2 只读语料、C3 直接对象布局、C4 fork/流转保留原意。旧语料与直接对象脚本仍用于专项资格/辅助诊断，不混入本协议主表。资源池用 `pool-a`、`pool-b` 命名，与 C1–C4 无关。

代码入口：[suite](../../bench/suite.py)、[方法目录](../../bench/suite_catalog.py)、[计划](../../bench/suite_plan.py)、[状态](../../bench/suite_state.py)、[worker](../../bench/runtime/workloads.py)、[工作量核对](../../bench/suite_validate.py)、[报告](../../bench/suite_report.py)。实际 SSH/cohort/观测继续使用 [既有 runtime](../../bench/runtime/README.md)，没有后台包办整轮的第二套控制器。
