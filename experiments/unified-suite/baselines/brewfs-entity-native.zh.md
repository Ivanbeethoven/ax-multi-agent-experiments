# BrewFS entity native lower 执行入口

负责entity引擎加冻结native lower的格式路径对照。先读[共同执行步骤](README.md)，默认systems中的`entity-native`在pool-a；可用精确arm映射拆到独立集群。

## 身份与工作负载

| 项目 | 固定身份 / 范围 |
|---|---|
| arm / product | `entity-native` / `brewfs` |
| engine / volume_format | `workspace-entity-v1` / `workspace-entity-v1` |
| lower / packed | `native` / false |
| entity_read_cache_bytes | **0**，当前native lower不支持packed payload cache |
| 范围 | C2完整trace、C3百万小文件、C4 fork |

## 本路径准备

1. 首次按本批BrewFS原生`--entity-native-source`能力冻结源，保存实际输出的`entity_native_manifest_key`、数据身份和所有导入成本。后续挂载只读保存的manifest，不再次扫描source做隐性导入。完整base校验及缓存重置按[输入](../04-inputs.zh.md)一次记账。
2. config核`volume_format=workspace-entity-v1`、真实非空`entity_volume_id`/`workspace` UUID、`workspace_namespace`及`entity_native_manifest_key`；不得同时填`packed_manifest_key`。每个新child的workspace/namespace独立。
3. 使用BrewFS prepare适配器生成entity挂载参数，显式`entity_read_cache_bytes=0`、`fuse.keep_cache=false`和冻结TTL；保留experimental entity挂载、同步上传和实际FUSE行为的原生资格。[配置约定](../examples/README.md)列共同字段。

## 测量与交付

执行[C2](../07-trace.zh.md)、[C3](../08-smallfiles.zh.md)、[C4 fork](../09-fork-handoff.zh.md)中本路径已列的cell。保存每文件对象路径、GET/Range及传输字节、metadata访问、冷/热读、COW、同步写和完整校验成本；没有payload cache支持时照实记录。

systems中的entity-native vs packed包含lower格式与缓存实现差异。需要缓存对齐的格式路径对照时使用[消融入口](brewfs-ablations.zh.md)的`packed-format-native-control`；纯metadata消融还需相同payload布局证明，不能只比较两条系统路径就认定纯metadata收益。

当前entity fresh-only挂载不属于原生handoff路径；不安排C1或handoff新场景。封存、排空、退出、隔离和远端持久化按[共同步骤](README.md)完成。
