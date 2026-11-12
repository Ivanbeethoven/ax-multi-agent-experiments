# C3 Packed：停机后的 A 池原生证据

本检查用于原 `16f4ac389d2a2da6-r1-packed`。它比对 ECS 原实例、私网、规格、六块数据盘和原生挂载，检查三台机器确已进入本次新 boot，Redis、RustFS 的运行进程及配置一致，客户端 cgroup 空闲。原硬件收据的 SHA-256 固定在工具内；不从本次输出倒造历史证据。

**不为运行此检查而启动云机。** 只有 A 池已有已批准的恢复窗口时，才在 [停机后手册](POST-STOPCHARGING.zh.md) 的挂载和服务恢复之后取样。保留每次原始输出；同一物理锁下核对各控制库无租约。历史记录或本地测试不能代替本次读回。

在原实验仓库的本机工作树使用 PowerShell 7。`$cfg` 是现有 A 池 SSH 配置；`$dir` 是本次证据的空目录。`$wake` 是本次实际唤醒请求发出前记录的 UTC ISO-8601 时间。`$stopped` 指向本次唤醒前保留的 [StopCharging 格式](evidence/preparation/c4-observer-fold-real-03/historical-objects-pack/cloud-stopped.json)，不能用唤醒后的记录。

~~~
$p = 'experiments/unified-suite/pilot-aliyun/scripts/c3-packed-reboot-evidence.py'
$cfg = 'C:/Users/HXY/.cache/ax-pilot-20261111/bootstrap-ssh.conf'
# 已设置 $dir、$wake 和 $stopped
$ii = '["i-bp1809mw7rlujtj9f705","i-bp1gy3cfuj9jik0y4oc3","i-bp1h9kaoya0predubdkn"]'
$di = '["d-bp1809mw7rlujtjc8epi","d-bp1gy3cfuj9jik0upt3k","d-bp1h9kaoya0predx6kvd","d-bp1h9kaoya0predx6kve","d-bp1h9kaoya0predx6kvf","d-bp1h9kaoya0predx6kvg"]'
function EvidenceRef($path) {
    $resolved = (Resolve-Path -LiteralPath $path).Path
    $linux = wsl.exe -d Ubuntu-24.04 -u hxy -- wslpath -a ($resolved.Replace('\','/'))
    if ($LASTEXITCODE -or -not $linux.StartsWith('/')) { throw "WSL path lookup failed: $resolved" }
    @{path=$linux;sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $resolved).Hash.ToLowerInvariant()}
}
function EvidenceWrite($path, $rows) {
    $text = ($rows -join [char]10) + [char]10
    $text | ConvertFrom-Json | Out-Null
    $target = [IO.Path]::GetFullPath($path)
    $f = [IO.File]::Open($target,[IO.FileMode]::CreateNew)
    try { $b = [Text.UTF8Encoding]::new($false).GetBytes($text); $f.Write($b,0,$b.Length) } finally { $f.Dispose() }
    EvidenceRef $path
}
$i = aliyun ecs DescribeInstances --RegionId cn-hangzhou --PageSize 100 --InstanceIds $ii
if ($LASTEXITCODE) { throw 'DescribeInstances failed' }
$it = [DateTimeOffset]::UtcNow.ToString('o')
$d = aliyun ecs DescribeDisks --RegionId cn-hangzhou --PageSize 100 --DiskIds $di
if ($LASTEXITCODE) { throw 'DescribeDisks failed' }
$dt = [DateTimeOffset]::UtcNow.ToString('o')
$r = @{schema='packed-c3-reboot-evidence-request-v1';cell_id='16f4ac389d2a2da6-r1-packed';wake_requested_at=$wake;
    previous_stopped=(EvidenceRef $stopped);instances=(EvidenceWrite "$dir/instances.json" $i);
    disks=(EvidenceWrite "$dir/disks.json" $d);native=@{}}
$r.instances.at = $it
$r.disks.at = $dt
foreach ($role in 'client','metadata','objects') {
    $sent = [DateTimeOffset]::UtcNow.ToString('o')
    $v = Get-Content -Raw $p | ssh -F $cfg "ax-pilot-$role" python3 - probe $role 2> "$dir/$role.stderr"
    if ($LASTEXITCODE) { throw "Native readback failed: $role" }
    $received = [DateTimeOffset]::UtcNow.ToString('o')
    $r.native[$role] = EvidenceWrite "$dir/$role.json" $v
    $r.native[$role].sent_at = $sent
    $r.native[$role].received_at = $received
}
$request = EvidenceWrite "$dir/request.json" ($r | ConvertTo-Json -Depth 5)
~~~

在同一本机 WSL 发行版验证：

~~~
wsl.exe -d Ubuntu-24.04 -u hxy -- python3 (EvidenceRef $p).path verify --request $request.path --sha256 $request.sha256
~~~

请求中的路径必须对运行验证的环境可读；命令上方的函数已转换为 WSL 绝对路径。验证只读标准输出，成功返回 `evidence_consistent:true`，拒绝时退出 2。窗口上限为 900 秒，主机应已完成 NTP 同步，样本必须落在协调者每次 SSH 请求的起止时间内，容差 2 秒。进程数字 PID 允许在新 boot 中重复；身份由当前 boot、PID、内核 start ticks 和读回前后的一致性确定。原历史盘清单没有 UUID；验证能核对本次设备与挂载的 UUID 一致，不能证明未重格式化，也不能代替原不可变基线的冷读。

它不会写状态库、授予租约或产出 `reviewed/passed` 准入。仍缺：新 boot 的运行绑定与按原计划口径的跨池证据、本次不可变基线冷验、以及结合原有增长证据和本次原生余量的逐盘预算。冻结 `pilot_context.py` 和 `packed-c3-capacity.py` 仍拒绝直接使用跨 boot 的旧证据。
