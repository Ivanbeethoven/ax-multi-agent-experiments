# fio 运行配置与复测

使用基础 fio 的同一份 2 GiB 输入，8 个 job，顺序请求 1 MiB。随机读写的请求为 4 KiB，总读写量为 64 MiB。

## 线程配置

BrewFS CLI 默认根据可用 CPU 选择 worker 数，上限为 8，允许通过 `TOKIO_WORKER_THREADS` 覆盖。单进程配置先比较 `1`、`2`、`4`、`8`，固定缓存、后端、文件和请求参数。正式多挂载配置还需考虑每台主机的活动挂载数量。

复用 `bench/cluster/runtime/compare_runtime.py`。在 A366 执行，参数如下：

```text
/srv/ax-idcai-20261113/venv/bin/python3 /srv/ax-cluster/direct/scripts/compare_runtime.py
  --source /srv/ax-cluster/runs/direct/run-002/01-io/01/r1/attempt-04
  --binary <新版本 BrewFS 绝对路径>
  --runtime-count <同一源码构建的 runtime-count 路径>
  --out <新的结果目录>
  --workers 1 2 4 8
```

将上述参数作为同一条命令执行。输出保存每档冷读、第二遍读、fio 原文、客户端计数、主机快照、挂载参数、实际 worker 数和卸载结果。比较期间不运行其他正式性能条件。输入保持只读，每档使用独立冷缓存目录。

顺序读取表现最好的两个配置，再通过 `run_fio_candidate.py --workers <数量> --binary <路径> --job 01-io-04-r1 --out <新目录>` 比较相同的随机写负载。使用正式执行函数完成输入生成、写入、上传完成检查和冷重挂内容检查。

采集开销检查使用 `compare_runtime.py` 的 `--periodic on` 与 `--periodic off`，固定线程数和输入，分别指定新的输出目录。关闭周期采样时仍在计时前后记录计数快照，程序结束后恢复周期采样。

## 缓存、同步与后端

当前 fio 资源配置为每挂载 16 GiB 内存上限、64 GiB 磁盘缓存预算，其中读缓存 56 GiB、写暂存 8 GiB。实际缓存容量和并发参数以本次 `mounts.json` 与挂载审计为准。调整线程数时保持这些参数不变。

写入同步间隔由 `fsync_bytes_all_jobs` 生成；当前值为 64 MiB，分摊到每个 job 为 8 MiB。随机写每个 job 总共写 8 MiB，因此同步发生在末尾；另启用 `end_fsync=1`。同步等待计入总耗时，fio 读写延迟与 fsync 延迟分别保留。

Dynamic 开启使用 BrewFS hint `auto` 和修改版 RustFS `9000`；关闭使用 hint `off` 和原版 RustFS `19000`。固定 B 条件使用具体字节数和修改版后端。输入创建与冷重挂检查使用相同选择。完整配置见 [实验方案](../docs/PLAN-REVIEW.md)。

## 正式结果

每次复测创建新的 attempt 目录，保留旧结果。只有内容检查、实际读写量和上传完成检查通过后，才更新该条件的当前结果。汇总保存源码版本、二进制路径、线程数和原始结果目录；不同配置的结果分开列出。
