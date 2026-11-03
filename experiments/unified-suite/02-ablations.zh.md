# JuiceFS、native 与三个技术点

## 系统路径

| arm | 路径 | 用途 |
|---|---|---|
| juicefs | 原生 JuiceFS，metadata 与 BrewFS 同类；原生 clone | 唯一外部 baseline |
| native | BrewFS flat-v1，无共享 lower | 普通 fio / trace / 小文件；fork 使用完整独立拷贝 |
| native-workspace | workspace-native-v2，原生 lower + workspace | Delta、trace、可重开的原生 handoff |
| entity-native | workspace-entity-v1 + 冻结 native lower | 与 entity packed 共用执行引擎的格式路径对照 |
| packed | workspace-entity-v1 + authenticated packed lower | packed 主候选，payload cache 默认 512 MiB |

执行者按[路径入口目录](baselines/README.md)分别接入；[消融执行入口](baselines/brewfs-ablations.zh.md)列每个case的精确arm及与系统路径入口的对应关系。全局矩阵在本页和方法目录维护，路径入口不追加未冻结的实验。

C1 用 native/JuiceFS；C2 用全部五条路径；C3 用 native/entity-native/packed/JuiceFS；C4 fork 用五条路径；handoff 只用具有原生重开能力的 native-workspace/JuiceFS，各做 stay/move。entity 的 fresh-only 挂载不能冒充现有 namespace 的跨机恢复。

设计时核对的 BrewFS CLI 已有 `--entity-native-source` 与 `--entity-native-manifest-key`；native lower 不是一个虚构开关。导入阶段使用 source，后续使用已保存 manifest。当前 `entity_read_cache_bytes` 只支持 packed lower，native lower 必须 0。正式批次仍需对本批二进制实际功能做资格检查。

## D：Delta / workspace 派生

固定引擎、base、payload/layout、缓存、元数据后端及数据语义，比较共享 lower 的新 workspace 与 eager private copy。测创建到首次可用、首次 COW、写放大、磁盘增长、强排空、parent/sibling 不变。

`delta` 条件在 native-workspace 引擎内比较 delta-on / delta-eager，后者必须实际物化为独立同引擎 base/upper，并记录总拷贝成本。仅 flat-v1 vs workspace-native-v2 是系统对照，不能标成“只关闭 Delta”。若同引擎物化适配器尚未资格通过，该条件保持阻断。

## P：Packed 元数据

有两种不同结论，均已列入计划：

1. `packed-format-native-control`：entity-native vs packed-cache-off，双方 payload cache=0。可回答整个 lower 格式路径的差异，保留 native 每文件对象与 packed 聚合对象的差异。
2. `packed-metadata`：同引擎、同 payload 对象/帧边界和相同缓存，仅切换 metadata 表示。必须通过 `identical-payload-layout`，否则只能做上一项，不能把格式路径对照写成纯 metadata 消融。

`delta-packed-interaction` 明确设计 D0P0、D0P1、D1P0、D1P1 四格，缓存=0。四格都需同引擎、相同 payload、同语义；缺少相应物化/寻址适配器时保留待实现状态，不生成假的性能单元。只运行可证明的组合不会消除缺失项，报告必须列出它们。

## G：对象粒度与服务端布局

| 条件 | 变化 | 固定 |
|---|---|---|
| client-object-size | g=256 KiB / 1 MiB / 4 MiB | 实际服务端 B/q、缓存、读写形状、上传策略 |
| backend-layout | 无 hint、64 KiB / 256 KiB / 1 MiB / 4 MiB hint | 客户端 g=4 MiB、最终对象字节、同 enabled RustFS |
| checksum-quantum | block-only / enabled | 实际 B=1 MiB、对象内容、服务拓扑 |

g、packed read frame、RustFS EC block B、校验读取 quantum q 是四个不同量。一次只改声明的因素；小对象内联使 hint 未生效、B相同q也相同、或上层根本没传 hint，都应记录 no-effect/不具备条件，不能推导因果收益。

所有批次先解析指定 RustFS 分支最新代码，批次内固定镜像/客户端；默认服务端四开关开启、BrewFS 请求 auto。auto 的行为以本批源码与实际对象为准，不称多档自适应。[RustFS 部署与实际布局验收](../../infra/rustfs/README.md)提供探测方法。

设计时检查 `src/cli/entity_mount.rs` 发现 entity S3 构造使用默认 hint；给挂载命令加 auto 不足以证明 upper 上传传出了 hint。因此 G 主消融使用已核实上传路径的 native，entity lower producer 和 upper 另记录实际 hint/status/B/q。这里不修改 BrewFS 运行时。

## 缓存辅助消融

`payload-cache` 固定 packed 格式与实际对象，只切 512 MiB / 0，其他参数一致。必须同时记录 lower/upper 命中、miss、驻留、eviction、对象 GET/Range 和传输字节。预取/解码帧实现的不同版本不能混在同一 block；若测试独立预取策略，另做明确开关/版本的研究，不能暗中换 binary。

最终报告分别写系统结果、纯技术点结果、格式路径结果、未具备条件的组合。配置有开关不等于能力实现，也不等于性能更好。
