# Private workspace 本地修改与合并提交

后续的耗时拆分、锁等待优化和前后对照见[合批性能诊断](../local-metadata-profile-20261113/README.zh.md)。本页保留首次实现的测试结果。

代码已提交：[BrewFS 6c42259](https://github.com/brewfs/brewfs/commit/6c422594bbc2fc152f259d2637ca40e7ff0ddb22)。
同一元数据键反复修改时，只向元数据库提交最终值；提交旧批次的确认不会删除
新修改。数据文件的区间覆盖继续由 BrewFS 原有逻辑处理。

## 配置

| `BREWFS_METADATA_SYNC` | 操作返回条件 | 崩溃边界 |
|---|---|---|
| `remote`（默认） | 元数据库确认 | 原有同步语义 |
| `local-journal` | 本地日志 fsync | 本地盘保留时可恢复；过期租约拒绝自动重放 |
| `local-buffered` | 本地内存已更新 | 未 fsync 的修改可能丢失 |

两种本地模式都要求 `BREWFS_METADATA_JOURNAL_DIR` 指向挂载点外的独占本地目录。
默认后台间隔 10 ms，每批最多 1024 次操作、1 MiB 元数据；分别用
`BREWFS_METADATA_FLUSH_MS`、`BREWFS_METADATA_MAX_OPERATIONS`、
`BREWFS_METADATA_MAX_BYTES` 调整。文件 fsync、目录 fsync、flush、正常卸载等边界
必须等元数据库确认。上层读取缓存保持默认 65536 项。

在途批次固定后，新修改进入下一批。事务同时写入元数据和确认摘要；确认丢失时
读取摘要核对，不能盲目重复提交。队列满先提交，不丢弃脏条目。

## 本地复现

使用 Linux/WSL、Rust、Docker、FUSE 和带 boto3 的 Python。先按
[原热 lower 测试说明](../hot-lower-20261112/README.zh.md)准备本地镜像及 RustFS
版本回执。RustFS 仍使用 `Ivanbeethoven/rustfs` 的
`feat/dynamic-block-size-rebased` 分支；脚本校验源码与镜像，不回退其他版本。

```bash
export CARGO_TARGET_DIR=/local/build/brewfs-tests
export CARGO_BUILD_JOBS=1
# 在 BrewFS 仓库内，以普通用户运行：
bash tools/perf/check_hot_overlay.sh /local/results/new-ci
bash tools/perf/check_local_metadata.sh /local/results/new-redis
bash tools/perf/build_hot_overlay.sh /local/results/new-build
# 随后在有本地 Docker 权限的 root shell 中，进入实验仓库：
export PYTHON=/path/to/venv/bin/python
bash experiments/local-metadata-20261113/run.sh \
  /path/to/brewfs /local/results/new-build /local/results/new-run
```

脚本依次运行完整正确性检查、真实 Redis 合批检查、正常优化 release 构建、
三种同步模式的实际 FUSE 检查及写入回归检查。各输出目录必须不存在。
只创建本机临时容器；退出时只清理本次创建的容器和挂载。

Redis 小测试每种模式 3 次，轮换执行顺序。每次 100 次同键修改，记录每次延迟、
最终排空耗时和 Redis commandstats。它使用测试构建，衡量提交合并与开销，
不作为 release/FUSE 性能结论。
Redis 禁用 AOF 和 RDB；这个测试不模拟远端持久化延迟。`local-journal` 则实际
执行本地 fsync，两者的崩溃语义不同。

FUSE 检查使用 `opt-level=3`，覆盖原两个小文件和上层覆盖写、截断、删除、创建、
重命名。每组先验证热态，再记录真实 RustFS 请求/字节、Redis 命令和逐次延迟。
各模式 3 对缓存开关、每单元 4 轮只用于功能检查，不能据此计算正式性能收益。
卸载后另起进程，以 `remote` 模式重挂并核对最终文件树和内容，不读取本地日志。
热读计时结束后，另测 100 次 chmod/stat，再 fsync 和 close；保存每次延迟和
含排空总耗时，重挂后核对最终权限。三种模式固定按 remote、local-journal、
local-buffered 顺序运行，因此模式间耗时只作诊断，不作为正式性能结论。
另外用同一二进制补测 `local-buffered` 的 100 ms 间隔，共 12 个单元。
写入检查使用原 compose runner、512 MiB、每场景 20 秒，分别以 buffered/direct
运行 fio-seqwrite、fio-randwrite 和 fio-randrw，核对默认同步路径的写入、排空与
卸载。direct 的每个场景使用新服务和新挂载。它不是本地合批模式的吞吐性能对比。

## 结果

[完整检查日志](evidence/checks-07/check.log)：默认库测试 1353 项、二进制测试
2 项、Overlay 测试 2806 项通过，零失败；分别跳过 265/486 项，未运行全部外部
服务检查。新增专项测试 16 项通过，真实 Redis 测试另外执行并通过。
fmt、shell 检查、workspace check/build、两种 FUSE runtime 检查、Clippy 均通过。
[最终核验](evidence/verification.json)；[源码、二进制和后端版本](evidence/provenance.json)。

100 次同键修改、每种模式 3 次，本地 Redis 的结果如下。耗时含最后排空；
SET 包含提交确认摘要。源码为 [source.json](evidence/source.json) 中的基线加
[源码补丁](evidence/source.patch)。

| 模式 | Redis SET 次数（三次运行） | 含排空耗时中位数 |
|---|---|---:|
| remote | 100 / 100 / 100 | 68.44 ms |
| local-journal | 50 / 48 / 50 | 836.51 ms |
| local-buffered | 6 / 8 / 8 | 84.68 ms |

合并确实减少了远端写入，但这个测试中两种本地模式都没有降低总耗时，默认保持
`remote`。不能将请求减少等同于性能提升。
[逐次数据](evidence/redis-07/results.json)和[汇总](evidence/redis-07/summary.json)
可用 `python3 summarize.py evidence/redis-07/results.json` 重算。
早期逐次落盘方案的原始结果也保留在 [redis-05](evidence/redis-05/summary.json)。

优化构建下的 **48 个 FUSE 单元全部通过**：两个负载、缓存开关各 3 对，覆盖
三种模式及额外的 100 ms 内存合批。每个单元都在远端模式重挂，核对文件内容、
目录删除结果和最终权限。热读及连续修改阶段的 RustFS 请求和字节均为零。

下面是读取缓存开启时，100 次 chmod/stat 的诊断结果。耗时中位数包含 fsync/close。

| 模式 | 两文件：SET / 耗时 | 已修改上层：SET / 耗时 |
|---|---|---|
| remote | 200 / 137.07 ms | 200 / 144.64 ms |
| local-journal，10 ms | 135–147 / 1177.73 ms | 138 / 1159.60 ms |
| local-buffered，10 ms | 36–42 / 303.27 ms | 36–39 / 291.76 ms |
| local-buffered，100 ms | 6–9 / 198.59 ms | 6 / 170.51 ms |

更长间隔使合并更充分，但本机 Redis 场景中仍没有超过同步模式。
这里只验证可配置合并及远端写回，不据此推荐默认开启异步提交。
[原始 FUSE 记录](evidence/smoke-08/fuse-local-buffered/run.json)、
[100 ms 记录](evidence/buffered-100ms/run.json)、[汇总](evidence/summary.json)。

buffered/direct 的六类写入检查均完成，最终排空为零，正常卸载。
另外做了一对独立的新服务、新挂载下的混合读写复核：

| 默认同步路径 | 含排空吞吐 | 读 P99 | 写 P99 |
|---|---:|---:|---:|
| 原版 | 250.12 MiB/s | 295.70 ms | 101.19 ms |
| 修改后 | 268.76 MiB/s | 158.33 ms | 84.41 ms |

这一对未观察到回退；只有一对，不作为性能提升结论。
[混合读写复核](evidence/mixed-pair-10/verification.json)、
[direct 独立运行](evidence/direct-isolated-09/verification.json)。

## 保留的失败与限制

- `smoke-07`：记录脚本对同一结果文件重复使用独占创建，触发 FileExistsError。
  已改为热读、元数据修改、最终结果分别保存；原文件不改，重跑使用新目录。
- `smoke-08/write-guard-1`：复用服务连续运行 direct 场景时，统计读取被判失败。
  原始样本的指标和值齐全，可能是读取超时；原脚本未记录读取退出码，原因未完全确认。
  fio-randwrite 的错误码为零且最终排空为零，后续 randrw 未完成。
  随后三个 direct 场景分别在新服务和新挂载上通过；没有修改超时或采样门槛。
- 多场景连续运行后的 buffered 混合读写为 146.21 MiB/s，不能直接与先前单场景
  的 229.48 MiB/s 比较。上表另用同机的新原版/候选对照复核，两个结果都保留。

源码以 `evidence/source.json` 和 `evidence/source.patch` 为准。补丁由 Windows Git
导出，避免 WSL 未配置相同 LFS 过滤器产生无关的归档文件差异。构建日志和二进制
SHA 保存在 `evidence/build-07/`，不把大型二进制放入 Git。

尚未验证 TiKV 实例、跨机器故障恢复和正式大负载性能。过期租约的未提交日志
会保留并阻止挂载，当前没有自动换租约恢复流程。

重算本次汇总：

```bash
python3 summarize.py evidence/redis-07/results.json \
  --fuse-root evidence/smoke-08 --fuse-extra evidence/buffered-100ms
```
