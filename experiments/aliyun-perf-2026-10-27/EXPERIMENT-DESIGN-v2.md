# brewfs / native-overlay 实验设计 v2

v1 的问题：把「跑完矩阵」当成目标，没有先证明**每个数字到底在测什么**。今天一天的数据说明，
同一条 fio 命令在不同缓存/实例/挂载条件下能差 56 倍，而我们把这种差异当成了系统差异。
v2 把**先证明测的是什么**放在测之前。

---

## 0. 三个必须先承认的事实（今天的实测）

| 事实 | 证据 |
|---|---|
| 实例族决定吞吐上限 | 同一块 500 GiB PL1 盘：`ecs.e-c1m4.2xlarge` 205 MB/s，`ecs.g7.2xlarge` 392 MB/s |
| 盘容量/PL 不是瓶颈 | PL2 500 GiB 实测 184 MB/s，**比 PL1 还慢**；4K 随机能到 29k IOPS，说明限制在实例的完成路径 |
| **`direct=1` 在 FUSE 上不等于读盘** | `overlay-native` 的 `direct=1` 顺序读写 **4,273–4,536 MiB/s**，是 native 实测上限 355 MB/s 的 **12 倍** ⇒ 读的是 brewfs 自己的缓存，O_DIRECT 没有穿透到后端 |

第三条是 v1 最严重的漏洞：**我们一度把 4.3 GB/s 当作 overlay 的「真实后端吞吐」**。
在 FUSE 上，`O_DIRECT` 是否穿透取决于实现与挂载选项，不能假设。

---

## 1. 先定义「这条数字测的是哪条路径」

任何一行结果都必须标出它落在哪一段：

```
应用 → [内核页缓存] → [brewfs 块缓存] → [JuiceFS 本地缓存] → FUSE → 后端(S3/裸盘)
       ①              ②                 ③                    ④      ⑤
```

| 目标 | 必须关掉 | 必须验证 |
|---|---|---|
| **后端路径**（⑤，论文主表） | ①②③ 全关；每次测量前 `drop_caches` 并 remount | 观测到 `read_disk > 0`、`read_memory ≈ 0`；吞吐 ≤ 实测盘上限（g7: 355 MB/s） |
| **缓存收益**（①+②+③） | 不关，但**语料必须装进内存** | `read_disk ≈ 0`、`read_memory = 语料字节数` |
| **元数据路径**（不碰数据盘） | 无所谓 | 用 `metaperf`/`smallfiles`/`dirstat`，报告 ops/s 与 p99 |

**硬规则：任何一行的吞吐超过实测盘上限，必须能指出它命中了哪一层缓存，否则该行作废。**

---

## 2. 开测前的门槛（不通过就不测）

1. **盘上限标定**：同一台机、同一个盘，裸 ext4 跑一次 `4m × 8 jobs × depth 32 × O_DIRECT`，
   把数字写进 `ceilings.json`。后面所有结果都对着它判断「是否饱和」。
2. **缓存可控性证明**：同一格跑两次，一次关全部缓存、一次全开；两次结果必须**显著不同**，
   否则说明缓存开关没生效，本轮作废。
3. **`direct=1` 穿透证明**：FUSE 系统的 `direct=1` 结果若高于盘上限，必须在报告里标注「缓存命中」，
   或者改用 brewfs/JuiceFS 自己的缓存关闭开关，不要依赖 O_DIRECT。
4. **`--cache-size` ≤ 内存 60%**，且**语料总量 ≤ cache-size**。今天误设 64 GiB 缓存 + 124 GiB 语料，
   内核直接回收 130 万次，`load` 175，读数全部无效。
5. **后端版本钉住**：RustFS 用 `feat/dynamic-block-size-rebased`，每批写 `backend-pin.json`
   （`frozen_commit` / `image_digest` / `built_from_commit`），并用 OCI label
   `org.opencontainers.image.revision` 机械校验源码与镜像一致。
6. **抢占不算不可用**：`SpotDuration=1` 只保 1 小时。要么用按量实例，要么**每 10 分钟把台账同步出机**。

---

## 3. 矩阵（按优先级排序，先做能出结论的）

