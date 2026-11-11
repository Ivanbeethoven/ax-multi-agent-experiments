# 两组并行试跑

2026-11-11新增一组纯私网节点。每组各有客户端、Redis、RustFS；两组可以同时工作，同组一次只运行一个准备或测量任务。继续使用原跳板机和协调者。

| 角色 | pool-a：BrewFS | pool-b：JuiceFS |
|---|---|---|
| 客户端 | 10.20.2.161，4C16G，100G PL1 | 10.20.2.171，4C16G，100G PL1 |
| Redis | 10.20.2.162，4C16G，60G PL1 | 10.20.2.169，4C16G，60G PL1 |
| RustFS | 10.20.2.163，8C32G，4×60G PL1 | 10.20.2.170，8C32G，4×60G PL1 |
| Agent 限额 | 2 CPU、8 GiB、禁止 swap | 相同 |

每组 RustFS 都是**单节点、四块独立盘**。本轮没有宣称四节点容错能力。Redis 和对象存储均不跨组共享。

## 输入、版本和记录位置

- 新计划：协调者 `/srv/ax-pilot-control/parallel-control-01/suite/plan.json`，19 个 C1–C3 条件；C3 明确为 100,000×4KiB，完整 C2 trace 不缩减。
- 唯一状态库：同目录的 `state.sqlite`。旧 `c123-control-01` 计划、状态和结果保留，不把旧结果改成新版本结果。
- 当前执行代码：协调者使用 `code-13`，六台执行节点使用各角色数据盘的 `code-12`；实际八份 SSH/限额探测及代码绑定在 `parallel-control-01/runtime-12-01/`。运行批次固定原始代码包和逐文件 SHA，不随仓库更新热改。
- BrewFS：`main`，提交 `ca07159f3da4b670e4aeb878faef928e69bc1634`；原生干净构建。新二进制放在客户端 `build-ca07159-01/bin/`，旧二进制保留。
- RustFS：`feat/dynamic-block-size-rebased`，提交 `f4ed202508f2153afb7956bae6f7799b60eccd00`；两组使用同一镜像，四个 layout 开关开启。
- 每批开始前仍用 `scripts/verify-latest.py` 核远端分支；资格检查期间分支推进时重新准备，测量期间保持本批产物固定。
- C2 两种文件系统统一使用 `/srv/ax-client/assets/bin/full-fuse-replay-parent-fd-01`，SHA `35c45abd910d826ea17b5d6dfc750c7083cda5e8ce8f3bbe5ef77a78ffbea1aa`，参数为 `--portable-terminal-probe --cache-parent-fds`。资格、校准和正式测量保持相同路径处理方式；完整回放全部回调，检查实际读写内容、最终目录和硬链接。旧无此参数的二进制不能代用。

## 执行和指标

`scripts/configure-pilot.py --phase c123 --files 100000` 接收真实双组 inventory，按产品分配两组。`setup-controller.py` 核六台实际主机和两个 Agent 槽。`qualify-parallel-hardware.py` 比较实例、磁盘、CPU、内存、服务版本和限额；同配置不等于同速度，比较仍保留组别信息。

每项使用 `draft-one-c1.py` 固定输入和六轮开销校准，再由 `run-one-c1.py` 执行一个条件。校准顺序为 off/on、on/off、off/on；计时差和任务 P99 差均须不超过 5%。文件锁覆盖校准和正式测量，SQLite 记录领取、各阶段及收尾；同组或共享实际资源的任务不能重叠。

每组部署三个外部采集器：客户端、Redis 节点、RustFS 节点。5 秒采样，最长间隔 30 秒；同时保留任务输出、FUSE/缓存计数、Redis 命令和内存/AOF、S3 请求和网络字节、主机 CPU/内存/网卡/物理磁盘/cgroup 原始记录。RustFS 原生指标经该组独立 OTLP collector 导出。对象服务 compose 路径和 SHA 来自实际部署，不能复用另一组的路径。

实际运行的条件保留排空后的两次零上传债务样本、正常退出、冷重挂检查、父子隔离检查及逐文件核验的原始包。启动负载前失败的记录单独关闭，不能填入性能结果或冒充原生零债务观测。失败停止并保留现场；修正后另建资产、输出和数据库，输入改变时另建输入。`retain-calibrations.py` 保留六轮原始记录；独立组之间可以分别收尾。

## 容量和费用

C3 导入前后分别记录四块对象盘、元数据盘和 Redis 用量，预算六份校准 child 和一份正式 child，保留至少 20% 余量。已有原始服务检查只有可用空间时，`capacity-check.py import-before` 保留其真实时间、设备和可用量；不补造导入前总量或已用量。

新增 pool-b 报价为 ¥4.566/小时；两组及新增协调者数据盘合计约 ¥9.237/小时，另计原跳板、EIP、OSS和网络。报价和按实际创建时间计算的费用分别留档。结束后先独立核验归档，再释放本轮新增节点及磁盘，原跳板和 OSS 数据保留。
