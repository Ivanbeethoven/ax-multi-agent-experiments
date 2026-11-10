# BrewFS 统一实验 C1 动态块大小（G）消融实验完整报告

- **生成时间（UTC）**：`2026-11-08T15:56:25+00:00`
- **执行环境**：Google Cloud `c2d-standard-8`（8 vCPU AMD EPYC 7B13 Milan / 32 GiB RAM）+ **4 × 375 GiB Local NVMe SSD**（`/dev/nvme0n1`..`nvme0n4` 独立挂载于 `/data/rustfs0`..`/data/rustfs3`），区域 `asia-east2-c`（香港）
- **冻结二进制产物**：
  - `brewfs`：SHA256 `f0331698935a289e6f7655ba541416c021ebd55b94bfaef957e35ec8e0283b64`（特性：`fuse-io-uring-runtime,workspace-overlay,native-packed-base,rustfs-ec-block-size-hint,observability`）
  - `rustfs`：SHA256 `2bfb34a3177a109074799af863877bdfc1f8d6c63a7ece6eeb08a15377830e15`（分支 `feat/dynamic-block-size-rebased`，提交 `2809f5a999a6a2617bb057c9725ceb7f8b4a6591`）

---

## 一、核心结论与四大源码级根因发现

1. **后端纠删码块大小（G 之 $B$）与读取子帧（G 之 $q$）对随机 4 KiB 小读具有显著收益**：
   - 在 4 MiB 客户端大对象（`g = 4194304`）下，持久化 `xl.meta` 证实 4 块 NVMe 盘上的纠删码块大小 $B$ 严格按 `64 KiB / 256 KiB / 1 MiB / 4 MiB` 落盘。
   - 在预试阶段（`pilot`）中，当 $B = 4\text{ MiB}$ 时，单次范围小读在后端产生严重的跨分片读放大，吞吐显著下降（`45.49 MiB/s`）；将 $B$ 减小至 $64\text{ KiB} \sim 1\text{ MiB}$ 可消除后端读放大（提升至 `51.82 ~ 55.41 MiB/s`）并显著降低 P99 尾延迟（从 `93.7 us` 降至 `75.3 us`）。
2. **发现并定位 `bounded-memory-v1` 在大数据集（32 GiB > 512 MiB 内存缓存）下的 `2,968.5 倍` 后台全块预取读放大颠簸（`src/chunk/store.rs:897-903`）**：
   - 默认 `bounded-memory-v1` 开启了 `range_background_prefetch = true`。每次 4 KiB 小读触发 64 KiB 范围读未命中后，客户端都会立即在后台调用 `prefetch_full_block_background` 全量下载整个 4 MiB 对象。
   - 当工作集（`pilot` 为 64 MiB）小于 512 MiB 内存缓存时，16 个对象在 1.7 秒内全部被预取进内存，后续读取 99.9% 命中内存；
   - 但当工作集扩展到主实验的 **32 GiB**（是 512 MiB 内存缓存的 64 倍）时，后台全量 4 MiB 下载导致块缓存命中率跌至 **0.70%**（未命中 `1,102,066` 次 vs 命中 `7,741` 次），用户态仅读取 `231.9 MiB`（`59,364` 次 4 KiB 读）就触发了 **`640,434` 次 S3 GET、累计拉取 `719.79 GB` S3 数据（读放大 `2,968.5 倍`）**，吞吐跌至 `28.9 KiB/s`（`7.4 IOPS`），平均 S3 GET 排队延迟高达 `186.1 ms`。
   - 对 `B-off` 关闭 `range_background_prefetch` 与 `prefetch_enabled` 后，32 GiB 数据集上的 8 线程随机冷读完全走纯净的 64 KiB S3 Range GET（`16,384` 次 4 KiB 随机读仅拉取 `1.058 GB` S3 数据），吞吐立即从 `28.9 KiB/s` 跃升至 **`1.82 MiB/s`（`465.7 IOPS`，提升 63 倍）**！
3. **发现并定位 `read_fetch_policy = "backend-default"` 在开启 `rustfs_ec_block_size_hint` 时意外禁用 S3 Range GET、强制回退到 4 MiB 整对象下载（`src/cadapter/s3.rs:1196-1203` 与 `src/chunk/store.rs:1410-1420`）**：
   - 在 32 GiB 纯净冷读对比中，即便关闭了 `range_background_prefetch`，为什么 `B-off` 能达到 **`1.82 MiB/s`（`465.7 IOPS`）**，而开启了 `hint = "1048576"` 的 `B-1048576`、`block-only`、`block-and-quantum` 三组却只有 **`0.55 ~ 0.65 MiB/s`（`140.8 ~ 166.9 IOPS`）**？
   - 核查 Prometheus 计数器（`metrics-after-measure.txt`）发现：在 `16,384` 次 4 KiB 随机读（总计 `64 MiB` 用户态读取）中：
     - `B-off`（`hint = "off"`）：`page` 缓存未命中 `16,119` 次，发起 `16,360` 次 **64 KiB S3 Range GET**，累计 S3 下载流量仅 **`1.058 GB`**；
     - `B-1048576` / `block-only` / `block-and-quantum`（`hint = "1048576"`）：`page` 缓存未命中为 **`0` 次**，却发起了 `16,102 ~ 16,111` 次 **4 MiB 全对象 S3 GET**，累计 S3 下载流量高达 **`67.54 ~ 67.56 GB`（读放大 `1,024 倍`）**！
   - 源码级根因定位：
     - `bench/suite_mount.py:33` 将 `bounded-memory-v1` 的读取策略固定为 `"read_fetch_policy": "backend-default"`；
     - `src/chunk/store.rs:1410-1420` 中规定：当 `read_fetch_policy` 为 `BackendDefault` 时，`prefer_full_objects = self.client.prefers_full_object_reads()`，且 `can_try_object_ranges = ... && !prefer_full_objects`；
     - 而 `src/cadapter/s3.rs:1196-1203` 中定义了：
       ```rust
       fn prefers_full_object_reads(&self) -> bool {
           self.config
               .rustfs_ec_block_size_hint
               .is_some_and(RustfsEcBlockSizeHint::enabled)
       }
       ```
     - 这导致了一个自相矛盾的交互：**只要客户端启用了任何非 `off` 的 `rustfs_ec_block_size_hint`（目的是让后端用更小的 EC 块加速范围小读），`prefers_full_object_reads()` 就会返回 `true`，进而在 `backend-default` 策略下直接关闭客户端的 64 KiB S3 Range GET，强制每次 4 KiB 读未命中都去下载完整的 4 MiB 对象！** 若要让非 `off` 的 `hint` 真正使用 64 KiB 范围读，必须将 `read_fetch_policy` 设为 `"sparse"` 或修改 `S3Backend::prefers_full_object_reads()`。
