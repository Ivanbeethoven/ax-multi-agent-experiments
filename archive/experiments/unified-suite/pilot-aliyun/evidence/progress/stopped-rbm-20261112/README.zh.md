# rbm 实验任务已停止

2026-11-12，按用户要求停止当前任务。独立的“BrewFS Packed 预取优化”会话按用户明确选择保留。

## 已完成

| 项目 | 本任务已取得的证据 |
|---|---|
| C1 | [最小模式](../../preparation/rbm-unified-modes-20261112/README.zh.md)中的基础 I/O、动态块配置和重挂内容检查通过。配置路径通过不等于布局性能结论。 |
| C2 | [同引擎热读两组](../../preparation/rbm-c2-read-controls-20261112/README.zh.md)通过；[367 次真实 FUSE 小负载](../../preparation/rbm-recorded-trace-20261112/README.zh.md)在 BrewFS、JuiceFS 均通过回放、内容/硬链接/SQLite 校验和冷重挂。读 82,192 B、写 107,307 B；不是完整 Agent 任务。 |
| C3 | 128 文件功能探针通过；[十万文件基线中的四阶段抽样](../../preparation/cloud-c3-script-fixes-20261112/README.zh.md)完成 128 次内容校验，覆盖 64 个固定文件，不是全量读回。 |
| C4 | [非空父数据的原生 fork/clone](../../preparation/rbm-c4-published-20261112/README.zh.md)通过子读、覆盖隔离和冷重挂；[原机 stay](../../preparation/rbm-c4-stay-20261112/README.zh.md)两产品均在空 HOME 下打开原 OpenCode 会话，4 条原消息及导出 SHA 相同，模型调用 0。 |

可运行脚本与上述结果已推送 AX 实验仓库。以上是功能及局部资格结果，未作为全部正式 C1–C4 性能实验完成。

## 未完成

- 当前任务的 C4 跨机 `move` 和真实模型续跑未验证。
- Packed-off 同引擎实现及 C2/C4 已有补丁已整合为源码草稿。统一 features 的 `cargo check` 通过，dev build 按用户要求中止；尚无最终统一产物、完整 CI 或 Packed-off 挂载验证。
- 可写 Overlay 开关、all-off、完整任务与规模性能对照未完成。
- 未验收的 BrewFS 源码没有提交或推送，不将其标为可用。

## 资源清理

- 子 agent 已停止。`ax-packed-off-check-01/02/03` 无活动构建，`MainPID=0`；共享重负载锁已释放。
- 已让旧矩阵的停止父进程回收其已退出子进程；三个残留 PID 均消失。
- 无本任务 FUSE 挂载、测试容器或回放进程。已有公共 RustFS 和 registry 服务保持原状。
- 删除约 2.1 GiB 未完成编译缓存，以及约 41 MiB 回放构建中间文件；保留已验证的回放可执行文件。
- 删除本机 140,844,862 B 的重复 OpenCode 可执行文件；rbm 上的原版本、压缩检查点和已验证数据保留。
- 本任务未新增云实例；此前临时复用的 A 池和跳板机已在各自试验结束时停机。后来其他会话启动的实例不属于本次清理，未停止或删除。

[rbm 清理回执](cleanup.json)记录删除路径、剩余公共服务和构建状态。

## 保留位置

- AX 工作树：`rbm:/tmp/ax-feature-align`；最后功能提交 `2b6dd4e`，说明对齐提交 `e6bd2f1`。
- 原始测试证据：rbm 的 `/var/tmp/ax-mode-suite-20261112`、`/var/tmp/ax-c2-recorded-*-20261112-*`、`/var/tmp/ax-c4-stay-*-20261112-01`，以及仓库中的各证据目录。
- 未完成源码、补丁及日志：`rbm:/home/dev/ax-packed-off-stop-20261112`；独立源码工作树 `/home/dev/ax-packed-off-brewfs` 和实验工作树 `/home/dev/ax-packed-off-exp`。
- 该草稿备份也已取回本机：`C:/Users/HXY/.cache/ax-rbm-reconcile-20261112/ax-packed-off-stop-20261112`。

停止后没有继续运行实验或构建。
