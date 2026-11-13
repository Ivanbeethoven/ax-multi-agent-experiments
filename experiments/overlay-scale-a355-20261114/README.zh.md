# A355：Overlay 热读结果与复现

54 个读取单元完成，6 个上层修改/重挂单元通过。**完整验收未通过**：原版和候选都在
并发替换文件时出现 ENOENT，混合读写时出现 EIO。代码保留为待审候选，不合并、不计入 C1–C4 正式完成数。
只使用已有 A355，没有新建云资源；本轮未运行 SQLite 测试。

[逐次结果、阶段耗时与查询计数](RESULTS.zh.md) · [原始记录](evidence/phase2-results.tar.gz) ·
[验收状态](evidence/final-validation.json) · [源码与二进制对应关系](evidence/code-provenance.json)

## 测到什么

下表为整批计时的中位数，单位 ms；预热不计入。2/32 文件各计时 257 轮，1 万文件计时 3 轮。
“候选/原版”回答代码改动的影响；“候选/lower”回答整个 Overlay 的增量成本。

| 实际文件 / 上层行缓存 | 原版 Overlay | 候选 Overlay | 直接 lower | 候选/原版 − 1 | 候选/lower − 1 |
|---|---:|---:|---:|---:|---:|
| 2 / 64 MiB | 989.184 | 963.925 | 646.872 | −2.55% | +49.01% |
| 32 / 64 MiB | 9332.770 | 9154.190 | 7475.006 | −1.91% | +22.46% |
| 10,000 / 16 MiB | 62686.925 | 61530.110 | 26387.810 | −1.85% | +133.18% |
| 10,000 / 64 MiB | 42122.125 | 40281.624 | 26631.886 | −4.37% | +51.25% |

1 万文件、64 MiB 组的三对变化为 **−4.37%、+4.29%、−18.34%**，不能称为稳定的 4.37% 收益。
16 MiB 组为 −3.22%、−2.54%、+1.06%。小输入也有反向波动，全部逐对值见结果页。

各阶段取重复的算术平均值。例如 32 文件组：打开目录 332.363→218.366 ms，列目录
355.522→257.917 ms，文件 open 5021.305→4927.757 ms；pread 反而为 1732.784→1815.936 ms。
这些阶段均值不能与表中的中位总耗时混加，也不能分别归因于某一项代码修改。

## 改动与剩余成本

- 权限缓存命中时共享不可变快照，省掉向量和记录复制；只读 open/close 立即完成时省掉任务切换。
  等待中的操作仍交给后台任务，保留取消后的执行、排空与错误处理。
- 目录初始化把读取 head、验证两层及租约合为一次一致快照。小输入一次遍历包含两次目录初始化，
  独立 Redis 审计从 10 个客户端命令 + 6 个 Lua 内部命令，减为 8 + 0；直接 lower 为 0。
  这是实际命令数，不是网络往返数。返回结果前仍验证 namespace。
- 最新 main 已有空区间和空目录缓存。小输入两版计时阶段这些上层查询都为 0，本轮没有再加一套缓存。
  默认上层行缓存保持 64 MiB；容量、TTL、租约失效、写入失效和合并提交确认规则不变。
- 1 万文件、16 MiB 组容量不足：两版均为 30,000 次上层区间查询、0 次空区间命中。
  64 MiB 组约保留 25.70 MiB，两版均为 10,000 次查询、20,000 次空区间命中。
  64 MiB 组预热耗时 62–66 秒，超过行缓存 60 秒 TTL，数据已热但部分元数据必须重新查询。
  第三次候选把 2,815 次查询推到后两轮；三轮查询总数未减少。刷新位置与运行波动各占多少，尚未隔离。
- KV 续租回归验证 head 版本、权限快照和空目录结果不因这次续租失效；未复现 JuiceFS 参考中怀疑的问题。
  心跳仍可能产生 Redis 请求。零对象读取不等于零元数据请求。

## 正确性与未通过项

