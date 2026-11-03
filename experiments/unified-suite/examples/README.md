# 配置样例

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

JuiceFS配置使用 `metadata_url`、`cache_dir`、`cache_mib:0`（clean SSD）、`buffer_mib:512`、`writeback:false`、`attr_cache_seconds`、`entry_cache_seconds`，实际FUSE/page-cache与buffer驻留另验证。512MiB buffer参数不等于512MiB可驻留读缓存；预算、实际占用和缓存实现都要披露，不能当作与BrewFS payload结构完全等价。
