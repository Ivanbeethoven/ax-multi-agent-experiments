# 六台 IDCAI：C1–C4 十万文件验收

在现有两台客户端和四台存储机上运行。RustFS 四节点、每台一盘，Redis 自建；BrewFS 和 JuiceFS 顺序使用同一后端。C2 使用完整 `pytest-full-02`，C3/C4 派生各十万个 4 KiB 文件，迁移使用 894 项工作区。全程不调用模型。

[下次运行的命令](NEXT-RUN.zh.md) · [问题、修复与验证](ISSUES.zh.md) · [本批配置](performance-100k-brewfs-8a215a6.json) · [脚本](scripts/)

本批固定 AX `86f704be`、BrewFS `8a215a65`、RustFS `f4ed2025` 和 JuiceFS `1.4.2`。BrewFS 在 A366 编译，再由内网分发到 A355；[构建](evidence/brewfs-update-8a215a6/build.json)和[两台校验](evidence/brewfs-update-8a215a6/target-installed.json)有实际回执。新批次升级须重新固定版本，不替换正在测量的产物。

## 运行状态

| 项目 | 当前结果 |
|---|---|
| C2 BrewFS Flat | [完整回放和冷重挂通过](evidence/ax-four-9bf61ed86533/c2-brewfs-flat.json)，229.33 秒 |
| C2 BrewFS Workspace | [完整回放和冷重挂通过](evidence/ax-four-72e316106d56/c2-brewfs-workspace.json)，1,316.63 秒；182 个 lower 对象保持不变 |
| C2 JuiceFS | [完整回放和冷重挂通过](evidence/ax-four-65e01ef71d7e/c2-juicefs.json)，4,019.99 秒 |
| C1 | 九项 I/O 已启动，运行编号 `ax-four-aa0966f7c0c2`；之后检查三种 B/q 配置，只跳过 JuiceFS 混合读写 |
| C3 | 两组十万文件已排队；旧版 Packed 的 ENOMEM 尚待新版实测 |
| C4 | 两系统 stay/move、十万文件 fork/clone 已排队；派生后检查六类修改和完整父/子树 |

三组 C2 都执行 1,747,109 次操作，实际文件读取 281,774,624 B、写入 194,706,661 B。计时仅为 Rust 回放过程；准备、终态校验和冷重挂另记。本次 Workspace 比 Flat 慢、比 JuiceFS 快，保留原值。捕获负载中的重复读取比例为 89.74%，不等于后端缓存命中率。[三组汇总](evidence/ax-four-9bf61ed86533/c2-comparison.json)核对了输入、二进制和所有完整性检查。

这些是单次脚本验收，尚未做正式重复、观测开销校准或多 Agent 扩展。C2 连续监控包含整机计数、RustFS 进程/cgroup、Redis 和客户端原生文件系统计数；客户端逐进程 CPU 采集从后续 C1/C3/C4 开始。

## 数据与证据

完整 trace 的下载地址和哈希见[数据目录](../../scenarios/k8s-containers/data-catalog.md)。本批另保留[权限恢复](evidence/c2-mode-restoration.json)、[十万文件输入](evidence/input-set-100k.json)和[迁移输入](evidence/c4-handoff-894-install.json)的校验回执。

每台原始数据位于 `/srv/ax-idcai-20261113/campaign-100k-001/<运行编号>/`；归档位于该节点的 `evidence-archives/<运行编号>/`。仓库保留小型回执、监控检查和 `raw-archives.json` 下载索引，不复制 trace 或原始日志大包。

[Flat 原始归档清单](evidence/ax-four-9bf61ed86533/raw-archives.json) · [Workspace 原始归档清单](evidence/ax-four-72e316106d56/raw-archives.json) · [JuiceFS 原始归档清单](evidence/ax-four-65e01ef71d7e/raw-archives.json)。重新取回归档后核对清单中的 SHA-256。三组六台采集器分别保存 76、349、935 个样本/台，均无缺失或采集错误，服务和挂载清理回执也保存在对应目录。

[源码清单](source-manifest.json)绑定本目录脚本；[AX 适配](evidence/brewfs-8a215a6-001/adapter.json)、[C4 六类修改](evidence/brewfs-8a215a6-001/adapter-c4-six-ops.json)和[进程采集更新](evidence/brewfs-8a215a6-001/process-observation-update.json)分别记录改动时的源码哈希。C4 迁移辅助脚本的后续版本以进程采集更新为准。
