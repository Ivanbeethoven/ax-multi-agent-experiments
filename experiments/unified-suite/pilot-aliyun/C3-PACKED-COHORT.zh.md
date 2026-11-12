# C3：定位 Packed 小文件读回卡点

[上一轮](evidence/preparation/c3-current-workspace-budget-control8-01/)把输出、控制预算调到 128 MiB 和 8 MiB 后，没再看到预算拒绝；4:24 只累计 1,004 次 FUSE 读，24 个核验线程在等 FUSE 请求答复。云上同版本的[三个分散样本](evidence/preparation/c3-current-workspace-base-01/three-offset-diagnostic.txt)已经读对；本机同版本的[两文件试验](evidence/preparation/c2-local-packed-warm-01/)发现第二遍仍有对象端 payload 读取。本轮不重跑十万文件，先确定慢在属性查询、打开还是读取，以及串行是否也慢。

沿用 A 池已有的 100,000×4 KiB 基线和二进制、2 CPU / 8 GiB 槽位、4 个 FUSE worker 和上述两项预算。一次新挂载，从 64 个不同目录各取一个文件；A、B 各 32 个，目录编号与文件编号交错。按「串行首次 A → 并行首次 B → 串行复读 B → 并行复读 A」执行。并行阶段同时放行 32 个读者，不做全树列目录。首次指本轮未读过该路径；四段共用一个挂载，不能把首次当作元数据缓存或对象缓存全冷。

在批准的 A 池窗口从 PowerShell 运行。入口传入 `Database=0`，持有全池物理锁后从诊断数据库范围选择空库，记录检查结果和实际编号；不删除已有数据。显式指定非空库时仍拒绝。不新建机器或盘，不自动重试：

```powershell
$cache = 'C:/Users/HXY/.cache/ax-pilot-20261111'
& ./experiments/unified-suite/pilot-aliyun/scripts/run-existing-jump-window.ps1 `
  -SshConfig "$cache/bootstrap-ssh.conf" -ReceiptRoot $cache -Activity C3Cohort
```

如果跳板和控制盘已就绪且有独立窗口持有人，可以直接调用 `run-c3-workspace-base.ps1`。整窗入口会在启动 A 池前验证并重挂现有跳板控制盘；不格式化盘，不写入 `fstab`。

从新回执目录查看 `receipt/worker-cohort-events.ndjson`：每条路径分别记录 `lstat`、`open`、`pread`、`close` 的开始、耗时与错误；核验成功才写 4,096 B 的 SHA-256。超时保留已有事件，只有开始的调用表示当时尚未返回。四段全部通过才产生 `worker-cohort-result.json`；另外检查正常卸载和三台 `StopCharging`。

- 串行第一次也慢：先查串行耗时最高的调用；只有 payload 读慢，才优先关联 RustFS Range 请求。
- 串行正常而并行等待长：再查 FUSE 请求排队和后端并发，不能用同一轮共享缓存推断倍率。
- 四段均快：目录遍历或长运行后的行为尚未覆盖；下一步单测固定目录的 `readdir`，再做较大 cohort。

此回执只定位读回路径。全量正确性、C3 性能比较以及百万文件仍须各自验证。


2026-11-12 接线更新：前一轮首次访问已用约 115 秒，因此整窗入口把同样四段诊断的上限从 120 秒设为 240 秒；64 个固定文件、操作顺序、槽位和缓存预算不变。这不是正式性能阈值。入口还会分发并核对当前 `a-pool-up.py` 的 SHA256，objects 明确传入 `--without-legacy-observer-scratch`，只恢复四块 RustFS 数据盘，不再要求已退役的 On1 临时盘。旧脚本和旧失败证据保留。
