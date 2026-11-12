# 历史文件

当前入口：[C1–C4 方案](../experiments/unified-suite/current/README.zh.md)。历史代码保存在 Git，不参与当前调度。

| 内容 | 查阅位置 | 校验清单 |
|---|---|---|
| 2026-11-12 退役的 34 个旧控制脚本、12 个专属测试、7 份过时指南 | [删除前的固定提交](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/tree/2443946b40b5644b8cf4eabc6728c96f6c6e6c5e) | [53 个文件、原路径与 SHA256](retired-pilot-20261112.manifest.json) |
| 旧重复仓库及四 RQ 方案，181 个文件 | [legacy-20261028.zip](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/blob/fc6a64583112ea7aa6f0f64cd8e972bac064a79c/archive/legacy-20261028.zip) | [manifest.json](manifest.json) |
| 旧调度器、Terraform、Ceph/磁盘换挂及专属测试，121 个原件 | [legacy-code-20261029.zip](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/blob/fc6a64583112ea7aa6f0f64cd8e972bac064a79c/archive/legacy-code-20261029.zip) | [清单](legacy-code-20261029.manifest.json) |
| 10 月 28 日计划 | [plans/2026-10-28](plans/2026-10-28/README.md) | 原文件 |

11 月 12 日清理对象是原 A/B 机器专用的续跑、值守、扩盘、释放脚本，以及已经取消的 Flat 完整复制流程。删除前逐文件比对固定提交并记录哈希，合计 553,640 B。最新五项检查、配置、通用部署与资源保护脚本保留。continue-c4-formal 和 watch-c4-formal-boundary 仍被归档及停止状态检查使用，因此保留。原始输入、结果、失败记录和冻结证据未改动；旧回执中的路径仍指当时版本。

两个 ZIP 已于 11 月 10 日从工作目录移除，内容仍在上述 Git 提交中。不要把它们重新加入仓库。[history.json](history.json)记录取回位置。完整 clone 后可直接核验历史原件，无需恢复文件：

```bash
python3 scripts/check_repository_archive.py
```

浅克隆须先 `git fetch --unshallow origin`。查阅单个退役脚本：

```bash
git show 2443946b40b5644b8cf4eabc6728c96f6c6e6c5e:experiments/unified-suite/pilot-aliyun/scripts/continue-c4-native-flat.py
```

需要旧环境时，在仓库外检出对应提交。不要将固定旧机器 ID 的脚本恢复到当前目录后直接执行。

本次清理的[检查记录](../validation/repository-cleanup-20261113.json)：683 项测试运行完毕，26 项按条件跳过，无失败；最新配置和原始结果未改动。
