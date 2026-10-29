# BrewFS 实验仓库

先看 **[本轮四集群实验总览](experiments/retest-2026-10-29/PLAN.zh.md)**：每组场景、baseline、机器数、指标与阿里云/自备服务器差异。当前是准备阶段，64 Agent 为主目标、128 为扩展；基础资格的 9/3 台模板另列，不代表规模实验。

| 要做什么 | 从这里进入 |
|---|---|
| 审阅实验设置、配置、方法和指标 | [基础方案](paper-design/FINAL-PLAN.zh.md) · [规模方案](experiments/retest-2026-10-29/SCALE.zh.md) |
| 给四位实验 Agent 分工 | [C1–C4 与初始 prompt](experiments/retest-2026-10-29/runbook/CLUSTERS.zh.md) |
| 交给新的执行方启动 C1 | [独立环境交接 prompt](experiments/retest-2026-10-29/runbook/agents/C1.prompt.md) · [5 节点首次试跑](experiments/retest-2026-10-29/runbook/agents/C1-pilot.prompt.md)；从 GitHub 获取代码，在执行方环境重新核资源/输入/版本 |
| 逐步操作与检查 | [基础操作](experiments/retest-2026-10-29/runbook/RUN.zh.md) · [规模操作](experiments/retest-2026-10-29/runbook/SCALE-RUN.zh.md) |
| 找具体测量脚本 | [bench](bench/README.md) |
| 准备服务器和 RustFS | [infra](infra/README.md) |
| 查看 trace 来源、采集与回放 | [输入来源](paper-design/TRACE-WORKLOADS.zh.md) · [采集/回放脚本](scripts/README.md) |
| 看已完成的验证与历史结果 | [本轮证据](experiments/retest-2026-10-29/README.zh.md) · [历史结果](experiments/README.md) |
| 验证代码或查找旧实现 | [测试](tests/README.md) · [历史归档](archive/README.md) |

## 目录职责

```text
experiments/retest-2026-10-29/   本轮总览、规模方案、runbook、Agent prompts、证据
paper-design/                  基础方法、参数索引、trace 来源（不再放旧网格和运行器）
bench/                         当前单步测量、输入核验、计划生成
  reference/                   仍被资格验证/历史结果校验使用的参考实现
infra/                         阿里云 / 自备服务器准备；共用 RustFS 部署工具
scripts/                       Trace 采集、打包、回放；仓库检查
tools/                         Rust 测量程序与回放器
tests/                         当前代码回归测试
archive/                       已退役代码和旧方案；原路径、SHA、恢复说明
```

`inputs/` 是冻结输入，`runs/` 是不入库的运行产物，`results/`、`validation/` 与历史实验目录保留原始记录。[scenarios](scenarios/README.md) 是既有容器场景参考，`skills/` 是 trace 技能的可分发包，`paper/` 是论文子模块；都不作为本轮第二套执行入口。

**执行顺序：读方案 → 准备环境 → 负责人逐条执行脚本并检查输出 → 保全结果。** 配对组内比较 baseline；采集、建机、测量和清理不由一个脚本包办。部署约定见 [AGENTS.md](AGENTS.md)。
