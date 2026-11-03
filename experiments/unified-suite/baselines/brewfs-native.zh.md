# BrewFS native flat 执行入口

负责`flat-v1`内部原生对照。先读[共同执行步骤](README.md)，默认与其他BrewFS路径共用pool-a；若单独并行，冻结前将`arm_pools.native`指向独立实际pool。

## 身份与工作负载

| 项目 | 固定身份 / 范围 |
|---|---|
| arm / product | `native` / `brewfs` |
| engine / volume_format | `flat-v1` / `flat-v1` |
| 范围 | C1 fio、C2完整trace、C3百万小文件、C4 fork |
| 派生语义 | 无共享lower；fork为完整独立拷贝 |

本入口不执行`native-workspace`或entity路径；它们各有[workspace](brewfs-native-workspace.zh.md)、[entity-native](brewfs-entity-native.zh.md)和[packed](brewfs-packed.zh.md)入口。flat与workspace比较是系统路径对照，不能直接称为同引擎Delta消融。

## 本路径准备

1. 核本批BrewFS binary/features及RustFS固定版本。使用独立flat namespace，按[输入](../04-inputs.zh.md)经原生挂载复制源到base，保存真实复制、排空、退出和完整base读回；不使用packed producer或entity manifest。
2. 各cell使用新的私有数据身份/工作副本及缓存。fork完整复制成本按实际阶段保留，容量按base×children及元数据预留；不能将硬链接到可写父目录当作私有副本。
3. 挂载草稿使用`bench.runtime.prepare mount --filesystem brewfs`和本批`volume_format=flat-v1`配置，按[单步执行](../05-execution.zh.md)核配置/命令摘要并review。

原生config核`meta.backend`、`data.backend=s3`、`cache.writeback_mode=upload_before_commit`及显式FUSE TTL/keep_cache。客户端payload预算按plan记录，实际生效值与page-cache单列；不要给flat路径套用entity专用`--entity-read-cache-bytes`。BrewFS hint默认auto，记录实际请求与RustFS布局。

## 测量与交付

按冻结cell执行[C1 fio](../06-fio.zh.md)、[C2 trace](../07-trace.zh.md)、[C3小文件](../08-smallfiles.zh.md)或[C4 fork](../09-fork-handoff.zh.md)。保留完整拷贝的读写/对象量、首次可用、fsync、强排空、真实退出和隔离/远端读回。

系统计划没有flat handoff arm，不自行补做跨机恢复。g/B/q技术点条件使用[消融入口](brewfs-ablations.zh.md)的精确arm和资格，不能把本系统对照换参数后仍叫`native`。完成后按[共同步骤](README.md)封存和关闭本cell。
