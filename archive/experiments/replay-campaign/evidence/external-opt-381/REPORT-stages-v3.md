# 四臂六段复测 v3

本轮完全复用指定仪器化二进制，未运行 Rust/Go 编译、未用 sqlite、未用 JuiceFS writeback、未开 PR。原始产物、命令、累计快照和差分见 `/work/stage-v3/`；`/work/stage_bench.sh` 已指向 v3 驱动 `/work/bench_v3.py`，native fixture 构造为 `/work/native_lower_v3.py`，分析脚本为 `/work/analyze_v3.py`。

结果有效范围：native 的全新 upper 已完整读回并校验 2 GiB，lower 数据与元数据在测量前后保持一致。packed **在 8×60 s 内未达稳态**；仍完整完成 W 三次和 C 一次，W 是该预热预算之后的测量，不能称为稳态。

## 方法与资产

按 flat → native → packed → JuiceFS 串行运行，每臂构造、预热、W 3×30 s、C 1×30 s 后卸载；brewfs 与 JuiceFS 的开销对照分别在 flat 与 JuiceFS 臂内完成。跨臂开始使用新客户端进程和独立缓存目录，并清页缓存。语料为四个 536,870,912 B 文件，总计 2,147,483,648 B。fio 为 4 KiB randread、4 jobs、libaio、iodepth=32、direct=1、`filename_format=r.$jobnum`、固定种子 20261029、group_reporting。每个非 packed 预热轮完整随机读 2 GiB。W 轮间不清页缓存；C 在 `sync; echo 3 > /proc/sys/vm/drop_caches` 后立即启动，**仅清内核页缓存，不清客户端内存/本地块缓存和 RustFS 进程缓存**。

Redis：flat=db2、native=db0、packed=db1、JuiceFS=db3；RustFS 容器 `rustfs-stage-timing` / 127.0.0.1:9000。S3 桶为 `stage-v3-{flat,native,packed,juicefs}`。旧 DB2/3 数据在重置前保存了 DUMP 备份；native/packed 使用全新独立命名空间。brewfs：4 GiB 读 RAM 预算、SSD 0、prefetch 关闭、raw/none；块 4 MiB、chunk 64 MiB，FUSE workers=16/max_background=512。JuiceFS：4 GiB 块缓存、4 GiB buffer，块 4 MiB、chunk 64 MiB，元数据/预读保持默认，关闭备份与后台清理；完整 mount argv 在各臂目录。两者缓存策略和实现不同，不能将同样的配置预算理解成相同的实际缓存布局。

二进制 SHA-256（开始/结束复核相同，字节数 319,834,408 / 126,293,296 B）：

```text
3896fb19a7bce5d61b45b74b05bc6839eed69721f0902726c2bdf0f60d0f73a9  /work/brewfs/target/release/brewfs
b1dc2b15edfcc847531919a619ae96cd348b437b58b9f0e53db46961e769c1ab  /work/brewfs/target/release/packed_v3_snapshot_fixture
c1fa734a5bf7a3ad330472f8f2f2845ad85de82ead6a745efc4a7cbdf1163285  /work/juicefs-src/juicefs
```

## 四臂 × 六段：W 三次中位

每轮先对累计快照做差，再以该轮 `fuse_dispatch.count` 归一为 µs/FUSE read；百分比为该轮 `stage.sum_us / fuse_dispatch.sum_us`，两者分别取三轮中位。所有占比使用同一个 `fuse_dispatch` 分母。**span 可嵌套、可并发，不可相加**，没有把残差伪称为锁/背压。`fuse_dispatch` 是 read handler wall span，不包含 handler 之前的内核/FUSE 排队；它不同于 fio clat。

| 臂 | fuse_dispatch µs / % | resolve µs / % | row_cache µs / % | redis_meta µs / % | extent_plan µs / % | data_path µs / % |
|---|---:|---:|---:|---:|---:|---:|
| flat | 9.7287 / 100.000% | 0.0000 / 0.000% | 0.0000 / 0.000% | 0.0035 / 0.026% | 0.0000 / 0.000% | 4.4425 / 45.664% |
| native | 12.1368 / 100.000% | 0.5494 / 4.287% | 0.1024 / 0.844% | 0.0258 / 0.209% | 1.5999 / 12.940% | 5.0372 / 41.503% |
| packed | 110273.8573 / 100.000% | 7.2171 / 0.007% | 6.6315 / 0.006% | 17624.9234 / 15.973% | 28115.1985 / 25.634% | 81541.7684 / 74.286% |
| juicefs | 68.1463 / 100.000% | 0.0005 / 0.001% | 0.0022 / 0.003% | 0.0301 / 0.044% | 3.9704 / 5.826% | 86.1933 / 126.516% |

