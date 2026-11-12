# C3：Packed 输出预算调到 128 MiB

接着[默认预算探针](../c3-current-workspace-budget-default-01/)复用 A 池、100,000×4 KiB Packed 基线、2 CPU / 8 GiB 槽位和 32 路核验。BrewFS `ca07159f`、RustFS `f4ed2025`。本轮输出预算设为 134,217,728 B，[挂载前回执](worker-intent.json)记录实际启动设置。

[BrewFS 日志](worker-mount.log)未出现输出预算拒绝，但在 1,048,576 B 的控制预算上拒绝了 12 次。例：已用 1,045,504 B，再请求 4,096 B。[核验器](worker-verify.log)报告 9 个 `ENOMEM` 和 3 个 `EIO`；同次[采样](live-budget.txt)中 `oom`、`oom_kill` 均为 0。两份日志没有逐请求关联，不能据此给每个错误归因。

读回已失败，操作员只向该核验进程发送 SIGTERM；[工作回执](worker.log)记录信号，协调服务以失败状态退出。[挂载已正常退出并确认消失](worker-mount-exit.json)，[三台 A 实例](cloud-stopped.json)在 07:18:02 UTC 均已停机不收费。没有创建或扩容资源。本轮没有全量校验成功，也不计入正式 C3。

[启动参数](stage.json) · [操作记录](early-stop.txt) · [协调服务退出](unit-result.txt) · [SHA-256 清单](SHA256SUMS)
