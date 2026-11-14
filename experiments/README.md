# 实验索引

当前入口是 [C1–C4 方案](unified-suite/current/README.zh.md)。正式实验暂停，已完成的最小检查与正式性能结果分别保存。

| 目录 | 内容 |
|---|---|
| [overlay-open-profile-a355-20261114](overlay-open-profile-a355-20261114/README.zh.md) | open/close 状态与调度优化；Redis 查询分解、并发回归及三对复测 |
| [overlay-scale-a355-20261114](overlay-scale-a355-20261114/README.zh.md) | 热 Overlay 优化前后、直接只读 lower；1 万真实文件、两档缓存、分页和原始记录 |
| [overlay-open-a355-20261113](overlay-open-a355-20261113/README.zh.md) | open/close/权限缓存分解；修正旧对照实际 TTL 不一致的问题 |
| [idcai-six-node-20261113](idcai-six-node-20261113/README.md) | 现有六台机器的完整 C2、十万文件 C3/C4、四节点 RustFS；运行命令、修复和原始数据索引 |
| [unified-suite/current](unified-suite/current/README.zh.md) | 当前方案、配置、负载、结果和未完成项 |
| [最新五项检查](unified-suite/pilot-aliyun/evidence/preparation/rbm-five-checks-20261112/README.zh.md) | Packed 消融和文件系统跨机检查；不是性能比较 |
| [pilot 脚本入口](unified-suite/pilot-aliyun/scripts/README.zh.md) | 输入准备、最小检查、归档和资源核验 |
| [unified-suite](unified-suite/README.md) | 旧冻结协议及其历史回执 |
| [retest-2026-10-29](retest-2026-10-29/README.zh.md) | 10 月四集群计划、机制资格和外部 opt-381 报告 |
| [aliyun-main/2026-10-28](aliyun-main/2026-10-28/README.zh.md) | 原三机 trace 结果及缺测记录 |
| [aliyun-ai-trace/2026-10-27](aliyun-ai-trace/2026-10-27/README.md) | 完整 trace、磁盘矩阵和清理记录 |
| [aliyun-ai-trace/2026-10-26](aliyun-ai-trace/2026-10-26/README.md) | 早期回放与失败诊断 |
| [agent-fuse-observations](agent-fuse-observations/README.md) | 访问分析与绘图 |
| [cloud-smoke/2026-10-25](cloud-smoke/2026-10-25/report.md) | 早期冒烟报告 |
| [paper-planning](paper-planning/) | 历史论文规划 |

历史数据保持当时的规模、代码版本与判定。已退役的重复代码、旧机器控制脚本从 [Git 历史](../archive/README.md) 查阅，不复制回当前目录。
