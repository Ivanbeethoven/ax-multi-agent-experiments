# 实验脚本的踩坑清单（fio / trace 回放 / workspace fork / Redis 元数据）

这份文档记录的是 **2026-11-07 至 11-08** 在阿里云三机环境（client / TiKV+PD / RustFS）上，
做 BrewFS 与 JuiceFS 对比实验时**真实踩过并已修复**的问题。每条都对应一个会给出错误数字的陷阱，
以及 `scripts/` 下相应脚本里的处理方式。

配套脚本：

| 脚本 | 用途 |
|---|---|
| [`bench_fio_matrix.py`](bench_fio_matrix.py) | fio 矩阵（write/randwrite/randrw/coldread），两系统交替，含磁盘护栏 |
| [`object_store_ctl.sh`](object_store_ctl.sh) | 安全重启对象存储（冷读前置条件） |
| [`bench_workspace_fork.py`](bench_workspace_fork.py) | overlay workspace fork 成本 + 挂载到可用延迟 |
| [`check_trace_xattr.py`](check_trace_xattr.py) | 回放前置检查：trace 是否含 xattr 事件 |
| [`bench_redis_meta.py`](bench_redis_meta.py) | Redis 元数据后端的写入吞吐与元数据内存 |

---

## 1. fio：新建文件与覆盖写的带宽差 2× 以上

**现象**：同一挂载点，「写 1 GiB」第一次 168 MiB/s、第二次 386 MiB/s，差一倍多。

**原因**：第二次是覆盖写，对象存储/元数据路径都命中已有对象；新建文件要额外分配 inode、
写目录项、首次 PUT。

**结论**：论文里的 write 数字必须声明是 create-new 还是 overwrite。若一个 cell 复用了上一次的
文件，得到的是 overwrite 数字。

**处理**：`bench_fio_matrix.py` 每个 cell 用独立文件名（`<cell>.dat`），跑完即删。

实测（1 MiB block，1 GiB，3 轮中位数，MiB/s）：

| 系统 | create-new | overwrite |
|---|---:|---:|
| JuiceFS | 168.5 | 385.8 |
| BrewFS | 257.5 | 382.7 |

> 注意：create-new 时 BrewFS 反而更快（257 vs 168），overwrite 时两者接近。
> 如果只看 overwrite，会得出「BrewFS 写更慢」的相反结论。

## 2. fio 3.36：`--percentile_list` 只认冒号

**现象**：`--percentile_list=50,95,99` 时 JSON 里只有 `50th`，p95/p99 永远拿不到。

**验证**：

```
--percentile_list=50,95,99  ->  |  50th=[ 5211]
--percentile_list=50:95:99  ->  |  50th=[   69],  95th=[  171],  99th=[  192]
```

**处理**：脚本统一用 `50:95:99`，并且从 `job[<direction>].clat_ns.percentile` 取值
（`lat_ns` 只有 min/max/mean/stddev/N，没有 percentile）。

## 3. 冷读必须重启对象存储，`drop_caches` 不够

**现象**：`echo 3 > /proc/sys/vm/drop_caches` 之后重读，带宽仍有 2 GB/s，明显还是热的。

**原因**：RustFS 进程内部有页缓存/对象缓存，不受内核 drop_caches 影响。

**处理**：每次冷读 cell 前重启对象存储进程，并**确认 health=200 再读**。
`object_store_ctl.sh` 把这个流程参数化。

## 4. 重启对象存储不能用 `pkill -f <name>`

**现象**：执行 `ssh host 'pkill -f rustfs; ... nohup rustfs ...'` 之后服务没起来，
日志里是 `Connection refused`。

**原因**：`pkill -f rustfs` 会匹配到这条 ssh 命令**自身**（命令行里含 "rustfs"），
把执行者自己杀掉，后面的启动命令根本没执行。

**处理**：用 `pkill -x rustfs`（精确进程名），或 `pkill -f '/srv/bin/rustfs'`（完整路径，但要注意
命令里不要出现同一路径）。

## 5. 并行 lane 会把吞吐打崩

**现象**：JuiceFS 单跑 603 MiB/s，同时开第二个 lane 后掉到 14.3 MiB/s。

**结论**：单对象存储 + 单 TiKV 节点时，所有对比必须**串行**执行；交替是为了消除漂移，
不是并行。

