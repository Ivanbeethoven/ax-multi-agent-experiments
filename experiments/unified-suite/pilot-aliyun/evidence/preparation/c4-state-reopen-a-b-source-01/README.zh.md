# C4：A 端已打开原会话，B 端未接手

2026-11-12，在阿里云复用 A 的 Redis、RustFS 和 A/B 两台客户端。原 894 项检查点只传到 A。修复执行环境后，两端的空槽 CPU 配额在跨 SSH 的 191.29 秒检查中仍是 2 核，实验结束后再检查仍相同。两个临时 systemd 服务均已停止。已保留[启动前](prelaunch-quota-proof.json)和[结束后](post-experiment-quota-proof.json)的实际读回。

A 在空 HOME、工作区内 XDG 路径及只有 loopback 的网络命名空间中运行 OpenCode `export`，返回 0。[导出原文的长度和 SHA256](source-export-digest-and-size.txt)与[原检查点的首次导出及冷重挂导出](../opencode-native-capped-01/source-manifest.json)完全相同（10,734 字节）。本次只公开哈希，保留原会话内容的既有访问范围。

OpenCode 主进程退出时，同一进程组仍有两个活进程，未达到源关闭条件；没有启动 B，也没有正式 C4 样本。此后 A [正常卸载](source-node-stopped.json)，协调机[首次关闭探针](source-closure-probe.json)确认进程和挂载已消失、执行槽为空。下次应只在原进程组有明确截止时间地等待自然退出，再重新核对空槽；如果到期还有活进程，继续拒绝交接。

09:59:51 UTC [独立云 API 读回](closeout.json)确认七台实例均为 `Stopped/StopCharging`，没有新建实例或云盘。DB 210 保留本次源卷作为失败证据，后续不得当作空库使用。