flat 不经过 workspace resolve/row_cache/extent_plan 钩子，这三列的零表示对应探针未执行；不能据此断言 flat 所有等价处理的成本为零。Redis span 只覆盖已埋点的 API 调用。JuiceFS 的 resolve 是 Lookup，row_cache 为 open/chunk cache，extent_plan 为 prepareRequests，data_path 包含并发 slice/block cache/object 读取；其边界与 brewfs 不完全相同。微秒整数累计会把单个 <1 µs 的 span 截为 0。

## 四臂端到端：W 与 C

每格为 min / median / max；吞吐 MiB/s，p99 和 mean clat 为 ms。W n=3，C n=1，故 C 的三个统计值相同；p99 为 fio group_reporting 汇总直方图的 99 分位。

| 臂 | W 吞吐 | W p99 | W mean clat | C 吞吐 | C p99 | C mean clat |
|---|---:|---:|---:|---:|---:|---:|
| flat | 79.260 / 214.263 / 224.334 | 2.540 / 4.293 / 27.656 | 2.160 / 2.262 / 6.112 | 73.110 / 73.110 / 73.110 | 80.216 / 80.216 / 80.216 | 6.618 / 6.618 / 6.618 |
| native | 51.675 / 188.630 / 194.269 | 2.966 / 5.079 / 27.132 | 2.494 / 2.569 / 9.374 | 102.223 / 102.223 / 102.223 | 52.691 / 52.691 / 52.691 | 4.734 / 4.734 / 4.734 |
| packed | 0.139003 / 0.139156 / 0.139332 | 3909.091 / 3942.646 / 3976.200 | 3269.814 / 3269.938 / 3271.675 | 0.129927 / 0.129927 / 0.129927 | 4798.284 / 4798.284 / 4798.284 | 3461.064 / 3461.064 / 3461.064 |
| juicefs | 145.481 / 147.311 / 147.806 | 7.504 / 7.832 / 8.028 | 3.279 / 3.290 / 3.331 | 114.244 / 114.244 / 114.244 | 33.817 / 33.817 / 33.817 | 4.239 / 4.239 / 4.239 |

| 臂 | W1 / W2 / W3 MiB/s | W1 / W2 / W3 对象 GET |
|---|---:|---:|
| flat | 224.334 / 214.263 / 79.260 | 0 / 266 / 25691 |
| native | 194.269 / 188.630 / 51.675 | 0 / 474 / 29597 |
| packed | 0.139156 / 0.139003 / 0.139332 | 3216 / 3213 / 3219 |
| juicefs | 147.806 / 145.481 / 147.311 | 0 / 0 / 0 |

flat 的 W 吞吐并非全程稳定：W1/W2/W3 的对象 GET 为 0/266/25691，客户端缓存未命中在后续 W 轮重新出现；未过滤慢轮，表中保留全部三次。预热相邻差通过只能说明当时两个预热轮接近，不保证后续所有 30 s 均保持稳态。原始主机 CPU/load/memory/disk 计数保存在每轮 `host-*-before/after.txt`，没有同时运行其他臂或编译器。

## 每臂预热曲线

相邻差 = |本遍吞吐−上遍吞吐| / 上遍吞吐；只比较相邻遍。packed 每遍为 60 s；其余每遍完整随机读 2 GiB。每遍原始 fio/stage/Redis/GET 均保留。

