# C3：提高 Packed 预算后的读回进度

沿用 A 池现有的 100,000×4 KiB Packed 基线、2 CPU / 8 GiB 槽位和 32 路核验。BrewFS `ca07159f`、RustFS `f4ed2025`。输出预算仍为 128 MiB，控制预算从 1 MiB 提到 8 MiB；[挂载前回执](worker-intent.json)记录实际值 134,217,728 B 和 8,388,608 B，核验上限 900 秒。

| 核验进程已运行 | FUSE 读操作累计 | FUSE 读取字节累计 |
| ---: | ---: | ---: |
| [0:51](live-0722.txt) | 30 | 122,880 B |
| [2:16](live-0723.txt) | 408 | 1,671,168 B |
| [3:38](live-0724.txt) | 806 | 3,301,376 B |
| [4:24](live-host.txt) | 1,004 | 4,112,384 B |

4:24 的采样里，33 个核验线程含主线程：24 个等 FUSE 请求答复，8 个等页状态，1 个等 futex；FUSE 连接有 36 个待处理请求。该次采样的 CPU 限流计数为 0，较早采样的 cgroup OOM 计数为 0。[挂载日志](worker-mount.log)没有预算拒绝，[核验器日志](worker-verify.log)为空。以上是读回进度和等待位置，尚不能确定 FUSE 答复慢的具体原因。

07:26:14 UTC，操作员因读回进度不足，向这个核验进程发出 SIGTERM；没有等到 900 秒超时，也没有看到核验线程报错。[工作日志](worker.log)记录了信号，协调服务以失败状态退出。[挂载](worker-mount-exit.json)正常退出并确认消失；[三台 A 实例](cloud-stopped.json)于 07:26:38 UTC 均已停机不收费。没有创建或扩容资源。本轮未完成全量校验，不计入正式 C3。

[上一轮 128 MiB 输出预算](../c3-current-workspace-budget-output128-01/) · [启动参数](stage.json) · [操作记录](early-stop.txt) · [服务退出](unit-result.txt) · [SHA-256 清单](SHA256SUMS)
