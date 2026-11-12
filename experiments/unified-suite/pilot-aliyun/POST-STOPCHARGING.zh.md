# A 池停机后恢复：C3 Packed

恢复挂载不等于恢复准入。C3 `16f4ac389d2a2da6-r1-packed` 正式运行需要本次启动的实测证据。BrewFS Flat 完整拷贝 `c5e97774bcffa69a-r1-native` 已撤出；[历史 On1 归档](evidence/preparation/c4-observer-fold-real-03/recovered-01/publication-01/README.zh.md)不需要重开测量。

## 1. 开机前

- 协调者 `ax-pilot-jump`：`/srv/ax-pilot-control`。C3 原控制目录 `parallel-control-01`；C4 原控制目录 `fork-control-01`、运行目录 `runtime-a-03`。核对该目录下所有 `state.sqlite`：A 池及共享资源无租约，物理锁共用 `parallel-control-01/runner-locks`。历史控制库和失败回执原地保留。
- 本机先查分支：`git ls-remote https://github.com/brewfs/brewfs.git refs/heads/main`、`git ls-remote https://github.com/Ivanbeethoven/rustfs.git refs/heads/feat/dynamic-block-size-rebased`。原批次分别是 `ca07159f3da4b670e4aeb878faef928e69bc1634` 和 `f4ed202508f2153afb7956bae6f7799b60eccd00`。不匹配时先在本机重建并重做受影响的资格；不替换冻结批次的二进制。
- A 三台合计约 ¥4.67/小时（历史估算）；默认保持 B 停机。若 C3 门禁要求 B 的原生证据也重采，另排短窗口。

## 2. ECS API 及盘、服务、槽读回

准备好 C3 脚本、预算和准入材料后，按本轮资源清单启动 A 池。下块在本机 PowerShell 7 运行；每轮用新回执目录。`DescribeDisks` 精确查询四块对象盘、采样盘和 client/metadata 数据盘。

```powershell
$cfg = 'C:/Users/HXY/.cache/ax-pilot-20261111/bootstrap-ssh.conf'
$run = 'a-reboot-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ').ToLowerInvariant()
$local = "C:/Users/HXY/.cache/ax-pilot-20261111/$run"
New-Item -ItemType Directory -Path $local -ErrorAction Stop | Out-Null
$byRole = @{objects='i-bp1h9kaoya0predubdkn'; metadata='i-bp1gy3cfuj9jik0y4oc3'; client='i-bp1809mw7rlujtj9f705'}
$serials = @('bp1h9kaoya0predx6kvd','bp1h9kaoya0predx6kve','bp1h9kaoya0predx6kvf','bp1h9kaoya0predx6kvg','bp16jaiu2fcmbij8mg7i','bp1gy3cfuj9jik0upt3k','bp1809mw7rlujtjc8epi')
$expectedDisk = @{}
foreach ($s in $serials) {
    $role = if ($s -eq $serials[5]) { 'metadata' } elseif ($s -eq $serials[6]) { 'client' } else { 'objects' }
    $expectedDisk["d-$s"] = $byRole[$role]
}
$i = aliyun ecs DescribeInstances --RegionId cn-hangzhou --PageSize 100 --InstanceIds (ConvertTo-Json -Compress @($byRole.Values))
if ($LASTEXITCODE) { throw 'DescribeInstances failed' }
$d = aliyun ecs DescribeDisks --RegionId cn-hangzhou --PageSize 100 --DiskIds (ConvertTo-Json -Compress @($expectedDisk.Keys))
if ($LASTEXITCODE) { throw 'DescribeDisks failed' }
$i | Set-Content -Encoding utf8NoBOM "$local/DescribeInstances.json"
$d | Set-Content -Encoding utf8NoBOM "$local/DescribeDisks.json"
$instances = ($i -join [Environment]::NewLine | ConvertFrom-Json).Instances.Instance
$disks = ($d -join [Environment]::NewLine | ConvertFrom-Json).Disks.Disk
if ((Compare-Object @($byRole.Values) @($instances.InstanceId)) -or (Compare-Object @($expectedDisk.Keys) @($disks.DiskId))) { throw 'ECS resource IDs differ' }
$instances | Select-Object InstanceId,Status,ZoneId,InstanceType
$disks | Select-Object DiskId,InstanceId,Status,Size,PerformanceLevel
foreach ($row in $disks) {
    if ($row.Status -ne 'In_use' -or $row.InstanceId -ne $expectedDisk[$row.DiskId]) { throw "Wrong attachment: $($row.DiskId)" }
}
if ($instances | Where-Object { $_.Status -ne 'Running' -or $_.ZoneId -ne 'cn-hangzhou-j' }) { throw 'A must be running in its original zone before SSH recovery' }
```