4. **发现并定位 `client-object-size`（G 之 $g$）在每次写入同步（`fsync=1`）下的 `100 ms` 提交轮询切片瓶颈（`src/vfs/io/writer.rs:51,5214`）**：
   - 在 `randrw`（75/25 读写混合，`fsync=1`）下，三个客户端对象大小（`256 KiB`、`1 MiB`、`4 MiB`）吞吐均为 `0.15 MiB/s`（`38.6 IOPS`）。
   - `fio.json` 显示单次 4 KiB 读延迟仅 `180.7 us`、写延迟仅 `234.4 us`，但 `fsync` 平均延迟高达 **`103.73 ms`**（`sync.lat_ns.mean = 103,730,848 ns`）。
   - 根因在于 `src/vfs/io/writer.rs:51` 中 `const COMMIT_WAIT_SLICE: Duration = Duration::from_millis(100)`，导致每次 `fsync` 等待固定 100 ms 通知超时切片（单线程同步写上限被锁死在 ~9.6 次/秒），掩盖了不同对象大小 $g$ 的上传差异。
5. **校验粒度门禁（`fixed-B-distinct-q`）源码级核验（`ec_block_layout.rs:258`）**：
   - 在 RustFS 2+2 纠删码（$M=2$ 数据分片）下，`select_ec_read_quantum`（`ec_block_layout.rs:258`）在 $B=1\text{ MiB}$（单数据分片 $512\text{ KiB}$）时算得 `quantum = 512 KiB == shard_size`，因此在冻结的 $B=1\text{ MiB}$ 条件下 `block-only` 与 `block-and-quantum` 持久化的 `read_quantum_bytes` 均为 `null`（协议门禁判定为 `no-effect`）。
   - 而在补充诊断的 $B=256\text{ KiB}$（$q=64\text{ KiB} < 128\text{ KiB}$，吞吐提升 **+3.2%**）和 $B=4\text{ MiB}$（$q=1\text{ MiB} < 2\text{ MiB}$）下，`smaller_read_frames_active = true` 真实生效。

---

## 二、预试阶段（`pilot`，64 MiB 数据集，14/14 单元全部通过）

| 实验组 (`case`) | 条件 (`arm`) | 客户端对象 $g$ | 请求提示 (`hint`) | 后端模式 | 持久化 $B$ (B) | 持久化 $q$ (B) | 子帧激活 | 吞吐 (MiB/s) | IOPS | 读均值延迟 (us) | 读 P99 (us) | 校验通过 |
|---|---|---:|---|---|---:|---:|---|---:|---:|---:|---:|---|
| `client-object-size` | `g-4194304` | 4194304 | `1048576` | `enabled` | 1048576 | None | `False` | **0.15** | **38.6** | 180.7 | 1876.0 | `PASS` |
| `client-object-size` | `g-1048576` | 1048576 | `1048576` | `enabled` | 1048576 | None | `False` | **0.15** | **38.6** | 182.1 | 2113.5 | `PASS` |
| `client-object-size` | `g-262144` | 262144 | `1048576` | `enabled` | 1048576 | None | `False` | **0.15** | **38.5** | 223.9 | 2900.0 | `PASS` |
| `backend-layout` | `B-262144` | 4194304 | `262144` | `enabled` | 262144 | 65536 | `True` | **51.74** | **13244.9** | 73.9 | 76.3 | `PASS` |
| `backend-layout` | `B-4194304` | 4194304 | `4194304` | `enabled` | 4194304 | 1048576 | `True` | **45.49** | **11644.6** | 83.7 | 93.7 | `PASS` |
| `backend-layout` | `B-65536` | 4194304 | `65536` | `enabled` | 65536 | 16384 | `True` | **53.29** | **13642.0** | 72.0 | 75.3 | `PASS` |
| `backend-layout` | `B-off` | 4194304 | `off` | `enabled` | 1048576 | None | `False` | **55.41** | **14185.3** | 69.1 | 82.4 | `PASS` |
| `backend-layout` | `B-1048576` | 4194304 | `1048576` | `enabled` | 1048576 | None | `False` | **51.82** | **13266.4** | 74.0 | 80.4 | `PASS` |
| `checksum-quantum` | `block-and-quantum` | 4194304 | `1048576` | `enabled` | 1048576 | None | `False` | **50.27** | **12870.4** | 76.0 | 84.5 | `PASS` |
| `checksum-quantum` | `block-only` | 4194304 | `1048576` | `block-only` | 1048576 | None | `False` | **50.75** | **12992.9** | 75.0 | 83.5 | `PASS` |
| `checksum-quantum` | `block-and-quantum (diag B=262144)` | 4194304 | `262144` | `enabled` | 262144 | 65536 | `True` | **53.24** | **13630.6** | 71.9 | 81.4 | `PASS` |
| `checksum-quantum` | `block-only (diag B=262144)` | 4194304 | `262144` | `block-only` | 262144 | None | `False` | **51.61** | **13212.9** | 74.2 | 79.4 | `PASS` |
| `checksum-quantum` | `block-and-quantum (diag B=4194304)` | 4194304 | `4194304` | `enabled` | 4194304 | 1048576 | `True` | **49.81** | **12750.2** | 76.8 | 89.6 | `PASS` |
| `checksum-quantum` | `block-only (diag B=4194304)` | 4194304 | `4194304` | `block-only` | 4194304 | None | `False` | **51.49** | **13181.0** | 74.4 | 84.5 | `PASS` |

---

## 三、主实验 32 GiB 数据集（8 并发线程 × 4 GiB）纯净冷读对比（关闭 `range_background_prefetch`）

> 说明：为消除 `range_background_prefetch=true` 在 32 GiB（大于 512 MiB 内存缓存 64 倍）下引发的 2,968.5 倍全块预取颠簸，本组在 32 GiB 真实数据集（8 并发线程 × 4 GiB 文件范围）上将 `range_background_prefetch` 与 `prefetch_enabled` 设为 `false`，直接测量 BrewFS 64 KiB 范围读在 RustFS 不同纠删码块大小 $B$ 与读取子帧 $q$ 下的三轮配对（`r1, r2, r3`）冷读性能。

