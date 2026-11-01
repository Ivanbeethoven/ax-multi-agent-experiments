# 环境准备

已有SSH环境直接使用[SSH-RUN](../experiments/retest-2026-10-29/runbook/SSH-RUN.zh.md)。本目录用于需要单独准备的基础设施和对象服务。

| 目录 | 用途 |
|---|---|
| [alicloud](alicloud/PROVIDER.zh.md) | 云资源清单、网络、归属和收尾 |
| [servers](servers/README.zh.md) | 服务器设备与交付检查 |
| [rustfs](rustfs/README.md) | 版本冻结、配置生成、节点检查与S3探测 |

方法、baseline、输入与指标由[实验方案](../experiments/retest-2026-10-29/PLAN.zh.md)定义，硬件按实际交付验收。旧Terraform和整批控制器保存在[archive](../archive/README.md)。