## 6. 对象盘写满会产出「假数据」

**现象**：JuiceFS 显示 14.3 MiB/s、fsync 18 分钟、FUSE 挂载死亡；BrewFS 报告 2,174 MiB/s 但只花 0.4 s
（实际没写进去）。

**处理**：每个 cell 前检查对象盘剩余空间，低于阈值直接 abort（脚本默认 80 GB）。
另外 JuiceFS 的 `TrashDays=1` 会阻止空间回收，需要 `juicefs config --trash-days 0` + `juicefs gc --delete`。

## 7. BrewFS 不能用 `pkill -9` 停挂载

**现象**：强杀 BrewFS 挂载进程后，重启挂载写入极快但读到 EIO。

**原因**：未 flush 的写缓存丢失，元数据与数据不一致。

**处理**：必须 `umount` 优雅卸载；实在要强杀，之后按需重建卷。

## 8. trace 回放会被 xattr 卡死（BrewFS TiKV 元数据后端）

**现象**：

```
Error: "mismatch at 203: \"getxattr\" Err(Os { code: 95, kind: Unsupported })"
```

**原因**：trace 里 `getxattr("security.capability")` 记录的是 `ENODATA(61)`（属性不存在），
而 BrewFS 的 TiKV MetaStore 没有实现 xattr，返回 `EOPNOTSUPP(95)`。回放是严格比对，直接中止。

**规模**：pytest trace 有 22,881 行 xattr 事件、django 42,091、rust 693,861
⇒ 任何含 xattr 的 trace 都无法在 TiKV 元数据后端的 flat 卷上回放。

**处理**：回放前先跑 `check_trace_xattr.py`；对 BrewFS 要么用实现了 xattr 的元数据后端，
要么在论文中作为 limitation 说明。

## 9. workspace fork 的 revision 字符串没有文档

**现象**：`brewfs workspace fork <revision>` 报
`corrupt workspace metadata: revision sealed version is missing` / `invalid revision layer id`。

**原因**：revision 是三元组 `<layer_id>:<sealed_version>:<root_hash_hex>`，`--help` 里没写。

**处理**：`bench_workspace_fork.py` 从 `workspace list` 的 JSON 里解析出三元组再调用。

另外两点：

- `fork` 只接受**已封存**的 revision；对 live workspace 派生会得到一个空 overlay。
- `mount` 不读 YAML 的 `meta:` 段（会回落 `sqlite::memory:`），连接参数必须走 CLI。

## 10. Redis 元数据后端的三个坑

1. **安全组**：客户端连另一台机器的 `6379` 可能被拒（`Connection refused`），即使同 VPC。
2. **`--s3-force-path-style` 要带值**（`true`/`false`），写成裸 flag 会让 clap 把挂载点当值。
3. **EIO 不一定是 Redis 的错**：对象存储没起时，客户端只看到 `EIO`，日志里才有
   `pipeline upload failed ... Connection refused`。先起对象存储再写。

实测（LocalFS 元数据 vs TiKV）：

| 后端 | 建 10,000 × 4 KiB 文件 | 元数据占用 |
|---|---:|---:|
| Redis 7.0.15 | 54.7 s（183 文件/s） | 7.90 MB（30,359 keys） |
| TiKV（1M 规模折算） | 420–550 文件/s | 未单独测量 |

## 11. 编译验证：`strings` 是唯一可信的判据

**现象**：patch 已经 `patch -p1` 成功，但编出来的二进制行为没变。

**原因**：两个 cargo 抢同一个 `target` 目录锁时，可能是先启动的那个（旧源码）在编译；
后启动的等锁结束后发现「已是最新」，直接返回，于是产出的还是旧代码。

**判据**：拿补丁里独有的字符串去二进制里 grep：

```
strings target/release/brewfs | grep -c 'invalid setxattr flags'   # 必须 > 0
```

**其它编译经验**：

- `CARGO_PROFILE_RELEASE_CODEGEN_UNITS` 一改，所有依赖失效重编（本项目 451+ crate，>1.5 小时）。
- 16 vCPU / 61 GB 机器上 `-j8` 编 brewfs release 峰值 40–63 GB，可能拖死 sshd；
  建议先加 64 GB swap。
