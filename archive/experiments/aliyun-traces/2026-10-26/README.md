# AI trace 性能重测（2026-10-26）

这次只使用真实 Agent 的 `pytest-full-02` trace：1,747,109 个回调，原始写入内容，原始顺序。不是 4096 小文件合成测试。

## 本轮安排

1. 同一台按需 ECS：8 CPU / 32 GiB，100 GiB XFS 数据盘；Redis 和 RustFS 同机。单机初测，不代表分布式扩展性。
2. XFS 分别跑严格模式和性能模式，估计回放器附加检查的影响。各一次，不作统计显著性结论。
3. native、native-overlay、native-overlay-pack、JuiceFS 各完成一次性能回放；同一 trace、工作目录层级、操作数和字节数。
4. 每项单独记录初始数据准备、packed 构建与上传、挂载、回放耗时。打包源是已静止的初始数据，本轮不测在线冻结成本、fork 或迁移。
5. 数据准备后重新挂载、清空本轮客户端缓存并清理主机页缓存。每种方法记录首次完整回放，暂不做重复均值。

## 性能模式怎么减负

继续使用 `tools/full-fuse-replay` Rust 工具；Python 只负责调度和低频观测。严格模式保留。

- 输入归档 SHA256 和可信私有工作目录检查只做一次，计时阶段不增加 `exists/canonicalize` 路径探测。
- 不逐次计算读写内容 SHA256、不比较 stat/目录内容、不做最终全树和数据库扫描。
- 保留全部 xattr/ioctl 调用，过滤操作数为 0。只读探测预期和实际都失败时，允许错误码不同并计数；不把这种结果称作严格正确性通过。
- 保留读写返回长度、操作完成数、总字节数和未关闭句柄计数。非探测操作的异常会停止该项，失败/超时不能充当完整耗时。
- 每 65,536 个操作打印一条进度；主机/进程每 2 秒、服务每 5 秒采样，日志写系统盘。

## 本轮结果（已结束）

| 配置 | 回放耗时 | 结果 |
|---|---:|---|
| XFS，性能模式 | 56.82 s | 完成全部 1,747,109 个回调 |
| XFS，严格回放对照 | 69.63 s | 完成全部回调及回放器内部检查；未做额外最终全树扫描 |
| native，性能模式 | 160.87 s | 完成全部回调，读写字节数一致；允许的探测 errno 差异 1,048 次 |
| native-overlay | 1,500 s 上限 | 超时，不能作为完整耗时 |
| native-overlay-pack | 144.45 s 后失败 | 第 130,117 个回调 opendir 返回 EBUSY，不能作为完整耗时 |
| JuiceFS | 1,500 s 上限 | 超时，不能作为完整耗时 |

各配置只测一次，XFS 两种模式不构成统计性配对实验。观察到的差值 12.81 s 不是检查开销的精确估计，也不能解释 overlay 的 25 分钟超时。初次严格 XFS 调度遇到目录已存在，原记录保留，随后使用新目录补跑得到表内结果。packed 构建上传 7.73 s、初始化 0.064 s，均在回放计时之外。

北京时间 2026-10-27 00:05 已下载最终归档（SHA256 `59ae8531634c64f786f17904cd7927b595b73bed64262268f37c3418c707209c`，7,061,344 bytes），已知凭据扫描通过；本轮实例、两块新盘、安全组、临时桶均已释放，每 10 分钟检查任务已删除。修复跟踪见 [issue #354](https://github.com/brewfs/brewfs/issues/354) 和 [初步定位](diagnosis.md)。

入口：[run.py](run.py)。原始数据、临时控制状态和结果下载在 `runs/aliyun-ai-perf-20261026/`（不提交 Git）。小型结果另存本目录 `results/`。结果中的 `completed_workload` 表示完整性能负载完成，`correctness_certified` 始终为 false。

本地修复对照入口：[repro_local.py](repro_local.py)。在 Linux 上以 root 运行，传入新的 `--root`、原始 `--runtime-archive`、`--trace-archive`、修复后的 release `--candidate` 和原观测脚本 `--observer`。按用户 10 月 27 日的新要求，后续回放不设总时长上限，持续低频观测，直到负载完成或实际报错。上表、`run.py` 和原始归档保留旧实验的真实配置，后续复测使用更新后的入口，不复用历史时限或云实例自动截止设置。先通过本地 CI，再运行测量。工具复用原 Rust 回放器，输出 `<root>/results/status.json`，仅清理本次创建的挂载和容器；不申请云资源、不清空主机页缓存。本地诊断与上表云端成绩分开记录。

本地后端均走回环地址；脚本会清除子进程继承的 HTTP/HTTPS/ALL_PROXY，避免 `NO_PROXY=127.*` 在 Rust HTTP 库中不匹配 IP 的问题。系统代理不变。早期本地 `20261027-01` 继承了这类环境，只保留为故障诊断，不用于性能验收。

已保留基线失败证据后，可用 `--methods candidate-packed candidate-overlay` 单独验证候选的两种完整回放，不筛选 trace 操作，也不使用 `--timeout` 参数。实际方法选择保存在 `status.json`，`replay_timeout_s` 为 null。复测结果单独保存，不能覆盖历史超时或失败。

JuiceFS 的旧记录是被 1,500 s 截止停止，最后采样为 1,179,648 次操作（1,451.39 s），没有回放错误，不能据此判为 JuiceFS 故障。复测可加 `--methods candidate-packed candidate-overlay juicefs`，或单独 `--methods juicefs`。它从原 runtime 归档提取并核验同一 JuiceFS 二进制，使用独立 Redis DB 6、私有桶、512 MiB 缓存/缓冲区和零 metadata TTL；准备后正常卸载，再以全新客户端缓存重新挂载。前台挂载 PID 由 harness 保留并等待退出，持续使用原低频观测和全量 Rust 回放，不设回放时限。10 月 27 日独立云端复测已完成全部操作（1853.92 s），结果与中断恢复记录见 [JuiceFS 复测](../2026-10-27/juicefs/README.md)；不把性能完成称为严格正确性通过。

传入 `--compose-runner <BrewFS仓库>/docker/compose-xfstests/run_perf_in_container.sh`，可在候选版本完成两种完整 trace 后，使用该原有脚本做修复前后 compose 对照：512 MiB fio 顺序写、随机写、混合读写（各 20 s，buffered/direct）及 metaperf，包含 prefill、drain 和 remount；同为 workspace-v1、upload_before_commit、512 MiB 内存预算。它使用独立 compose 项目，避开原宿主包装脚本按固定端口清理容器的行为。
