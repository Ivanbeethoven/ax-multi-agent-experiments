# 每项测试后的清理

每个条件按“检查空间 → 准备 → 测量 → 排空与验证 → 停服务 → 保存结果 → 清临时目录 → 复查空间”执行。清理仍占用本池和迁移目标的锁，完成后才派下一项。正式实验继续暂停。

## 保留什么，删除什么

| 内容 | 处理 |
|---|---|
| 结果、阶段时间、正确性与退出回执、原始失败证据、版本与配置、输入 SHA、资源账本、state.sqlite | 保留在结果目录；必要原文按原协议归档到盘外。私有配置单独保存。 |
| 下载的 trace、events/blobs、初终态清单、二进制、镜像、共享 lower/base | 同批复用。最后一个使用者结束后才评估回收；不随每个 case 删除。 |
| 成功小试自己的缓存、生成输入、RustFS 临时数据目录 | 确认进程退出、挂载消失、私有容器删除、Redis 退出后删除。 |
| 失败、中断、缺回执、身份不明的目录 | 保留路径和失败原因，停止该块。先诊断并将所需证据核验到盘外，再按精确路径单独审查清理。 |
| 正式共享服务里的 workspace、Redis key、RustFS 对象 | 走文件系统删除与 GC 接口，先核引用；不直接删服务数据目录。 |

历史目录没有本次所有权记录时，自动清理器不接管。它不删除云资源、不按名称或时间批量删除，也不执行 Docker prune、Redis FLUSHALL 或 S3 通配删除。

## 8 块的清理边界

| 块 | 每个条件结束后回收 | 同批保留 |
|---|---|---|
| 1 基础读写、2 动态块 | 私有测试文件、客户端缓存；原生删除后等待后端回收 | 工具、输入清单、布局与读回证据 |
| 3 完整 trace、5 trace 消融 | 本次 workspace/upper、解压副本和缓存 | 原 trace、blobs、初终态、已核验的共享 base |
| 4 热 Overlay | 本次挂载与缓存 | 热工作集和预期内容；下一臂重新预热并核后端零读取 |
| 6 小文件、7 fork/clone | 子工作区先删除，确认无引用后再删父；留隔离与冷验结果 | 各自独立的不可变输入与 oracle |
| 8 迁移 | 源与目标都退出后清各自缓存、关隧道，最后处理共享卷 | 两端身份、文件树校验、分段耗时和退出回执 |

对象删除、GC、压缩、传输都放在测量窗口外。后端删除命令成功后仍查物理空闲量；异步 GC 尚未释放的容量不计入下一项可用空间。

## 空间检查

每项开始前，对客户端、迁移目标、Redis 和每块 RustFS 数据盘同时查容量和 inode：

```text
当前可用 − 下一项新增峰值 ≥ 总量 × 20%
```

新增峰值包含导入副本、私有写、缓存、AOF rewrite、归档临时副本和拟保留的失败现场。估算来自实际输入与已测峰值。余量不够先停派发；保留结果，清本任务已确认可再生且无引用的内容。测量途中不做 GC 或扩盘。

小试新增的自动检查覆盖 `--scratch-parent` 所在文件系统；正式多盘检查仍由原准入脚本完成。每项收尾再记实际可用字节/inode。目录占用与磁盘释放量分开记录，因为硬链接、缓存和异步回收会使两者不同。

## 小试：自动隔离与清理

Linux 上先准备专用数据盘目录，例如 `/srv/ax-scratch`；结果放在另一个目录。`common.json` 及分块参数见 [BLOCKS](BLOCKS.zh.md)。`PEAK_BYTES`、`PEAK_INODES` 填本块预计新增峰值，填 0 只检查当前余量。

```bash
python3 -m bench.experiment_smoke_matrix --block c1-io \
  --common-args /private/common.json --out /srv/ax-runs/c1-io-001 \
  --scratch-parent /srv/ax-scratch \
  --scratch-peak-bytes "$PEAK_BYTES" --scratch-peak-inodes "$PEAK_INODES" \
  --dry-run
```

核参数和池锁后，去掉 `--dry-run` 执行。每个 case 分配新的 `ax-owned-*` 目录，通过 `TMPDIR/TMP/TEMP` 传给子进程；结果旁的 `<CASE>.scratch/` 保存目录身份、进程身份、退出码、清理预览和回执。共享输入使用外部只读路径。

自动删除需要同时满足：子进程退出码为 0、原生测试通过、FUSE 进程退出、所有挂载消失、私有 RustFS 容器删除、Redis 退出、所有权与目录身份未变。容器按完整 ID 和本次标签核验；Docker 查询失败记为未知。某一步报错仍尝试停止其他私有服务。失败保留临时目录并停止本块，独立池可继续。

跨机目标使用自己的临时目录；成功退出后自行删除，失败在 `move.json` 留下 `scratch_path`。源端的 `--scratch-parent` 不会清目标机器。

## 成功后遗留目录：先预览再删除

下面只处理已登记、测试通过且退出证据完整的目录。适用于清理器未执行完但目录和证据仍完整的情况；失败或强制中断不能用此命令强行放行。

```bash
python3 -m bench.run_cleanup preview \
  --directory /srv/ax-runs/c1-io-001/C1-io-brewfs-basic.scratch \
  --out /srv/ax-runs/cleanup-preview-001.json
sha256sum /srv/ax-runs/cleanup-preview-001.json

# 核对 root、eligible、reasons 和预估占用；SHA 填上一步实际值。
python3 -m bench.run_cleanup apply \
  --plan /srv/ax-runs/cleanup-preview-001.json --sha256 "$SHA" \
  --out /srv/ax-runs/cleanup-applied-001.json
```

创建进程仍活跃、主机重启、挂载出现、目录被换、回执或预览变化时拒绝删除。符号链接的外部目标保留。清理回执记录精确路径、证据 SHA、前后空间/inode 和实际差值。

失败现场先保留小型日志和阶段回执；如诊断需要缓存、对象文件或私有配置，在盘外按精确清单保存并核 SHA。确认所属任务、进程/挂载、保留范围与共享引用后再单独清理，不能把失败回执改成 passed。未结束的服务、远端遗留和历史目录记在交接清单，下一项不自动绕过。

## 本次检查与验证

修复了“Docker 查询失败被当成容器不存在”“清理报错跳过 Redis 停止”“失败现场被删除”三类问题；目标端卸载报错后仍等待文件系统进程退出。增加容量不足拦截、按 case 隔离与清理回执。

Linux 回归运行 704 项：678 通过、26 条件跳过、无失败；新增 16 项清理测试全部通过，包含真实临时文件的命令行预览/删除。服务异常分支使用受控替身，本轮没有启动真实 FUSE/Redis/RustFS。临时测试目录已删除，小型测试日志留在本地并核 SHA。详见 [检查记录](../../../validation/cleanup-flow-20261113.json)。

自动目录清理适用于最小脚本；正式 suite 仍执行其冻结的关闭、归档和准入流程。云资源释放继续按 [RELEASE](../pilot-aliyun/RELEASE.zh.md)核本任务创建证明。
