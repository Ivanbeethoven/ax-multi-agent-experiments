# 原 C2 五项核对

2026-11-11 21:30（北京时间），只读查询协调者原冻结计划及 SQLite。**C2 验收 2/5**：`05357b5089cd679c-r1-native`（flat BrewFS，A）和 `05357b5089cd679c-r1-juicefs`（B）均完成九阶段，无租约；各自有完整回放资格和正式关闭回执。见 [原始文件摘要](audit.json)。

| 未完成的单元 | 最后实际状态 | 冻结版本的阻塞 |
|---|---|---|
| `05357b5089cd679c-r1-packed` | 未启动，未找到本项资格或失败回执；C3 已有 Packed 发布与读回证据，输入和操作与本项不同 | `workspace-entity-v1` 的 Entity FUSE 缺少原 trace 已成功执行的 `chown`、`utimens`、`rename`、`symlink`、`link`；`statfs` 也没有专用实现。 |
| `05357b5089cd679c-r1-entity-native` | 未启动，未找到本项资格或失败回执 | 同一个 Entity FUSE 缺口。换 native lower 不会改变回调实现；此输入没有发现额外的 native lower 容量限制。 |
| `05357b5089cd679c-r1-native-workspace` | 未启动，未找到本项资格或失败回执；C4 空父版本检查不能作为本项资格 | 走不同的 `workspace-native-v2`。冻结生产入口不能把原非空 `pytest-full-02` 发布成可挂载、可派生的 native revision。 |

三个剩余项均放在 A，`pending`、无阶段、无租约。当前没有一项能只改实验脚本就按冻结条件完成。[现有只读读取器](../../../scripts/verify-entity-remote.rs) 已能比较符号链接目标、权限和硬链接组；旧说明中这项限制已过时。它可以接入原树清单，但无法补齐 FUSE 操作或 native 发布。先用极小样例验证对应产品功能，再申请完整资格；本次没有重跑、改门槛或访问执行节点。
