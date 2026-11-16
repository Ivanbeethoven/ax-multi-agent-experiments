# fio 运行配置与复测

使用基础 fio 的同一份 2 GiB 输入，8 个 job，顺序请求 1 MiB。随机读写的请求为 4 KiB，总读写量为 64 MiB。

## 线程配置

BrewFS CLI 默认根据可用 CPU 选择 worker 数，上限为 8，允许通过 `TOKIO_WORKER_THREADS` 覆盖。单进程配置先比较 `1`、`2`、`4`、`8`，固定缓存、后端、文件和请求参数。正式多挂载配置还需考虑每台主机的活动挂载数量。

当前正式挂载使用 8 个 worker。线程筛选使用同一二进制和正式 RustFS 后端：8 个 worker 的顺序冷读优于 1、2、4 个，增加到 16 个没有继续提高；随机写与 4 个 worker 接近。原始筛选记录保存在 A366 `/srv/ax-cluster/diagnostics/sequential-read/`。

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

采集开销检查使用 `run_fio_candidate.py` 的 `--periodic on` 与 `--periodic off`，固定线程数和输入，分别指定新的输出目录。选择 64 MiB 随机写，使测量期间包含多个 5 秒采样周期。关闭周期采样时仍在计时前后记录计数快照，程序结束后恢复周期采样。

## 缓存、同步与后端

当前 fio 资源配置为每挂载 16 GiB 内存上限、64 GiB 磁盘缓存预算，其中读缓存 56 GiB、写暂存 8 GiB。实际缓存容量和并发参数以本次 `mounts.json` 与挂载审计为准。调整线程数时保持这些参数不变。

fio 使用 BrewFS 16 MiB 预读窗口、8 个预读任务；JuiceFS 使用 16 MiB 最大预读、16 个下载任务。BrewFS 读内存缓存为 512 MiB，JuiceFS 读写缓冲为 768 MiB。配置分别保存于 `resource_profiles.fio`；多挂载实验继续使用各自的 4 GiB 内存预算。冷读使用新的客户端缓存目录，第二遍读取复用该挂载的缓存。阶段计数同时记录下载字节，判断预读对请求量与流量的影响。

写入同步间隔由 `fsync_bytes_all_jobs` 生成；当前值为 64 MiB，分摊到每个 job 为 8 MiB。随机写每个 job 总共写 8 MiB，因此同步发生在末尾；另启用 `end_fsync=1`。同步等待计入总耗时，fio 读写延迟与 fsync 延迟分别保留。

Dynamic 开启使用 BrewFS hint `auto` 和修改版 RustFS `9000`；关闭使用 hint `off` 和原版 RustFS `19000`。固定 B 条件使用具体字节数和修改版后端。输入创建与冷重挂检查使用相同选择。完整配置见 [实验方案](../docs/PLAN-REVIEW.md)。

## 指定候选版本复测

`run_fio_candidate.py` 支持重复传入 `--job`，按顺序执行所选条件；`--out` 必须指向新的目录。候选二进制先分发到 A366 和 A355 的同一个绝对路径，分别用于测量和跨机器内容检查。

`--plan <路径>` 可指定本次复测的完整方案，保留正在运行的其他实验方案；`--source-record <路径>` 指定实际候选源码与构建记录，写入每个条件的结果目录。

复测 [BrewFS PR #454](https://github.com/brewfs/brewfs/pull/454)（响应缓冲预分配，提交 `c1e9dd1`）时，修复前后均显式传入 `--workers 8`，只更换对应的二进制和源码记录。核对挂载进程的 `TOKIO_WORKER_THREADS=8`，并将实际线程数、`--binary` 路径、`source-record.json` 与本次 `attempt-01` 原始目录一同列入汇总。附带其他预读或清零改动的版本单独记录。

- `--workers 8` 固定 BrewFS Tokio 工作线程。
- `--input-write-memory-mib 1024` 只将 BrewFS 输入准备挂载的写缓冲和对应内存预算增加至 1 GiB；正式测量挂载保持原有写缓存配置。
- `--read-fetch-policy whole-object` 用于顺序读；`--read-fetch-policy sparse` 用于随机读和固定 B 比较。写入条件保持正常读取策略。
- 批量执行不同负载时，`--read-fetch-policy workload` 自动按上述规则选择，写入条件保留原配置。
- 不指定预读参数时，沿用配置中的 BrewFS 8 个预读任务和 JuiceFS 16 个下载任务。

每个条件保存 `input-mounts.json`、`mounts.json` 和挂载审计，确认实际参数。输入准备不计入读写测量时间，其耗时和对象布局单独保存。读缓存是否为空以新的缓存目录和下载计数判断；服务端磁盘缓存状态另外记录。

## 正式结果

每次复测创建新的 attempt 目录，保留旧结果。只有内容检查、实际读写量和上传完成检查通过后，才更新该条件的当前结果。汇总保存源码版本、二进制路径、线程数和原始结果目录；不同配置的结果分开列出。

需要重新运行某组时，先让服务在条件结束处停止，然后在 A366 执行 `reset_suite_groups.py --group 01-io --label <本次归档名称>`。该脚本将当前配置、版本、状态和汇总保存到 `run-002/archive/results/<名称>/`，原始 attempt 目录保持原位。

本机执行 `python infra/idcai/start_suite.py --calibrate-after-io`：先完成 fio，额外运行一次关闭周期采样的相同随机写，再继续其余三组。时间与延迟差值保存在 `run-002/calibration/`。该流程由服务器上的 systemd 服务运行，SSH 断开后继续执行。