| 臂 | 遍 | MiB/s | 相邻差 | fio 墙钟 s | 读取 MiB |
|---|---:|---:|---:|---:|---:|
| flat | 1 | 42.6676 | — | 48.414 | 2048.000 |
| flat | 2 | 225.4017 | 428.27% | 9.392 | 2048.000 |
| flat | 3 | 219.8132 | 2.48% | 9.623 | 2048.000 |
| native | 1 | 41.4323 | — | 49.822 | 2048.000 |
| native | 2 | 201.6939 | 386.80% | 10.449 | 2048.000 |
| native | 3 | 191.6526 | 4.98% | 10.991 | 2048.000 |
| packed | 1 | 0.1337 | — | 60.480 | 8.035 |
| packed | 2 | 0.1388 | 3.76% | 60.408 | 8.340 |
| packed | 3 | 0.1382 | 0.38% | 60.377 | 8.305 |
| packed | 4 | 0.1384 | 0.12% | 60.388 | 8.316 |
| packed | 5 | 0.1392 | 0.58% | 60.376 | 8.363 |
| packed | 6 | 0.1391 | 0.10% | 60.410 | 8.359 |
| packed | 7 | 0.1388 | 0.19% | 60.386 | 8.340 |
| packed | 8 | 0.1388 | 0.01% | 60.431 | 8.344 |
| juicefs | 1 | 60.4308 | — | 34.265 | 2048.000 |
| juicefs | 2 | 147.4973 | 144.08% | 14.180 | 2048.000 |
| juicefs | 3 | 148.9563 | 0.99% | 14.052 | 2048.000 |

| 臂 | 判据通过 | 遍数 | 总预热墙钟 s（含快照/抓包开销） | 总读取 B |
|---|---|---:|---:|---:|
| flat | 是 | 3 | 68.284 | 6,442,450,944 |
| native | 是 | 3 | 72.128 | 6,442,450,944 |
| packed | 否 | 8 | 485.391 | 69,627,904 |
| juicefs | 是 | 3 | 63.340 | 6,442,450,944 |

## packed 冷启动成本

从全新 packed 测量挂载、清页缓存后开始计时：预热共 **485.391 s 墙钟**，fio 自身墙钟累计 483.256 s，读取 **69,627,904 B（66.402 MiB）**。吞吐序列为 0.1337 → 0.1388 → 0.1382 → 0.1384 → 0.1392 → 0.1391 → 0.1388 → 0.1388 MiB/s。

**packed 在 8×60 s 内未达稳态。** 达到稳态所需时间和字节数未被本次窗口测出；只能给出“超过上述墙钟、已读上述字节仍未达标”的观测下界，不能把预热预算写成已经达到稳态的成本。没有中途 SIGINT；预算结束后仍完成全部主测。**时间花在预热上**，完整 8 遍是结论的一部分。

另记构造耗时：packed fixture 生产/上传 85.519 s，未混入上面的预热墙钟。测量的冷启动时间不包括 fixture 构造和 CLI mount 建立。raw codec、inline-data=off，保留既有外部数据对象读取路径。

预算之后的 W 仍有 **3.0000 GET/读**；已观测 data_path 占 fuse_dispatch 74.29%，redis_meta 占 15.97%（span 可嵌套）。低吞吐持续存在；本轮能量出未达热态的预热预算成本，尚不能把一次性冷启动成本与每读持续对象/元数据成本完全分离。

## native lower 构造与三条验收

现有 CLI 未导出通用 native seal 操作，因此仅在未挂载的独立测试命名空间，使用 Python fixture 把已通过现有 brewfs 写入的 upper 数据/extent/ownership 元数据 compact 到初始 sealed root，并清空 seed upper。fixture 按 `model.rs` 的 bincode 编码和 `digest.rs` 的规范 BLAKE3 摘要更新 sealed revision、索引与 generation；原始记录留有备份。先验证规范编码器计算的初始 root digest 与二进制实际结果完全一致，再进行构造。**这是离线测试 fixture，不声称验证了生产 seal 生命周期。** 随后调用现有 CLI `workspace create --from <精确 revision>` 创建全新 upper，正式测量只挂载该新 workspace。

