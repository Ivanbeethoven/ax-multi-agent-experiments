# 实验规划入口

1. 先读 [最终方案](FINAL-PLAN.zh.md)：五个 RQ、主组/扩展组、计时和结果规则。
2. 再读 [执行器审阅](REVIEW-20261028.md)：正式实验前必须修复的问题。
3. 开跑时查 [机器与磁盘](EXPERIMENT-CONFIG.zh.md)、[流水线说明](PIPELINE.md) 和 [部署](../infra/alicloud/deploy/README.md)。

`design.json`、`execution-grid.json`、`run-grid.*` 是保留的参数与展开表，尚需按最终方案同步；`execution_enabled=false` 保持不变。它们的静态校验通过不等于正式实验已放行。

`fixtures.json`、`trace-archives.json`、`live-tasks.json` 是输入定义；`run-record.template.json` 是记录模板。脚本在 `pipeline/` 与 `tools/`，细节备查在 `reference/`。

旧迭代方案、审计与重复仓库已从日常目录删除，追溯见 [历史资料](../archive/README.md)。不要用旧版本覆盖当前方案。