### P0 —— `#350` 的 reopen 变体（今天完全没跑，是最大缺口）
`FOPEN_KEEP_CACHE` 只影响「关闭后重开同一文件」时的页缓存保留。单次顺序读**测不出来**，
今天四格 before/after 差异只有 −1%～+2.7%，就是这个原因。

| 格 | 负载 | 说明 |
|---|---|---|
| 4 格 | `open → 读 64 MiB → close → open → 再读 64 MiB`，`direct=0` | 第 2 次读的 `read_disk` 应为 0（keep-cache 生效） |
| 4 格 | 同上，`direct=1` | 对照组 |
| × 2 | `before 8dbe009` / `after 5a8c6ae` | 唯一能正面回答 #350 的设计 |

**判据**：比较「第 2 次读的 `read_disk` 字节数」。keep-cache 生效时它应该接近 0。

### P1 —— 后端路径的四方对比（论文主表）
`native` / `overlay-native` / `overlay-pack` / `juicefs`，全部缓存关、每次 drop+remount。

- 载荷：`seqread`、`seqwrite`（`bs=1m`）、`bigread`、`bigwrite`（`bs=4m`）、`randread`、`randwrite`（`bs=4k`）
- `direct=1` 与 `direct=0` 各一组（`direct=0` 在有缓存时只作对照，不作主表）
- `numjobs=8`，`iodepth=32`，**每格 3 次，报 min/median/max**
- 合计 **4 系统 × 6 载荷 × 2 direct = 48 格**

### P2 —— 缓存收益（单独一条曲线，不与 P1 混）
语料 8 GiB，缓存 8–12 GiB，预热后计时：每系统 6 载荷 × 3 次 = 18 格 × 4 系统。
**必须与 P1 分表报告**，两表的比值就是「缓存加速比」，今天看到的是 56×。

### P3 —— 元数据路径（不碰数据盘，最能拉开差距）
`metaperf`、`smallfiles-create/stat`、`dirstress`、`dirperf`，4 系统 × 5 负载 × 3 次。
这一组不受盘上限影响，**是 overlay 与 native 差距最可能显现的地方**（#364 报告 9.85–15.51× 耗时比）。

### P4 —— 可选：实例族/盘规格敏感性
只在需要解释「为什么 130–200 MB/s 打平」时做，一支就够：同一负载在 `e-c1m4` 与 `g7` 各测一次。

---

## 4. 失败策略（写死，避免现场纠结）

| 情况 | 处理 |
|---|---|
| `direct=1` 结果 > 盘上限 | 标记「缓存命中」，不计入后端表 |
| 缓存开关前后结果相同 | 缓存开关无效，该轮作废，先修开关 |
| `load > 4×vCPU` 且无 fio 进程 | 内存回收风暴，降缓存或降并发后重跑该格 |
| 实例被抢占 | 从最后一个已落盘格子续跑；台账每 10 分钟同步 |
| 打包卷建不出来（需编译夹具） | **预编译夹具随二进制分发**，不在测量机上编译 |
| 后端分支/构建不可用 | 写 `blocked_reason` 停批，不换分支、不退回 `latest` |

---

## 5. 交付格式

- `cells.csv`：`group,system,tool,direct,numjobs,iodepth,path,cache_config,compression,min,median,max,n,binary_sha,backend_pin,status`
  - `path` 是新加的：`backend` / `cache` / `metadata`，对应 §1 的三条路径
- `ceilings.json`：`{instance_type, disk, seq_write, seq_read, randread_iops}`
- `backend-pin.json`：见 §2.5
- 报告首段必须写：**每个数字属于哪条路径、是否被盘上限限制、缓存是否命中**

---

## 6. 与 v1 的差异总结

| v1 | v2 |
|---|---|
| 直接跑矩阵 | 先标定盘上限 + 证明缓存开关有效 |
| `direct=1` 当作「读盘」 | 显式区分 5 段路径，超过盘上限就判缓存 |
| 缓存全开取最大值 | 缓存作为独立曲线（P2），与后端表（P1）分开 |
| 没跑 reopen | P0，只有它能回答 #350 |
| 语料越大越好 | 语料必须 ≤ 内存，否则热读是假热读 |
| 一台机器跑到底 | 抢占会丢台账，必须增量同步 |
