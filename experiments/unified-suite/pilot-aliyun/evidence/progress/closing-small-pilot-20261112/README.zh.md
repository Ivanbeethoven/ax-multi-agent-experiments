# C1–C4 小规模收尾清单

本轮优先完成 A→B 跨机重开。A 池完整 trace 仍未开始回放，第三次卡在初态发布；C3/C4 派生另设一万文件资格，窗口不足就跳过。以下小试不改变旧冻结套件的 **14/28**（C1 9/10、C2 2/5、C3 2/4、C4 派生 1/5、C4 跨机 0/4）。

| 项目 | 已有证据 | 能说明什么 |
|---|---|---|
| C1 同步混合读写 | [B 池两种顺序](../../preparation/c1-b-randrw-scratch-reverse-02/README.zh.md)：64 MiB、4 KiB、每写一次 `fsync`；BrewFS 36.160 / 63.425 s，JuiceFS 792.007 / 741.537 s。四臂冷挂 CRC、零上传债务及卸载均通过。 | 这份输入上两种顺序均测得更短的 BrewFS 用时；每种顺序只一轮，没有分离动态块布局等设计的贡献，不能外推到 Agent。 |
| C2 热态 lower | [两文件同引擎对照](../../preparation/c2-rbm-namespace-seqfix-01/README.zh.md)：每臂 256 次 FUSE 读取；计时内 RustFS 读取为零，256 次上层 extent 查询变为 256 次跳过。 | 证明开关改变了上层查询路径、差异发生在热读。只有一次固定顺序的小试，不能替代全 trace 或云端性能对照。 |
| C3 Packed 小文件 | [A 池四阶段原始回执](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/blob/0484cc9/experiments/unified-suite/pilot-aliyun/evidence/preparation/cloud-c3-script-fixes-20261112/passed/receipt/worker-cohort-result.json)：十万文件基线中固定 64 个文件，128 次校验、512 KiB 全通过。四段分别为 93.96、18.99、18.89、9.27 s。 | 只验证抽样读取和复读；各阶段共用挂载、顺序和缓存历史，没有同引擎 Pack 开关对照，不据此估计完整树或并发收益。 |
| C4 原生派生 | 同样两文件、77 B：[BrewFS 候选 fork](../../preparation/c4-local-packed-fork-candidate-01/root-principal-smoke-receipt.json)和 [JuiceFS clone](../../preparation/c4-local-juicefs-clone-01/receipt.json)分别通过子目录读回、写隔离和冷挂；BrewFS 普通用户写根目录的 [EACCES 回执](../../preparation/c4-local-packed-fork-candidate-01/root-nlink-smoke-receipt.json)单独保留。 | 两边有各自的功能证据；BrewFS 用候选补丁，写隔离用 root，优化版没有产物。没有派生性能对照。 |

待收尾事项到时间上限先停止并取回证据，不自动重试：

1. **C2 完整回放。** 在 A 池用同一份 `pytest-full-02`，验输入哈希，回放 1,747,109 次 FUSE 回调（读 281,774,624 B、写 194,706,661 B），核对操作结果与终态、硬链接关系、两次零上传债务和冷挂内容。存原始计数、耗时、失败阶段、卸载和停机回执。通过也仅是完整方案资格。[执行步骤](../../preparation/c2-current-trace-a-draft-01/README.zh.md) · [首次客户端准备失败](../../preparation/c2-current-trace-a-preflight-failed-01/README.zh.md) · [第二次 Redis 预检失败](../../preparation/c2-current-trace-a-preflight-failed-02/README.zh.md) · [第三次 Packed 发布超时](../../preparation/c2-current-trace-a-producer-timeout-03/README.zh.md)。第三次用的是原 trace 的 614 文件初态，300 秒上限内未完成发布；与 C3/C4 原十万文件规模无关。若再跑，先定位发布的慢阶段。
2. **C4 跨机重开。** 复用已核对的 894 项 checkpoint，从 A 正常停旧进程、同步并卸载，再由 B 从同一共享工作区冷挂，使用空 HOME 和工作区内的 XDG 路径核对会话 ID、历史及导出 SHA；验证旧执行授权已拒绝，保留源/目标身份、阶段计时和停机回执。仅做无模型资格，不算真实模型继续任务或崩溃客户端 fencing。[两端步骤](../../c4-state-reopen-draft-01/README.zh.md)。

3. **C3 / C4 派生一万文件资格。** 每项独立使用 10,000 × 4,096 B（40,960,000 B，约 39 MiB）、1 个 Agent；C4 每臂派生 1 个子工作区，仍只用原生 fork/clone。见[独立配置](qualification-10k.json)。现有十万/百万文件执行器固定了规模；一万文件需新输入清单和独立验收入口，配置本身不可直接派发。当前没有实跑结果。跨机仍用原 894 项 checkpoint。C4 优化版的 [30 分钟构建已超时](../../preparation/c4-rbm-release-timeout-03/README.zh.md)，不继续大构建，也不把未优化候选拿来对比性能。缺可写 Overlay 同引擎开关、Pack 对照或最新主线原生 fork 支持时，记为机制未就绪，不拿别的路径补数。
