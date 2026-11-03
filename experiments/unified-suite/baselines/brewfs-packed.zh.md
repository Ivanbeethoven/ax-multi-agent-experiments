# BrewFS packed 执行入口

负责entity引擎加authenticated packed lower的主候选。先读[共同执行步骤](README.md)，默认systems中的`packed`在pool-a；独立并行可将arm_pools.packed映射到自己的实际集群。

## 身份与工作负载

| 项目 | 固定身份 / 范围 |
|---|---|
| arm / product | `packed` / `brewfs` |
| engine / volume_format | `workspace-entity-v1` / `workspace-entity-v1` |
| lower | authenticated packed manifest，packed=true |
| entity_read_cache_bytes | **536870912**（512MiB） |
| 范围 | C2完整trace、C3百万小文件、C4 fork |

## 本路径准备

1. 按[输入与一次导入](../04-inputs.zh.md)执行本批packed producer并上传一次，保留manifest/authentication、producer源码与参数、实际对象/索引量、上传hint、排空及真实退出。producer临时盘路径/峰值与临时SQLite计入导入，不放未声明的系统盘。
2. 本协议初始producer参数为chunk/block=4MiB、metadata/data codec=raw、inline=off、size-only、random-small-file profile、max random frame raw=256KiB；以本批CLI实际能力及manifest为准。参数或布局变化使用独立asset。
3. 保存实际`packed_manifest_key`，完整base验证一次后复用此不可变资产。config核entity volume/workspace UUID、workspace_namespace、恰好一个packed lower；不填`entity_native_manifest_key`。
4. 用BrewFS prepare适配器生成experimental entity挂载，显式cache=536870912、keep_cache=false及冻结TTL。plan写明的预算仍须以本批client实际计数/驻留和对象请求核生效；不通过改运行时补齐正在测量的资格。

## 测量与交付

执行[C2](../07-trace.zh.md)、[C3](../08-smallfiles.zh.md)、[C4 fork](../09-fork-handoff.zh.md)中本路径的cell。分别保存创建、冷读、lower预热与hot-read、首次COW、upper预热/热写/mixed、fsync、强排空、普通卸载、真实退出和隔离/远端读回。记录lower/upper命中、miss、eviction、GET/Range、压缩/解码与传输字节，逻辑热集大小不等于实际帧驻留量。

对象粒度g、packed frame、RustFS B/q分别核；entity upper是否实际传hint以原生请求/对象布局为准，不能仅因命令含auto就认定生效。预取/解码实现版本随本批冻结，测量中不热换二进制。

`cache-on/off`、`packed-on/off`、`packed-cache-off`和D×P均走[消融入口](brewfs-ablations.zh.md)对应cell，不能把主arm临时设0后仍报告为同一个packed系统结果。当前entity路径不做原生handoff，系统计划不包含packed C1；封存与关闭按[共同步骤](README.md)。
