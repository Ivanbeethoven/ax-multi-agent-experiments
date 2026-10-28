# 实验规划入口

1. 先读 [最终方案](FINAL-PLAN.zh.md)：五个 RQ、主组/扩展组、计时和结果规则。
2. 再读 [阿里云执行入口](../infra/alicloud/RUN.zh.md)：已接脚本、验证范围与剩余接口。
3. 开跑时查 [机器与磁盘](EXPERIMENT-CONFIG.zh.md)、[流水线说明](PIPELINE.md) 和 [部署](../infra/alicloud/deploy/README.md)。

`harness/final_plan.py` 生成当前五个 RQ 的任务表。`design.json`、`execution-grid.json`、`run-grid.*` 仅保留历史追溯；`execution_enabled=false` 保持不变。静态校验通过不等于正式实验已放行。

`fixtures.json`、`trace-archives.json`、`live-tasks.json` 是输入定义；`run-record.template.json` 是记录模板。脚本在 `pipeline/` 与 `tools/`，细节备查在 `reference/`。

旧迭代方案、审计与重复仓库已从日常目录删除，追溯见 [历史资料](../archive/README.md)。不要用旧版本覆盖当前方案。