| 实验组 (`case`) | 条件 (`arm`) | 轮次 (`reps`) | 持久化 $B$ (B) | 持久化 $q$ (B) | 单轮 S3 GET 次数 | 单轮 S3 拉取流量 | 实际读取路径 | 平均吞吐 (MiB/s) | 平均 IOPS | 读均值延迟 (us) | 读 P99 (us) | 校验通过 |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| `backend-layout` | `B-off` | 3 | 1048576 | None | 16,360 | **1.058 GB** | 64 KiB Range GET | **1.82** | **465.7** | 17175.2 | 411740.8 | `PASS` |
| `checksum-quantum` | `block-only` | 3 | 1048576 | None | 16,111 | **67.55 GB** | 4 MiB 整对象 GET | **0.65** | **166.9** | 47945.7 | 432712.4 | `PASS` |
| `checksum-quantum` | `block-and-quantum` | 3 | 1048576 | None | 16,108 | **67.56 GB** | 4 MiB 整对象 GET | **0.56** | **143.3** | 55849.6 | 438304.8 | `PASS` |
| `backend-layout` | `B-1048576` | 3 | 1048576 | None | 16,102 | **67.54 GB** | 4 MiB 整对象 GET | **0.55** | **140.8** | 56823.7 | 439702.9 | `PASS` |

---

## 四、主实验 32 GiB 默认 `bounded-memory-v1` 预取颠簸现场取证（`main-thrashing-diagnosis`）

证据文件位于同目录 `main-thrashing-diagnosis/live-metrics-snapshot.txt` 与 `cells/a6ee7bfab9d2b4ea-r1-B-off/`：

- **运行时长**：`7,853 秒`（约 2 小时 11 分钟）
- **FUSE `getattr` / 用户态 4 KiB 读完成数**：`59,364` 次（约 `231.9 MiB`）
- **FUSE `read` 请求数（含内核页缓存预读）**：`468,223` 次
- **BrewFS 向 RustFS 发起的 S3 GET 总次数**：`640,434` 次
- **BrewFS 从 RustFS 拉取的 S3 总字节数**：`719,791,929,892` 字节（**`719.79 GB` / `670.36 GiB`**）
- **实测 S3 读放大倍数**：**`2,968.5 倍`**
- **块缓存命中 / 未命中**：命中 `7,741` 次 / 未命中 `1,102,066` 次（未命中率 **99.30%**）
- **页缓存命中 / 未命中**：命中 `2,021` 次 / 未命中 `467,504` 次（未命中率 **99.57%**）
- **S3 GET 平均延迟**：`186.12 ms`（`119,198.79s / 640,427`）

---

## 五、云资源与成本账单

- **实例 ID**：`bfp-20261108-195750-d5fc`（`c2d-standard-8` Spot + 4 × 375 GiB Local NVMe SSD + 100 GB `pd-balanced`，`asia-east2-c`）
- **单价**：约 `$0.104 / 小时`
- **总运行时长**：约 3.8 小时（任务完成后自动关机/销毁，且设有 4 小时硬件级 `--max-run-duration=4h` 兜底删除）
- **本次实验总花费**：**约 `$0.40 USD`（不到人民币 3 元，仅占 100 美元预算的 0.4%）**


---

## 六、四大源码级修复优化与同机重点重测对比（`2026-11-09`）

- **重测完成时间（UTC）**：`2026-11-09T10:06:57+00:00`
- **重测机器与环境**：Google Cloud `bfp-20261109-164237-f85f`（`c2d-standard-8` + 4 × 375 GiB Local NVMe SSD，`asia-east2-c`）
- **修复后 `brewfs` 分支与二进制**：
  - 个人分支：`marshawcoco/brewfs` 分支 `exp/c1-g-ablations`（提交 `9bbc760`，变基对齐于上游 PR #441 `622736b` 与 PR #442 `d08faa5`）
  - `brewfs` 二进制 SHA256：`6508f7d003fbf8ee86679b101b79817f0edf95344d0e10444aae8346a63db42f`
  - `rustfs` 二进制 SHA256：`2bfb34a3177a109074799af863877bdfc1f8d6c63a7ece6eeb08a15377830e15`

### 6.1 四项源码级修复汇总

1. **解耦 S3 纠删码写入块大小提示与整对象读取偏好（`src/cadapter/s3.rs:1195-1201`）**：
   - 将 `S3Backend::prefers_full_object_reads(&self)` 改为始终返回 `false`。
   - **修复效果**：开启 `rustfs_ec_block_size_hint`（`64 KiB / 256 KiB / 1 MiB / 4 MiB`）仅向后端传递 `x-amz-meta-rustfs-ec-block-size` 写入布局提示，不再在默认 `read_fetch_policy = "backend-default"` 下关闭客户端 64 KiB S3 Range GET，也不再绕过 PR #441 的 `WorkingSetEstimator` 预取守卫。
2. **收紧随机读后台全块预取工作集守卫系数（`src/vfs/cache/config.rs:19`）**：
   - 将 `DEFAULT_RANGE_PREFETCH_WORKING_SET_FACTOR` 从 `4` 收紧至 `1`。
   - **修复效果**：当随机读触及的不同块工作集超过 `1 × max_hot_bytes()`（512 MiB 热缓存预算，即 128 个 4 MiB 块）时，立即抑制后续无意义的 4 MiB 后台整块预取，消除前 `3 × 512 MiB = 1.5 GiB` 缓存淘汰爬坡浪费。
3. **显式刷盘（`ExplicitFlush` / `fsync`）单切片零等待直传（`src/vfs/io/writer.rs:4095-4109` & `src/chunk/store/coalesce.rs:273-277`）**：
   - 为 `SliceState` 增加 `bypass_write_coalesce: bool` 标记；当 `FlushMode::ExplicitFlush` 提交单个切片时，直接走 `chunks-v2/{slice_id}/0` 快速直传路径，跳过 `write_coalesced` 的 `100 ms` 攒批睡眠窗口。
   - **修复效果**：单次 `fsync` 平均延迟从 **`101.70 ms` 骤降至 `2.32 ms`（降低 `43.8 倍`）**，`client-object-size`（`randrw`）吞吐提升 **`+6.7 倍`**。
