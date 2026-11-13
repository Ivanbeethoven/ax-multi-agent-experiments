# 阿里云 pilot 记录与脚本

正式实验暂停。原 A/B 测试机和附属盘已释放，jump 与两块盘保留。历史清单中的资源 ID 已不可用于启动新实验。

| 内容 | 入口 |
|---|---|
| 当前方案与未完成项 | [C1–C4](../current/README.zh.md) · [缺项](../current/PENDING.zh.md) |
| 已有性能结果 | [结果与图](../current/results-20261112/RESULT.zh.md) |
| 最新五项最小检查 | [结果与实际版本](evidence/preparation/rbm-five-checks-20261112/README.zh.md) · [运行方法](FIVE-CHECKS.zh.md) |
| 首轮 26 个入口检查 | [原始结果](evidence/preparation/rbm-script-check-20261112/README.zh.md) · [公共参数](RBM-MINIMAL.zh.md) |
| 输入、运行、归档 | [脚本入口](scripts/README.zh.md) |
| 指标采集 | [观测配置](OBSERVABILITY.zh.md) |
| 资源归属与释放核验 | [收尾说明](RELEASE.zh.md) |
| 退役文件 | [清单和固定 Git 版本](../../../archive/README.md) |

C4 当前只验证工作区文件、跨机重挂与后续读写，不再启动真实 Agent。最小输入的功能结果不能替代正式规模的配对计时。

`evidence/` 保存原始结果、失败及创建/清理回执；不按成绩删数据。目录内其他旧指南用于解释冻结批次，不作为新实验入口。早期准备细节和逐时费用记录见 [原版索引](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/blob/2443946b40b5644b8cf4eabc6728c96f6c6e6c5e/experiments/unified-suite/pilot-aliyun/README.zh.md)。