- sealed lower：`01a186af-dad3-7ec2-bc12-34ed18796c2a`；全新 upper：`01a186b1-42a9-78b1-b924-199da915b864`；新 workspace：`01a186b1-42a9-78b1-b924-198d56c31420`。
- 自检 1：清空 seed upper 后，新 upper 的 Data/Hole extent 行数为 **0**；在新 upper 上完整读回 **2,147,483,648 B**，四文件 SHA-256 与源完全一致；另外每文件 6 处 4 KiB 抽样一致，详情在 `native-acceptance.json`。
- 自检 2：lower **2,147,485,696 B**（2.000001907 GiB，64 slices / 512 个块对象，含每对象 4 B 头）；upper 数据对象 **0 B**、0 slices。lower 逻辑 payload 2,147,483,648 B；对象列表逐项保存。
- 自检 3：读回验收的 `resolve.count` 增量 **2,637**，`data_path.count` 增量 **2,584**；W 三次 `resolve` 增量为 1492051/1448788/396907。新 upper 没有数据 extent，全部数据引用归属 sealed lower，完整读回和对象 GET 因此证明 lower 数据被实际读取。

测量后 upper 实际控制面统计：private_metadata_rows=0，private_bytes=0 B，shared_bytes=2147483648 B；lower 不可变记录/数据引用集合 SHA-256 为 `68240f3a6495172fb0b7f59133907c1eb9b11e4f384ff9d037ca6d8d1b1814ec`，前后完全一致。数据对象实际字节与 lower 的规范摘要、行数、所有文件哈希均见 `native-freeze.json`、`native-object-inventory.json`、`native-acceptance.json` 和 `native-lower-after-measurement.json`。

另计 Redis layer/delta 记录的实际元数据占用：lower 10,033 B 序列化值 / 36,512 B Redis MEMORY USAGE；upper 仅有 layer 记录 **130 B 序列化值 / 296 B Redis MEMORY USAGE**，没有数据 extent。该项不含 catalog/lease/index 的公共管理开销，也不把客户端共享对象缓存算作 upper 私有数据。

| 文件 | 完整读取 B | SHA-256（与源一致） |
|---|---:|---|
| r.0 | 536,870,912 | `5d27e16f3ae3acc0afe07a463a4e23a06ebe4a0bfe1f08442ef12345fac44e79` |
| r.1 | 536,870,912 | `b850055acdf9c32433f198aeb978034222ee3bc99b9538a5b3a27084a53383a7` |
| r.2 | 536,870,912 | `fdae0e8804dd59166f4562513174e7faef203e7791065a05d617f49e3e1c6ebc` |
| r.3 | 536,870,912 | `157becc495f489659180e06dce8bb07fc09e9638e75b00f5248b9bbf426b544b` |

## 仪器化开销：三组成对 30 s

同一二进制、卷、挂载点与缓存目录；顺序 off→on、on→off、off→on，每个实例先完整随机预热两遍，再计时 30 s。**无法满足“同一个持续存活的挂载内仅靠 env 切换”**：brewfs 使用 `OnceLock<bool>`，JuiceFS 使用包级 `enabled = timingEnabled()`，环境开关在进程启动时锁定，故本次必须重挂载。每次计时期间没有调试器或动态改写代码/开关。下面是同一挂载点的交替重挂载近似对照；不能冒充字面要求的同挂载严格因果证明。brewfs 使用 flat 代表臂，不能据此覆盖 native/packed 的额外 span 开销。

| 实现 / 组 | 执行顺序 | off MiB/s | on MiB/s | on 相对 off 吞吐变化 | 绝对差 <3% |
|---|---|---:|---:|---:|---|
| brewfs(flat) / 1 | off → on | 219.164 | 213.961 | -2.374% | 是 |
| brewfs(flat) / 2 | on → off | 217.989 | 218.214 | +0.103% | 是 |
| brewfs(flat) / 3 | off → on | 219.756 | 214.383 | -2.445% | 是 |
| JuiceFS / 1 | off → on | 145.430 | 145.875 | +0.306% | 是 |
| JuiceFS / 2 | on → off | 145.610 | 143.070 | -1.744% | 是 |
| JuiceFS / 3 | off → on | 146.532 | 144.994 | -1.050% | 是 |

brewfs(flat)：三组吞吐损失中位 +2.374%；三组绝对差均 <3%。

JuiceFS：三组吞吐损失中位 +1.050%；三组绝对差均 <3%。

开启探针每个 span 增加时钟读取、微秒累计与共享原子计数/最大值更新；brewfs 还会检查 OnceLock，JuiceFS 会查找 stage index 并调用 End/defer。热点共享计数可造成 CPU/cache-line 竞争；嵌套/并发路径会多次计数。关闭探针避免时钟和计数，但编译进程序的 disabled 检查仍存在。本次配对还包含重挂载后的客户端缓存演化与调度波动，若差异 >3% 或出现 on 反而更快，不能把差异全部归因于探针，也不能用平均抵消来宣布所有组达标。