第一段只读，**确认已有挂载的源盘 serial/UUID 与原部署一致**，再运行第二段。`a-pool-up.py` 跳过已有挂载，不能代替这次源盘检查。使用原卷，不能格式化或执行 `object-paths` 重新初始化。

```powershell
foreach ($role in 'objects','metadata','client') {
    ssh -F $cfg "ax-pilot-$role" 'set -eu; hostname; cat /proc/sys/kernel/random/boot_id; lsblk -b -o NAME,SERIAL,SIZE,FSTYPE,UUID,MOUNTPOINTS; findmnt -rn -t ext4 -o SOURCE,TARGET,FSTYPE,OPTIONS' | Tee-Object -FilePath "$local/$role-before-mount.txt"
    if ($LASTEXITCODE) { throw "Pre-mount readback failed: $role" }
}
```

```powershell
foreach ($role in 'objects','metadata','client') {
    ssh -F $cfg "ax-pilot-$role" "python3 /srv/ax-receipts/a-pool-up/a-pool-up.py $role --out /srv/ax-receipts/a-pool-up/$role-$run"
    if ($LASTEXITCODE) { throw "Restore failed: $role" }
    scp -F $cfg "ax-pilot-$($role):/srv/ax-receipts/a-pool-up/$role-$run/result.json" "$local/$role-restore.json"
    if ($LASTEXITCODE) { throw "Receipt missing: $role" }
}
ssh -F $cfg ax-pilot-objects 'set -eu; for n in 0 1 2 3; do p=/srv/ax-objects-$n; q=/srv/ax-rustfs-data-$n; findmnt -rn -M "$p" -o SOURCE,TARGET,FSTYPE,OPTIONS; findmnt -rn -M "$q" -o SOURCE,TARGET,FSTYPE,OPTIONS; test "$(stat -Lc %d:%i "$p/rustfs-data")" = "$(stat -Lc %d:%i "$q")"; done; findmnt -rn -M /srv/ax-c4-observer-scratch -o SOURCE,TARGET,FSTYPE,OPTIONS; test "$(findmnt -rn -M /srv/ax-c4-observer-scratch -o UUID)" = 91d693ad-0c15-4e6e-b843-73e581aec594; docker inspect -f "{{.Id}} {{.Image}} {{.State.Running}} {{.State.Pid}} {{json .Mounts}}" ax-pilot-20261111-objects-01-rustfs-1' | Tee-Object -FilePath "$local/objects-live.txt"
if ($LASTEXITCODE) { throw 'RustFS mount or identity check failed' }
ssh -F $cfg ax-pilot-metadata 'set -eu; findmnt -rn -M /srv/ax-metadata -o SOURCE,TARGET,FSTYPE,OPTIONS; systemctl show ax-pilot-redis.service -p ActiveState -p MainPID; redis-cli -h 10.20.2.162 CONFIG GET appendonly databases dir bind maxmemory maxmemory-policy; redis-cli -h 10.20.2.162 INFO persistence' | Tee-Object -FilePath "$local/metadata-live.txt"
if ($LASTEXITCODE) { throw 'Redis readback failed' }
ssh -F $cfg ax-pilot-client 'findmnt -rn -M /srv/ax-client -o SOURCE,TARGET,FSTYPE,OPTIONS' | Tee-Object -FilePath "$local/client-live.txt"
if ($LASTEXITCODE) { throw 'Client data mount absent' }
```

