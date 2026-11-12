# B 池 C1：JuiceFS 维护窗口的实测与失败边界

本轮只做维护准备，没有产生任何 C1 混合读写正式样本。B 池三台仍按原计划可停机；`client` 全程 `Stopped/StopCharging`，从未被 `StartInstance` 调用。

## 已经拿到的实测

第 14 次尝试时（objects boot `32e75a38…`），四块盘按原 serial/UUID 重挂、原 bind 重建、原 RustFS 容器拉起后，节点同时给出了 `statvfs` 与 `du -sx -B1` 的逐盘实测：

| 盘 | 设备 | 已分配（du） | 文件数 | 可用字节 |
| --- | --- | ---: | ---: | ---: |
| /srv/ax-objects-0 | /dev/vdb | 56,987,799,552 | 438,024 | 3,066,109,952 |
| /srv/ax-objects-1 | /dev/vdc | 37,986,533,376 | 273,645 | 22,067,294,208 |
| /srv/ax-objects-2 | /dev/vdd | 37,986,746,368 | 273,711 | 22,067,408,896 |
| /srv/ax-objects-3 | /dev/vde | 37,986,131,968 | 273,375 | 22,068,023,296 |

合计已分配 **170,947,211,264 B**（1,258,755 个文件），可用 **69,268,836,352 B**。原始行见 `objects-inventory-14.json`；第 06 次只含 `statvfs` 的行见 `objects-capacity-06.json`。

盘 0 用掉了 57 GB、只剩 3.06 GB，其余三盘各余 22 GB：这正是「单清盘 0 不够」的量化依据。重挂本身（整盘 ext4 + 原 bind + Docker 身份）已经实际跑通两次。

## 没有跑成的部分

JuiceFS `gc --threads 1` **尚未执行**。九次尝试都停在协调者/节点脚本的接口缺陷上，每次都留下回执，没有一次走到 gc：

| attempt | 停在哪 | 证据 |
| --- | --- | --- |
| 01 | 协调者 guard 用 `-c` 传多行脚本被 ssh 拆参 | 无（未启动实例） |
| 02 | 启动后仍读到 `StoppedMode`，被误判为未运行 | `attempts/failed-02.json` |
| 03 | 本地不存在协调者侧 juicefs 路径 | `attempts/failed-03.json` |
| 04 | `stderr` 以二进制打开却按文本传 | `attempts/failed-04.json` |
| 05 | objects 盘元组解包 2/3 不符；metadata 判「已有 6379 监听」 | `attempts/b-c1-gc-05-node-*.stderr.log` |
| 06 | objects 到 ready；metadata 未被 `redis-cli PING` 判就绪 | `attempts/b-c1-gc-06-node-metadata.stderr.log` |
| 07 | 上一次失败的 guard 进程没释放，物理锁被占 | 无（未启动实例） |
| 08 | 上一次 stop 未落定，StartInstance 前状态检查拒绝 | `attempts/failed-08.json` |
| 09 | 已运行的 Redis `INFO persistence` 没有 `aof_current_size` | `attempts/b-c1-gc-09-node-metadata.stderr.log` |
| 10 | 同 09（加长等待后确认该字段在 Redis 7 里确实不存在） | `attempts/b-c1-gc-10-node-metadata.stderr.log` |
| 11 | 上一次失败留下的 guard 仍占着 metadata 磁盘锁 | 无（未启动实例） |
| 12 | 改由 appendonly 目录实测大小后，Redis 报 `aof_enabled = 0` | `attempts/b-c1-gc-12-node-metadata.stderr.log` |
| 13 | JuiceFS 二进制首次 125 MB 上传被 ssh 断开（exit 255） | `attempts/failed-13.json` |
| 14 | objects 到 ready 并给出逐盘 du；metadata 在私网地址上 `CLIENT LIST` 返回 1 | `attempts/b-c1-gc-14-node-metadata.stderr.log` |