新增回归覆盖快照共享及 chmod 失效、任务取消/排空/panic、单次快照、base 版本冲突、旧写者和过期租约拒绝、
并发 head 更新、实际续租。KV 组 476 项通过、92 项 ignored；准备路径 27/1、行缓存 13/1、合并写回 19/2。
这些分组与单独运行的新增测试有重叠，不累加为唯一测试数。检查日志见归档和 [检查回执](evidence/check-receipt.json)。
构建、fmt、默认及实测 features 的严格 Clippy、两种 FUSE runtime 检查和 shell 检查通过；未运行全后端测试集。

真实挂载的 6 个单元验证改写、截断、扩展、删除、创建、重命名、并发 chmod/read、fsync，以及新进程重挂后的内容和权限。
此外保留两类失败：

| 未通过项 | 原版 | 候选 | 已确认 / 未确认 |
|---|---|---|---|
| rename 替换与打开并发 | ENOENT | ENOENT | 替换目标持续存在；具体 lookup/open 生命周期问题未定位 |
| 完整写入并读回 16 MiB 文件后 fio randrw | EIO | EIO | 两版 debug 日志均为 `workspace is busy`；具体冲突来源未定位 |

fio 失败不是只在稀疏文件发生，也没有可用的混合读写吞吐对照。没有通过忽略错误或放宽一致性处理这些失败。
最终候选的冷态、大规模跨机、完整 fio/xfstests 和其它元数据后端未验收；第一轮冷态数据属于较早候选，不能替代本轮。

## 测量口径

- 原版 Overlay、候选 Overlay、同引擎直接 Packed lower 三条路径。
  两版 Overlay 都可写；直接 lower 仅辅助解释增量成本。
- 正常 release、opt-level=3；实际属性/目录项 TTL 均为 0，按 stats 验证。
- FUSE 读取使用 direct I/O；上层行缓存 TTL 为 60 秒。关闭通用内存/SSD 读缓存、后台预取和
  Packed verification memo；只保留下面列出的 Packed 缓存。完整环境变量和挂载配置随每单元保存。
- 小输入为 2 文件和 32 文件，各 6 对；大输入为 10,000 个真实 4 KiB 文件，其中 9,999 个在同一目录，
  每档缓存 3 对。每对交换版本顺序，原始顺序保留。
- 大输入分别使用 16 MiB、64 MiB 上层行缓存。两档的 Packed 元数据预算均为 256 MiB，
  数据帧缓存均为 64 MiB；不同档位单独报告。小输入数据帧缓存为 8 MiB。
- 每单元先验证内容、卸载、用新目录重挂，再预热并确认热读对象请求及字节为零。
  大输入另在计时前以 4 KiB getdents64 缓冲区核验分页和全部目录名，保存实际用户态分页调用次数。
- 大输入计时为首轮加后续两轮，共 30,000 次 pread、122,880,000 B。
  总耗时不含预热及阶段间采样等待；open/readdir/pread 等逐次值全部保留。
- 百分比统一为 `100 × (本项耗时 / 对照耗时 − 1)`。正数表示变慢，负数表示这次更快。
  汇总直接用表中的中位耗时相除；逐对值另列，不把“比值的中位数”冒充这个结果。
- Redis commandstats 是实际服务端执行命令，含 Lua 内部调用，不能称为网络往返次数。
  对象零读取不代表元数据零查询。
- 每单元在计时与计数快照结束后，另跑一次相同遍历，用 MONITOR 区分客户端命令与 Lua 命令。
  原始记录保留时间和命令名，去掉参数；这个审计不计入性能耗时，也不冒充计时区间的客户端请求数。
  计数包含该窗口内的心跳等后台活动，排除审计自身的起止 PING。

## 复现

依赖 Linux FUSE、Docker、Python 3 + boto3、Rust 工具链；fio 检查另需 `/usr/bin/fio`。
在没有其他存储测量的现有主机运行。脚本持有本实验锁，服务用完回收，失败输出保留。
输入在本机按固定内容生成，不下载 trace；不与构建、归档并行。
固定版本和基础环境见[第一轮](../overlay-open-a355-20261113/README.zh.md)。