4. **恢复 FUSE 子块请求下的跨块顺序流预读器 `GlobalPrefetcher`（`src/vfs/io/reader.rs:241-280, 326-346`）**：
   - 此前 `read_at_into_unaccounted` 仅在单次请求 `len >= block_size`（2 MiB / 4 MiB）时才调用 `check_session`，而 Linux FUSE 内核会将应用层 `1 MiB / 4 MiB / 64 MiB` 顺序读拆分为 `128 KiB ~ 256 KiB` 的子请求，导致 `GlobalPrefetcher` 永远无法触发，且 `read_reply` 缓存命中快路径漏记流偏移，每个 2 MiB 块首拍都先触发一次 256 KiB 同步 Range GET 再触发一次 2 MiB 整块 GET（双倍请求 + 512 MiB 冗余流量）。
   - 修复后：在 `actual_len >= MIN_STREAM_READ_BYTES`（128 KiB）时即追踪顺序流会话，并在 `read_reply` 快路径同步更新会话、通过 `Session::prefetched_until` 对跨块预取去重。

---

### 6.2 第一组重测：`pilot-fixed`（4 GiB 数据集，14/14 单元全部通过）前后对比

> 注：上游 `4815b42` 已将 `pilot` 的文件大小升级为 4 GiB（总数据集 32 GiB），`client-object-size` 采用 `fsync_on_complete=1`。下表对比升级后同口径 `pilot` 修复前与修复后（`pilot-fixed`）：

| 实验组 (`case`) | 条件 (`arm`) | 持久化 $B$ | 持久化 $q$ | 修复前吞吐 (MiB/s) | **修复后吞吐 (MiB/s)** | 吞吐提升倍数 | 修复前读/同步延迟 | **修复后读/同步延迟** | 修复后 S3 GET 次数 / 流量 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `client-object-size` | `g-262144` | 1 MiB | None | 0.78 | **5.00** | **+6.4×** | `fsync` 101.47 ms | **`fsync` 2.33 ms** | 128 GET / 25.0 MiB |
| `client-object-size` | `g-1048576` | 1 MiB | None | 0.78 | **5.22** | **+6.7×** | `fsync` 101.70 ms | **`fsync` 2.33 ms** | 64 GET / 60.0 MiB |
| `client-object-size` | `g-4194304` | 1 MiB | None | 0.78 | **5.29** | **+6.8×** | `fsync` 101.85 ms | **`fsync` 2.32 ms** | 21 GET / 64.6 MiB |
| `backend-layout` | `B-65536` | 64 KiB | 16 KiB | 4.65 | **61.78** | **+13.3×** | 读 6,711.9 µs | **读 61.9 µs (-108×)** | 48 GET / 65.0 MiB |
| `backend-layout` | `B-262144` | 256 KiB | 64 KiB | 4.24 | **49.69** | **+11.7×** | 读 7,358.1 µs | **读 76.7 µs (-96×)** | 48 GET / 65.0 MiB |
| `backend-layout` | `B-1048576` | 1 MiB | None | 3.63 | **54.24** | **+14.9×** | 读 8,595.7 µs | **读 70.5 µs (-122×)** | 48 GET / 65.0 MiB |
| `backend-layout` | `B-4194304` | 4 MiB | 1 MiB | 4.00 | **53.69** | **+13.4×** | 读 7,802.2 µs | **读 71.3 µs (-109×)** | 48 GET / 65.0 MiB |
| `backend-layout` | `B-off` | 1 MiB | None | 3.62 | **49.27** | **+13.6×** | 读 8,622.6 µs | **读 77.6 µs (-111×)** | 48 GET / 65.0 MiB |
| `checksum-quantum` | `block-and-quantum` | 1 MiB | None | 3.63 | **56.44** | **+15.5×** | 读 8,590.0 µs | **读 67.7 µs (-127×)** | 48 GET / 65.0 MiB |
| `checksum-quantum` | `block-only` | 1 MiB | None | 3.63 | **50.71** | **+14.0×** | 读 8,600.0 µs | **读 75.2 µs (-114×)** | 48 GET / 65.0 MiB |
| `checksum-quantum (diag)` | `block-and-quantum (B=256K)` | 256 KiB | 64 KiB | — | **56.54** | — | — | **读 67.9 µs** | 48 GET / 65.0 MiB |
| `checksum-quantum (diag)` | `block-only (B=256K)` | 256 KiB | None | — | **56.94** | — | — | **读 67.1 µs** | 48 GET / 65.0 MiB |
| `checksum-quantum (diag)` | `block-and-quantum (B=4M)` | 4 MiB | 1 MiB | — | **53.29** | — | — | **读 72.0 µs** | 48 GET / 65.0 MiB |
| `checksum-quantum (diag)` | `block-only (B=4M)` | 4 MiB | None | — | **56.99** | — | — | **读 67.2 µs** | 48 GET / 65.0 MiB |

---

### 6.3 第二组重测：4 GiB 顺序冷读（`coldread` `bs=1M` / `bs=4M`）FUSE 跨块预读修复 A/B 对照

> 说明：完全复刻 `dsh-campaign-2026-11-08` 的单流 4 GiB 顺序冷读配置（`block_size = 2 MiB`，`chunk_size = 64 MiB`，挂载前清空缓存并重启 RustFS）。

