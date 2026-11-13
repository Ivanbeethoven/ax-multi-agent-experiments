# 合批为什么更慢，以及本次修改

代码：[BrewFS 076cd91](https://github.com/brewfs/brewfs/commit/076cd91c83a35fe9b36767832134840abdd7001e)，待审 [PR #450](https://github.com/brewfs/brewfs/pull/450)。
源码补丁、二进制 SHA、构建参数和脚本 SHA 见[版本记录](evidence/provenance.json)。

合入最新主线的兼容修改、完整检查与 A355 的 12 单元挂载验证，见[合并检查](merge-main-20261113/README.zh.md)。
下表保留原测量版本；本轮没有重测当前主线相对 lower-only 的开销。

上一轮减少的是 Redis **SET 写入次数**，不是全部请求。每次修改仍校验远端值和
时钟；合批另外执行发送前日志、确认日志和远端事务。旧实现落盘时持有状态锁，
所以刚修改完再读本地值，也会等后台 fsync。
另一个多余步骤是：普通批量读取也走了带服务器时间的事务。现已恢复为调用方要求的
普通读取或一致读取；修改时的时钟、租约校验保持原样。Redis 的命令计数包括 Lua
内部调用，不能把每条命令都计作一次网络往返。

日志锁的调整仅作用于 `local-buffered`：固定快照后释放状态锁，再写日志。提交驱动保持串行，
新修改进入下一批。发送前落盘、确认日志、远端校验和 fsync 边界均保留。
取消等待不会让两次日志写入交叉；确认日志失败，即使没有新修改也必须重试保存。
默认仍为 `remote`。`local-journal` 仍逐次落盘，未改变它的返回条件。

## 分段诊断

同一台机器、同一个临时 Redis、同样的测试构建，每次 100 次同键修改并读回，
最后排空。前后版本按 AB / BA / AB 顺序运行；每次各模式内部重复三次，共各九次。
Redis 未开启 AOF/RDB。下面是耗时中位数；这是定位用的测试构建结果。

| 项目 | 修改前 | 修改后 |
|---|---:|---:|
| 内存合批：含最后排空 | 92.52 ms | 56.28 ms |
| 内存合批：前台修改与读回 | 77.83 ms | 38.50 ms |
| 内存合批：最后排空 | 13.91 ms | 19.02 ms |
| 内存合批：读操作等待状态锁 | 35.41 ms | 0.30 ms |
| 内存合批：远端校验 | 38.65 ms | 36.04 ms |
| 内存合批：合并计算 | 0.37 ms | 0.34 ms |
| 内存合批：日志编码与校验和 | 0.33 ms | 0.23 ms |
| 内存合批：日志写入（不含同步） | 0.39 ms | 0.33 ms |
| 内存合批：日志文件 fsync | 22.78 ms | 16.08 ms |
| 内存合批：替换日志与同步目录 | 20.36 ms | 14.87 ms |
| 内存合批：读取远端确认 | 2.57 ms | 1.61 ms |
| 内存合批：远端提交 | 1.96 ms | 1.45 ms |
| 内存合批：等待提交驱动 | 5.34 ms | 7.70 ms |
| 逐次日志：含最后排空 | 896.24 ms | 889.34 ms |
| 同步模式对照：含最后排空 | 70.22 ms | 74.51 ms |

内存合批总耗时下降约 39.2%，最后排空增加约 5.1 ms，已计入总耗时。逐次日志没有明显改善；它每次都
同步本地盘，不能只凭 SET 变少就认为会比未开启持久化的本地 Redis 更快。
各行分别取中位数；分段计时还包含嵌套和并行部分，**不能把所有阶段相加**。
日志同步耗时也随实际批次数和本地盘状态变化，不能把它的下降全部算作算法收益。
可直接归因的变化是：日志同步期间，前台不再持有并等待同一把状态锁。
每次修改的远端校验仍存在；它现在是这组负载中最大的前台耗时。
九次运行中，两版每次均做 100 次远端修改校验。远端提交次数中位数从 4 降到 3，
日志文件 fsync 从 8 降到 6；读取本地值的锁等待下降，并不代表这些同步步骤消失。

这组负载使用单键读回，验证的是释放状态锁。只改锁的前一轮为 73.77 → 54.88 ms，
原始结果另行保留；两轮磁盘耗时不同，不能把差值归到去掉 TIME 上。

普通一致读取另外用同一二进制单独比较旧、新路径，交换顺序各三次，每次 100 次读取。
MGET 均为 100 次，TIME 从 100 次降为零；总耗时中位数为 30.67 → 32.14 ms，
没有测出独立的耗时改善。这项修改减少多余命令，主要时间收益仍来自锁的处理。

## 实际挂载对照

前后均为 `opt-level=3` 的 release，关闭分段计时，读取缓存均开启。固定 100 次
`chmod/stat`，计入最后的 `fsync/close`；三个外层配对按 AB / BA / AB 运行，每组
内部再重复三次。下表是各九次运行的中位数，共 72 个测试单元。

| 合批间隔 | 初态 | 修改前 | 修改后 | 总耗时下降 |
|---|---|---:|---:|---:|
| 10 ms（默认） | 原来的两个文件 | 290.58 ms | 170.01 ms | 41.5% |
| 10 ms（默认） | 有上层修改和删除 | 304.65 ms | 170.99 ms | 43.9% |
| 100 ms | 原来的两个文件 | 171.05 ms | 153.17 ms | 10.5% |
| 100 ms | 有上层修改和删除 | 175.89 ms | 151.37 ms | 13.9% |

每个单元都完成了预热、逐次读取校验和新进程的远端重挂核验。热读及元数据修改
计时阶段的 RustFS 请求和收发字节均为零；Redis 访问仍存在。10 ms 两文件用例中，
SET 次数中位数为 39 → 24，TIME 为 329 → 118。100 ms 时 SET 均为 6，TIME 为
307 → 106。不能把零对象请求当作零元数据请求。

主要改善是周期性长停顿减少。10 ms 两文件用例中，stat 的样本 P99 为
7.90 → 0.84 ms，但单次 stat 中位数为 0.502 → 0.532 ms；不是每次操作都变快。
这些百分比只比较修改前后的合批实现，不是与同步模式相比，也不是正式大负载结论。

详细数据：[挂载汇总](evidence/summary-fuse.json)、[原始逐次记录](evidence/runtime-03/fuse-pairs/)。

## 普通读写检查

缓冲/直接 I/O 的顺序写、随机写和混合读写共六项通过。另外做了三对 20 秒、
两对 40 秒的前后版本混合读写，共 16 个 fio 运行；全部正常卸载，最终 pending、
dirty、buffer dirty 均归零，无排空超时。输入为每任务 512 MiB、4 个任务、4 MiB
I/O、70% 读，固定随机种子。20 秒与 40 秒分别分析，不混在一起取平均。

| 混合负载 | 包含排空的吞吐，前 → 后 | 说明 |
|---|---:|---|
| 20 秒，第 1 对 | 232.05 → 250.35 MiB/s | 初次出现写尾延迟升高；旧版写入仅 806 次 |
| 20 秒，第 2 对 | 234.51 → 267.95 MiB/s | 反向顺序；两侧写 P99 样本均不足 |
| 20 秒，第 3 对 | 251.78 → 252.00 MiB/s | 两侧写 I/O 均超过 1000，写 P99 下降 19.2% |
| 40 秒，第 1 对 | 205.62 → 231.68 MiB/s | 写 P99 下降 31.7%，读 P99 升高 49.7% |
| 40 秒，第 2 对 | 182.21 → 244.56 MiB/s | 反向顺序；写 P99 下降 57.5%，读 P99 下降 1.4% |

没有观察到稳定的吞吐或平均延迟回退。读取尾延迟波动较大，**不能据此宣称全局
尾延迟改善，也不能给出严格的“读 P99 无回退”结论**。这些普通读写结果只作为
附加检查，不算合批优化的收益；初次不利结果完整保留。40 秒的四次运行均满足
报告工具原有的每侧读/写至少 1000 次要求。

全部对照及样本数量见[混合读写汇总](evidence/summary-mixed.json)。
28 份运行回执和逐文件校验见[核验记录](evidence/verification.json)；全部证据可在
本目录执行 `(cd evidence && sha256sum --quiet -c SHA256SUMS)` 核验。

## 正确性检查

默认测试 1355 项、Overlay 测试 2810 项通过，格式、工作区构建、运行时 feature
检查及两组 Clippy 通过。20 项本地元数据测试覆盖旧确认与新修改、取消等待、
提交前日志失败、确认日志失败后重试、容量、租约失效和远端写回。
两个需要真实 Redis 的测试另行运行并通过。

CI 曾在完整 feature 的 Clippy 阶段中断，当时测试已通过。中断原因未确认；
保持源码不变后补完剩余检查，原日志和继续执行的记录一并保留。

## 本地复现

沿用 [已有构建和镜像说明](../hot-lower-20261112/README.zh.md)。
源码的完整检查仍用 `tools/perf/check_hot_overlay.sh`，release 构建仍用
`tools/perf/build_hot_overlay.sh`，不要覆盖已有输出目录。

分段诊断用 `BREWFS_METADATA_PROFILE=1` 启用计时。先在前后两份源码中分别执行
`tools/perf/check_local_metadata.sh`，从日志中的 `Running unittests` 找到测试程序。
基线需应用本目录证据中的计时补丁；它只加计时，不改行为。随后执行：

```bash
bash run_redis_pairs.sh /build/before-tests /build/after-tests /results/new-redis-pairs
python3 summarize.py /results/new-redis-pairs/{1,2,3}-before.json
python3 summarize.py /results/new-redis-pairs/{1,2,3}-after.json
```

实际 FUSE 对照使用 root 和带 boto3 的 Python；脚本关闭分段计时，固定读取缓存，
轮换前后版本和 10/100 ms 间隔的顺序。三个外层配对，每次内部三次重复，两个负载，
共 72 个单元。每个单元先证明数据已热，记录 chmod/stat、fsync/close、热读逐次
延迟、Redis 命令和 RustFS 请求/字节，最后用不读取本地日志的新进程重挂核对。

```bash
python3 run_fuse_pairs.py \
  --before-build /build/before --before-source BEFORE_COMMIT \
  --before-patch /evidence/before.patch \
  --after-build /build/after --after-source AFTER_BASE_COMMIT \
  --after-patch /evidence/after.patch \
  --out /results/new-fuse-pairs
python3 summarize_fuse.py /results/new-fuse-pairs
```

上述默认参数复现历史版本。若某一侧使用已移除版本化格式名的新主线，给该侧加
`--before-workspace-format workspace` 或 `--after-workspace-format workspace`；
脚本会选择对应构建目录中的 `packed_snapshot_fixture`。旧版仍使用
`packed_v3_snapshot_fixture`。两侧须保持相同缓存和负载设置。

普通读写使用 [run_compose_guard.py](../hot-lower-20261112/run_compose_guard.py)，每次
使用新目录，切换 `--direct 0/1` 和 `--tools fio-seqwrite/fio-randwrite/fio-randrw`。
默认 20 秒；排查尾延迟时显式加 `--runtime 40`，前后版本必须一致。例如：

```bash
python3 ../hot-lower-20261112/run_compose_guard.py \
  --repository /src/brewfs --binary /build/after/brewfs \
  --out /results/new-mixed-after --direct 0 --runtime 40 --tools fio-randrw
```

汇总本目录保留的全部混合读写记录：

```bash
python3 summarize_mixed.py --compare-tool /src/brewfs/tools/perf/compare_artifacts.py \
  --runtime evidence/runtime-03 --repeats evidence/mixed-repeat-04 \
  --extended evidence/mixed-40s-05
```

小任务的结果只说明这条路径的开销。TiKV 实例、跨机器故障恢复和正式大负载性能
不在这次本地验证范围。旧实验原始文件保持不变。
