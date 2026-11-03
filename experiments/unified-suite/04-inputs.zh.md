# 输入、一次导入和 base 验证

## 资产与工作区分开

`plan.assets` 给出实际 pool/dataset/representation/layout 的 asset ID。相同不可变 asset 跨重复/冷热/缓存开关复用；不同对象布局使用新前缀，不能靠重新挂载改写旧布局。每个 cell / workspace 创建新 upper 与私有缓存；写过的 workspace 不回到待测队列。

准备期由一个协调者逐池分配写权限，记录正在执行的 prepare/import PID；同池不并发准备和测量。只有准备完成、进程/挂载已核对后才注册 asset 并领取 block。状态库的测量锁不追踪在库外手动执行的 import，不得另开第二个 AI 重复导入。

所有导入包含：原始输入验证、准备与导入 wall、逻辑字节/文件数、实际对象/元数据量、producer 临时盘峰值、排空、原始退出、首次全量校验、随后缓存重置。`timings_seconds` 保留各段，等待/失败写在事件时间线。

## 百万文件

```bash
"$BIN/smallfiles" prepare --root "$DATA/million-source" --output "$OUT/source-prepare" --files 1000000
"$BIN/smallfiles" verify-base --root "$DATA/million-source" --output "$OUT/source-verify" --files 1000000
```

生成 1024 个目录中的 1M 个4KiB文件，内容由 file ID 决定，源不含用户数据。root 必须不存在，output 必须是新目录；每文件 fsync、目录 fsync 都计入源准备。不使用不可压缩假设替代数据说明；算法与版本固定，报告实际压缩/封装大小。

三类导入按本批 CLI 和路由资格执行一次：

| 路由 | 导入/复用方式 | 必须保存 |
|---|---|---|
| native flat | 冻结配置挂载独立命名空间，将源复制到原生 base | native配置、真实复制退出、全量读回 |
| native-workspace | 原生 base publisher/导入，再建立可引用的 immutable revision | revision、对象前缀、writer release、manifest |
| entity-native | 首次 `--entity-native-source` 冻结源；后续 `--entity-native-manifest-key` | 原生输出的真实 manifest key，不猜测路径 |
| entity packed | packed producer 构建/上传；后续使用同一个 manifest key | manifest/authentication、producer参数、对象哈希 |
| JuiceFS | 原生 `juicefs sync` 导入一次；后续 `juicefs clone -p BASE NEW` | 原生 sync结果、base绑定、实际metadata URL |

每次 producer 使用数据盘临时目录：

```bash
export TMPDIR="$DATA/producer-tmp"
# 预先创建并核空间，记录 st_dev；它必须在被声明的数据盘。
```

producer 自身用于排序/索引的临时 SQLite 是**导入成本**，与作为数据库服务的 metadata baseline 无关。本仓库控制端 SQLite 只管理领取与事件，不放测试数据、不计入文件系统读写延迟；控制成本仍在全程成本中。

Packed 初始参数冻结为 chunk=4 MiB、block=4 MiB、metadata/data codec=raw、inline=off、size-only、random-small-file profile、max random frame raw=256 KiB；以实际 producer `--help` 支持和 manifest 记录为准。codec/g/frame变化必须独立 asset 和实验 ID。导入前后记录真实 uploader hint；不能只核 mount flag。

导入后在各自只读 base 上执行一次 `smallfiles verify-base`，读全量内容并检查目录/文件名。之后的新 child 验 touched/父/兄弟与远端逻辑，无须每 child 都反复扫全量；资产变化、源码/manifest不符或发现异常时该一次校验失效，保留现场，不能跳过问题继续。

## 完整 trace

四份目录为 pytest-full-02、django、ripgrep-20261026、archive-io-20261026，实际来源见 [trace 数据目录](../../paper-design/TRACE-WORKLOADS.zh.md)。别用旧简短 v1 trace 代替 full trace。

每份须有 `capture/events.jsonl`、`capture/blobs/`、`initial/`、`initial-tree.json`、`final-tree.json` 和 `metrics.json`，包括 repo/HOME/XDG。全量文件 hash 清单必须与来源包对应；不要把 secrets、真实 HOME 的额外内容混进输入。

```bash
python3 bench/trace_checks.py inputs --case "$TRACE_CASE" --manifest "$TRACE_MANIFEST" \
  --sha256 "$TRACE_MANIFEST_SHA" --output "$OUT/trace-input-check.json"
```

此 trace manifest 格式是既有 `相对路径→SHA256`，与 suite 的文件清单格式不同，不能混用。源包还原/检查用 [full replay 工具](../../tools/full-fuse-replay/README.md)。每个任务恢复原 initial tree，底层共享不可变 base，任务写入独立 upper；首次严格 replay 与最终树验证是资格成本。测量段用同一 trace 的性能模式，随后验证实际结果树。

## fio

每 job 私有 regular file，完整 CRC 初始化一次/asset；block size、numjobs、file size 是资产身份的一部分。顺序1MiB fixture不能充当4KiB验证格式。写型条件每次从冻结 base 派生新 workspace，避免前一个条件写入改变后一个条件。详情见 [fio](06-fio.zh.md)。

## 注册

填写生成的 `asset-<id>.draft.json`：四个阶段各引用真实结果及精确成功条件，`native_binding` 给出不可变 base 身份，`timings_seconds` 写实际费用对应时间。所有证据先下载到控制端，路径/摘要保持明确。

```bash
python3 -m bench.suite asset --db "$RUN/state.sqlite" --receipt "$RUN/asset-verified.json"
```

四阶段为 source-verify/import/full-base-verify/cache-reset。此处 cache-reset 表示导入后的准备完成；**每个单元仍需 fresh client/cache**。失败导入的日志和费用继续保留，不伪造已注册资产，也不清理原现场后静默重来。
