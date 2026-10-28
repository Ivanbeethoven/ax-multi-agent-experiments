# 当前实验方案入口

本文件是当前 `paper-design/` 方案的稳定入口。参数和实验类身份以 [`design.json`](design.json) 与 [`experiment_class_registry.json`](experiment_class_registry.json) 为准；执行资格、停止规则和产物契约见 [`TEST-PLAN.zh.md`](TEST-PLAN.zh.md)；8 vCPU/30 GiB、四个 250 GiB 数据盘和落盘位置见 [`EXPERIMENT-CONFIG.zh.md`](EXPERIMENT-CONFIG.zh.md)。

操作章节见 [`REORG-EXPERIMENT-PLAN.zh.md`](REORG-EXPERIMENT-PLAN.zh.md)，JuiceFS 对照规则见 [`JUICEFS-PARITY.md`](JUICEFS-PARITY.md)，50 格执行摘要见 [`run-grid.json`](run-grid.json)，精确展开计划见 [`execution-grid.json`](execution-grid.json)。本入口不复制参数，避免与这些现行权威文件分叉。

2026-10-28 批次的版本冻结记录见 [`BACKEND-PIN-20261028.json`](BACKEND-PIN-20261028.json)，四组实际执行及覆盖边界见 [`EXECUTION-20261028.md`](EXECUTION-20261028.md)。
