# 五个剩余入口：最小检查通过

2026-11-12，原来阻塞的五个入口全部完成最小功能检查。两项迁移按用户最新要求改为**只验证文件系统，不运行 Agent**。这些结果不抵扣正式实验，也不是性能比较。

| 入口 | 输入与结果 | 回执 |
|---|---|---|
| C2 关闭 Packed 元数据 | 367 次回调，读 82,192 B、写 107,307 B；文件内容、权限、硬链接、SQLite 和冷重挂正确 | [结果](raw/ablation-results-03/C2-replay-packed-off/receipt.json) |
| C2 全关闭 | 同一输入，先完整写入私有 workspace-v1，无共享 lower 绑定；动态布局和 hint 关闭；回放及冷重挂正确 | [结果](raw/ablation-results-03/C2-replay-all-off/receipt.json) |
| C3 关闭 Packed 元数据 | 8 个 4 KiB 文件；38 次验证读、8 次写，冷重挂正确 | [结果](raw/ablation-results-03/C3-smallfiles-packed-off/receipt.json) |
| C4 BrewFS 跨机 | 同一后端、同一 workspace；113 个条目全部一致，目标 4 KiB 写入、fsync、读回及删除成功 | [结果](raw/move-results-fs-05/C4-handoff-brewfs-full-move/run/brewfs/move.json) |
| C4 JuiceFS 跨机 | 相同输入和检查，正常卸载通过 | [结果](raw/move-results-fs-05/C4-handoff-juicefs-move/run/juicefs/move.json) |

C2 沿用已登记的 `strict-portable-terminal-probe` 模式：保留所有字节校验，允许 4 次 TCGETS 探测由 ENOTTY 变为 EOPNOTSUPP。回执的 `strict=false` 保持原值，不能把它写成所有 errno 完全相同。

Packed 关闭确实走 KV：C2 运行阶段/冷验为 596/101 次 KV 操作，C3 为 1469/65 次；这两个阶段的 Packed 命名空间对象读取均为零。lower 对象的键、大小和 ETag 未变。全关闭仍保留 workspace-v1 的 catalog 根记录与租约，不是删除引擎内部所有层结构。

## 修好的卡点

- KV lower 不存在上层新建的目录或文件时，lookup、xattr 和 ACL 查询应返回“这一层没有”，此前错误地报 NotFound，导致创建或扩展属性查询失败。
- 导入器保留了已经用完的根记录解码空间，嵌套目录解码撞上 8 MiB 预算。写完根记录立即释放；没有提高预算。
- `os.walk` 默认吞掉 OSError，连 TimeoutError 也会吞；现在直接上报。断开的 FUSE 挂载改用 `/proc/self/mountinfo` 判断，避免误报已经卸载。
- 目标验证器此前同时保留、枚举大量目录，触发解码预算限制。现在逐目录完成并关闭句柄，最多两个文件读取线程，完整核验内容不变。

`ablation-results-01/02`、`move-results-01/02/03` 保留各次失败及回执。旧会话导出超时和用户取消后的中断没有改写为成功。

## 迁移范围与耗时

源为 rbm，目标为原有 ax-c1-jump。机器/启动身份不同，源先排空并干净卸载；目标新缓存重开同一卷，未复制 workspace 数据。Redis/RustFS 通过 SSH loopback 转发访问，没有公开服务端口。

新输入仅去掉 `source/config/opencode/node_modules`，保留任务、Git 和状态文件：71 个文件、113 个条目、280,418 B，见 [输入清单](raw/input/input.json)。原 894 项输入没有修改。Agent 状态作为普通文件校验，不验证对话继续、模型执行或故障切换 fencing。

公网环境下全树核验分别耗时 648.08 s / 197.56 s；这是验证程序的耗时，不能用作正式迁移停顿或性能对比。挂载出现分别为 9.13 s / 4.81 s，也不等于首次正确读取时间。

## 版本、复现与清理

消融使用同一个 [candidate-03 构建](raw/build/candidate-03/build-provenance.json)：main `ca07159` 加[完整诊断补丁](raw/build/candidate-03/candidate.patch)，默认 features 保留。主包 opt-level=0，Rust 全套 CI 和新增 Rust 单元测试未执行；不作为性能产物或已合入上游的功能。C4 使用未改源码的 `ca07159` 优化产物，双机二进制 SHA 一致；RustFS 为 `f4ed2025` 对应的固定镜像，JuiceFS 1.4.2。

运行方法见 [五项检查说明](../../../FIVE-CHECKS.zh.md)；本轮精确命令见 [消融](raw/commands/run-ablations-03.sh)和[跨机](raw/commands/run-move-fs-05.sh)。原 jump 已停机，重跑需提供实际可用的两台机器。三个消融共用最终候选，历史候选不再用于新运行。

AX [748 项测试](checks/python-full.txt)完成，26 项按条件跳过；[范围调整和定向检查](checks/scope-tests.txt)通过。另两个会话的工作范围见[只读核对记录](other-sessions.zh.md)。没有推送 BrewFS 主线修改。

只下载约 734 KiB 的证明包，未下载工作区、原 trace 或二进制。`raw/manifest.json` 保存 304 个原始证明文件的 SHA256。jump 已核为 [Stopped / StopCharging](cleanup/cloud-after.json)，[临时密钥](cleanup/target-cleanup.json)及[安全组规则](cleanup/rule-cleanup.json)已移除。未新增实例或磁盘。rbm 删除本任务约 27.4 GiB 可重建编译缓存和旧候选，保留最终二进制；[清理回执](cleanup/rbm.json)确认原有两个容器保留。
