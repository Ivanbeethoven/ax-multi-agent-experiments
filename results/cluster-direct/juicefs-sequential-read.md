# C1 JuiceFS Redis 顺序冷读

node-coord 通过四节点 RustFS 读取 8 个各 256 MiB 的文件。每个 fio 作业读一个文件，请求大小 1 MiB，`psync`、`direct=1`、队列深度 1，8 个作业合计读取 2 GiB。每次首遍使用新的客户端缓存目录。两次挂载均配置 768 MiB 缓冲、56 GiB 磁盘缓存、16 MiB 最大预读和 16 个并发下载。

| 指标 | 首次测量 attempt-02 | 复测 attempt-03 |
|---|---:|---:|
| 首遍 fio 吞吐（MiB/s） | 1011.358 | 988.894 |
| 首遍 fio 计时（秒） | 2.025 | 2.071 |
| 首遍应用总耗时（秒） | 2.508 | 2.557 |
| 按首遍应用总耗时计算的吞吐（MiB/s） | 816.705 | 800.876 |
| 首遍读完成延迟 P99（毫秒） | 103.285 | 103.285 |
| 首遍 S3 GET 次数及读取量 | 512 次，2 GiB | 512 次，2 GiB |
| 第二遍按应用总耗时计算的吞吐（MiB/s） | 2486.311 | 854.188 |
| 第二遍 S3 GET 读取量（MiB） | 156 | 2048 |

两次跨机读取校验均通过。首遍 fio 吞吐相差 2.2%。第二遍的对象读取量相差很大，复测未复现首次的热读速度。

node-coord 的原始目录为 `/srv/ax-cluster/runs/direct/run-002/01-io/05/r1/` 下的 `attempt-02` 和 `attempt-03`。每个目录包含 `workload.fio`、`fio-cold.json`、`fio-hot.json`、`measurement.json`、`result.json` 和 `verify.json`。
