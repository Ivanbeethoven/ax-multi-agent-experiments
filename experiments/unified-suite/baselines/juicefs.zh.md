# JuiceFS 执行入口

负责唯一外部baseline。先读[共同执行步骤](README.md)，然后按本页核对JuiceFS差异；默认systems映射到pool-b，实际以冻结cell.pool为准。

## 身份与工作负载

| 项目 | 固定身份 / 范围 |
|---|---|
| product / engine | `juicefs` / `juicefs` |
| 普通arm | `juicefs`：C1 fio、C2完整trace、C3百万小文件、C4 fork |
| 流转arm | `juicefs-stay`、`juicefs-move`：C4原生会话流转 |
| 默认映射 | `product_pools.juicefs=["pool-b"]`，覆盖该product全部arm |

## 本路径准备

1. 使用本批冻结的JuiceFS二进制、独立metadata实例与对象前缀；metadata类型与配对BrewFS同类，默认TiKV。版本、实际存储拓扑、客户端资源和缓存策略进入资格。
2. 按[输入与一次导入](../04-inputs.zh.md)，使用本批支持的原生`juicefs sync`导入一次，保存base路径及真实metadata URL。新workspace使用原生`juicefs clone -p BASE NEW`，计入原生clone总耗时和退出；不套用BrewFS manifest发布流程。
3. 挂载草稿用`bench.runtime.prepare mount --filesystem juicefs`，传实际pool、`--metadata-backend`、JuiceFS binary/config及摘要，其他参数按[挂载步骤](../05-execution.zh.md)填写并逐层review。

## 配置核对

原生JSON字段见[配置约定](../examples/README.md)：`metadata_url`对应实际TiKV/Redis；从plan生成`bounded-memory-v1`设置，`writeback=false`、`cache_dir=memory`、`cache_mib=512`、`buffer_mib=512`。独立512MiB读缓存与读写缓冲分别记录，clean SSD为0；其余TTL/预取/后台任务参数见[默认配置](../13-defaults.zh.md)。FUSE类型、metrics端口和凭据环境名按本批原生资格记录。memory store须由冻结binary真实支持，不能退回禁用读缓存的配置。

RustFS服务保持本批统一版本与默认enabled布局；JuiceFS请求没有BrewFS hint，不为了让hint“生效”改JuiceFS arm或服务默认值。实际对象布局/读取字节照实记录。

## 测量与必须留存的差异

- [C1](../06-fio.zh.md)使用相同固定fio工作量；[C2](../07-trace.zh.md)重放同份完整trace及initial tree。
- [C3](../08-smallfiles.zh.md)保存每个原生clone、lower预热/热读、首次COW、upper热写与mixed原始结果；[C4 fork](../09-fork-handoff.zh.md)保留整批clone/首次可用及父兄弟隔离。
- stay/move单元保留真实原生恢复、writer fencing、目标主机身份和邻居吞吐。只执行冻结的对应arm，不能把重新复制到目标当作原生恢复。

后续排空、普通卸载、真实退出、远端读回和归档按[共同步骤](README.md)执行。clone较慢、缓存未命中、维护或中断等待均计入；只在完整配对通过后由协调者生成比值。
