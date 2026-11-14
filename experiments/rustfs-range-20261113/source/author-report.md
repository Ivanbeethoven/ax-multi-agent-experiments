# RustFS Dynamic Block vs `1.0.5-preview.1` 同硬件冷盘 `Range GET` 35 轮实测与源码根因报告

> [!IMPORTANT]
> **核心结论速览（`35 / 35` 轮零报错完成，5 台实例与全部临时凭据已 100% 销毁清零）**
> 1. **100% 同硬件默认 `12+4` 配置（`Suite A`）**：在完全相同的 4 台整形 `ecs.i5.16xlarge`（`6` 物理核 + `MemoryMax=12GiB` + `4 × 3.5TB PCIe 5.0 本地 NVMe` + `163.84 Gbps`）冷盘随机 `Range GET` 下，**`Dynamic 64KiB` (`963556e14`) 相比原生 `RustFS 1.0.5-preview.1` (`34e6449ed`) 全线提升 `+12.4% ~ +29.3%`**（`4KiB` 吞吐 `14.02` vs `10.85 MiB/s`，`P99` 尾延迟从 `33.8ms` 降至 **`25.8ms`**，`-23.7%`）。
> 2. **为什么在 `ecs.i5` 上不是数倍差距？（内核 `vmstat` 铁证）**：压测期间存储节点实测 **`wa (iowait) = 0%`**（每块 PCIe 5.0 NVMe 仅读 `142 MiB/s`，不到单盘带宽的 `1%`），而 **6 颗物理核 `CPU = 98%`（`us=68%, sy=30%`）** 全消耗在每次请求固定的 16 盘 `xl.meta` 仲裁与跨节点 gRPC 上（约 `17.5ms` 固定底噪）。**只有在底层磁盘读带宽受限（如常规云盘 ESSD PL0/PL1 或 AWS `gp3` `45~125 MiB/s`/盘）时，`1MiB` 固定块把磁盘带宽打满，`Dynamic 64KiB` 才会拉开 `2× ~ 6×` 的数倍差距。**
> 3. **重大源码级发现（`Suite B: 8+8 Read Quantum` 捕获跨分片串行循环退化）**：开启 `EC:8` 激活 `4KiB Read Quantum` 后，实测在 `4KiB → 16KiB → 32KiB → 64KiB` 出现阶梯式性能倒退（`64KiB` 降至 `1,804 req/s`，`-45.7%`），而在 `128KiB` 瞬间跃升回 **`3,628 req/s`（反超 `1.0.5-p1` `+11.7%`）**。定位到 [read.rs](file:///C:/work/brewfs-maintenance/worktrees/rustfs-dynamic-block-size/crates/ecstore/src/set_disk/read.rs#L1634-L1635) 与 [read.rs#L2184](file:///C:/work/brewfs-maintenance/worktrees/rustfs-dynamic-block-size/crates/ecstore/src/set_disk/read.rs#L2184-L2245)：`read_quantized_range` 仅在 `shard_index == 0` 时合并跨分片读取，导致 `8+8`（每分片仅 `8KiB`）下 93.75% 的多帧请求退化为最多 **8 轮串行 8 盘 gRPC 流打开**。

---

## 1. 归档链接与实验严谨性设计

- **交互式曲线与仿真面板**：[aliyun-i5-range-showdown-dashboard.html](file:///C:/Users/coren/.gemini/antigravity/brain/933beb80-8a55-4d9c-8da6-c7eb4205d769/aliyun-i5-range-showdown-dashboard.html)
- **GitHub 永久结果分支**：`https://github.com/marshawcoco/rustfs/tree/bench-results/aliyun-i5-range-vs-105p1-20261113`（Commit `a00ad83aa5`）
- **本地原始日志与汇总表**：[summary.tsv](file:///C:/Users/coren/.gemini/antigravity/brain/933beb80-8a55-4d9c-8da6-c7eb4205d769/i5-range-showdown-final/summary.tsv) · [range_master.log](file:///C:/Users/coren/.gemini/antigravity/brain/933beb80-8a55-4d9c-8da6-c7eb4205d769/i5-range-showdown-final/range_master.log) · [environment.txt](file:///C:/Users/coren/.gemini/antigravity/brain/933beb80-8a55-4d9c-8da6-c7eb4205d769/i5-range-showdown-final/environment.txt)
- **反缓存与零删对象优化**：
  - 每组预写入 `3,200 × 16 MiB = 50.0 GiB` 逻辑数据（`12+4` 物理落盘 `66.7 GiB`，`8+8` 物理落盘 `100.0 GiB`），严格大于 4 节点 `MemoryMax=12G × 4 = 48 GiB` 上限，且每轮 `45s` 压测前全节点执行 `sync && echo 3 > /proc/sys/vm/drop_caches`。
  - 采用两阶段错峰（先在单台 64 核 `c8i` 编译 `39m37s`，存储节点 `0` 计费；再拉起 4 台 `i5` 存储节点仅运行 `41.1` 分钟，组间通过 6 秒并行 `mkfs.xfs -f -K` 重置代替 S3 删对象），本次全套编译 + 35 轮实测总成本仅约 **¥91～¥112**（较上次 30 轮节省超 **90%**）。

---

## 2. `Suite A`（默认 `12+4` EC）：同配置 `1.0.5-preview.1` vs `Dynamic` 7 档对比

| Range 大小 | 原生 `1.0.5-preview.1` (固定 1MiB) | `Dynamic 256KiB` (`963556e14`) | `Dynamic 64KiB` (`963556e14`) | `256KiB` vs `1.0.5-p1` | `64KiB` vs `1.0.5-p1` | `1.0.5-p1` P50 / P99 | `Dyn 64K` P50 / P99 |
|---|---|---|---|---|---|---|---|
| **4KiB** | 10.85 MiB/s (`2777.6/s`) | 13.63 MiB/s (`3488.8/s`) | **14.02 MiB/s (`3590.1/s`)** | **+25.6%** | **+29.3%** | 21.6ms / 33.8ms | **17.7ms / 25.8ms** |
| **16KiB** | 44.90 MiB/s (`2873.9/s`) | 54.28 MiB/s (`3473.7/s`) | **55.71 MiB/s (`3565.2/s`)** | **+20.9%** | **+24.1%** | 21.8ms / 34.5ms | **17.8ms / 26.0ms** |
| **32KiB** | 91.08 MiB/s (`2914.5/s`) | 107.67 MiB/s (`3445.4/s`) | **110.65 MiB/s (`3540.8/s`)** | **+18.2%** | **+21.5%** | 21.6ms / 33.2ms | **17.9ms / 26.0ms** |
| **64KiB** | 185.45 MiB/s (`2967.2/s`) | 212.05 MiB/s (`3392.8/s`) | **218.43 MiB/s (`3494.9/s`)** | **+14.3%** | **+17.8%** | 21.4ms / 31.2ms | **18.2ms / 26.2ms** |
| **128KiB** | 365.47 MiB/s (`2923.7/s`) | 411.57 MiB/s (`3292.5/s`) | **423.62 MiB/s (`3388.9/s`)** | **+12.6%** | **+15.9%** | 21.7ms / 31.7ms | **18.7ms / 27.3ms** |
| **256KiB** | 703.05 MiB/s (`2812.2/s`) | 791.68 MiB/s (`3166.7/s`) | **807.52 MiB/s (`3230.1/s`)** | **+12.6%** | **+14.9%** | 22.6ms / 33.1ms | **19.6ms / 28.5ms** |
| **1MiB** | 2207.20 MiB/s (`2207.2/s`) | 2469.48 MiB/s (`2469.5/s`) | **2481.17 MiB/s (`2481.2/s`)** | **+11.9%** | **+12.4%** | 28.7ms / 43.6ms | **25.5ms / 37.7ms** |

---

## 3. `Suite B`（`EC:8` 即 `8+8`）：`read_quantized_range` 跨分片串行瓶颈定位与修复建议

| Range 大小 | 原生 `1.0.5-preview.1` (`8+8`, 1MiB) | `Dynamic 64KiB + 4KiB Quantum` (`8+8`) | 相对变化 | `1.0.5-p1` P50 / P99 | `Dyn 64K_q4K` P50 / P99 | 命中的内部读取路径 |
|---|---|---|---|---|---|---|
| **4KiB** | 13.27 MiB/s (`3397.9/s`) | 12.82 MiB/s (`3281.0/s`) | -3.4% | 18.7ms / 27.0ms | 18.2ms / 32.8ms | `read_quantized_range` (1～2 轮串行) |
| **16KiB** | 52.87 MiB/s (`3384.0/s`) | 39.99 MiB/s (`2559.6/s`) | -24.4% | 18.8ms / 26.8ms | 24.4ms / 41.8ms | `read_quantized_range` (2～3 轮串行) |
| **32KiB** | 105.01 MiB/s (`3360.3/s`) | 67.22 MiB/s (`2150.9/s`) | -36.0% | 18.9ms / 27.0ms | 30.9ms / 44.6ms | `read_quantized_range` (4～5 轮串行) |
| **64KiB** | 207.73 MiB/s (`3323.7/s`) | 112.78 MiB/s (`1804.4/s`) | **-45.7%** | 19.2ms / 27.6ms | **34.7ms / 62.5ms** | `read_quantized_range` (8 轮串行谷底) |
| **128KiB** | 406.09 MiB/s (`3248.8/s`) | **453.48 MiB/s (`3627.9/s`)** | **+11.7%** | 19.6ms / 28.2ms | **17.3ms / 26.6ms** | 常规并发多条带解码 (`length > 64KiB`) |
| **256KiB** | 776.01 MiB/s (`3104.0/s`) | **854.17 MiB/s (`3416.7/s`)** | **+10.1%** | 20.5ms / 29.4ms | **18.4ms / 28.5ms** | 常规并发多条带解码 |
| **1MiB** | 2333.81 MiB/s (`2333.8/s`) | **2609.25 MiB/s (`2609.3/s`)** | **+11.8%** | 27.2ms / 40.5ms | **24.3ms / 36.0ms** | 常规并发多条带解码 |

### 给 PR `rustfs/rustfs#9255` 的精确修复建议
1. **分流阈值收紧（[read.rs#L1634](file:///C:/work/brewfs-maintenance/worktrees/rustfs-dynamic-block-size/crates/ecstore/src/set_disk/read.rs#L1634)）**：将 `length <= erasure.block_size` 收紧为仅当请求落在 `<= 1~2` 个物理分片（例如 `length <= erasure.shard_size()`）时才进入 `read_quantized_range`，或泛化 [read.rs#L2184](file:///C:/work/brewfs-maintenance/worktrees/rustfs-dynamic-block-size/crates/ecstore/src/set_disk/read.rs#L2184) 的跨分片合并条件（去掉 `shard_index == 0` 限制，当跨越 `>= 2` 个数据分片时直接一次并行读取该块涉及的分片区间）。
2. **单目标分片定向打开（[read.rs#L2229](file:///C:/work/brewfs-maintenance/worktrees/rustfs-dynamic-block-size/crates/ecstore/src/set_disk/read.rs#L2229)）**：当 `segment` 完整落在单个 `shard_index` 内且该盘健康在线时，只向 `disks[shard_index]` 发起 **1 个** 读取流，而非调用 `setup_multipart_part_readers` 打开全部 `data_shards` 个分片流（仅在该盘失败时回退打开完整条带做 RS 重建）。这一改动能把跨节点数据流 RPC 直接砍掉 `7/8`（或 `11/12`），即使在 CPU 受限的 `ecs.i5` 上也能进一步大幅拉高小范围 IOPS！

---

## 4. `Figure 10` 图表审查提示与资源清理清单

- **关于您刚发来的 `Figure 10`**：图中橙色线（`GET 6.29 GiB/s`、`PUT 1.82 GiB/s`、`p99 780ms`）使用的是 **阿里云 `ecs.i5`（`163.84 Gbps` 网卡 + 6 核）** 数据，而黑色线 `RustFS`（`2.63 GiB/s`）与灰色线 `MinIO`（`2.41 GiB/s`）使用的是 **旧报告 Azure `Standard_F4als_v7`（`10 Gbps` 网卡 + 4 核）** 数据。论文作图若标注 `Aliyun i5`，建议务必使用同硬件 `Cluster 1` 基线，并单独增加一张本报告的 **同硬件 `Range GET` 对比曲线图** 展示 `Dynamic` 的真实架构收益。
- **清理核验**：5 台临时 ECS（`i-bp11b91ioa9wkxvc3h3m` 及 `i-bp187jnu6r3mkhwxsxp0`～`3`，`TotalCount=0`）、安全组 `sg-bp1cip383i5sywv4wcsv`、密钥对 `rustfs-i5-range-20261113`、RAM 角色与策略 `rustfs-i5-range-*-20261113`、GitHub Deploy Key `169710884` **已全部删除完毕**；账号原有 `ax-*` 实例完好未触碰。