```bash
# 在实验仓库根目录；CANDIDATE_COMMIT 见 code-provenance.json。
bash experiments/overlay-open-a355-20261113/build.sh /src/brewfs /out/build CANDIDATE_COMMIT
bash experiments/overlay-scale-a355-20261114/run.sh \
  /out/build/baseline-artifacts /out/build/candidate-artifacts /out/new-scale

# 上层修改、并发 chmod/read 和重挂；--concurrent-kind replace 可复现替换/open 失败。
python3 experiments/overlay-scale-a355-20261114/run_mutations.py \
  --concurrent-kind chmod \
  --binary /out/build/candidate-artifacts/brewfs \
  --producer /out/build/baseline-artifacts/packed_snapshot_fixture \
  --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725 \
  --source-patch /out/build/candidate-artifacts/source.patch \
  --workspace-format workspace --build-profile optimized-release \
  --comparison empty-cache --object-meter proxy --cache-state on \
  --metadata-sync remote --metadata-edits 8 --pairs 3 --passes 32 --out /out/new-mutations

# 已知原版也失败；首个失败即停，不把未执行单元记为通过。
bash experiments/overlay-open-a355-20261113/run-regression.sh \
  /out/build/baseline-artifacts /out/build/candidate-artifacts /out/new-regression
```

单独运行 mutation/fio 脚本时也须持有 `/run/lock/ax-overlay-open-diagnostic.lock`，并确认没有其它任务。
Rust 定向检查入口为 BrewFS 的 `tools/perf/check_overlay_hot.sh`。脚本与报告检查：

```bash
python3 -m unittest discover -s experiments/overlay-open-a355-20261113 -p 'test_*.py' -v
python3 -m unittest discover -s experiments/overlay-scale-a355-20261114 -p 'test_*.py' -v
python3 experiments/overlay-scale-a355-20261114/report.py \
  experiments/overlay-scale-a355-20261114/evidence > /out/RESULTS.zh.md
```

## 版本和原始记录

冻结时 BrewFS 最新 main 为 `03c8cc55d61473533c00a8afe13b4ef1fcd9b725`。
参考 JuiceFS 分支 `codex/lazy-workspace-fork-20261113` 的 `8aeb68f` 与 `5708225`；没有复制其补丁。
基线只加两版共有的 standalone Packed 缓存预算接线，候选再加本轮优化。
[完整源码核验](evidence/full-source-audit.json)比对了 2,015 个文件；只归一化 CRLF/LF，
构建原文件不改写，[原始与归一化哈希](evidence/source-normalization.json)均保留。

原始包 `phase2-results.tar.gz` 为 42,366,770 B，SHA256：
`a327702e6f3ce7bb98bfb05744c1bcb17c959eff1498dfb926206e6e1ad31fc8`。
包含 `measure-02/{small,large16,large64}` 的逐调用数据、预热与分页证明、实际参数、Redis 命令审计、
对象/缓存计数、构建与测试日志、源码补丁、失败现场和启动命令。不含二进制；SHA 见
[binary-SHA256SUMS](evidence/binary-SHA256SUMS)。

`measure-01` 是审计 PING 污染计数导致的失败尝试，已保留；正式批次把审计移到计时与快照之后，
没有修改“直接 lower 不应访问 Redis”的检查。每单元保存的脚本副本对应实测版本；当前脚本后续增加了
独立诊断日志开关和更完整的汇总字段，不冒充原运行时的脚本 SHA。

```bash
sha256sum experiments/overlay-scale-a355-20261114/evidence/phase2-results.tar.gz
mkdir /out/archived-scale
tar -xzf experiments/overlay-scale-a355-20261114/evidence/phase2-results.tar.gz -C /out/archived-scale
python3 experiments/overlay-scale-a355-20261114/summarize.py \
  /out/archived-scale/measure-02/large64
```