核对并保存原始读回：盘为原设备的 `ext4 rw,noatime`；RustFS 为原镜像和四个绑定，运行且 PID 非零；Redis 为 `appendonly yes`、`databases 256`、`dir /srv/ax-metadata/redis`、`bind 127.0.0.1 10.20.2.162`、`maxmemory 10737418240`、`maxmemory-policy noeviction`，AOF 已加载且当前状态健康。协调者与三节点时钟同步、相差 ≤2 秒。

`a-pool-up.py client` 不重建 cgroup。仅在槽不存在时用原 spec 重建；槽存在但限额不符或有进程就停手。

```powershell
$spec = '/srv/ax-pilot-control/parallel-control-01/hardware-inputs-01/data/pool-a-client-spec.json'
$specSha = '3592411238e1af9eb21980c78d61394c24ea33f74d6452babd335a497d0b5d55'
$remoteSpec = "/srv/ax-receipts/a-pool-up/client-$run/slot-spec.json"
$clientScript = '/srv/ax-client/code-12/experiments/unified-suite/pilot-aliyun/scripts/configure-node.py'
ssh -F $cfg ax-pilot-client 'test -d /sys/fs/cgroup/ax-slot-1'
if ($LASTEXITCODE -eq 1) {
    scp -F $cfg "ax-pilot-jump:$spec" "$local/slot-spec.json"
    if ($LASTEXITCODE -or (Get-FileHash -Algorithm SHA256 "$local/slot-spec.json").Hash -ne $specSha) { throw 'Frozen spec pin differs' }
    scp -F $cfg "$local/slot-spec.json" "ax-pilot-client:$remoteSpec"
    if ($LASTEXITCODE) { throw 'Slot spec copy failed' }
    ssh -F $cfg ax-pilot-client "set -eu; test ! -e /sys/fs/cgroup/ax-slot-1; echo '$specSha  $remoteSpec' | sha256sum -c -; python3 '$clientScript' slot --spec '$remoteSpec' --out '/srv/ax-receipts/a-pool-up/client-$run/slot'"
    if ($LASTEXITCODE) { throw 'Slot restore failed' }
} elseif ($LASTEXITCODE) { throw 'Slot existence check failed' }
ssh -F $cfg ax-pilot-client 'set -eu; cd /sys/fs/cgroup/ax-slot-1; test "$(cat cpu.max)" = "200000 100000"; test "$(cat memory.max)" = 8589934592; test "$(cat memory.swap.max)" = 0; test "$(cat pids.max)" = 4096; test -r io.stat; test -z "$(cat cgroup.procs)"; cat cpu.max memory.max memory.swap.max pids.max io.stat cgroup.procs' | Tee-Object -FilePath "$local/client-slot-live.txt"
if ($LASTEXITCODE) { throw 'Slot is not empty or its limits changed' }
```

下面是 C3 的新采集器。已有监听时先核对进程归属。

```powershell
$collector = "ax-pilot-c3-packed-$run-collector"
$collectorOut = "/srv/ax-objects-0/q/c3-packed-$run-collector"
$start = 'set -eu; ports=$(ss -H -ltn "( sport = :4318 or sport = :18889 )"); test -z "$ports"; python3 /srv/ax-objects-0/code-12/experiments/unified-suite/pilot-aliyun/scripts/metric-collector.py start --image-receipt /srv/ax-objects-0/assets/provenance/otel-collector.json --name {0} --out {1}' -f $collector,$collectorOut
ssh -F $cfg ax-pilot-objects $start
if ($LASTEXITCODE) { throw 'Collector startup or port check failed' }
ssh -F $cfg ax-pilot-objects 'ss -ltnp; curl -fsS --connect-timeout 1 --max-time 3 --retry 3 --retry-connrefused --retry-max-time 10 http://127.0.0.1:18889/metrics' | Tee-Object -FilePath "$local/collector-live.txt"
if ($LASTEXITCODE) { throw 'Collector scrape failed' }
```