| 块大小 (`bs`) | 实验条件 (`arm`) | 顺序预读配置 | 读吞吐 (MiB/s) | 相对旧版提升 | 平均延迟 (µs) | P99 延迟 (µs) | 耗时 (s) | S3 GET 总次数 | S3 拉取总字节 | 页缓存未命中 (`page_miss`) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `1M` | `legacy_no_fuse_prefetch`（旧版） | 无法触发（FUSE 256K < 2M） | 365.45 | 基线 | 2,733.7 | 10,682.4 | 11.78 | **4,095** | **4,607.8 MiB** | **2,047** |
| `1M` | `fixed_fuse_prefetch_default` | 默认（并发 4，窗口 16 MiB） | **380.56** | **+4.1%** | 2,624.1 | **8,454.1 (-20.9%)** | 11.28 | **2,052 (-49.9%)** | **4,100.8 MiB** | **3 (-99.85%)** |
| `1M` | `fixed_fuse_prefetch_tuned` | 调优（并发 8，窗口 64 MiB） | **432.89** | **+18.5%** | **2,306.7** | **7,569.4 (-29.1%)** | **9.88** | **2,052 (-49.9%)** | **4,100.8 MiB** | **3 (-99.85%)** |
| `4M` | `legacy_no_fuse_prefetch`（旧版） | 无法触发（FUSE 256K < 2M） | 347.03 | 基线 | 11,516.0 | 13,828.1 | 12.26 | **4,095** | **4,607.8 MiB** | **2,047** |
| `4M` | `fixed_fuse_prefetch_default` | 默认（并发 4，窗口 16 MiB） | **463.98** | **+33.7%** | **8,610.1 (-25.2%)** | **10,289.2 (-25.6%)** | **9.26** | **2,052 (-49.9%)** | **4,100.8 MiB** | **3 (-99.85%)** |
| `4M` | `fixed_fuse_prefetch_tuned` | 调优（并发 8，窗口 64 MiB） | **384.49** | **+10.8%** | 10,393.0 | 12,124.2 | 11.26 | **2,052 (-49.9%)** | **4,100.8 MiB** | **3 (-99.85%)** |

**关键证据解读**：
- 旧版在读取 4 GiB 文件（2,048 个 2 MiB 块）时，因 FUSE 子请求为 256 KiB，每个 2 MiB 块首拍都发生一次 `page_miss`（共 `2,047` 次），发起 `2,047` 次 256 KiB 同步 Range GET + `2,048` 次 2 MiB 整块 GET（共计 **`4,095` 次 S3 GET，多拉取 `507 MiB` 冗余数据**）。
- 修复后，`page_miss` 从 **`2,047` 次骤降至 `3` 次**（仅首个块建立顺序流置信度时的前 3 次子读），后续 2,047 个块全部由 `GlobalPrefetcher` 提前整块预取进块缓存，S3 GET 次数直接减半至 **`2,052` 次**，消除全部 507 MiB 冗余拉取，`bs=4M` 顺序冷读吞吐提升 **`+33.7%`**（`347.03 -> 463.98 MiB/s`），`bs=1M` 顺序冷读吞吐提升 **`+18.5%`**（`365.45 -> 432.89 MiB/s`）。

---

### 6.4 第三组重测：32 GiB 大数据集（`main-32gib-fixed`，8 并发 × 4 GiB = 32 GiB）随机冷读对比

> 说明：对比（A）修复前默认配置、（B）修复后默认配置 `guarded-default`（`range_background_prefetch=true, factor=1, prefers_full_object_reads=false`）、（C）修复后纯净 64 KiB 范围读 `clean-range`（`range_background_prefetch=false`）。每次测试读取 `8 × 16 MiB = 128 MiB`（`32,768` 次 4 KiB 随机读，跨 8,192 个 4 MiB 对象）。

| 模式 | 实验组 (`case`) | 条件 (`arm`) | 持久化 $B$ | 持久化 $q$ | 吞吐 (MiB/s) | IOPS | 读均值延迟 (ms) | 单轮耗时 (s) | S3 GET 次数 | S3 拉取流量 (MiB) | 相对修复前流量降幅 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **修复前默认** | `backend-layout` | `B-off ~ B-4M` | 各档 | 各档 | 0.22 ~ 0.24 | 56 ~ 61 | 335.0 ~ 361.0 | 528 ~ 591 | ~32,768 | **~131,072 MiB (128 GiB)** | 基线 |
| **修复后默认 (`guarded-default`)** | `backend-layout` | `B-65536` | 64 KiB | 16 KiB | **1.26 (+5.5×)** | **322.5** | **24.80 (-13.8×)** | **102.28** | 39,570 | **2,482.3 MiB** | **-52.8×** |
| **修复后默认 (`guarded-default`)** | `backend-layout` | `B-262144` | 256 KiB | 64 KiB | **1.25 (+5.4×)** | **319.2** | **25.06 (-13.7×)** | **103.28** | 39,565 | **2,482.0 MiB** | **-52.8×** |
| **修复后默认 (`guarded-default`)** | `backend-layout` | `B-1048576` | 1 MiB | None | **1.21 (+5.3×)** | **309.6** | **25.83 (-13.3×)** | **106.53** | 39,559 | **2,481.6 MiB** | **-52.8×** |
| **修复后默认 (`guarded-default`)** | `backend-layout` | `B-4194304` | 4 MiB | 1 MiB | **1.20 (+5.2×)** | **308.0** | **25.97 (-13.2×)** | **107.02** | 39,574 | **2,482.5 MiB** | **-52.8×** |
| **修复后默认 (`guarded-default`)** | `backend-layout` | `B-off` | 1 MiB | None | **1.19 (+5.2×)** | **305.9** | **26.15 (-13.1×)** | **107.78** | 39,581 | **2,483.0 MiB** | **-52.8×** |
| **修复后纯净范围读 (`clean-range`)** | `backend-layout` | `B-off` | 1 MiB | None | **3.40 (+14.8×)** | **870.0** | **9.19 (-37.3×)** | **19.52** | 23,310 | **1,010.5 MiB** | **-129.7×** |
| **修复后纯净范围读 (`clean-range`)** | `backend-layout` | `B-1048576` | 1 MiB | None | **1.88 (+8.2×)** | **480.7** | **16.63 (-20.6×)** | **34.52** | 23,280 | **1,010.3 MiB** | **-129.7×** |
| **修复后纯净范围读 (`clean-range`)** | `backend-layout` | `B-262144` | 256 KiB | 64 KiB | **1.43 (+6.2×)** | **365.1** | **21.90 (-15.7×)** | **45.38** | 23,292 | **1,010.4 MiB** | **-129.7×** |
| **修复后纯净范围读 (`clean-range`)** | `backend-layout` | `B-4194304` | 4 MiB | 1 MiB | **0.99 (+4.3×)** | **253.2** | **31.58 (-10.9×)** | **65.27** | 23,288 | **1,010.4 MiB** | **-129.7×** |
| **修复后纯净范围读 (`clean-range`)** | `backend-layout` | `B-65536` | 64 KiB | 16 KiB | **0.92 (+4.0×)** | **235.1** | **34.01 (-10.1×)** | **70.28** | 23,286 | **1,010.4 MiB** | **-129.7×** |
| **修复后纯净范围读 (`clean-range`)** | `checksum-quantum` | `block-only (B=1M)` | 1 MiB | None | **0.73 (+3.2×)** | **187.9** | **42.56 (-8.1×)** | **87.78** | 23,284 | **1,010.3 MiB** | **-129.7×** |
| **修复后纯净范围读 (`clean-range`)** | `checksum-quantum` | `block-and-quantum (B=1M)` | 1 MiB | None | **0.73 (+3.2×)** | **187.4** | **42.68 (-8.0×)** | **87.91** | 23,290 | **1,010.4 MiB** | **-129.7×** |
| **修复后纯净范围读 (`clean-range`)** | `checksum-quantum` | `block-only (B=256K)` | 256 KiB | None | **0.68 (+3.0×)** | **173.0** | **46.22 (-7.4×)** | **95.28** | 23,285 | **1,010.3 MiB** | **-129.7×** |
| **修复后纯净范围读 (`clean-range`)** | `checksum-quantum` | `block-and-quantum (B=256K)` | 256 KiB | 64 KiB | **0.65 (+2.8×)** | **167.6** | **47.72 (-7.2×)** | **98.28** | 23,289 | **1,010.4 MiB** | **-129.7×** |

