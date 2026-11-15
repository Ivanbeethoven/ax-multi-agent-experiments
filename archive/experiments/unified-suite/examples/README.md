# 配置样例

> 历史协议，仅用于原冻结计划；新实验见[当前方案](../current/README.zh.md)。

这些文件展示字段，`FILL_*`、null和reviewed=false均不能通过执行。复制到本轮RUN后填实际值并用suite_support pin引用；不改仓库样例充当运行状态。

| 文件 | 接收工具 |
|---|---|
| [fio.request.json](fio.request.json) | runtime.workloads / fio_workload |
| [smallfiles.request.json](smallfiles.request.json) | runtime.workloads |
| [fork-burst.json](fork-burst.json) | fork_burst；各child step的stage必须为fork-<child id> |
| [closure.request.json](closure.request.json) | suite_support closure；在实际Linux节点执行 |
| [cell-close.json](cell-close.json) | suite finish；证据完整与真实关闭后 |

所有worker请求中的mount.spec必须已review；step/worker/cohort按叶子到父级顺序review。fio参数从plan复制；sample/qualification/asset/stage草稿由 `suite_support drafts` 为真实cell生成，不需要手写所有ID。

BrewFS原生配置须明确 `volume_format`、meta.backend（TiKV/Redis实际字段）、data.s3、同步cache.writeback_mode、cache_root、fuse.cache_ttl_ms/keep_cache。entity另用真实entity_volume_id/workspace UUID、workspace_namespace及恰好一个packed_manifest_key/entity_native_manifest_key；配置中的entity字段由生成器转为CLI显式参数。packed payload cache=536870912，entity-native=0，entity keep_cache=false。

native-workspace还必须提供真实、通过本批BrewFS原生验证的`native_base`完整配置及`volume.chunk_size/block_size`；生成器固定`volume.format/schema_version/native_control_version`与所选引擎一致。native_base的P1合同预算/禁用预取声明不等于native VFS实际缓存实现，不用这些声明替代有效缓存计数。切换到flat/entity时不能残留native workspace控制字段；对象粒度消融同时固定layout与volume的block_size。

使用`bench.suite_mount`从本cell plan生成[完整显式设置](../13-defaults.zh.md)，保留实际metadata_url/endpoint/namespace。JuiceFS新默认`cache_dir:memory`、`cache_mib:512`、`buffer_mib:512`、`writeback:false`；前者是内存读缓存，后者是读写缓冲。BrewFS native读内存512MiB、clean SSD=0，entity使用独立CLI预算；不得继承未声明的生产缓存值。prepare必须携带suite-plan/suite-cell，node会再次核对。实际FUSE/page-cache/缓存生效与RSS仍要原生验证。
