# C4 stay：原机重挂 OpenCode 状态

2026-11-12，rbm。**BrewFS workspace-v1 + Packed-v3 和 JuiceFS 均通过**。同一份原生 OpenCode 检查点（恢复时逐项核对 894 项），源端导出原会话、同步、正常卸载，再用新 FUSE 进程和新缓存重挂。打开 OpenCode 前核完整工作区内容与权限；打开后核同一会话、原 4 条消息和完整导出 SHA256。两次均为空 HOME、工作区内四个 XDG 路径，仅 loopback 网络。模型调用 0，不代表模型已继续任务。[摘要](summary.json)；完整实际命令在各产品 receipt 的 argv。

- 输入 SHA256：`d66cb316b2d1d66b80bb56cbc48448d57f15cba410761d2f1541612b5d131fb5`。
- 两产品、两次导出均为 `b3bdd63f6969387f11394cf78fd050352522c60a61a37c69ee835e4d30bd1bd9`。
- BrewFS 为 main `ca07159f` 的统一 feature 二进制；RustFS 为 `feat/dynamic-block-size-rebased` 的 `f4ed2025` 镜像。运行前核过远端分支，产物摘要见 receipt。OpenCode 固定原检查点版本 1.2.26，SHA `025e625d7ee316f9ce246056997551869b72e4bffa778dd3ad8248ba0e014233`。
- 两次卸载均正常，独立 Redis、RustFS 容器和临时缓存均清理。本次补测没有新开云机。

复用 [最小命令](../../../RBM-MINIMAL.zh.md)，选择 `--experiment C4-handoff --mode brewfs-full` 或 `juicefs`，增加：

```bash
--placement stay \
--checkpoint /home/dev/ax-session-reopen-input-20261112/verified/checkpoint.json \
--opencode /home/dev/ax-session-reopen-input-20261112/opencode
```

输出目录每次换新。检查点按 `package-session-checkpoint.py install` 核验恢复；原包与回执来自既有协调者，未生成新会话。会话原文和完整工作区清单留在 rbm 的 `/var/tmp/ax-c4-stay-{juicefs,brewfs}-20261112-01`，仓库只收结果、摘要及挂载日志。

`stay` 已接线；`move` 不会退回单机。这里未验跨机、旧写者拒绝或真实模型续跑，未填正式性能样本。`test_session_smoke.py` 2 项及既有 `test_experiment_smoke.py` 6 项通过。