第 12 次把问题指到根上：节点上那个「原配置的 redis-server」当时在 loopback 上 `INFO persistence` 报 **AOF 未启用**，而原 conf 里是 `appendonly yes`。第 13–14 次改到私网地址查询后 AOF 检查通过（说明原实例确实在 `10.20.2.169:6379` 上、AOF 是开的），但 `CLIENT LIST` 在该地址返回非零——同一个端口上可能同时存在只绑 loopback 的另一个实例，这一点必须在只读诊断窗口里一次看清。

第 09 次之后协议层还有一个真实缺陷：节点进程已退出时 `NodeSession.wait` 仍等到 1 小时 deadline，导致锁和两台实例被多占。已改为「进程退出且队列为空即立即报错」。

## 已修复并保留的接口问题（`b-c1-gc-apply.py`、`b-c1-gc-node.py`）

- 协调者 guard 改为「先上传脚本再执行」，不再把多行脚本塞进 `-c`；被拒绝的 guard 也会主动释放，不再留下持锁进程。
- 重复 `StartInstance` 前先等 `Stopped/StopCharging` 落定；运行中实例不再因残留 `StoppedMode` 被误判。
- juicefs 二进制按「jump 上已核 SHA 的副本」拉取后上传，不假设本地存在。
- 节点会话改用文本日志；节点进程退出立即失败，不再占锁一小时。
- 磁盘身份由 2 元组改为 3 元组（device/serial/rdev），修掉 `snapshots` 的解包错误。
- metadata 侧允许「原配置的 redis-server 已在运行」，但拒绝任何陌生监听；就绪探测同时试私网地址与 loopback，并把最后一次应答写进错误。

## gc 跑通了（第 18 次，只读，未删）

根因是 `redis_listening()` 用 `ss` 行尾匹配 `:6379`，而实际行尾是 `users:(...)`，于是「有监听」永远判假、镜像自带的 redis 从未被释放，原 unit 每次都因 `bind: Address already in use` 起不来。改成按地址端口子串匹配后一次通过。

第 18 次结果（`gc-18/result.json`，`passed: true`，`errors: []`）：

- meta `redis://10.20.2.169:6379/61`，data `s3://10.20.2.170:9000/axp-392c124c743749b1/ax-pilot-66928d652930/` —— **db 61 确认就是原 C1 卷**（8 个 slice、70,948,978,688 B 活数据）。
- gc 明文（`gc-18/gc.stderr`）：

```
using 8 slices (70948978688 bytes)
scanned 20187 objects, 83 valid, 20101 pending delete (69078298624 bytes),
0 compacted (0 bytes), 3 leaked (12582912 bytes),
0 delslices (0 bytes), 0 delfiles (0 bytes), 0 skipped (0 bytes)
Please add `--delete` to clean leaked objects
```

也就是：**20,101 个对象、69,078,298,624 B（约 69 GB）处于 pending delete**，真正「leaked」的只有 3 个对象 12.6 MB。四盘合计已用 170.9 GB，如果 `--delete` 把这批清掉，按均摊约可为每盘腾出 ~17 GB —— 盘 0 会从 3.06 GB 回到 20 GB 左右。这正是 C1 混合读写需要的量级。

- 安全性证明齐全：AOF 前后完全一致（manifest/两个 part 的 size/mtime/sha256 全部相同，`changed: false`）；`CLIENT LIST` 无 JuiceFS/Go 会话（`peers: 0`，`suspect: []`）；gc 后无 juicefs 进程、无到 `10.20.2.169:6379` 的 established 连接；`gc-exit.json` exit 0 且未超时。
- client 全程 `Stopped`；objects/metadata 已在 22:54 回到 `StopCharging`。
- 唯一告警是成本报价 schema 不匹配（`Dated CNY disk quote...`），已作为 warning 记录，不影响 gc 结论。

**注意：本次没有执行 `--delete`。** 69 GB 的 pending delete 是实测出来的「可回收候选」，按协议在删之前仍需一次审核过的决定。

## 只读诊断的结论（决定性）

02:00 单独启动 metadata 做了一次只读诊断（`metadata-redis-diagnosis-02.json`，探针只读、实例随后回到 `StopCharging`）：

