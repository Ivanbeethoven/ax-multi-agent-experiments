# 修复版本与复测

run-002 的 C4 已完成，两组 64 个 Agent 均通过跨机冷重挂验证。三项 C3 Packed 读取依次使用原定的 Packed 程序。下一轮使用新的运行目录。候选由 AX、BrewFS 和修改版 RustFS 三部分组成；原版 RustFS 1.0.5 和 JuiceFS 保留作对照。

| 组件与任务 | 当前候选 | 进入正式复测前要确认的事 |
|---|---|---|
| AX：C4 回放目录 | `c5fd99b2`，已进入实验仓库 main | 各系统都用新建的 v2 输入，在普通子目录保存并回放 trace；读回验证也从该子目录开始。 |
| BrewFS：C1 读 | 缓存命中时先读现有页，再决定是否等待后台整对象 GET | 读取补丁的覆盖写、默认配置及 Workspace 测试均已通过。组合版本需固定读取策略，分别测顺序吞吐和随机 P99。 |
| 修改版 RustFS：C1 写 | [单提交 Git bundle](../../bench/cluster/diagnostics/dynamic_fio/rustfs-fsync-workers.bundle)，基于 `f4ed202` 的 `b525408` | 隔离复测与覆盖写后冷重挂已通过。正式复测显式记录每盘同步线程数，保留相同的文件和目录同步要求。 |
| BrewFS：C3 Native | TiKV 明确报告前缀结束时，省去空页查询 | 真实 TiKV 比较扫描次数与文件读取时间；缓存预算不变。验证字节限制下的短页、删除和并发修改。 |
| BrewFS / RustFS：C2 父子读写 | 诊断完成，保留现有程序和配置 | 单机交替读取父子工作区未复现稳定的父目录劣势。覆盖写的两次提交更新了不同时间戳；复测保留原有 fsync 语义。 |
| BrewFS / RustFS：C4 慢 PUT | flush 等待截止时间和 RustFS 慢副本分别验证 | 用可控的慢 PUT 复现上游等待，用重复通知测试本地截止时间。 |

确定候选后，记录各仓库提交、程序版本、构建参数与运行配置。用同一组合通过受影响的测试，并用独立挂载验证写后读、截断、权限、工作区隔离和冷重挂，再分发同一批二进制。源码目录有未提交改动的历史构建不作为可重建的版本标识。

C1 复测使用 `run_fio_candidate.py --read-fetch-policy workload`：Dynamic 开、关两边的随机读都设为 `sparse`，顺序读都设为 `whole-object`。按 [fio 运行配置](../../experiments/fio-runtime.md) 检查生成的输入和测量挂载参数。旧记录中的默认策略会随 hint 改变，旧对照不能单独说明块尺寸的效果。

**取出 RustFS 候选。** 在已有 `f4ed202508f2153afb7956bae6f7799b60eccd00` 的 RustFS 检出中，执行 `git fetch <bundle的绝对路径> refs/heads/codex/fsync-lane-workers:refs/heads/codex/integrated-fsync-workers`。该分支包含 `b525408eec9fc748a800f31d3d7ac153c8015025`；编译该版本后设置 `RUSTFS_DISK_FSYNC_WORKERS=8`。旧二进制不支持此参数。

A355 使用同一候选、hint off 的两次串行测量（每盘 1 → 8 个同步线程）：64 MiB 随机写吞吐 1.105 → 1.378 MiB/s，P99 109.576 → 95.945 ms；顺序写吞吐 160.804 → 176.552 MiB/s，P99 17.170 → 20.578 ms。另两轮分段观测完成 16,384 个 4 KiB 随机覆盖块的重挂读回检查。原始目录为 A355 的 `/srv/ax-cluster/diagnostics/write-a355-20261117/{quiet,quiet-segments}/`。

复测先覆盖出现差距的负载。C1 保留正式写入量和同步频率，读取显式记录 Sparse 或 WholeObject。C2 固定父子输入、权限和持久写条件。C3 区分 Native 与 Packed，并将准备和读取分别计时。C4 保持任务、顺序、随机等待计划、主机分配和 60 秒 truncate 设置相同；开启 Dynamic 使用修改版 RustFS 9000，关闭使用原版 19000。两轮均通过跨机校验后，按 `task_id` 比较应用耗时、文件系统操作、模拟等待、上传排空和冷重挂时间。

**run-002 的同期主机活动。** 北京时间 11 月 17 日 00:52:26 至 01:08:36，A355 有独立诊断构建；r1 与 r3 的内存峰值均为 6.0 GiB、交换空间占用峰值均为 7.9 GiB。它与 Dynamic 关闭的回放部分重叠。使用该轮结果时一并核对逐任务时间、主机 I/O 和 Redis 原始采样；没有逐秒换页记录的时段不据此推定受影响程度。下一轮正式测量期间，参与节点暂停诊断构建和其它性能测试。

