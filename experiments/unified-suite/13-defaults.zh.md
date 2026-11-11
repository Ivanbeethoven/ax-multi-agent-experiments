# 默认配置与启动前核对

> 历史协议，仅用于原冻结计划；新实验见[当前方案](current/README.zh.md)。

新模板使用 `client_profile=bounded-memory-v1`，解析后的数值写入每个cell的`client_settings`并纳入plan哈希。它限定实验预算；不修改BrewFS或JuiceFS生产默认值。已有config/plan/结果保持原值，不能把旧结果换算成这套配置的性能。

## 客户端缓存分别记账

| 层 | BrewFS native / native-workspace | BrewFS entity | JuiceFS |
|---|---|---|---|
| 可驻留读缓存 | `cache.read_memory_bytes=536870912` | packed的`entity_read_cache_bytes=536870912`；native lower=0 | `cache_dir=memory, cache_mib=512` |
| clean SSD缓存 | `read_ssd_bytes=0` | native VFS读缓存设0，entity使用独立路径 | memory store不落盘 |
| 写入/IO缓冲 | `write_memory_bytes=512MiB`，另`memory_budget_bytes=512MiB` | 同步entity不使用native VFS这两个预算，记录真实RSS | `buffer_mib=512`，读写共享 |
| 写入暂存盘上限 | `write_ssd_bytes=1GiB`，私有目录；实际使用计入成本 | 同步模式不启用entity异步writeback | `writeback=false`，无异步stage |
| FUSE metadata TTL | 0 ms，keep_cache=false | 当前adapter固定0 TTL、direct I/O | attr/entry/dir-entry=0，open-cache=0 |

512MiB是**读数据缓存预算上限**，不是进程总内存，也不是三种实现完全相同。每个Agent仍独立2 CPU/8GiB/swap=0，读写缓冲、metadata、page cache、packed解码/索引与峰值RSS另列。native lower没有packed payload缓存的事实保留；纯格式消融双方读缓存都设0。

JuiceFS的buffer-size控制读写缓冲；`--cache-dir memory --cache-size 512`才明确配置内存读缓存。前者不能代替后者。[官方缓存说明](https://juicefs.com/docs/community/guide/cache/)给出了两者的用途。该配置先做本批二进制的真实挂载资格；不支持memory store时阻断，不能静默退回cache-size=0。

本profile显式保留native预取：BrewFS native最大128MiB、并发64、background prefetch=true、fetch=backend-default；JuiceFS prefetch=1、max-readahead=128MiB。两种算法不等价。entity不使用native VFS的预取开关，按本批代码/计数记录实际packed行为；不能据native配置宣称entity预取已开启或关闭。JuiceFS开启cache-large-write，BrewFS开启populate_write_cache_after_upload；都记录实际缓存命中和对象请求。预取消融须另列条件。

native压缩显式none、缓存checksum full；JuiceFS format阶段也固定压缩none并保存实际volume格式回执，mount参数不能改变已有volume压缩。packed producer的对象/帧格式按实际manifest资格核对。JuiceFS关闭自动metadata备份和usage报告，避免测量期额外后台任务；实验原始证据仍按协议完整归档。

TTL=0是本轮明确的元数据新鲜性条件，与entity现有实现对齐。因此C3的“热”指预热过的数据集合，逐文件open/lookup仍可能访问元数据；不能把它称为完全命中内核缓存的生产上限。其他TTL/内核缓存策略应在运行前建立独立配置。

本次审查依据BrewFS `f570671`的[native缓存默认值](https://github.com/brewfs/brewfs/blob/f5706712302435206269a253bf861a1a648761d1/src/vfs/cache/config.rs)和[entity FUSE行为](https://github.com/brewfs/brewfs/blob/f5706712302435206269a253bf861a1a648761d1/src/fuse/entity.rs)。这只是审查来源；实际实验仍按本批构建/资格冻结版本。

## 固定工作量与容量

| 项目 | 新默认 | 需要核对 |
|---|---|---|
| C1 pilot | 1 job，64MiB文件/测量 | 用于接入，不代表主表规模 |
| C1 main顺序 | 8 jobs，各4GiB文件及测量；1MiB块 | 每cell共32GiB测量 |
| C1 main随机 | 8 jobs，各4GiB文件、256MiB测量；4KiB块 | 共524,288次IO，偏移仍覆盖32GiB工作范围；逐次fsync |
| C3 pilot/main | 1M×4KiB，1/4个新workspace，8线程 | 不缩小百万文件；保留全部8阶段/预热/正确性 |
| C4 Delta消融pilot/main | 1/8个child | pilot不再意外启动8份eager-copy |
| C4 main最大fork | 64个child | 单池至少128 CPU/512GiB Agent预算，另加OS/服务；eager base+children逻辑量约248GiB，物理盘另加放大/日志/保留现场 |

旧fio随机默认每job测完4GiB，合计8,388,608次IO；新默认减少测量次数以控制同步写成本，没有减少文件工作范围或取消fsync。两种系统使用相同参数。32GiB fixture准备、完整CRC验证、每单元冷读回仍保留；随机测量2GiB不等于整单元只处理2GiB。固定工作量不能承诺运行时长；pilot用于观察成本，再在main冻结前决定是否采用其他工作量，运行中不改。

冻结前生成容量清单：

```bash
python3 -m bench.suite_support sizing --plan "$RUN/plan.json" --output "$RUN/sizing.json"
```

该文件按cell给IO次数、字节数和slot预算，按pool给峰值Agent预留。handoff move同时考虑source及target预留。它不是物理空间或耗时预测；metadata/object服务、临时盘、输入、日志、归档双份及失败保留量另算。`agent_slots=64`、相同hardware_class和resource ID只是计划声明，实际inventory/probe/校准必须证明。

## 机械检查与仍需原生资格的部分

1. planner提前拒绝不支持的fio模式/depth/direct/time_based/fsync、越界工作量、非完整trace、handoff人数/路线、百万文件规模和超预算缓存。
2. `suite_mount`生成本cell的显式设置；prepare与node启动前各自重核plan哈希、pool、product、backend、hint和缓存字段。更改预算必须新计划，不能在mount里偷偷覆盖。
3. fio回执核实际JSON里的模式、块大小、seed、文件范围、同步选项及逐job字节，不能靠总字节数相同过关。原始文件哈希绑定到summary。
4. 真实FUSE模式、有效缓存/预取、上传债务、实际B/q、native/eager能力、跨机writer fencing仍需实际测量资格。设置文件不代替通过证明。没有完整遥测、真实退出或正确性证据的单元继续判失败。

命令与配置使用[执行步骤](05-execution.zh.md)；路径差异从[baseline目录](baselines/README.md)接入。本轮没有新增测试场景、改变历史结果或自动分配资源。
