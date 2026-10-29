# 实验机器与磁盘配置要求（强制）

> 当前研究与执行口径以 [最终方案](FINAL-PLAN.zh.md) 为准。本文冲突的超时、性能阈值作废及拓扑解释不再适用；现有流水线尚未通过机制/测量资格，具体问题见 [执行器审阅](REVIEW-20261028.md)。

这份文件规定**每一批实验**的机器与磁盘配置。不符合这里的条件，该批数据不能进论文。
写这份文件的直接原因：2026-10-28 那批四台实验机，数据盘配到了 14,300 IOPS，
但**没有任何地方规定「重活必须落在数据盘」**，而 `/work` 本身在系统盘（6,800 IOPS / 170 MB/s），
一旦有人把语料写到 `/work/corpus`，整格数据会被系统盘卡住而不自知。

---

## 1. 机器规格

| 项 | 要求 | 原因 |
|---|---|---|
| 实例族 | **通用型 8 vCPU**（`ecs.g7.2xlarge` 或同代同规格） | 经济型 `e-*` 的顺序吞吐封顶 205 MB/s，会把所有实现压成同一个数 |
| 内存 | **≥ 30 GiB** | 8 GiB 语料 + 8192 MiB 缓存 + 后端与客户端进程 |
| swap | **≥ 15 GiB** | 防止 OOM killer 杀掉后端或测量进程 |
| 区 | 同一批**全部同区同规格** | 跨区/跨规格会引入与方法无关的方差 |
| 计费 | 抢占优先，抢不到用**按量** | 抢占随时可能被回收；按量贵但不会中途消失 |

---

## 2. 磁盘配置（本节是硬要求）

### 2.1 容量与等级
| 盘 | 最低要求 | 供应能力（ESSD PL1） |
|---|---|---|
| **数据盘** | **4 × 250 GiB ESSD PL1** | 每块 **14,300 IOPS / 245 MB/s**，四块聚合 **57,200 IOPS / 980 MB/s** |
| **系统盘** | 100 GiB ESSD PL1 | **6,800 IOPS / 170 MB/s** —— **不允许承载任何测量 I/O** |

ESSD PL1 的能力按容量推算（阿里云规则）：
```
IOPS     = min(50,000, 1,800 + 50 × 容量GiB)
吞吐MB/s = min(350,    120   + 0.5 × 容量GiB)
```
所以 **100 GiB 只有 6,800 IOPS，而 250 GiB 有 14,300**。
若某实验需要更高，要么加容量，要么升到 PL2/PL3（PL2 需显式设置，且 2026-10-27 实测
PL2 的顺序吞吐反而低于 PL1，**升 PL 之前必须先跑裸盘标定**）。

### 2.2 为什么必须 4 块盘
不是冗余偏好，而是**实验前提**：RustFS 的纠删码需要 ≥4 个 shard 才能真实分片。
单盘时 EC 退化为 1 个分片，**与纠删码、动态块、读量子相关的实验全部失去意义**
（2026-10-27 实测：单盘时每个对象只有一个 `part.1`，8 MiB 对象占 8.1 MB，无任何冗余开销）。

4 块 250 GiB 下实测：8 MiB 对象在四块盘各占约 4 MiB，总计约 2 倍冗余 ⇒ **EC 2+2 生效**。

### 2.3 目录落点（强制）
| 目录 | 必须落在 | 说明 |
|---|---|---|
| **语料**（8 GiB 数据集） | **数据盘** | 例：`/work/d1/corpus` |
| **fio 目标文件** | **数据盘** | 落系统盘等于自废 IOPS |
| **客户端缓存目录**（BrewFS / JuiceFS `--cache-dir`） | **数据盘** | 缓存命中率实验直接受其 IOPS 影响 |
| **打包卷产物** | **数据盘** | 体积可达数 GB |
| **RustFS 的 4 个 volume** | **数据盘**（每块盘一个） | `/work/d1..d4`，各挂一块 |
| 脚本、日志、结果 CSV、挂载点 | 系统盘可以 | 体积小、不参与测量 |

**判定方法**（每次开工与收尾都跑）：
```bash
df --output=source,target /work /work/d1 /work/d2 /work/d3 /work/d4
df --output=source /work/d1/corpus          # 必须指向数据盘的设备名
```
**并要求每个测量格在报告里写明：它的语料、目标文件、缓存目录分别在哪个设备上。**

### 2.4 裸盘标定（每批必做，先于任何性能结论）
在**数据盘**上（不是系统盘）用裸 ext4 跑：1 MiB 顺序写、4 KiB 随机读，`direct=1`、8 任务、深度 32。
结果写进该批的 `ceilings.json`。
**之后任何超过该上限的结果，一律标为「缓存命中」，不得作为后端能力写入对比表。**

---

## 3. 后端部署要求

| 项 | 要求 |
|---|---|
| 镜像来源 | 从**冻结提交**构建，`org.opencontainers.image.revision` 必须等于该提交号 |
| 分支 | `feat/dynamic-block-size-rebased`（`Ivanbeethoven/rustfs`），**pin 提交号，不 pin 分支名**（分支无保护） |
| volume 数 | **4**（对应 4 块数据盘），否则 EC 不真实分片 |
| 动态块开关 | 四个环境变量全 `true`：`RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE`、`..._HINT_FLEET_CONFIRMED`、`RUSTFS_EC_READ_QUANTUM_ENABLE`、`..._QUANTUM_FLEET_CONFIRMED` |
| 运行时证据 | 必须记录 `x-rustfs-internal-ec-read-quantum` 标记**出现或未出现**，以及四块盘的对象占用比。**开关为 true 不等于特性生效。** |

