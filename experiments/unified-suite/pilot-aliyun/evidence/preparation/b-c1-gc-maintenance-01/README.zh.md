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

## 下一步的前置

1. 需要一次只读诊断窗口：只启动 metadata，确认实际在跑的 redis-server 用的是哪份 conf、`INFO persistence` 为什么报 `aof_enabled:0`，以及 `appendonlydir` 里到底有哪些文件。结论出来前不要启动 objects，也不要跑 gc。
2. 该结论出来之前不运行 `gc --delete`，也不手工清理盘 0；盘 0 上 3.06 GB 的存量与其它三盘 22 GB 的空闲都还没有被证明可回收。
3. `meta_url` 的 database 仍按交接记录用 **61**；本次没有取得原 C1 asset 的 `metadata_url` 直接证据（asset 在已停机的 B client 上），因此 gc 即使打通也要先核对卷身份再考虑删除。
4. 若诊断确认原 Redis 的 AOF 已关闭或已丢，C1 混合读写的元数据前提本身就要重新评估——这已经不是「清空间」的问题，需要回到原 C1 asset 重建路径。

本轮不改变任何已验收结果，也不把维护窗口计成 C1 正式样本。B 池三台现已全部回到 `Stopped/StopCharging`。
