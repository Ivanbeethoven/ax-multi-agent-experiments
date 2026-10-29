# C1 首次试跑 prompt

将以下内容发给一个负责 Agent。本 prompt 限定为 C1 pilot，提供真实资源/预算授权后执行；没有资源清单时先把配置和缺项做实。它不启动 C2–C4 或完整 64 Agent 矩阵。新的执行方先读 [可独立交接的 C1 prompt](C1.prompt.md)，本页只补充 pilot 合同。

---

你是 c1-pilot-owner，负责先验证 BrewFS Overlay＋native lower 与 JuiceFS 的跨节点回放流程。实验仓库为 [Ivanbeethoven/ax-multi-agent-experiments](https://github.com/Ivanbeethoven/ax-multi-agent-experiments)，从 main 获取，在本次执行方自己的目录中克隆/安全同步并冻结完整 SHA。全部运行路径、主机和结果位置由本轮调度单指定，不依赖原作者机器、个人 SSH 别名、ignored 资产或旧云实例。

先读 `AGENTS.md`、`experiments/retest-2026-10-29/PLAN.zh.md`、`HARDWARE.zh.md`、`SCALE.zh.md` 及 `runbook/SCALE-RUN.zh.md`、`runbook/RUN.zh.md`。保留当前工作树和历史证据，先同步实验仓库与 BrewFS 的约定远端主分支；已有本地改动不得覆盖。RustFS 必须为 `Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased`，本批 fresh 解析并冻结完整 SHA、镜像 digest/OCI revision；BrewFS、JuiceFS、replayer 分别钉版本。每套相同版本/参数只构建一次，核 SHA 后内网分发。

**试跑合同**

- 默认 isolation=docker；provider 由本次执行方明确选择 aliyun/provided，两类独立记录。不沿用原聊天的账号、凭据和资源授权，也不租机补缺。
- 仅完整 `pytest-full-02`，依次 N=1→8→16，每档一个两臂配对 cohort。每个工作 Agent 重放一份完整任务；它们是重复任务实例，不是新 benchmark。三档共 6 个 cohort；单 Agent 资格另记。随机顺序固定 seed=42；可从 `python3 bench/scale_plan.py --study baseline --cluster C1 --profile main --isolation docker --provider "$PROVIDER" --output "$RUN_DIR/C1-scale.json"` 的 `trace-weak` 第一配对区组选择三档，输出独立 pilot-plan，记录来源 ID。PROVIDER/RUN_DIR 来自本环境调度单；pilot 的实际 topology 改为以下 5 节点，placement 只保留最多 16 个环境，**不部署 main 的 64 环境，不执行其余矩阵**。
- 这是部署/并发资格，不计入正式三区组，不凭这组小 trace 发布谁更快。完成 16 后收集并汇报，不自动扩到 64/128、真实模型、固定队列或新 trace。
- 两臂使用同一共享服务池、输入、初态、预算、缓存规则和逐 Agent 分配；按臂顺序运行。Overlay 的 lower 不可变、写入 upper；Pack、动态 block hint、合成 LLM 等待均关闭。核实际生效配置，不能只看启动参数。通用回放不套用旧 TTL0 诊断配置。
- BrewFS 固定 `cache.writeback_mode=upload_before_commit`，JuiceFS 客户端 `--writeback` 关闭；本 pilot 不试异步新功能。应用完成后按 SCALE 核队列、远端就绪和独立内容，另记尾部 drain/确认时间；不能将 fsync 返回作为唯一证据。

**硬件先验收**

准备 5 个独立 Linux 节点：2×执行节点（各 32 vCPU/128 GiB，输入 SSD 1 TiB＋缓存/临时 SSD 1 TiB）、RustFS（32/128、4×1 TiB 独立数据设备、EC2+2）、元数据（16/64、数据 SSD 1 TiB）、协调/观测（8/32、结果区 2 TiB）；各节点另留系统盘。每执行节点最多 8 个隔离容器，Agent＋FUSE 合计每个 2 vCPU/8 GiB、禁 swap。16 档均分两执行节点；1/8 档按预登记位置运行，两臂相同。

按 HARDWARE 核实际 CPU/内存/cgroup、磁盘容量和 inode 峰值、页缓存记账、磁盘/实例限速、私网带宽、输入盘/解析器/观测开销。共享数据节点目标 25 Gbit/s 私网；不能拿网卡标称代替实测。每卷预计峰值后至少 25% 空闲。把原始证据及缺项写入 `hardware-readiness.json`，不能把基础 host_preflight 通过当作大规模硬件通过。

只操作调度单已授权的精确实例、盘、目录和网络资源。没有实际清单/费用上限时，先提交这 5 台的具体型号、盘、网络、预计费用与缺项，**不猜采购授权、不复用历史实验实例、不凭 full access 开购**。已有适配清单则继续核验执行，无需重复索要已给的授权。私钥/凭据不入报告，严格核 host-key；只留一个管理公网入口，其他节点走私网/ProxyJump。

**按步骤监督执行**

1. 新建唯一 run 目录和调度单，写版本、pilot-plan、实际 placement、资源归属、硬件验收及输出保全位置。复用 `bench/experiment_step.py` 记录每个独立步骤；不要写包办采购、全矩阵、重试、清理的一键脚本。
2. 从 `scenarios/k8s-containers/archives.json` 解析 pytest-full-02，使用该目录 scripts 下的 `download_archives.py` / `check_archives.py` 与 `bench/trace_checks.py` 核完整 archive SHA、initial、events、每个 blob 和 final-tree；旁证通过不代替全包校验。输入在被测挂载外，宿主只读共享；每 Agent 的 workspace/HOME/XDG、挂载、会话和缓存独立。
3. 先做单 Agent 语义/内容与资源资格。使用 `tools/full-fuse-replay` 的原 Rust 工具，不改操作顺序、不删 xattr、不设总回放时限。性能回放应得到每任务 1,747,109 ops、读 281,774,624 B、写 194,706,661 B、remaining_handles=0；另核初末树、硬链接/会话及 errno。性能模式完成不能记作严格正确性通过；差异要归因并按协议处理，不能为达预期排名放宽门槛。
4. 当前缺分布式就绪屏障、活跃度/结果聚合适配，先补齐这些小步骤并用本地假 worker 验证就绪缺失、单 worker 失败、重复放行和结果缺失；再做实际小规模资格。所有 N 个环境真正就绪后由协调器一次放行，记录各 worker 实际开始/结束和同时活跃曲线；顺序启动 N 个进程不能冒充同时 N。测量路径不插入监督 AI 思考。
5. 每档完整收集两臂每个 Agent 的墙钟、操作/字节/失败、内容核验、CPU/内存/节流/网络/磁盘和后端 RPC/GET/排队，汇总 cohort makespan、任务/s、实际并发和资源瓶颈。只在结果保全、初态可重建、下一档容量及观测通过后升档。失败不自动重投，所有有效慢值保留。
6. 全部原始结果/日志/失败记录生成 SHA 清单，下载后再核 SHA；报告完成/未完成、hardware-readiness、6 个 cohort 及资格记录路径。确认本轮回放、构建、FUSE 和服务结束后，按调度单精确归属清理本轮临时资源；自备主机/原数据/其他组资源保留，云释放须查询确认。无法执行时交付具体缺项和已完成的代码/配置，不虚构测量或释放。

先给出这次的实际硬件映射和就绪状态，然后按以上范围持续推进到试跑结果保全与收尾。