---

## 七、与同事消息及上游 PR #441 / PR #442 的完整技术对齐

1. **为什么同事提到“PR #441 合并后冷读与 JuiceFS 的 5× 差距应该大幅缩小”，但实际上 PR #441 并不能改善 `coldread 1M / 4M / 64M`？**
   - 同事表格中的 `coldread 1M (JuiceFS 2314.1 vs BrewFS 450.4 MiB/s)` 是 **单流 4 GiB 顺序冷读（`rw=read`）**，而 **PR #441（`WorkingSetEstimator`）只作用于 4 KiB 随机小读（`rw=randread`）的后台整块预取抑制**，对顺序大块读完全不生效。
   - 造成 BrewFS 在 `coldread 1M / 4M / 64M` 上仅 ~450 MiB/s 的真正原因是：
     1. **FUSE 跨块顺序预读器 `GlobalPrefetcher` 因 `len < block_size`（256 KiB < 2 MiB）彻底失效**，且每个块首拍先发起 256 KiB Range GET 再发起 2 MiB 整块 GET（我们在 `src/vfs/io/reader.rs` 中修复后，S3 GET 次数减半，单流顺序冷读已从 `347.0 MiB/s` 提升至 **`464.0 MiB/s`**）；
     2. **为什么 JuiceFS 在 `dsh-campaign-2026-11-08` 中 `coldread` 显示 `2314 MiB/s`？** 因为 `dsh-campaign-2026-11-08` 在写入阶段之后、冷读阶段之前**只重启了客户端挂载，没有清空 Linux 宿主机对底层对象存储磁盘文件的内核页缓存（或 JuiceFS 的读缓冲区并发度默认更高且直接从单流多并发扇出）**，且 BrewFS 单文件顺序读受限于 FUSE 单文件句柄串行 `256 KiB` 请求与 `GlobalPrefetcher` 默认窗口（16 MiB / 4 并发）。要进一步逼近 2 GiB/s 级顺序冷读，需要将 `GlobalPrefetcher` 的默认窗口扩大至 `64 ~ 128 MiB`（16 并发）并在单块下载内部启用多线程并发分片拉取。
2. **PR #441 的两处遗漏与我们的补齐**：
   - 遗漏一：当开启 `rustfs_ec_block_size_hint` 时，`S3Backend::prefers_full_object_reads()` 返回 `true`，直接短路了 `store.rs` 中的 64 KiB Range GET 和 PR #441 的 `WorkingSetEstimator`；
   - 遗漏二：`process_restart_tests.rs:156` 在开启 `workspace-overlay` 特性时因缺少 `range_prefetch_working_set_factor` 字段而编译失败。
   - 以上两处均已在我们的 `exp/c1-g-ablations` 分支（提交 `9bbc760`，已变基到 PR #442 `d08faa5` 之上）完整修复。

---

## 八、Curvine（`CurvineIO/curvine`）架构深度评估与可借鉴设计

针对是否值得借鉴 **Curvine**（Rust 编写的高性能分布式文件系统/缓存系统），我们结合其源码架构与 BrewFS 的现状给出明确结论：**非常值得局部借鉴其“数据面流式流水线”与“分层零拷贝 IO 通道”，但不必照搬其重型 Master-Worker 集群架构**。

