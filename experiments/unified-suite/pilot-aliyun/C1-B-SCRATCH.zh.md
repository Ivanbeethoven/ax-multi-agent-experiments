# C1 混合读写：B 池独立小试

这套脚本在 B 池用四块新 100 GiB PL1 盘做一次独立诊断。JuiceFS 和 BrewFS Flat 使用同一客户端、Redis、RustFS、四块盘与同一份 64 MiB CRC 输入，先 JuiceFS、后 BrewFS。计时阶段固定 4 KiB、75:25、每写同步，保存 fio 原始 JSON、两次原生零上传债务、冷重挂的 CRC 校验、挂载生命周期和四盘两秒采样。两臂是不同的新桶和 Redis 库；它们共用 RustFS 进程及其后台活动。一次顺序结果只用于容量和路径验证，不能作为性能排名，也不计入旧 28 个单元。

协调者沿用 B 的物理锁；租约检查覆盖实际控制库。只创建本轮四块临时盘，不扩原盘、不写原数据卷。原客户端二进制只通过 `ro,noload` 私有挂载读取；运行前对 Git 分支头、BrewFS/JuiceFS SHA 和 RustFS OCI revision。旧挂载或现有服务不空闲就停。采样跌破 20% 预留加 2 GiB 时停止临时 RustFS。每臂在客户端持久化一次启动请求，显式禁止 systemd 自动重启；SSH 回执不确定时只查既有服务的身份和关闭回执，不重发启动请求。只有同一进程正常退出并留下完整冷验结果才算关闭。局部结果仍保留。

从 WSL 在本机执行；协调节点须已运行。默认只读预检。`--apply` 仅用于已安排的 B 窗口；它启用按量资源。当前报价为临时盘 ¥0.84/小时，B 三台启动后边际计算约 ¥3.60/小时；保留 5 分钟开始收尾。采集后确认三台 `StopCharging`，按本轮盘 ID、专用标签逐块卸载和删除，读回确认清零。原证据或用户盘不进入释放列表。

```bash
tools=experiments/unified-suite/pilot-aliyun/scripts
python3 -B "$tools/run-b-c1-scratch.py" --out "$HOME/c1-b-preflight"
python3 -B "$tools/run-b-c1-scratch.py" --out "$HOME/c1-b-attempt" --apply
python3 -B "$tools/audit-b-c1-scratch.py" "$HOME/c1-b-attempt" --out "$HOME/c1-b-attempt/audit.json"
```

如果只在云 API 挂载阶段失败、从未发出开机请求，四盘经核验仍是完全脱挂的同一组空白盘，可在新的输出目录用 `--resume-blanks OLD_ATTEMPT --apply`。它保留第一次 API 错误，沿用原窗口截止时间，逐盘等状态稳定后再提交下一个请求。已启动过节点或已开始测量的目录不能走这条路径。

`released.json` 和 `stopped.json` 缺失时，必须先用阿里云只读 API 查确切状态。不要启动下一轮，也不要用旧结果作为新轮的证据。