- `10.20.2.169:6379` **Connection refused** —— 原 `ax-pilot-redis.service`（bind `127.0.0.1 10.20.2.169`、256 库、`appendonly yes`、`dir /srv/ax-metadata/redis`）根本没有在服务。
- `127.0.0.1:6379` 上有一个镜像自带的 `redis-server`：`appendonly no`、`dir /var/lib/redis`、只有默认 16 个库（`SELECT 60/61/62` 全部 `ERR DB index is out of range`），db0 为空。

由此可以解释全部失败：原 conf 同时 bind `127.0.0.1`，而镜像自带的实例已经占着 `127.0.0.1:6379`，`ax-pilot-redis.service` 起不来；节点脚本的「原配置已在运行」容忍分支又把那个自带实例当成了原实例，于是 `INFO` 读到 `aof_enabled:0`、`CLIENT LIST` 在私网地址上连接被拒。

**结论：B 池 C1 的 JuiceFS 元数据现在没有在服务。** 这不是「空间不够」，而是元数据前提缺失。

## `gc --delete` 跑了，但只回收了约 4.1 GB（重要修正）

用户授权后执行了一次 `gc --delete`（`b-c1-gc-del-01`，`passed: true`、`errors: []`、`gc_mode: delete`，`delete_evidence` pin 住第 18 次的只读输出）。结果与预期不符，必须如实记录：

```
scanned 19873 objects, 83 valid, 19787 pending delete (68004319232 bytes),
0 compacted (0 bytes), 3 leaked (12582912 bytes), 0 delslices, 0 delfiles, 0 skipped
```

进程 6 秒退出（exit 0），并伴随 `stop deleting slice` 警告。逐盘 `du -sx -B1` 前后对比：

| 盘 | 删除前已用 | 删除后已用 | 实际回收 |
| --- | ---: | ---: | ---: |
| objects-0 | 56,989,388,800 | 56,002,793,472 | 986,595,328 |
| objects-1 | 37,986,762,752 | 36,932,370,432 | 1,054,392,320 |
| objects-2 | 37,987,065,856 | 36,932,280,320 | 1,054,785,536 |
| objects-3 | 37,986,451,456 | 36,931,653,632 | 1,054,797,824 |

合计 **4,150,571,008 B（约 4.1 GB）**，远低于扫描列出的 68,004,319,232 B。

**结论：`pending delete` 不等于 `gc --delete` 能立刻回收的量。** JuiceFS 把「已不被引用但仍在对象存储里」的对象列为 pending delete，真正删除受安全窗口保护（避免删掉正在写入的对象），或需要元数据侧处理（`--compact` 与后台清理）。盘 0 现在仍只有约 4 GB 可用，**C1 混合读写的容量门槛没有因此达到**。

安全性仍然齐全：AOF 前后一致、无 JuiceFS 会话残留、client 未启动、两台回 `StopCharging`。成本报价 schema 仍是不影响结论的 warning。

## 下一步的前置

1. **pending delete 需要另一条路径。** 已执行的 `gc --delete` 只回收 4.1 GB，证明这批对象受 JuiceFS 的安全窗口或元数据处理约束。下一步要么等窗口过后再跑一次 `gc --delete`，要么评估 `--compact`（会改元数据，必须单独审核），要么按最小扩容解决盘 0 的 3–4 GB 缺口；三者都要新的 reviewed 决定。
2. 每次开机窗口都要重复「释放镜像自带 redis → 起原 unit → `CONFIG GET` 核生效值」，因为镜像的 init 会在下次启动时再把它拉起来。若希望一次修好，需要另一次受审核的宿主改动（disable 该 init 或在镜像层去掉）。
3. 回收之后按 `C1-B-STORAGE-STEP.zh.md` 建新 controller、重做 64 MiB 完整 CRC、六轮新校准与正式单元；本轮维护窗口不计入 C1 正式样本。
4. `meta_url` 的 database 仍是 **61**，且已由第 18 次的 gc 明文确认是原 C1 卷（data 指向 B RustFS 桶、8 个 slice / 70.9 GB 活数据）。

本轮不改变任何已验收结果，也不把维护窗口计成 C1 正式样本。B 池三台现已全部回到 `Stopped/StopCharging`。
