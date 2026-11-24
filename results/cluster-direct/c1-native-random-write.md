# C1 Native Overlay 随机写复测

2026 年 11 月 18 日，node-coord 使用统一版本 `031caa1` 执行原 `01-io-04-r1` 负载。8 个 fio 作业各对一个 256 MiB 文件随机写入 2,048 次，每次 4 KiB，合计 64 MiB。各作业使用原种子，设置 `direct=1`、`fsync=2048`、`end_fsync=1`。实际挂载使用 8 个 Tokio 工作线程，缓存配置沿用原 04 项，写入合并窗口为 50 ms。Dynamic 开启，后端为四节点 RustFS 的 9000 端口。

| 指标 | GraftFS 原 04 项 | GraftFS 修复版 | JuiceFS Redis 原 08 项 |
|---|---:|---:|---:|
| fio 写吞吐（MiB/s） | 0.413 | 5.237 | 0.352 |
| 写完成延迟 P99（ms） | 308.281 | 8.585 | 227.541 |
| 对象 PUT 次数 | 16,360 | 1,207 | 16,731 |
| 写入后排空时间（秒） | 0.006877 | 0.007049 | 2.722621 |

修复版此次写入及排空用时 12.721 秒。node-coord 卸载后，node-meta 使用空客户端缓存重挂。8 份 fio 日志的写入偏移逐项匹配原负载。node-meta 逐字节读回完整 2 GiB，16,384 个写入页和 507,904 个未修改页均正确，校验耗时 22.733 秒。历史两组沿用各自原有的校验结果。

## 原始结果

| 测量 | node-coord 上的目录 |
|---|---|
| 修复版 | `/srv/ax-cluster/diagnostics/c1-unified-04-20261118-utOk8Q/measurement/01-io-04-r1/attempt-01/` |
| 原版 | `/srv/ax-cluster/runs/direct/run-002/01-io/04/r1/attempt-04/` |
| JuiceFS Redis | `/srv/ax-cluster/runs/direct/run-002/01-io/08/r1/attempt-02/` |

修复版目录中的 `fio.json` 记录带宽和延迟，`writes-*.iolog` 记录各作业的写入偏移，`verify.json` 记录跨机冷读校验。`comparison.json` 汇总三个系统版本的指标及各自的原始结果路径。