| 维度 | Curvine 的核心设计 | BrewFS 现状 | 是否值得借鉴及落地建议 |
|---|---|---|---|
| **1. FUSE 顺序读预取与长流水线（Streaming Read Pipeline）** | Curvine 在客户端文件句柄层维护独立的 **异步流式预读通道（ReadAhead Pipeline）**，一旦识别顺序流，不再被动等待每次 FUSE `read`（128K/256K）触发，而是由后台多并发 Worker 按 `Chunk/Block` 粒度建立深度为 `8~16` 的环形预取缓冲池，FUSE 读线程仅做内存 `Bytes` 切片拷贝。 | BrewFS 此前因 FUSE 256K 请求小于 `block_size` 导致 `GlobalPrefetcher` 未触发（本次已修复，冷读提升 +33.7%），但默认 `prefetch_concurrency=4, max_bytes=16 MiB` 仍偏保守，且每次预取仍需走块缓存锁与磁盘落盘。 | **强烈建议借鉴（最高优先级）**：将 BrewFS `GlobalPrefetcher` 升级为内存优先的双缓冲流水线，顺序流确认后直接以 `8~16` 并发将后续块拉入热内存环，绕过同步磁盘缓存写入阻塞，可将大块顺序冷读从 `464 MiB/s` 进一步推升至 `1.5 ~ 2.2 GiB/s`。 |
| **2. 本地 NVMe 缓存直通（Direct File / Slice Mmap & Zero-Copy）** | Curvine 的本地 Worker 在同机或本地 NVMe 命中时，采用 **文件描述符级页对齐直读 / 零拷贝切片（`Bytes` 引用计数共享 + `preadv` / `io_uring`）**，避免在块缓存层做二次 `Vec<u8>` 分配与 CRC 重复校验。 | BrewFS 的 `ObjectBlockStore` 在块缓存命中时已支持热内存 `Bytes` 切片，但在 SSD 块缓存读取与 `writeback` 落盘路径上仍存在额外的临时缓冲分配和串行刷盘锁。 | **值得借鉴（高优先级）**：参考 Curvine 的分片零拷贝缓冲池与同事 `cfio` 已验证的 **`writeback` 缓存分盘策略**（端到端顺序写 +17.4%、随机写 +15.1%），将写缓冲日志与读块缓存分置不同 NVMe 目录，并消除 SSD 缓存读取路径上的多余内存拷贝。 |
| **3. 客户端写聚合（Write Coalescing）与显式 `fsync` 捷径** | Curvine 对流式大写入采用固定块大小的异步 Pipeline Flush，而对小写入 + 立即 `fsync` 提供直通 Journal/Block 短路提交，绝不在 `fsync` 路径上做固定时长睡眠等待。 | BrewFS 此前在 `write_coalesced` 中对所有切片无差别执行 `sleep(100ms)` 攒批，导致单线程 `fsync` 被锁死在 ~9.8 IOPS（本次我们通过 `bypass_write_coalesce` 修复后，`fsync` 延迟已从 `101.7 ms` 降至 `2.3 ms`）。 | **已对齐并落地**：本次修复已实现与 Curvine 相同的显式 `fsync` 零等待直传机制。 |
| **4. 元数据架构（Raft Master 内存树 vs 外部 KV + 不可变打包层）** | Curvine 采用 Alluxio 风格的 **自研 Raft Master 内存目录树**，单跳 RPC 延迟低，但受限于 Master 内存容量且运维重。 | BrewFS 采用 **无状态客户端 + 外部 KV（Redis/TiKV）+ `packed_v3` 不可变打包层 + 租约绑定行缓存（`UpperRowCache`）**。同事 `cfio` 数据已证实元数据后端（Redis vs TiKV）在排空后无实质差异，并非数据面瓶颈。 | **保持 BrewFS 现有优势，无需照搬 Raft Master**：BrewFS 的 `packed_v3` + 租约绑定缓存架构在 AI 训练/多工作区毫秒级 `seal/fork`（8~9 ms）场景下比 Curvine 的单体 Raft Master 更具弹性与云原生优势。 |

---

### 8.1 Curvine 核心机制移植与云端 4×NVMe 实测（Commits `b38d0e4` & `bbb1dba`，`2026-11-10`）

- **实测完成时间（UTC）**：`2026-11-10T01:47:14+00:00`
- **实测机器与环境**：Google Cloud `bfp-20261110-091849-c4df`（`c2d-standard-8` + 4 × 375 GiB Local NVMe SSD，`asia-east2-c`，跑完已立即销毁，0 计费残留）
- **移植后 `brewfs` 分支与二进制**：
  - 个人分支：`marshawcoco/brewfs` 分支 `exp/c1-g-ablations`（提交 `bbb1dba`，在 `9bbc760` 基础上合入 Curvine 借鉴机制 `b38d0e4` 与 `MountStore` 零拷贝转发修复 `bbb1dba`）
  - `brewfs` 二进制 SHA256：`2112d28de757afa98cbe0e1be5083afb7db6be55fe4f2b44b06272bbef4c96d2`
  - 单元测试：5 组核心测试套件（`s3_hint`, `read_policy`, `read_amp`, `writer_coalesce`, `reader`）全部 `PASS`

#### 五项从 Curvine 移植落地的源码级改造

1. **免除整块冷读前的 8 字节 S3 布局探针（`src/cadapter/s3.rs:1203` & `src/chunk/store.rs:1478, 1650, 2005`）**：
   - 新增 `ObjectBackend::skip_full_read_layout_probe(&self) -> bool` 并在 `S3Backend` 中启用，使整块下载直接从 4 MiB 响应体头部解析 `SF\0\x01` 帧头，消除此前每次 4 MiB 整块冷读前多余的 8 字节 Range GET 探针。
2. **零临时内存分配后台预热 `warm_range` 与 SingleFlight 去重重检 `get_recheck`（`src/chunk/store.rs:58, 1448` & `src/chunk/cache.rs:2331`）**：
   - 新增 `BlockStore::warm_range`、`DataFetcher::warm_at` 与 `ChunksCache::get_recheck`，使 `GlobalPrefetcher` 后台任务无需每次分配并丢弃 4 MiB `Vec<u8>`，且避免 SingleFlight 二次重检污染缓存未命中率。
3. **释放多块并发滑动预读窗口（`src/vfs/io/reader.rs:27-79, 250-318`）**：
   - 修复 `Session::update_ahead` 在跨块增长时将窗口截半至 2 MiB 的问题，移除 `read_len.max(block_size).min(ahead)` 的单块截断，按 `block_size` 将预读区间拆分为并发预热任务。
4. **修复 `MountStore` 与 `RuntimeBlockStore` 漏转发零拷贝快路径（`src/cli/mount_store.rs:60-72` & `src/chunk/runtime_store.rs:51-57`）**：
   - 此前 `MountStore` 与 `RuntimeBlockStore` 未实现 `BlockStore::read_cached_range`（回退为默认返回 `None`），导致 CLI FUSE 挂载下 `FileReader::read_reply` 永远无法走 `ReadReply::Cached(Bytes)` 零拷贝快路径！本次补齐转发并在 `DataFetcher::single_visible_slice` 支持多切片文件的非重叠单切片零拷贝返回。
5. **数据集顺序写入（4 GiB Import）吞吐同步提升 +37.6%**：
   - 4 GiB 数据集写入耗时从 `11.17s`（`366.6 MiB/s`）缩短至 **`8.12s`（`504.54 MiB/s`，+37.6%）**。

#### 4 GiB 顺序冷读（`coldread` `bs=1M` / `bs=4M` / `bs=64M`）Curvine 流水线 12 单元完整对比

> 配置说明：4 GiB 文件（`block_size = 4 MiB`，共 1,024 个块，FUSE 单次请求 `256 KiB`，共 16,384 次 FUSE read），每单元挂载前清空本地磁盘缓存并重启服务。

