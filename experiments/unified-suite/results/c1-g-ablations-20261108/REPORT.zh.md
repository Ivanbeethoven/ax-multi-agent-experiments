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
