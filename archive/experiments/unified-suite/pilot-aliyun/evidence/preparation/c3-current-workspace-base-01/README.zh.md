# C3：云端挂载原版 workspace-v1，读回已存在的十万文件基线

两次尝试都在阿里云 A 池复用现有客户端、Redis、RustFS 和原盘。RustFS 运行镜像与指定分支提交一致；BrewFS 固定最新 `main` 的 `ca07159f`。第一次在[二进制 SHA 检查](old-path-refused.log)处停止：旧路径保存的是原历史构建。第二次改用 `/srv/ax-client/build-ca07159-01/bin/brewfs`，重新验 SHA；没有替换旧二进制。

第二次将原有 100,000×4 KiB 的 Packed 基线绑定到一个新的 workspace-v1，使用独立 Redis 库和命名空间。原基线桶、对象及旧 Redis 库保持原样。在真实 FUSE 挂载上，从三个分散目录各读出 4 KiB，[逐文件 SHA256](three-offset-diagnostic.txt)与本机输入一致，FUSE 计数也记录了 3 次读取、12,288 B。串行全量核验[到 480 秒上限](worker.log)仍未完成，因此本次只证明挂载与抽样读回。未验证十万文件全量正确性、子工作区隔离、性能或热缓存。

[卸载回执](worker-mount-exit.json)记录了卸载和原进程均正常退出。[阿里云回执](cloud-stopped.json)记录两轮的三台原有实例均回到 `StopCharging`；未新建或删除资源。从两次开机请求至确认停机合计约 617 秒，按 A 池 ¥4.67/小时粗估增加 ¥0.80，实际以账单为准。该记录不计入旧 14/28 的正式验收。

复用入口：[Windows 启停脚本](../../../scripts/run-c3-workspace-base.ps1)、[协调者持锁与回执](../../../scripts/run-c3-workspace-base.py)、[客户端核验](../../../scripts/qualify-c3-workspace-base.py)。串行超时后，另开轮次用已固定 SHA 的并行只读核验器；验证对象仍是全部十万文件。
