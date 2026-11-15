# C3：32 路十万文件核验的失败原因

沿用 A 池同一份十万文件基线、BrewFS `ca07159f` 和 RustFS `f4ed2025`，换用一个新的 workspace 和空 Redis 库。改动仅在核验器：任何线程遇到错误立即写日志。这次运行上限为 150 秒。

[线程日志](worker-verify.log)显示 23 个不同线程收到 `ENOMEM`。故障时 [cgroup 回执](live-enomem-diagnostic.txt)显示内存峰值 39,919,616 B，上限 8 GiB，`max/oom/oom_kill` 均为 0，内存压力为 0。[客户端 VFS 的通用分配拒绝计数](live-enomem-fuse.txt)也为 0。当前证据不能确定返回错误的内部路径，也不支持靠加大实例内存解决。

其余线程未在上限内完成；没有全量结果、未进入正式计时。原进程和挂载[正常退出](worker-mount-exit.json)，三台既有实例已[确认停机不收费](cloud-stopped.json)。本轮无新盘、无新实例，按请求至确认停机时间估算约 ¥0.28，账单为准。