**已知事实（2026-10-27/28）**：四卷 EC 2+2 已真实分片，四开关全 `true`，
但该标记在 `xl.meta` 与 S3 元数据里**仍未出现**。因此涉及动态块的组别目前一律标
`unmeasured(特性未生效)`，**不得声称「用了那个分支就等于启用了该特性」**。

---

## 4. 每批开工前的检查清单

```bash
# 1) 规格
nproc                                   # 应为 8
free -g                                 # total ≥ 30，swap ≥ 15
# 2) 磁盘等级与容量（API 侧）
#    aliyun ecs DescribeDisks --RegionId <R> --InstanceId <ID>
#    应看到 4 块 250GiB data (cloud_essd, PL1) + 1 块 100GiB system
# 3) 挂载
df --output=source,target /work/d1 /work/d2 /work/d3 /work/d4
# 4) 落点
bash paper-design/tools/check-disk-layout.sh /work
# 5) 裸盘标定
#    在数据盘上跑 fio，写入 ceilings.json
```

`check-disk-layout.sh` 会直接判红：只要发现语料/缓存/目标文件落在系统盘，就非零退出。

---

## 5. 不允许的省事做法

| 做法 | 为什么不行 |
|---|---|
| 系统盘从 40 GiB 起、把语料放 `/work` | 6,800 IOPS 会先于被测系统饱和，整格数据无效 |
| 单数据盘 | EC 退化，纠删码/动态块相关结论全部失去意义 |
| 混用实例族或跨区 | 顺序吞吐差 1.9 倍（205 vs 392 MB/s），结论会被硬件吃掉 |
| 抢占被回收后换一台不同规格续跑 | 前后两半不可比 |
| 只在容器 `env` 里看到开关为 true 就宣称特性生效 | 必须看持久元数据里的标记与分片形态 |

## 6. 动态块（dynamic block）为什么「开关全开也没有生效」—— 已定位到源码

2026-10-28 实测：4 卷 EC 2+2 已真实分片（4 块盘各占约一份分片，总计约 2 倍冗余，
8 MiB 对象在磁盘上约 16 MiB），但 `x-rustfs-internal-ec-read-quantum` 标记
在 `xl.meta` 与 S3 响应里都不存在。

读源码后确认**这不是配置错误，而是默认参数下该特性在数学上就是 no-op**。

### 写入条件（`crates/ecstore/src/set_disk/ops/object.rs:3691`）

```rust
let frame_size = if !is_inline_buffer && !protect_write && ec_read_quantum_write_enabled() {
    select_ec_read_quantum(fi.erasure.block_size, erasure.data_shards, shard_size)
        .unwrap_or(shard_size)
} else {
    shard_size
};
if frame_size != shard_size {                       // 只有「变了」才写标记
    insert_ec_read_quantum(&mut user_defined, frame_size);
}
```

`ec_read_quantum_write_enabled()` 只是两个环境变量相与：
`RUSTFS_EC_READ_QUANTUM_ENABLE && RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED`（默认均为 `false`）。
**开关为 true 只代表「允许走这条路」，不代表「这条路的输出会变」。**

### 几何条件（`ec_block_layout.rs:283`）

```
quantum <= shard_size
且 shard_size % quantum == 0
且 (quantum * data_shards) 整除 block_size
```

候选值固定为 `{1 MiB, 512 KiB, 256 KiB, 128 KiB, 64 KiB, 32 KiB, …}`。

### 关键结论：默认 `B = 1 MiB` 必然不写标记

`DEFAULT_EC_BLOCK_SIZE = 1024 * 1024`（`ec_block_layout.rs:13`）。以 EC 2+2 为例：

| B（EC 块大小） | shard_size | 选出的 quantum | 是否写标记 |
|---|---:|---:|---|
| 64 KiB | 32 KiB | 无（几何不匹配） | 否 |
| 256 KiB | 128 KiB | 64 KiB | **是** |
| **1 MiB（默认）** | 512 KiB | **512 KiB（等于 shard_size）** | **否** |
| 4 MiB | 2 MiB | 1 MiB | **是** |

因此在默认配置下，无论四个开关怎么设，`frame_size == shard_size` 恒成立，标记永不写入。
这一点与 `REORG-EXPERIMENT-PLAN.zh.md` 里的提醒一致：
「`B=1 MiB` 可能与旧布局相同，不能把『没有新 q 标记』当成失败」。

### 要让特性真正生效，必须显式改变 B

两条路，二选一：

1. **客户端提示头**：每次写请求带
   `x-rustfs-internal-ec-block-size-hint: 262144`（256 KiB）或 `: 4194304`（4 MiB）。
   该头名即常量 `EC_BLOCK_SIZE_HINT_INTERNAL_KEY`。
   服务端需 `RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE=true` 与 `..._FLEET_CONFIRMED=true`。
2. **服务端默认 B**：源码里 `DEFAULT_EC_BLOCK_SIZE` 是**编译期常量**，没有对应环境变量，
   所以只能通过客户端提示头改变。**不要以为调环境变量能改 B。**

### 因此对实验的约束

- 任何声称「启用了动态块」的批次，**必须记录 B 的来源**（提示头值或默认值），
  并记录 `x-rustfs-internal-ec-read-quantum` 的实际值（例如 `1048576`）；
- **B 取默认 1 MiB 的批次，动态块相关格子一律标 `unmeasured(默认 B 使特性成为 no-op)`**；
- 只把容器 `env` 里的四个开关打印出来**不构成任何证据**。

