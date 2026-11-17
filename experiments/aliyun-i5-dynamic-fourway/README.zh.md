# 阿里云 ecs.i5 四方独立对象存储评测与论文图

本目录将阿里云 `ecs.i5` 四节点 16 盘 NVMe 集群上的四方对比实测（Dynamic RustFS vs. 固定 1 MiB RustFS vs. 原生 MinIO vs. Dynamic MinIO 方案 B）提取为结构化数据，并渲染论文 `Section 8.6` 的独立对象存储评估图与数值宏。

## 实验口径

- **硬件与负载驱动**：`cn-hangzhou-j` 4 × `ecs.i5.16xlarge`（每节点限制 6 物理核、12 GiB 内存上限、4 块本地 PCIe 5.0 NVMe SSD，合计 16 盘、48 GiB 集群内存、163.84 Gbps 网络）；独立负载机使用 `MinIO Warp`（`concurrency=64`），直接通过 S3 接口施压，不经过 BrewFS 客户端。
- **四方对照**：
  1. **RustFS (dynamic)**：`{64 KiB, 256 KiB, 1 MiB, 4 KiB->4 MiB}` 动态编码块策略 + 帧对齐单分片定向直读（含 `PR #3` `1fbd48a39`）。
  2. **RustFS (1 MiB)**：固定 1 MiB 编码块基线（整对象 30 轮为同分支动态开关全关；冷盘 Range GET 为 `1.0.5-preview.1` 及同构 `8+8` 基线）。
  3. **MinIO (1 MiB)**：官方 `RELEASE.2025-10-15T17-29-55Z` 固定 1 MiB 编码块基线（含 `12+4` 与同构 `8+8` 冷盘基线）。
  4. **MinIO (dynamic)**：移植相同 `{64 KiB, 256 KiB, 1 MiB, 4 MiB}` 动态块策略 + 跨分片定向直读（逐帧完整性验证与 100 ms 对冲 RS 重构回退）。
- **图表结构（`240 × 268 pt`，单栏 `3×2`）**：
  - `(a)` 冷盘 `Range GET` 请求吞吐（`EC 8+8`，`4 KiB – 1 MiB`，50 GiB 数据集 + 每轮前 `drop_caches=3`）。
  - `(b)` 冷盘 `Range GET` 请求吞吐（`EC 12+4`，`4 KiB – 1 MiB`）。
  - `(c)` 冷盘 `4 KiB Range GET` P99 尾延迟对比（`ms`）。
  - `(d)` `EC 12+4` 整对象与混合负载下动态块相对各自 1 MiB 基线的代表性吞吐增益（含 `64 MiB PUT` 大条带 +11.1%、`128 KiB Mixed` +9.9%、`4 KiB GET` +5.4%、`16 KiB PUT/Mixed` +2.9%）。
  - `(e)` `EC 12+4` 16 盘 NVMe 后端聚合冷读带宽（`GiB/s`，`4 KiB` 下降 `98.8%` / `98.0%`，`1 MiB` 仍下降 `41.4%` / `38.7%`）。
  - `(f)` `EC 12+4` 后端磁盘与内存资源节省率（`4 KiB` 单请求读盘字节 `−99.1%`、`≤64 KiB` 编码缓冲内存 `−93.8%`、`4 KiB` 触盘数 `−85.4%`、`4 KiB` 16 盘读 IOPS `−81.1%` / `−69.5%`、`16–64 MiB` 写 chunk 操作数 `−75.0%`，XFS 4 KiB 对齐空间额外开销 `0.00%`）。

## 复现命令

```powershell
C:\Users\HXY\.cache\ax-fig-20261027\Scripts\python.exe experiments\aliyun-i5-dynamic-fourway\prepare.py
C:\Users\HXY\.cache\ax-fig-20261027\Scripts\python.exe experiments\aliyun-i5-dynamic-fourway\render.py
```

输出文件：
- `paper/figures/pilot/backend-dynamic-standalone.{pdf,svg,png}`
- `paper/figures/pilot/backend-dynamic-values.tex`
- `qa/alignment.{json,svg}`、`qa/pdf_text.json`、`qa/plot-data.json`