监听必须是 loopback。`started.json` 的容器 ID、镜像、配置 SHA 要与实际一致。正式开跑前还须按该单元的指标合同核 RustFS 原生序列、源时间和新鲜度；只有 HTTP 200 不算通过。

## 3. 尚缺的准入门禁

| 门禁 | C3 Packed |
| --- | --- |
| 运行绑定 | `pilot_context.py` 现有切换只支持同 boot，`parallel-control-01/runtime-12-01/binding.json` 不能重用。须绑定当前实例、盘、服务与槽，并给原计划提供有效的跨池证据。 |
| 冷验与容量 | `packed-c3-capacity.py` 要求旧 before 与当前服务同 boot、同 PID。以旧同窗增长为下界，以本次清理后实测盘和服务为基准另出新凭据；对原不可变基线做本次只读冷验。 |
| 执行 | 从 `parallel-control-01/packed-c3-next-03/formal-context.json` 派生新 attempt；保留 `pairs/overhead/result.json`（SHA `460b6294c7fea61c09d5d5638cd05b82c16fab2758e4f68fbe0bf6862d78a1dd`）与[已审阅披露](evidence/preparation/c3-packed-overhead-deviation-01/plan.json)。部署修过 SQLite 准入的 controller 后才能 admit。 |

可先离线实现和测试的最小接口：

1. 参照 [B 池 C1 的重启路径](C1-B-STORAGE-STEP.zh.md#重启后的原生命令链)：新控制目录保留字节相同的 `suite/plan.json` 和 `provenance/{inventory,build-manifest,input-manifest}.json`，录入本次实测 probes、服务、硬件资格。若用新状态库，用 `python3 -m bench.suite init --plan … --db …` 初始化；仍共用 canonical 物理锁并检查所有控制库。结果保留新 attempt 与原 cell 的映射，不能覆盖旧验收统计。`setup-controller.py` 会连接两池；A-only 适配器尚缺。若采用 A-only，只能明确引用 B 的历史硬件观察，并单独审阅这种口径；需要两池实时资格就另排窗口。
2. 输入：已审阅 cell/attempt、原计划/资产/工具的路径和 SHA、旧增长窗口、新 API 及主机读回、当前冷验、全部 lease 控制目录。输出：当前 boot/盘/服务/槽绑定与逐盘预算。`--validate-only` 应只读本地材料；测试拒绝旧 boot、错盘、错 PID、非空槽、过期样本、同池租约、容量不足、历史记录冒充本次采样。
3. 派发前在同一物理锁内按 **清理确认 → 原生快照 → 设备/输出位置核对 → 容量 → 请求**，最长 900 秒；超时在新目录重取。用 `capacity-check.py snapshot --control "$NEW" --pool pool-a --objects-container "$CURRENT_CONTAINER_ID" --out "$FRESH"` 采原生字节/inode、Redis 与容器身份，不传旧 `--before`。核 client、metadata、四个对象盘、协调者及 C4 采样盘；扣完预计新增和临时峰值仍留 ≥20%。C3 保留七份增长窗的保守预算。按旧盘总量算，六块数据盘最少可用字节为 **23,726,967,194 / 12,160,176,128 / 20,049,448,960 / 14,473,060,352 / 14,473,031,680 / 14,473,060,352**，按本次实测总量重算，同时核 inode、Redis 内存与 AOF。C4 采样盘另按实际写入和归档路径预算。

任一步失败，保留日志、API/主机读回和已采证据；不派发、不重放同一 attempt。窗口结束先取回执，再把本窗口启动的节点切回 `StopCharging`，用 `DescribeInstances` 确认。
