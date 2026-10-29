# 资格验证与历史格式兼容

这些代码仍有实际调用者，不能当作无用代码删除；它们也不是当前四集群的一键运行入口。当前执行步骤见[runbook](../../experiments/retest-2026-10-29/runbook/RUN.zh.md)。

| 文件 | 保留原因 | 边界 |
|---|---|---|
| [aliyun_suite.py](aliyun_suite.py) | 挂载参数、二进制/后端身份、真实回放与重新挂载校验；ML 资格工具依赖其公共函数 | 含旧整套 CLI；现行实验逐步执行，不直接继承旧默认值 |
| [ml_readonly_matrix.py](ml_readonly_matrix.py)、[ml_readonly_end_to_end.py](ml_readonly_end_to_end.py) | [本地 P1/P2 资格工具](../../experiments/retest-2026-10-29/scripts/local-ml-qualification.py) 的实际依赖 | 小规模路径资格，不能替代海量 ML-agent 任务 |
| [run_trace_matrix.py](run_trace_matrix.py)、[示例](trace-matrix.example.json) | 历史回放配置、归档与脱敏校验仍有回归测试 | 不自动恢复旧三机任务；历史运行须使用当时冻结提交 |
| [summarize_suite.py](summarize_suite.py) | 旧 JSON 结果格式的分组、失败过滤与统计 | 不混合不同控制条件 |
| [csv/validate.py](csv/validate.py)、[csv/summarize.py](csv/summarize.py) | 历史 11 列 CSV 的数值、状态和汇总检查 | 显式传入 CSV；旧 fixtures 在历史归档中，不作为新成绩 |

相应回归测试仍在 [tests](../../tests/README.md)。旧通用 adapter/campaign/worker 调度系统及其专属测试已整体归档，当前代码不再依赖它们。
