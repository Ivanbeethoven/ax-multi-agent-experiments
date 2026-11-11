# C3 缓存消融：同一份 Packed 数据，512 MiB 对 0

> 历史协议，仅用于原冻结计划；新实验见[当前方案](../current/README.zh.md)。

回答一个问题：只开启 Packed 数据缓存，能否减少对象存储读取，降低任务耗时？两档在 A 组顺序跑，用同一块客户端盘、同一 Redis/RustFS、同一 Packed manifest key/digest、同一产品二进制。沿用 C3 的 100,000 个 4 KiB 文件、seed、八阶段和全部 CSV。每档新建私有 volume/namespace/catalog。唯一客户端配置差异是 `--entity-read-cache-bytes 536870912` 或 `0`；保持 FUSE `keep_cache=false`、直接 I/O、零干净 SSD 缓存和同步写入。

用 `scripts/packed-c3-payload-cache.py draft` 读原 C3 Packed plan、原 base asset 和完整资格，生成独立 `payload-cache` 计划及两档参数。只复用原生产者、输入、完整 base 校验和正常退出，避免重新发布基线或再扫同一个 base 来取得资格。现有 Packed runner 仍严格匹配原系统 plan 和 arm，不能直接拿它执行草稿：接线时将原父空间按原 plan 校验，将新子空间按新 plan 校验；两边实际 manifest digest、端点和冻结设置必须匹配，只允许新身份和这一个缓存值变化。不能把现有系统校准当作消融样本或准入证据。两档正式样本分别保留自己的冷读、隔离和正常退出证明。

运行顺序：C1 结束并交还 A 组以后，先在独立临时命名空间读取一小组已知文件两次，核对两档配置生效；随后按新计划完成观测开销校准，通过原 5% 门槛才跑正式。资格探针不计时入样本。若正式前的短探针或校准未通过，停在相应门槛，不回退为默认缓存，不补测伪样本。当前原系统 Packed 校准未通过；本草稿没有启动任何任务。

| 需要回答的事 | 记录方法 |
| --- | --- |
| 两档确实生效 | 对同一挂载在 `measurement-start`、`application-finished` 读取 `brewfs_entity_read_cache_capacity_bytes`、`_retained_bytes`、`_entries`、`_evictions_total`。512 MiB 档要有实际 lower hit 和驻留，0 档必须没有命中、驻留、条目或驱逐；两档都要有实际 lower miss。0 驱逐可以是正确结果。 |
| 是否减少读请求和读量 | 记录 `brewfs_cache_hits_total/misses_total{cache="entity_lower_payload/entity_upper_payload"}` 的各标签增量；记录 `brewfs_object_get_total/get_bytes_total` 全部 `kind` 的增量和合计。以 RustFS 同窗口 `s3:GetObject` 和 HTTP 响应体增量交叉检查，保留 exporter 滞后和节点总量范围。 |
| 是否缩短任务 | 同时报告八阶段 wall time、I/O P99、应用 P99、任务 makespan，以及实际 20,224 次读、768 次写和逻辑字节。突出 `cold-read`、`hot-read` 和 `mixed`；另报实际内存峰值及缓存驻留。只算同一配对的变化。 |

`packed-c3-payload-cache.py check --observer RAW_CLIENT_DIR --budget 536870912 --out CACHE.json` 读取原 mark 的两份文件系统采样，校对 SHA、明确标签、计数不回退以及上述生效条件。它在已有 A 组 on3 原档中验证出 512 MiB 生效，拒绝把这份记录标成 0；还没有真实的 0 档运行。现版本的 `kind` 不是 lower/upper 的实际请求归属，客户端 GET 计数也不能区分 HTTP Range 和普通 GET；保留各原标签，报合计。若必须拆开 Range，需新加明确的原生计数并重新冻结版本，当前不填估计值。
