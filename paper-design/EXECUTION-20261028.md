# 2026-10-28 批次执行边界

本文件把 2026-10-28 的四组低层负载与 50 格计划分开记录。该批实际执行的是后端带宽 W1、缓存收益 W2、重复打开 W3、元数据 W4；它不是 `run-grid.json` 的完整 50 格批次。严格按负载、参数和有效产物完全一致计算，本批覆盖度为 **0/50**；不得把本批描述为覆盖 50 格，也不得把任何未测量值填成零。

## 负载到 cell 的映射

| 实际组 | 最接近的 `run-grid.json` cell_id | 对齐结论 | 本批状态 |
|---|---|---|---|
| W1 后端带宽（1 MiB write/read、4 KiB randwrite/randread、direct=1） | `write-flat`、`write-overlay`、`write-packed`、`write-juicefs` | 只有写入成本类别近似；网格是 ABBA/小文件/大文件/随机覆盖/写入中删除，参数不相同，故 canonical 覆盖为 `unmapped` | C4 全部 `unmeasured(G1 mount failed)`；C5 顺序读写 `void(cache hit)`，随机行仅作外部基线 |
| W2 缓存收益（8 GiB 语料、8192 MiB cache、冷/热 read/randread） | `no-cell` | 50 格没有独立冷/热缓存收益轴；`reopen-*` 不替代 W2 | C4 `unmeasured(G1 mount failed)`；审计批次的 C5 W2 全部 `void(heat/cold<2x)` |
| W3 重复打开（4 KiB/64 KiB/1 MiB，200 轮，KEEP_CACHE on/off） | `reopen-flat-on`、`reopen-flat-off`、`reopen-overlay-on`、`reopen-overlay-off`、`reopen-packed-on`、`reopen-packed-off`、`reopen-juicefs` | 参数和判据最接近；JuiceFS 的默认缓存保留 `no-equivalent` 标记 | C4 全部 `unmeasured(G1 mount failed)`；C5 有 9 行外部基线 |
| W4 元数据（1k/10k/100k inode，各 100,000 stat，TTL） | `attrs-flat-0`、`attrs-overlay-0`、`attrs-overlay-1000`、`attrs-juicefs` | 只部分对应；本批还测 packed 和更多 TTL 组合，网格没有 `attrs-overlay-packed` | C4 全部 `unmeasured(G1 mount failed)`；C5 有 9 行外部基线，`stat` 可能命中内核 attr 缓存 |

W1 的四个名字是类别近似，不是有效 cell 认领；W2 明确是 `no-cell`。因此“候选映射 11 行”不改变 0/50 的严格覆盖度。

## 产物位置和状态分布

| 机器/配置 | 节点侧产物路径 | 本机只读归档解析路径 | 归档 SHA-256 | status 分布（审计批次快照） |
|---|---|---|---|---|
| C4 BrewFS Overlay + Packed lower | `/work/mnt-c4`、`/work/d1/c4-data`、`/work/d1/c4-catalog.db` | `/home/hxy/exp-results/c4/` | `4b6f77051afc456155e3c37e5990d7aaacfd65c891e92eac7185a84e58c31ef0` (`c4.tgz`) | `ok=0`, `void=0`, `unmeasured=54`（W1 12、W2 6、W3 18、W4 18） |
| C5 JuiceFS | `/work/mnt-c5`、`/work/d1/jfscache`、`/work/d1/c5-corpus` | `/home/hxy/exp-results/c5/` | `7c7b166c94d6d15c834116a8ddc44dc4bf5adc55f987362ed8975d41f0e8a488` (`c5.tgz`) | `ok=24`, `void=18`, `unmeasured=0`；W2 12 行 `void(heat/cold<2x)`，W1 6 行 `void(cache hit)` |

路径同时保留节点解析路径和内容 SHA-256，便于跨主机复核。`c4` 的 G1 挂载失败阻断 G2/G3/W1-W4；`c5` 只作为 JuiceFS 外部基线，不能替代 BrewFS 格子。若 `/home/hxy/exp-results/c5/` 后续出现重跑文件，以归档 SHA 和本表为界，不回填本批状态。

## 已知缺陷和解释边界

- C4 全线 `unmeasured`：`workspace-v1` 挂载在 FUSE attach 前因 `packed workspace binding requires complete catalog permission/ID/lifecycle capabilities` 失败，三次相同失败。
- C5 的 W2 在本批快照全部 `void(heat/cold<2x)`；不把缓存收益写成有效结论。
- C5 的 `stat` 可能命中内核 attr 缓存，W4 的数据只作已标注外部基线。
- 动态块资格仍要求实际 B/q 与持久布局变化；本批 W1-W4 没有 object-granularity 的有效证据。