## Redis 命令/读与对象 GET/读

下表均为 W 三轮比值的中位。分母为每轮 fio 已完成 4 KiB 读数（逐轮与 `fuse_dispatch.count` 一致）。Redis `commandstats` 属于整个实例，包含 Lua 内部命令和挂载后台维护；主行排除采集器的 INFO/SELECT，另列原始数和 EVALSHA，**不是网络往返数**。

| 指标 | flat | native | packed | JuiceFS |
|---|---:|---:|---:|---:|
| Redis 命令/读（排除 INFO/SELECT） | 9.05447574e-05 | 0.000532865075 | 2.7108209 | 0.000147111808 |
| Redis 命令/读（raw） | 9.11524403e-05 | 0.000533555314 | 2.71175373 | 0.000147992717 |
| EVALSHA/读 | 4.86146348e-06 | 4.55558224e-05 | 0.0820895522 | 3.53548031e-06 |
| 对象 GET/读 | 0.000161643661 | 0.000327173634 | 3 | 0 |

flat/native 的 GET 使用客户端 `brewfs_s3_get_ops_total` 差值；JuiceFS 使用对象 GET histogram count 差值；packed 使用覆盖独立 lower 对象客户端的 HTTP GET 请求抓包，按 TCP 端口/序列号去重，包含数据及读取时触发的索引 GET。native/packed/JuiceFS 抓包开启 immediate-mode，日志记录丢包数；flat 早期抓包有结束缓冲未落盘，故 flat 以完整客户端计数为准。每轮 GET 数、Redis 各命令计数与来源保存在 `analysis.json`，pcap 和 tcpdump 日志可复核。GET=0 只表示该测量窗口客户端未访问 S3，不代表构造或预热没有 GET。

## 完整性、耗时与未验证项

首轮资产核查 2026-10-29T07:43:50Z；分析完成 2026-10-29T08:38:49.147509+00:00；累计 **54.99 分钟**，在 150 分钟内。共校验 69 个完整 fio 阶段；packed 预热单独占用 485.391 秒墙钟，未将这段等待隐去。

全部 4×(3 W+1 C) 正式 fio 的 error=0，均完成指定计时窗口；正式 W/C 以及探针开启的 30 s 对照逐轮的 FUSE、fio 读数一致；测量窗口不重叠，臂顺序正确，所有抓包日志的 kernel drops=0。JuiceFS 三个 on 实例的第一个开销预热轮，各多 3 次 supervisor 启动时对 `.config` 的 FUSE 读取；这些原始 span 没有删改，不参与 W 六段归因或开销计时样本。没有 SIGINT 中止后采用残缺样本。Redis INFO 解码、native ownership inventory 和 JuiceFS supervisor 子进程识别的脚本兼容修正均发生在各自正式测量前；设置失败日志保留于 `runner-*.log`，没有残缺 fio 被计入。开始/结束二进制 SHA 和 stat 保存在 `binaries-*.sha256`、`assets-*.txt`；最终机器卸载状态见 `final-state.txt`。

- 未验证同一持续挂载的 env-off/env-on 对照；两份现有二进制启动时锁定开关，字面要求需要支持运行期切换，受本轮禁止重编约束无法补齐。三组交替重挂载近似对照均 <3%，但不能据此证明同一活挂载或其他路径的严格开销上限。
- 未验证生产 native seal/快照发布协议；native lower 使用已校验的离线测试 fixture，实际读取、对象占用和不可变性验收均通过。
- packed 达到真正稳态所需的完整时间/字节数仍未测出；8×60 s 已按要求跑满，随后 W/C 完整数据有效，但不是稳态证明。
- C 只有一次，min/median/max 无法估计跨次离散程度；它只代表清页缓存参考，不代表客户端/对象存储全冷。
- 未对各实现做完全相同的缓存策略、所有等价路径埋点、物理磁盘读取数、独立锁/背压或 handler 之前排队的验证；span 结果不能当互斥耗时分解。
- 不能用本地 RustFS 单机结果外推远端对象存储、不同缓存预算或生产工作集；系统常驻监控进程未关闭。