**BrewFS 复测源码。** [源码增量包](../../bench/cluster/diagnostics/brewfs-run-002-base.bundle)以官方 main 的 `d94f4ad` 为基础，保留运行时最多 8 个默认工作线程、正式 Packed 使用的元数据读取重试和错误类型记录。当前组合为 `989c69e`。在含 `d94f4ad` 和 `5750af24` 的 BrewFS 仓库执行：

```text
git fetch <增量包的绝对路径> refs/heads/codex/cluster-retest-20261117:refs/heads/codex/retest-base
```

已在仅包含官方源码的独立仓库试过此命令。该包用于重建复测起点；表中的冷读、分页和写入等待改动另行测试。分页补丁需要经过真实挂载使用的 LocalMetadataBackend 测试。写入等待补丁需要同时通过重复通知的超时测试和上传前缀可见性测试。

**冷读候选。** 在上述复测起点取回 [读取改动](../../bench/cluster/diagnostics/read_path/brewfs-read-candidate.bundle)：

```text
git fetch <读取改动增量包的绝对路径> refs/heads/codex/retest-read-candidate:refs/heads/codex/retest-read
```

`c5f9491` 包含 [缓存命中先于整对象等待](https://github.com/brewfs/brewfs/pull/457)、读取缓冲区分配、稀疏区域处理和已有的 Native 预读。PR 457 修改前后各测 30 轮，同时阻塞一个 4 MiB GET 约 100 毫秒。命中已缓存的 4 KiB 页时，P99 从 101.985 毫秒降至 0.654 毫秒；两组各发出 30 次 GET。该补丁的默认配置测试通过 1,363 项，Workspace 测试通过 2,934 项。集成版本的集群 fio 结果另行记录。

**冷读与写入等待合并版本。** 从包含 `d94f4ad` 和 `5750af24` 的官方仓库，一次取回 [完整候选源码增量包](../../bench/cluster/diagnostics/brewfs-replay-candidate.bundle)：

```text
git fetch <增量包的绝对路径> refs/heads/codex/retest-replay-candidate:refs/heads/codex/retest-replay
```

`efd4417` 包含上述冷读修改与 flush 超时修复，后台上传通知不会重新开始计算超时时间。写入补丁单独测试时，阻塞一个 5 字节 PUT 后，真实挂载上的 ftruncate 在 30.003 秒返回 ETIMEDOUT；释放上传后 fsync 成功，重新挂载读回了截断后的单字节。此修改限制了等待时间，PUT 速度仍需单独测量。TiKV 分页修改等待实际后端测试，暂未进入此版本。

集成版本须在同一份源码上运行 BrewFS 仓库规定的 Workspace 测试、构建、Clippy 和运行时检查。使用 Compose 对照随机读写 `fio-randrw`，保留吞吐量、P99、关闭文件和排空上传所用的时间，再决定是否采用该版本。与 JuiceFS 比较时保持缓存预算、工作集、直接 I/O 和同步要求相同。运行这些检查前先等 Packed 读取完成；协调机上的候选构建和已排队的 JuiceFS 补测使用同一个 systemd 服务名 `ax-run-002.service`，先取得服务名的任务先运行。

64 个 Agent 的两轮记录中，每个任务的上传字节差都恰好等于 PUT 次数差乘以 4。按每个版本化对象的 4 字节头部扣除后，两组均上传了 5,227,516,032 字节的对象内容。开启和关闭 Dynamic 时，PUT 次数分别为 203,017 和 199,775，客户端每次 PUT 平均耗时为 420.777 和 172.560 毫秒。复测需区分 RustFS 临时文件写入、磁盘同步等待和对象重命名三个阶段。

写入等待补丁单独通过了工作区构建、Clippy、1,365 项测试和两项真实 FUSE 回归测试。正式开、关配置的 64 个挂载累计等待写回分别为 61,161.64 秒和 32,367.87 秒；多个挂载同时等待，这两项数字不代表整轮耗时。

定位 PUT 差异时，先复用[随机写诊断](../../docs/DYNAMIC-FIO.md)做 64 MiB 的小规模对照：在同一修改版 RustFS 上只切换块尺寸提示，再在关闭提示时与官方 RustFS 1.0.5 比较。固定 BrewFS 二进制、线程数、读写策略、磁盘及缓存；修改版两次的每盘同步并发相同，并记录官方版的实际并发。比较每轮的 PUT 次数、字节、延迟和同步等待。此诊断单独记录；正式 64 Agent 对照仍按开启用 9000、关闭用 19000 执行。
