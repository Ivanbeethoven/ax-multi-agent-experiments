# 冷读诊断结果

## A366 单机冷读：zero-gap 候选

2026-11-16，8/8 个读取用例通过内容检查。RustFS 在 A366 单机使用四个数据目录。每个负载依次测 JuiceFS Redis、BrewFS Flat、BrewFS Workspace Native、JuiceFS Redis；每次使用新挂载和独立空缓存目录。四节点正式 fio 复测另行统计。

| 冷读负载 | 系统（运行顺序） | 吞吐量 MiB/s | P99 ms | GET 次数 | 下载字节 B |
|---|---|---:|---:|---:|---:|
| 顺序 | JuiceFS Redis（前） | 863.868 | 62.652 | 256 | 1073741824 |
| 顺序 | BrewFS Flat | 806.699 | 35.389 | 256 | 1073742848 |
| 顺序 | BrewFS Workspace Native | 709.795 | 31.064 | 256 | 1073742848 |
| 顺序 | JuiceFS Redis（后） | 811.032 | 70.779 | 256 | 1073741824 |
| 随机 | JuiceFS Redis（前） | 26.048 | 5.603 | 4775 | 1169162240 |
| 随机 | BrewFS Flat | 23.894 | 7.176 | 3950 | 1299056640 |
| 随机 | BrewFS Workspace Native | 22.329 | 7.832 | 4011 | 1303054336 |
| 随机 | JuiceFS Redis（后） | 13.868 | 14.746 | 7839 | 2031435776 |

数据集 1 GiB，8 个 fio 作业，`direct=1`、`psync`、`iodepth=1`。顺序读总量 1 GiB，请求 1 MiB；随机读总量 64 MiB，请求 4 KiB。BrewFS 使用 8 个 Tokio 工作线程、512 MiB 读内存、8 个预读任务和 16 MiB 预读窗口；准备输入时写内存为 1 GiB。表中吞吐量按应用读取字节除以 fio 子进程实际经过时间（`seconds`）计算。

Workspace 顺序读吞吐低于前后两次 JuiceFS，尽管三者均执行 256 次 GET。随机读两次 JuiceFS 相差较大，BrewFS 两种模式的吞吐均处于两次 JuiceFS 之间。复用不超过 4 MiB 的整块读取计划属于后续候选，尚无性能结论。

本轮候选包含两项独立修改：[响应缓冲预分配（#454，`c1e9dd1`）](https://github.com/brewfs/brewfs/pull/454) 和 [只清零空洞与未覆盖区间（#455，`b0646da`）](https://github.com/brewfs/brewfs/pull/455)。复现时以本轮二进制和源码记录为准。

A366 原始目录：`/srv/ax-cluster/diagnostics/read-prefetch/`。逐项结果和用例路径见 `quick-1g/zero-gap-compare/comparison.json`；源码和差异见 `zero-gap-source-record.json`、`zero-gap-source.patch`，二进制为 `brewfs-zero-gap-candidate`，耗时与 CPU 采样见 `quick-1g/zero-gap-read-timing/`。

## 从已有结果生成统计

这三个脚本读取已有实验文件，不启动服务或重新生成负载。

```text
python compare-cold.py <结果目录>
python summarize-read-path.py <独立采集器结果目录>
python summarize-profile.py <CPU 采样结果目录>
```

- `compare-cold.py` 输出冷读吞吐、P99、GET 次数、下载字节及内容检查结果，支持 BrewFS 和 JuiceFS。
- `summarize-read-path.py` 生成 `monitoring-summary.json`，包含 RustFS 读取阶段、客户端与服务端 CPU 消耗、CPU 限流时间及服务端磁盘读取字节。
- `summarize-profile.py` 生成 `cpu-profile-summary.json`，包含 `perf` 函数采样比例和已记录的内存占用。

输入保留原有的 `fio-*/1-candidate/<操作>/` 结构。每个条件使用独立缓存目录和新挂载。RustFS 每次重新启动时，采集器也使用新进程，随后按冷读前后累计指标求差。

吞吐按应用读取字节除以运行 fio 的总时间计算；`fio_seconds` 单独保存 fio 报告的执行时间。CPU 采样和逐请求计时用于诊断，性能对比单独运行。各阶段可以同时执行，耗时不能直接相加。客户端冷缓存与服务端磁盘冷缓存分别记录。
