# C2：只定位 Packed 发布超时

用原 A 客户端已有的 `pytest-full-02/initial`（614 文件、35,526,497 B），原 `aa45b1ee…` 生产器、相同 S3 参数、`auto` 和完整 300 秒上限。只启动原跳板机、A client 和 A objects；A Redis 停机。不下载 trace、不编译、不运行回放或挂载。只创建一个新的 `axc2diag-<随机 ID>` 桶；无新增 ECS、云盘或公网 IP。按历史报价，12 分钟窗口三台节点费用约 ¥1。实际以账单为准，原有盘仍按持有时长收费。

从此工作树根目录运行 PowerShell。`$ReceiptRoot` 指向新建的本机私有回执目录，`$SshConfig` 使用原 A 跳板 SSH 配置。以下先真实核分支再只预览；若分支不是 `ca07159…` / `f4ed2025…`，立即停。

```powershell
$P='experiments/unified-suite/pilot-aliyun'
$Latest=Join-Path (Resolve-Path -LiteralPath $ReceiptRoot).Path 'publish-only-latest.json'
function WslPath([string]$p) {
    $p=[IO.Path]::GetFullPath($p).Replace('\','/')
    if ($p -cnotmatch '^([A-Za-z]):(/.+)$') { throw 'Expected Windows drive path' }
    '/mnt/'+$Matches[1].ToLowerInvariant()+$Matches[2]
}
$Git=(Get-Command git.exe -CommandType Application | Select-Object -First 1).Source
wsl.exe -d Ubuntu-24.04 -u hxy -- python3 -B (WslPath "$P/scripts/verify-latest.py") --manifest (WslPath "$P/evidence/preparation/brewfs-ca07159-build/build-manifest.json") --out (WslPath $Latest) --git (WslPath $Git)
if ($LASTEXITCODE) { throw 'Branch pin verification failed' }
$ahead=([DateTimeOffset](Get-Content -Raw $Latest | ConvertFrom-Json).at-[DateTimeOffset]::UtcNow).TotalSeconds
if ($ahead -gt 30) { throw 'WSL clock difference too large' }
if ($ahead -gt 0) { Start-Sleep -Milliseconds ([int][Math]::Ceiling($ahead*1000)+100) }
& "$P/scripts/run-c2-publish-only.ps1" -ReceiptRoot $ReceiptRoot -SshConfig $SshConfig -Latest $Latest
```

没有另一个云窗口时，在最后一条命令显式加 `-Apply` 执行一次。脚本恢复原控制盘、拿所有共享控制根的 A 物理锁，核实三台 A 均停机后才启动两台。核验五块原数据盘、四组 Docker bind 及 RustFS 实际进程挂载、运行镜像、四个 layout 开关、空 slot 和原始目录。若完整 300 秒和读回/停机预算已不够，放弃启动生产器；无自动重跑。

回执在 `$ReceiptRoot/c2-pubdiag-…/`：`worker/process.ndjson` 每秒采样原 PID/start tick、CPU、I/O、线程等待分布与 RustFS TCP 队列；`worker/object-count.ndjson` 每隔 5 秒做一次限 1,000 对象的 List，仅记数量、字节、响应耗时与截断标志。列表请求在 producer cgroup 外，但仍占用后端；本轮不计性能。没有原始命令输出、Authorization、请求体、对象列表键、`strace` 或 `RUST_LOG=trace`；若生产器在首次采样前退出，可能只得到阶段与退出码。发布成功要求真实进程退出 0，S3 读回 manifest 的 wire magic、键摘要与生产器 sidecar 一致；超时是失败，进程关闭后取回仍可用的证据。

先把已取到的 worker 回执与 SHA 下载，再请求两台 A `StopCharging`；确认状态后用新的 API 回执释放锁，取回协调者回执，再关跳板机。API 或拉取异常时以关停和留存已有证据为先；SSH、SCP 和 ECS 调用各自有绝对剩余预算，关停时另给有限取证时间；无法确认 A 已停，留下协调者与锁并写 `cleanup-stalled.json`；无法确认跳板机关停也写同一类回执。12 分钟是正常路径目标，API 异常时先确认实际状态。跳板机保留证据目录。新桶留作定位；只可在核对本轮 `intent.json` 的精确随机桶名、完整证据和停机回执后另行清理，不能按前缀删除、不能清理旧桶。本诊断不计入 C2 正式完成数。
