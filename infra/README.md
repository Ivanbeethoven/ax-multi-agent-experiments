# 环境准备

本目录只负责服务器、网络、磁盘和后端服务的准备。场景、测量、分工与执行顺序放在[本轮实验目录](../experiments/retest-2026-10-29/README.zh.md)。

| 目录 | 用途 |
|---|---|
| [alicloud](alicloud/PROVIDER.zh.md) | 阿里云资源清单、私网访问、归属与释放规则 |
| [servers](servers/README.zh.md) | 自备服务器条件、检查与清理边界 |
| [rustfs](rustfs/README.md) | 两种环境共用的版本冻结、配置生成、节点检查与 S3 接入探测 |

环境差异不改变 baseline、输入和测量方法。当前没有适用于 64/128 Agent 的自动购机入口；按实际资源清单由负责人分步准备。旧 Terraform、整批调度器、三机恢复/删除控制器、Ceph 生成器已[归档](../archive/README.md)，不能照旧参数启动新批次。