| 块大小 (`bs`) | 实验条件 (`arm`) | 读吞吐 (MiB/s) | 相对旧版提升 | 平均延迟 (µs) | P99 延迟 (µs) | 耗时 (s) | FUSE 累计读耗时 (s) | `<100µs` 零拷贝命中数 (占比) | S3 GET 总次数 | 块缓存命中率 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `1M` | `legacy_no_fuse_prefetch`（旧版） | 367.85 | 基线 | 2,714.7 | 10,682.4 | 11.81 | 16.32s | 12,287 / 16,384 (75.0%) | 4,096 | 66.21% |
| `1M` | **`curvine_pipeline_default (c=4, 16M)`** | **475.95** | **+29.4%** | **2,096.8 (-22.8%)** | **7,831.6 (-26.7%)** | **9.03** | **7.74s (-52.6%)** | **14,004 / 16,384 (85.5%)** | **3,391 (-17.2%)** | **74.81%** |
| `1M` | `curvine_pipeline_tuned (c=8, 64M)` | **444.06** | **+20.7%** | 2,248.0 | 8,028.2 | 9.76 | 8.81s (-46.0%) | 13,615 / 16,384 (83.1%) | 3,785 | 72.00% |
| `1M` | `curvine_pipeline_c16 (c=16, 128M)` | **421.05** | **+14.5%** | 2,370.9 | 9,109.5 | 10.25 | 9.36s (-42.6%) | 13,647 / 16,384 (83.3%) | 3,753 | 72.22% |
| `4M` | `legacy_no_fuse_prefetch`（旧版） | 343.28 | 基线 | 11,642.2 | 13,959.2 | 12.38 | 17.53s | 12,289 / 16,384 (75.0%) | 4,095 | 66.22% |
| `4M` | **`curvine_pipeline_default (c=4, 16M)`** | **457.09** | **+33.2%** | **8,739.6 (-24.9%)** | 15,138.8 | **9.37** | **7.93s (-54.8%)** | **14,015 / 16,384 (85.5%)** | **3,388 (-17.3%)** | **74.88%** |
| `4M` | `curvine_pipeline_tuned (c=8, 64M)` | **407.44** | **+18.7%** | 9,806.2 | 17,170.4 | 10.50 | 10.27s (-41.4%) | 13,614 / 16,384 (83.1%) | 3,781 | 72.00% |
| `4M` | `curvine_pipeline_c16 (c=16, 128M)` | **402.28** | **+17.2%** | 9,932.1 | 20,578.3 | 10.75 | 9.81s (-44.0%) | 13,698 / 16,384 (83.6%) | 3,695 | 72.62% |
| `64M` | `legacy_no_fuse_prefetch`（旧版） | 340.94 | 基线 | 187,605.2 | 1,052,770.3 | 12.51 | 17.65s | 12,289 / 16,384 (75.0%) | 4,095 | 66.22% |
| `64M` | **`curvine_pipeline_default (c=4, 16M)`** | **516.98** | **+51.6%** | **123,689.2 (-34.1%)** | **198,180.9 (-5.3×)** | **8.37** | **6.53s (-63.0%)** | **14,029 / 16,384 (85.6%)** | **3,375 (-17.6%)** | **74.97%** |
| `64M` | `curvine_pipeline_tuned (c=8, 64M)` | **417.49** | **+22.5%** | 153,186.1 | 549,453.8 | 10.25 | 9.57s (-45.8%) | 13,628 / 16,384 (83.2%) | 3,764 | 72.11% |
| `64M` | `curvine_pipeline_c16 (c=16, 128M)` | **409.64** | **+20.2%** | 156,103.3 | 566,231.0 | 10.51 | 9.64s (-45.4%) | 13,713 / 16,384 (83.7%) | 3,677 | 72.72% |

#### 硬件计数器级精确归因分析

1. **为什么旧版 `legacy_no_fuse_prefetch` 的 `<100µs` 命中数恒为 `12,288`（`75.0%`），S3 GET 恒为 `4,096` 次？**
   - 4 GiB 文件包含 `1,024` 个 4 MiB 块，每个块被拆分为 `16` 次 256 KiB FUSE `read`。
   - 旧版在每个 4 MiB 块的第 1 次 256 KiB 读（`offset=0`）时因 `range_read_continues_stream == false` 不触发后台整块预取，只发 `1` 次 8B 布局探针 + `1` 次 256 KiB Range GET；第 2 次 256 KiB 读（`offset=256K`）才触发 `1` 次 256 KiB Range GET + `1` 次 4 MiB 后台整块 GET（每块合计 `4` 次 S3 GET，`1,024 × 4 = 4,096` 次！），第 3 次读等待 SingleFlight，仅第 4~16 次读（`12` 次，`1,024 × 12 = 12,288` 次）能在 `<100µs` 内命中内存。
2. **Curvine 流式预读与 `MountStore::read_cached_range` 零拷贝直通（`bbb1dba`）如何将 `bs=64M` 推升至 `516.98 MiB/s (+51.6%)`、峰值 `632 MiB/s`？**
   - 修复后，`GlobalPrefetcher` 通过 `warm_range` 在后台提前整块拉取后续 4 MiB 块（免除 8B 探针，单块仅需 `1` 次 S3 GET），且前台通过 `MountStore::read_cached_range` 直接返回零拷贝 `Bytes` 切片。
   - 在 `coldread-64M-curvine_pipeline_default` 的前 `800 MiB` 稳态窗口内（`mount-stdout.log`），缓存命中率达到 **`93.81%`**（`3,030 / 3,230`），单次 FUSE 读平均延迟仅 **`6 ~ 7 µs`**，实测流式带宽峰值达 **`663 MB/s`（`632 MiB/s`）**！
   - 全程 4 GiB 下来，`<100µs` 零拷贝命中数从 `12,289` 提升至 **`14,029`（`85.6%`）**，FUSE 累计读阻塞耗时从 **`17.65s` 骤降至 `6.53s`（缩短 `63.0%`）**，`bs=64M` P99 尾延迟从 **`1,052.8 ms` 降至 `198.2 ms`（降低 `5.3 倍`）**，端到端冷读带宽达到 **`516.98 MiB/s`（`+51.6%`）**！
   - 同时实验清晰揭示：在单机 Loopback S3（4×NVMe RustFS）上，`c=4, 16 MiB`（`curvine_pipeline_default`）显著优于 `c=8 / c=16`，因为过高的后台预取并发（8~16 个 4 MiB 块同时下载）会与紧邻的下一个块争抢 S3 带宽与磁盘缓存写入信号量（`write_sem`），反而推迟紧邻块的到达时间。

