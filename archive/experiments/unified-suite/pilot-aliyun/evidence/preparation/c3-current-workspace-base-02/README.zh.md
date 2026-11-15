# C3：十万文件并行读回在 720 秒超时

在 A 池复用现有 RustFS、Redis 和十万份 4 KiB 文件的 Packed 基线，换成独立 workspace 和 Redis 库。本轮固定 BrewFS `ca07159f` 和 RustFS `f4ed2025`，核验器申请 32 个 worker；输入、二进制、库和命名空间写在[启动记录](worker-intent.json)。

到 669 秒，客户端 FUSE 计数为 [22,623 次读取、92,663,808 B](live-sample-064526.txt)。这是读请求数，不是已校验的不同文件数。到 [720 秒](worker.log)仍无全量核验结果，因此本轮没有完成十万文件校验，不进入 C3 计时对比。

原进程和挂载均[正常退出](worker-mount-exit.json)。[阿里云回执](cloud-stopped.json)确认三台原实例已停机不收费，未增加资源。开机请求至停机确认为 788.484 秒，按 ¥4.67/小时估算约 ¥1.02，账单为准。

脚本：[本机启停](../../../scripts/run-c3-workspace-base.ps1)、[云上协调](../../../scripts/run-c3-workspace-base.py)、[客户端核验](../../../scripts/qualify-c3-workspace-base.py)。

第二个采样起，核验进程只剩 10 个线程（含主线程）；没有完整结果，不能把它归因为“32 线程都慢”。旧核验器要等所有线程收尾才输出错误，超时时日志为空。新版在工作线程发现错误时立即写日志，可用 `-ParallelBinary` 将重新构建并固定 SHA 的核验器放到本轮私有目录，旧核验器不替换。已在本地通过 64 文件全量核验和单个线程遇到损坏文件的拒绝检查。
