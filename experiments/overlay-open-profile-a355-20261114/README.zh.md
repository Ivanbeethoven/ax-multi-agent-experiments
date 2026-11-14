# A355：Overlay 打开文件与元数据耗时

本轮以 BrewFS `3785e30` 为基线，继续使用 A355 本机的独立 Redis 和 RustFS 容器。源码修改在 BrewFS，测量脚本和证据在本目录。没有 SQLite，不申请云资源。

结果与限制见 [实测结果](RESULTS.zh.md)。逐对耗时、调用记录和失败记录一起保留。

## 测什么

| 检查 | 输入、步骤 | 输出 |
|---|---|---|
| CPU 定位 | 已热的 32 文件目录；路径打开和目录 fd 相对打开各 20,000 次 | 每次 open/close、Redis commandstats、FUSE 和缓存计数、perf 原始采样 |
| 正确性 | 原子替换、并发读取、随机混合读写，以及上层修改、删除、fsync、重挂 | 逐次内容检查、原始日志、持久化校验 |
| 混合 I/O 对照 | 新旧版各 6 个单元，交换顺序；预写并核验 16 MiB，4 KiB 随机读写，70% 读，direct=1；先 5 秒，再单独补 30 秒 | 带宽、延迟、含最终 fsync 的进程时间 |
| 热读对照 | 2、32、10,000 文件；小输入 257 轮，大输入 3 轮；每组 3 对 | 总耗时、open/readdir/pread/close、实际 Redis 命令、对象请求及字节 |
| 内部分段 | 单独打开诊断计时；另等待 61 秒使元数据缓存到期，验证数据仍热 | 权限、属性、句柄、目录校验和区间查询耗时 |

热读有三条路径：旧 Overlay、新 Overlay、新版二进制的同引擎只读 lower。分别报告新旧比值和新 Overlay/新版 lower 比值。耗时开销为 `分子耗时 / 对照耗时 - 1`，正数表示变慢。汇总使用展示的中位耗时相除，每次测量另列。

行缓存均为 64 MiB、TTL 60 秒；2/32 文件的 payload 缓存为 8 MiB，一万文件为 64 MiB。元数据同步为 remote，FUSE 属性和目录项 TTL 为零。每次重新挂载、预热，并检查计时内对象请求和读取字节为零。元数据请求另数，不能由零对象请求推断。

## 运行

先按 [构建脚本](../overlay-fixes-a355-20261114/build.sh) 用同一工具链和正常优化的 release 构建两个版本。两个产物目录均须包含 `brewfs`、相对共同基点 `03c8cc55d61473533c00a8afe13b4ef1fcd9b725` 的 `source.patch`；候选目录另放共同的 `packed_snapshot_fixture`。记录源码提交、构建参数和 SHA256。

```bash
bash experiments/overlay-fixes-a355-20261114/build.sh /src/brewfs \
  3785e30e9999d5efcab6e46d486a6204460320e2 \
  0844a9d7b6516deb068ed13d3760e855ef4bbc3d /absolute/new-build
bash experiments/overlay-open-profile-a355-20261114/run.sh \
  /absolute/baseline-artifacts /absolute/candidate-artifacts /absolute/new-output
bash experiments/overlay-open-profile-a355-20261114/run_tail30.sh \
  /absolute/baseline-artifacts /absolute/candidate-artifacts /absolute/new-output/fio-tail30
python3 experiments/overlay-open-profile-a355-20261114/summarize_checks.py \
  /absolute/new-output > /absolute/new-output/check-summary.json
```

宿主需要 Python 3、Docker、FUSE、fio、perf 和 flock。沿用已有 Redis/RustFS 镜像，启动时核验 RustFS 指定分支与镜像来源，见 [RustFS 部署](../../infra/rustfs/README.md)。整批持有宿主测试锁；编译未结束则拒绝测量。运行器只清理自己创建的容器和挂载，失败不覆盖原输出。

`profile_existing.py` 做 CPU 诊断；`profile_stages.py` 通过 `BREWFS_HOT_PATH_PROFILE=1` 和独立 loopback 指标端口采集内部耗时。诊断批次不参与优化前后的性能汇总。权限分段包含在 `open_record` 中，不能重复相加；FUSE handler 时间不含内核和调度队列，`open(2)` 还可能包含 LOOKUP。

Redis commandstats 包含 Lua 内部命令，不等于网络往返。热读结束后的独立 MONITOR 审计区分客户端命令和 Lua 命令，审计时间不计入热读结果。

`diagnose.py --results /absolute/new-output --baseline-profile /absolute/old-profile-existing` 汇总内部分段和 CPU 诊断；这些时间单列，不替代默认关闭诊断的性能对照。

## 下载与核验

[原始结果包](evidence/results.tar.gz)约 51.2 MiB，含逐次读操作、CPU 采样、Redis 命令、构建/测试日志和失败记录，不含二进制或原负载文件。[归档回执](evidence/archive.json)给出包的 SHA256；[文件清单](evidence/inputs.json)逐个记录包内 2,288 个输入的 SHA256。

解压后，`accepted/results` 是本轮完成的测试，`rejected` 是首轮失败及基线 CPU 诊断。`accepted/checks3` 为通过的检查；`checks`、`checks2` 保留测试夹具补齐前的失败。完成回执和源码对应关系见 [publication.json](evidence/publication.json)。
