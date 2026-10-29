# 实验配置入口

64/128 Agent 的规模、VM/Docker 角色预算和固定总量随机等待见 [SCALE](../experiments/retest-2026-10-29/SCALE.zh.md)；本页链接的 FINAL-PLAN 保留基础机制与资格配置。

硬件、资源限额、版本、baseline、语料、缓存与重复次数统一维护在 [实验方案](FINAL-PLAN.zh.md)，本页不复制参数。

| 配置文件 | 用途 |
|---|---|
| [layout-matrix.json](../experiments/retest-2026-10-29/layout-matrix.json) | RustFS 支持的块大小、开关与布局检查参数 |
| [trace-matrix.example.json](../bench/reference/trace-matrix.example.json) | trace 参数参考；包含占位值，不能直接执行 |
| 本批 `backend-pin.json` | 开批时生成的源码 SHA、镜像 digest 和客户端摘要 |
| 本批 `run-spec.json` | AI 填写并核验的机器、baseline、限额、目录、数据摘要与缓存设置 |

[操作顺序](../experiments/retest-2026-10-29/runbook/RUN.zh.md)。历史版本配置见 [归档](../archive/plans/2026-10-28/EXPERIMENT-CONFIG.zh.md)。
