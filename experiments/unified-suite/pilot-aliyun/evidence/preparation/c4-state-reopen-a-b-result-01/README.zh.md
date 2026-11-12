# C4：跨机读回了原 OpenCode 会话，整项未通过

在阿里云复用 A 的 Redis/RustFS。A 从原检查点导入 894 项（809 文件、85 目录），同步并两次确认上传债务为零，然后关闭挂载。协调机拒绝了旧授权，给 B 发新的执行授权；这只证明脚本的授权门禁，不是 JuiceFS 自带的故障隔离。

B 冷重挂 [A 的同一个卷](target-stopped.json)，先通过全树摘要检查，再用空 HOME 和工作区内的 XDG 目录执行 OpenCode `export`。目标只接收挂载和验证所需的配置/清单，没有接收工作区文件或会话导出。A、B 原生进程的网络空间都只有 loopback。两端导出都为 10,734 字节，SHA256 完全相同，也与[原检查点的两份导出](../opencode-native-capped-01/source-manifest.json)一致。公开证据保留哈希，不含会话内容。

[源端原进程组](source-native-exit.json)等待 3.45 秒后清空。[目标端](target-native-exit.json)的 OpenCode 主进程也返回 0，但 5 秒门限到时仍有两个活进程，所以整次资格检查失败，未执行目标的两次零债务采样。随后[目标正常卸载](target-stopped.json)，[首次关闭探针](target-closure-probe.json)确认没有原进程、挂载或槽内进程。这个结果验证了有序关闭后跨机读回同一个会话，没有模型调用，不提供继续生成、崩溃恢复或性能数据，不计正式样本。

[回执](closeout.json)包含版本、用到的 DB 211 和 API 请求。10:17:50 UTC，四台测试机及原本未启动的 B 存储节点均为 `Stopped/StopCharging`；运行中的跳板机已明确移交给存量归档任务。[双池物理锁和冲突租约](physical-released.json)均已释放。DB 210 未动，DB 211 的本轮卷留作证据。
